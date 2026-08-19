"""OG Forge — API server.

Free tier: unauthenticated requests are rate-limited per IP.
Pro tier: API key removes limits (key validation hook included).
"""

from __future__ import annotations

import hashlib
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

# API keys -> tier. In production, load from env/DB and validate against
# your billing provider (Stripe/Lemon Squeezy webhook-provisioned keys).
API_KEYS: dict[str, str] = {}  # {"ogf_live_xxx": "pro", ...}


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
