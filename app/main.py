"""OG Forge — API server.

Free tier: unauthenticated requests are rate-limited per IP.
Pro tier: API key removes limits (provisioned via Creem webhooks).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .generator import CardSpec, TEMPLATES, render

app = FastAPI(
    title="OG Forge",
    description="Open Graph image generation API. Free tier: 10 images/minute per IP.",
    version="1.0.0",
    docs_url="/docs",
)

# ---------------------------------------------------------------------------
# Rate limiting (in-memory; swap for Redis in production)
# ---------------------------------------------------------------------------

RATE_LIMIT_FREE = 10          # requests
RATE_WINDOW = 60              # seconds
_hits: dict[str, deque[float]] = defaultdict(deque)

# API keys -> tier. Persisted in SQLite (ephemeral on Render free tier;
# every provisioning event is also mirrored to service logs as backup).
API_KEYS: dict[str, str] = {}  # {"ogf_live_xxx": "pro", ...}

CREEM_WEBHOOK_SECRET = os.environ.get("CREEM_WEBHOOK_SECRET", "")
CHECKOUT_URL = os.environ.get("OGF_CHECKOUT_URL", "")

# SQLite path (shared by waitlist + api_keys; ephemeral on Render free tier,
# so every write is mirrored to service logs as a backup).
WAITLIST_DB = os.environ.get("OGF_WAITLIST_DB", "data/waitlist.db")
WAITLIST_ADMIN_KEY = os.environ.get("OGF_ADMIN_KEY", "")  # unset = export disabled


def _keys_db() -> sqlite3.Connection:
    d = os.path.dirname(WAITLIST_DB)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(WAITLIST_DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS api_keys ("
        "api_key TEXT PRIMARY KEY, "
        "email TEXT NOT NULL, "
        "subscription_id TEXT UNIQUE, "
        "status TEXT NOT NULL DEFAULT 'active', "
        "created_at TEXT DEFAULT (datetime('now')))"
    )
    return conn


def _load_api_keys() -> None:
    try:
        conn = _keys_db()
        try:
            rows = conn.execute(
                "SELECT api_key, status FROM api_keys WHERE status = 'active'"
            ).fetchall()
        finally:
            conn.close()
        for key, _status in rows:
            API_KEYS[key] = "pro"
        if rows:
            print(f"[keys] loaded {len(rows)} active key(s) from db", flush=True)
    except Exception as exc:  # pragma: no cover - defensive on ephemeral disk
        print(f"[keys] load failed: {exc}", flush=True)


_load_api_keys()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate_limit(key: str, limit: int = RATE_LIMIT_FREE, window: int = RATE_WINDOW) -> tuple[bool, int]:
    """Returns (allowed, retry_after_seconds)."""
    now = time.monotonic()
    q = _hits[key]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        retry = int(window - (now - q[0])) + 1
        return False, max(retry, 1)
    q.append(now)
    return True, 0


def anon_key(ip: str) -> str:
    return "ip:" + hashlib.sha256(ip.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse("static/landing.html", media_type="text/html")


@app.get("/privacy", include_in_schema=False)
async def privacy() -> FileResponse:
    return FileResponse("static/privacy.html", media_type="text/html")


@app.get("/terms", include_in_schema=False)
async def terms() -> FileResponse:
    return FileResponse("static/terms.html", media_type="text/html")


@app.get("/v1/templates")
async def list_templates() -> dict:
    return {"templates": TEMPLATES}


@app.get("/v1/generate", responses={200: {"content": {"image/png": {}}}})
async def generate_get(
    request: Request,
    title: str = Query(..., max_length=200, description="Card title"),
    subtitle: str = Query("", max_length=300),
    brand: str = Query("", max_length=60),
    template: str = Query("gradient"),
    bg_color: str | None = Query(None, max_length=9),
    bg_color2: str | None = Query(None, max_length=9),
    accent_color: str | None = Query(None, max_length=9),
    text_color: str | None = Query(None, max_length=9),
    width: int = Query(1200, ge=200, le=2400),
    height: int = Query(630, ge=200, le=2400),
    theme: str = Query("auto"),
) -> Response:
    # tier resolution
    api_key = request.headers.get("x-api-key", "")
    tier = API_KEYS.get(api_key)
    if not tier:
        allowed, retry = check_rate_limit(anon_key(client_ip(request)))
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Free tier limit reached ({RATE_LIMIT_FREE}/min). Add an API key or retry in {retry}s.",
                headers={"Retry-After": str(retry)},
            )

    if template not in TEMPLATES:
        raise HTTPException(400, f"Unknown template '{template}'. Available: {TEMPLATES}")

    spec = CardSpec(
        title=title,
        subtitle=subtitle,
        brand=brand,
        template=template,
        bg_color=bg_color,
        bg_color2=bg_color2,
        accent_color=accent_color,
        text_color=text_color,
        width=width,
        height=height,
        theme=theme,
    )
    png = render(spec)
    return Response(
        content=png,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-OG-Forge-Tier": tier or "free",
        },
    )


@app.get("/v1/health")
async def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Waitlist — capture demand while payments are being set up
# (SQLite on Render free tier is ephemeral; export periodically via the
# admin endpoint and mirror signups to service logs as a backup.)
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _waitlist_db() -> sqlite3.Connection:
    d = os.path.dirname(WAITLIST_DB)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(WAITLIST_DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS waitlist ("
        "id INTEGER PRIMARY KEY, "
        "email TEXT UNIQUE NOT NULL, "
        "created_at TEXT DEFAULT (datetime('now')))"
    )
    return conn


@app.post("/v1/waitlist")
async def join_waitlist(request: Request, payload: dict) -> dict:
    email = str(payload.get("email", "")).strip().lower()
    if not _EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(400, "A valid email address is required.")

    # 5 sign-up attempts / minute / IP keeps bots out.
    allowed, retry = check_rate_limit("wl:" + anon_key(client_ip(request)), limit=5)
    if not allowed:
        raise HTTPException(
            429,
            f"Too many requests. Retry in {retry}s.",
            headers={"Retry-After": str(retry)},
        )

    conn = _waitlist_db()
    try:
        conn.execute("INSERT OR IGNORE INTO waitlist(email) VALUES (?)", (email,))
        conn.commit()
        (count,) = conn.execute("SELECT COUNT(*) FROM waitlist").fetchone()
    finally:
        conn.close()
    print(f"[waitlist] signup total={count}", flush=True)  # log backup (ephemeral disk)
    return {"ok": True, "position": count}


@app.get("/v1/waitlist/export")
async def export_waitlist(key: str = Query("")) -> Response:
    if not WAITLIST_ADMIN_KEY or key != WAITLIST_ADMIN_KEY:
        raise HTTPException(404)
    conn = _waitlist_db()
    try:
        rows = conn.execute("SELECT email, created_at FROM waitlist ORDER BY id").fetchall()
    finally:
        conn.close()
    body = "\n".join(f"{email},{created}" for email, created in rows)
    return Response(
        content=body or "email,created_at",
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=waitlist.csv"},
    )


# ---------------------------------------------------------------------------
# Creem webhook — auto-provision API keys on payment, revoke on cancel.
# Signature: HMAC-SHA256(secret, raw_body) in "creem-signature" header.
# Events handled (idempotent, keyed by subscription_id / checkout id):
#   grant:   checkout.completed, subscription.active, subscription.paid,
#            subscription.trialing
#   revoke:  subscription.canceled, subscription.expired, subscription.unpaid
# ---------------------------------------------------------------------------

_GRANT_EVENTS = {"checkout.completed", "subscription.active", "subscription.paid", "subscription.trialing"}
_REVOKE_EVENTS = {"subscription.canceled", "subscription.expired", "subscription.unpaid"}


def _provision_key(email: str, subscription_id: str) -> str:
    """Create (or reuse) a Pro API key for this subscriber. Idempotent."""
    conn = _keys_db()
    try:
        row = conn.execute(
            "SELECT api_key FROM api_keys WHERE subscription_id = ? AND status = 'active'",
            (subscription_id,),
        ).fetchone()
        if row:
            API_KEYS[row[0]] = "pro"
            print(f"[key] reuse email={email} sub={subscription_id}", flush=True)
            return row[0]
        key = "ogf_live_" + secrets.token_hex(20)
        conn.execute(
            "INSERT OR REPLACE INTO api_keys(api_key, email, subscription_id, status)"
            " VALUES (?, ?, ?, 'active')",
            (key, email, subscription_id),
        )
        conn.commit()
    finally:
        conn.close()
    API_KEYS[key] = "pro"
    # log backup: keys survive ephemeral-disk redeploys via service logs
    print(f"[key] GRANT email={email} sub={subscription_id} key={key}", flush=True)
    return key


def _revoke_key(subscription_id: str, email: str = "") -> None:
    conn = _keys_db()
    try:
        rows = conn.execute(
            "SELECT api_key FROM api_keys WHERE subscription_id = ?", (subscription_id,)
        ).fetchall()
        conn.execute(
            "UPDATE api_keys SET status = 'revoked' WHERE subscription_id = ?",
            (subscription_id,),
        )
        conn.commit()
    finally:
        conn.close()
    for (key,) in rows:
        API_KEYS.pop(key, None)
    print(f"[key] REVOKE email={email} sub={subscription_id} count={len(rows)}", flush=True)


@app.post("/v1/webhook/creem")
async def creem_webhook(request: Request) -> dict:
    raw = await request.body()

    if not CREEM_WEBHOOK_SECRET:
        print("[webhook] rejected: CREEM_WEBHOOK_SECRET not configured", flush=True)
        raise HTTPException(503, "Webhook not configured.")

    signature = request.headers.get("creem-signature", "")
    expected = hmac.new(CREEM_WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        print(f"[webhook] invalid signature (len={len(signature)})", flush=True)
        raise HTTPException(401, "Invalid signature.")

    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON payload.")

    event_type = event.get("eventType", "")
    obj = event.get("object") or {}
    customer = obj.get("customer") or {}
    email = str(customer.get("email", "")).strip().lower()
    subscription = obj.get("subscription") or {}
    # subscription object may be nested or the object itself for sub events
    subscription_id = str(
        subscription.get("id") or obj.get("subscription_id") or obj.get("id") or ""
    )

    print(f"[webhook] type={event_type} email={email} sub={subscription_id}", flush=True)

    if event_type in _GRANT_EVENTS and email and subscription_id:
        _provision_key(email, subscription_id)
    elif event_type in _REVOKE_EVENTS and subscription_id:
        _revoke_key(subscription_id, email)

    # Always 200 so Creem doesn't retry known/unhandled event types.
    return {"received": True, "type": event_type}


@app.get("/v1/config")
async def config() -> dict:
    """Landing page reads this to decide: show waitlist form or buy button."""
    return {"checkout_url": CHECKOUT_URL if CHECKOUT_URL else None}
