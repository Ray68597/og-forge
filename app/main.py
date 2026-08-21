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
# Optional: enables Creem success-redirect signature verification on
# /v1/pro/key. Unset = subscription id alone authorizes (it is unguessable).
CREEM_API_KEY = os.environ.get("CREEM_API_KEY", "")
# Permanent Creem checkout link (the product page auto-creates a fresh
# checkout session per buyer). Override or disable via the OGF_CHECKOUT_URL
# env var: set a different URL, or an empty string to show the waitlist again.
DEFAULT_CHECKOUT_URL = "https://creem.io/product/prod_7PgaeSfn1hJUPmpBbw4B3f"
CHECKOUT_URL = os.environ.get("OGF_CHECKOUT_URL", DEFAULT_CHECKOUT_URL)

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


@app.get("/welcome", include_in_schema=False)
async def welcome() -> FileResponse:
    """Post-payment landing page: Creem's success redirect points here
    (product default_success_url) and the buyer's API key is shown."""
    return FileResponse("static/welcome.html", media_type="text/html")


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


def _get_backup_state(key: str) -> str | None:
    """Read a backup bookkeeping value. Survives sleep/wake (disk persists
    within a deploy); wiped on redeploy together with the waitlist."""
    try:
        conn = _waitlist_db()
        try:
            row = conn.execute(
                "SELECT value FROM backup_state WHERE key = ?", (key,)
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _set_backup_state(key: str, value: str) -> None:
    conn = _waitlist_db()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS backup_state ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO backup_state (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


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


@app.get("/v1/pro/key")
async def pro_key(request: Request, subscription_id: str = Query("")) -> dict:
    """Hand a paying customer their API key on the /welcome page.

    Auth: the Creem subscription id from the payment success redirect —
    an unguessable opaque id only the buyer (and Creem) knows. Creem can
    also sign the redirect (`signature` param, HMAC with the API key);
    verification is enabled when CREEM_API_KEY is set. The id alone is
    sufficient: guessing a valid sub id is infeasible and it only ever
    returns the buyer's own key.
    """
    subscription_id = subscription_id.strip()
    if not subscription_id:
        raise HTTPException(400, "subscription_id is required.")

    allowed, retry = check_rate_limit("pk:" + anon_key(client_ip(request)), limit=20)
    if not allowed:
        raise HTTPException(
            429, f"Too many requests. Retry in {retry}s.",
            headers={"Retry-After": str(retry)},
        )

    if CREEM_API_KEY:
        sig = request.query_params.get("signature", "")
        # canonical string: params in URL order, empty values excluded,
        # salt={api key} appended (per Creem checkout redirect docs)
        pairs = [
            f"{k}={v}"
            for k, v in request.query_params.multi_items()
            if k != "signature" and v
        ]
        expected = hashlib.sha256(
            ("|".join(pairs) + f"|salt={CREEM_API_KEY}").encode()
        ).hexdigest()
        if not sig or not hmac.compare_digest(sig, expected):
            print(f"[pro-key] invalid redirect signature for sub={subscription_id[:12]}…", flush=True)
            raise HTTPException(401, "Invalid signature.")

    conn = _keys_db()
    try:
        row = conn.execute(
            "SELECT api_key, email FROM api_keys WHERE subscription_id = ? AND status = 'active'",
            (subscription_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        # webhook may not have arrived yet — the welcome page polls
        raise HTTPException(404, "No active key for this subscription yet.")
    print(f"[pro-key] delivered key for sub={subscription_id[:12]}…", flush=True)
    return {"api_key": row[0], "email": row[1]}


@app.get("/v1/config")
async def config() -> dict:
    """Landing page reads this to decide: show waitlist form or buy button."""
    return {"checkout_url": CHECKOUT_URL if CHECKOUT_URL else None}


# ---------------------------------------------------------------------------
# GitHub webhook — self-triggered autodeploy. A push to master makes GitHub
# call this endpoint; we then ask Render's API to deploy. This closes the
# loop without the Render GitHub App (which requires dashboard access we
# don't have). Fallback remains: explicit POST /v1/services/{id}/deploys.
# ---------------------------------------------------------------------------

GH_WEBHOOK_SECRET = os.environ.get("OGF_GH_WEBHOOK_SECRET", "")
RENDER_API_KEY = os.environ.get("OGF_RENDER_KEY", "")
RENDER_SERVICE_ID = os.environ.get("OGF_RENDER_SERVICE", "")


def _trigger_render_deploy() -> str:
    """POST Render's deploy API. Returns the deploy id, or '?' when Render
    answers 2xx with no parsable body — observed on back-to-back pushes
    while a deploy is already building."""
    req = urllib.request.Request(
        f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/deploys",
        method="POST",
        headers={
            "Authorization": f"Bearer {RENDER_API_KEY}",
            "Content-Type": "application/json",
        },
        data=b"{}",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().strip()
    if not body:
        print("[autodeploy] 2xx empty body (likely queued)", flush=True)
        return "?"
    try:
        return json.loads(body).get("id", "?")
    except json.JSONDecodeError:
        print("[autodeploy] 2xx non-JSON body, assuming queued", flush=True)
        return "?"


async def _retry_deploy(pusher: str) -> None:
    """Background retry for transient trigger failures. Render always builds
    the latest master, so a delayed retry still ships every commit even if
    it fires long after the push that started it."""
    for delay in (90, 300):
        await asyncio.sleep(delay)
        try:
            deploy_id = await asyncio.to_thread(_trigger_render_deploy)
            print(f"[autodeploy] retry OK: deploy {deploy_id} (push by {pusher})", flush=True)
            return
        except Exception as exc:
            print(f"[autodeploy] retry failed: {exc}", flush=True)
    print("[autodeploy] retries exhausted; manual deploy may be needed", flush=True)


@app.post("/v1/webhook/github")
async def github_webhook(request: Request) -> dict:
    raw = await request.body()

    if not GH_WEBHOOK_SECRET:
        raise HTTPException(503, "Webhook not configured.")

    signature = request.headers.get("x-hub-signature-256", "")
    expected = "sha256=" + hmac.new(GH_WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        print(f"[gh-webhook] invalid signature (len={len(signature)})", flush=True)
        raise HTTPException(401, "Invalid signature.")

    event = request.headers.get("x-github-event", "")
    if event != "push":
        return {"received": True, "ignored": event}

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"received": True, "ignored": "bad-json"}

    if payload.get("ref") != "refs/heads/master":
        return {"received": True, "ignored": payload.get("ref", "?")}

    if not (RENDER_API_KEY and RENDER_SERVICE_ID):
        print("[autodeploy] render env missing, cannot trigger", flush=True)
        return {"received": True, "deployed": False}

    pusher = payload.get("pusher", {}).get("name", "?")
    try:
        deploy_id = await asyncio.to_thread(_trigger_render_deploy)
        print(f"[autodeploy] deploy {deploy_id} triggered by push from {pusher}", flush=True)
        return {"received": True, "deployed": True, "deploy_id": deploy_id}
    except Exception as exc:
        # 200 so GitHub doesn't disable the hook; background retry covers
        # transient failures (Render builds latest master on any trigger).
        print(f"[autodeploy] FAILED: {exc}; scheduling background retry", flush=True)
        asyncio.create_task(_retry_deploy(pusher))
        return {"received": True, "deployed": False, "retry_scheduled": True, "error": str(exc)}


# ---------------------------------------------------------------------------
# Self-backup — periodically commit an encrypted waitlist snapshot to the
# public repo's `backups` branch (ciphertext only; key lives in Render env).
# Rationale: Render free-tier disk is wiped on every redeploy, so SQLite is
# ephemeral. This loop + service-log mirror are the two recovery paths.
# Note: the loop only runs while the service is awake; on wake (first
# request) the first iteration fires immediately.
# ---------------------------------------------------------------------------

import asyncio
import base64
import urllib.error
import urllib.request

from cryptography.fernet import Fernet

GITHUB_TOKEN = os.environ.get("OGF_GH_TOKEN", "")
BACKUP_REPO = os.environ.get("OGF_BACKUP_REPO", "")     # "owner/repo"
BACKUP_KEY = os.environ.get("OGF_BACKUP_KEY", "")       # Fernet key
BACKUP_BRANCH = "backups"
BACKUP_INTERVAL = 1800  # 30 minutes


def _waitlist_csv() -> str:
    conn = _waitlist_db()
    try:
        rows = conn.execute("SELECT email, created_at FROM waitlist ORDER BY id").fetchall()
    finally:
        conn.close()
    return "\n".join(f"{email},{created}" for email, created in rows)


def _push_backup() -> None:
    """Fernet-encrypt the waitlist and commit it to the backups branch.
    Content-hash dedup: nothing is pushed when the CSV is unchanged since
    the last push — wakes (e.g. hourly uptime checks) stay silent.
    All failures are logged and never propagate — backup must not take
    the API down."""
    if not (GITHUB_TOKEN and BACKUP_REPO and BACKUP_KEY):
        print("[backup] not configured (env missing), skip", flush=True)
        return
    csv = _waitlist_csv()
    count = csv.count("\n") + 1 if csv else 0
    if not csv:
        print("[backup] waitlist empty, skip", flush=True)
        return
    csv_hash = hashlib.sha256(csv.encode()).hexdigest()
    if _get_backup_state("last_csv_sha256") == csv_hash:
        print("[backup] unchanged since last push, skip", flush=True)
        return
    blob = Fernet(BACKUP_KEY.encode()).encrypt(csv.encode())
    ts = time.strftime("%Y%m%d-%H%M", time.gmtime())
    path = f"backups/waitlist-{ts}.csv.enc"
    req = urllib.request.Request(
        f"https://api.github.com/repos/{BACKUP_REPO}/contents/{path}",
        method="PUT",
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        data=json.dumps({
            "message": f"backup: {count} emails",
            "content": base64.b64encode(blob).decode(),
            "branch": BACKUP_BRANCH,
        }).encode(),
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"[backup] pushed {path} ({count} emails, HTTP {r.status})", flush=True)
            _set_backup_state("last_csv_sha256", csv_hash)
    except urllib.error.HTTPError as exc:
        if exc.code == 422:  # same-path PUT without sha = file exists this cycle
            print(f"[backup] {path} already exists, skip", flush=True)
        else:
            print(f"[backup] FAILED HTTP {exc.code}: {exc.read()[:200]}", flush=True)
    except Exception as exc:
        print(f"[backup] FAILED: {exc}", flush=True)


def _fetch_latest_backup() -> str | None:
    """Return the decrypted plaintext CSV of the newest backup, or None."""
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{BACKUP_REPO}/contents/backups?ref={BACKUP_BRANCH}",
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            files = json.loads(r.read())
        names = sorted(f["name"] for f in files if f["name"].endswith(".enc"))
        if not names:
            return None
        req = urllib.request.Request(
            f"https://api.github.com/repos/{BACKUP_REPO}/contents/backups/{names[-1]}?ref={BACKUP_BRANCH}",
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            blob = json.loads(r.read())
        return Fernet(BACKUP_KEY.encode()).decrypt(base64.b64decode(blob["content"])).decode()
    except Exception as exc:
        print(f"[restore] fetch FAILED: {exc}", flush=True)
        return None


def _restore_waitlist_if_empty() -> None:
    """Rebuild the waitlist from the newest encrypted backup on startup.

    Render's ephemeral disk is wiped by every redeploy; without this the
    backup only existed for manual recovery. Runs before the backup loop
    so a restore is itself re-backed-up on the first cycle."""
    if not (GITHUB_TOKEN and BACKUP_REPO and BACKUP_KEY):
        print("[restore] not configured (env missing), skip", flush=True)
        return
    conn = _waitlist_db()
    try:
        count = conn.execute("SELECT COUNT(*) FROM waitlist").fetchone()[0]
        if count:
            print(f"[restore] waitlist has {count} row(s), skip", flush=True)
            return
        csv = _fetch_latest_backup()
        if not csv:
            print("[restore] no backup available, starting empty", flush=True)
            return
        rows = 0
        for line in csv.splitlines():
            email, _, created = line.partition(",")
            if _EMAIL_RE.match(email):
                conn.execute(
                    "INSERT OR IGNORE INTO waitlist (email, created_at) VALUES (?, ?)",
                    (email, created or None),
                )
                rows += 1
        conn.commit()
        # Remember what the restored snapshot is, so the first backup cycle
        # after this redeploy doesn't push a byte-identical duplicate.
        _set_backup_state("last_csv_sha256", hashlib.sha256(csv.encode()).hexdigest())
        print(f"[restore] restored {rows} row(s) from backup", flush=True)
    finally:
        conn.close()


async def _backup_loop() -> None:
    # first iteration runs immediately on startup / wake
    while True:
        try:
            await asyncio.to_thread(_push_backup)
        except Exception as exc:  # pragma: no cover — loop must never die
            print(f"[backup] loop error: {exc}", flush=True)
        await asyncio.sleep(BACKUP_INTERVAL)


@app.on_event("startup")
async def _start_backup_loop() -> None:
    await asyncio.to_thread(_restore_waitlist_if_empty)
    asyncio.create_task(_backup_loop())
