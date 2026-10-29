from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Literal

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from .security import decrypt_session_cookie, decrypt_session_token, encrypt_session_cookie
from .providers.letterboxd import HttpLetterboxdScraper, MockLetterboxdScraper
from .database import is_supabase_configured, run_migrations
from .store import normalize_movie_record
from .cron import router as cron_router

PROFILES = {
    "gold-standard": lambda m: m["rating"] >= 4.5,
    "hidden-gems": lambda m: m["rating"] >= 4.0 and m["popularity"] <= 50,
    "fresh-picks": lambda m: m["rating"] >= 3.8,
}

# src/api/app.py → parent = src/api, parent.parent = src, / "web" = src/web
_WEB_DIR = Path(__file__).parent.parent / "web"

SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "http").lower()
if SCRAPER_BACKEND == "mock" and os.getenv("APP_ENV", "development") != "development":
    print("[startup] WARNING: SCRAPER_BACKEND=mock in non-development environment", flush=True)
scraper = HttpLetterboxdScraper() if SCRAPER_BACKEND == "http" else MockLetterboxdScraper()
app = FastAPI(title="Swiperboxd API", version="0.6.0")

# CORS: Allow web app and extension to communicate
allowed_origins = [
    "https://swiperboxd.vercel.app",
    "https://swiperboxd.hong-yi.me",
    "http://localhost:3000",
    "http://localhost:8000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    max_age=3600,
)

# Security: Reject requests from untrusted hosts.
# Keep production strict while allowing test clients (`testserver`) in non-prod.
_trusted_hosts = ["swiperboxd.vercel.app", "swiperboxd.hong-yi.me", "localhost", "127.0.0.1"]
if os.getenv("APP_ENV", "development") != "production":
    _trusted_hosts.extend(["testserver", "*.localhost"])
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_trusted_hosts)

# Conditional store selection
if is_supabase_configured():
    from .store import SupabaseStore
    store = SupabaseStore()
    print("[startup] store=SupabaseStore", flush=True)
else:
    from .store import InMemoryStore
    store = InMemoryStore()
    print("[startup] store=InMemoryStore (Supabase not configured)", flush=True)

# Lists are now refreshed via cron job (every 3 hours on Vercel)
# No startup scraping - reads from database
print("[startup] lists loaded from database (refreshed via cron job)", flush=True)

print(f"[startup] scraper={SCRAPER_BACKEND} web_dir={_WEB_DIR}", flush=True)

# Include cron router for scheduled tasks
app.include_router(cron_router, prefix="/api/cron", tags=["cron"])


_SESSION_COOKIE_NAME = "letterboxd.user.CURRENT"


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if os.getenv("APP_ENV") == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Enhanced error responses with request context."""
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={
            "status": "error",
            "code": exc.detail.get("code", "http_error") if isinstance(exc.detail, dict) else "http_error",
            "message": exc.detail.get("message", str(exc.detail)) if isinstance(exc.detail, dict) else str(exc.detail),
            "path": str(request.url.path),
        },
    )


def _matches_profile(profile: str, movie: dict) -> bool:
    movie = normalize_movie_record(movie)
    try:
        return PROFILES[profile](movie)
    except Exception as exc:
        print(f"[deck] skipping invalid movie slug={movie.get('slug', 'unknown')} profile={profile} error={exc}", flush=True)
        return False


def _validate_letterboxd_session(
    username: str,
    session_cookie: str,
    fetch: Callable[..., object] | None = None,
) -> None:
    """Validate a Letterboxd session cookie by hitting the settings page (auth-gated).

    Uses letterboxd.user.CURRENT as the cookie name. A valid cookie gets a 200
    on /settings/ and contains the 'signed-in' body class. An invalid one gets 
    redirected to /sign-in/ OR receives a 200 OK that actually contains the sign-in page.

    When *fetch* is given it performs the request instead of a direct httpx call
    (used to route through the scraper's proxy tier chain on IP-blocked hosts).

    Raises RuntimeError if the cookie is invalid or the request fails.
    """
    import httpx as _httpx
    base_url = os.getenv("TARGET_PLATFORM_BASE_URL", "https://letterboxd.com").rstrip("/")
    url = f"{base_url}/settings/"
    try:
        if fetch is not None:
            resp = fetch(url, session_cookie=session_cookie)
        else:
            with _httpx.Client(
                cookies={_SESSION_COOKIE_NAME: session_cookie},
                timeout=15.0,
                follow_redirects=True,
            ) as client:
                resp = client.get(url)
        print(f"[auth] settings check status={resp.status_code} url={url}", flush=True)

        if "sign in" in resp.text.lower() and '<body class="signed-in"' not in resp.text:
            print(f"[auth] soft redirect to sign-in detected", flush=True)
            raise RuntimeError("session_expired_or_invalid")

        if resp.status_code != 200:
            raise RuntimeError(f"unexpected_status: {resp.status_code}")
    except _httpx.RequestError as exc:
        raise RuntimeError(f"network_error: {exc}") from exc


def _extract_username_from_cookie(
    session_cookie: str,
    fetch: Callable[..., object] | None = None,
) -> str | None:
    """Fetch the Letterboxd settings page with the given cookie and parse out the
    signed-in username. Returns None if the cookie is invalid or parsing fails.

    Letterboxd exposes the username in several stable places on every
    authenticated page:
      • `<body class="signed-in" data-owner="<username>" ...>`
      • `<a class="navitem account" href="/<username>/" ...>`
      • `<meta name="twitter:creator" content="@<username>">`
    We try each in order.

    When *fetch* is given it performs the request instead of a direct httpx call
    (used to route through the scraper's proxy tier chain on IP-blocked hosts).
    """
    import re as _re
    import httpx as _httpx
    base_url = os.getenv("TARGET_PLATFORM_BASE_URL", "https://letterboxd.com").rstrip("/")
    url = f"{base_url}/settings/"
    try:
        if fetch is not None:
            resp = fetch(url, session_cookie=session_cookie)
        else:
            with _httpx.Client(
                cookies={_SESSION_COOKIE_NAME: session_cookie},
                timeout=15.0,
                follow_redirects=False,
            ) as client:
                resp = client.get(url)
    except Exception as exc:  # includes the scraper's RuntimeError("all_tiers_failed")
        print(f"[auth] username extraction network error: {exc}", flush=True)
        return None

    if resp.status_code in {301, 302}:
        loc = resp.headers.get("location", "")
        if "sign-in" in loc or "login" in loc:
            return None
    elif resp.status_code != 200:
        return None
        
    html = resp.text
    if "sign in" in html.lower() and '<body class="signed-in"' not in html:
        print(f"[auth] username extraction: soft redirect to sign-in detected", flush=True)
        return None
    for pattern in (
        r'data-owner="([a-zA-Z0-9_-]+)"',
        r'data-current-user="([a-zA-Z0-9_-]+)"',
        r'data-username="([a-zA-Z0-9_-]+)"',
        r'<a[^>]+class="[^"]*navitem[^"]*account[^"]*"[^>]+href="/([a-zA-Z0-9_-]+)/"',
        r'<meta\s+name="twitter:creator"\s+content="@([a-zA-Z0-9_-]+)"',
        r'href="/([a-zA-Z0-9_-]+)/">\s*Profile\s*</a>',
        r'href="/([a-zA-Z0-9_-]+)/settings/"',
    ):
        m = _re.search(pattern, html)
        if m:
            return m.group(1)
    
    # Final fallback: look for the avatar link in the header which is highly stable
    avatar_match = _re.search(r'<div[^>]*class="[^"]*profile-avatar[^"]*"[^>]*>\s*<a[^>]+href="/([a-zA-Z0-9_-]+)/"', html)
    if avatar_match:
        return avatar_match.group(1)

    print("[auth] could not parse username from HTML. Snippet: " + html[:500], flush=True)
    return None


def verify_session(x_session_token: str = Header(..., alias="X-Session-Token")) -> str:
    """Decrypt X-Session-Token and return the verified username.

    Tokens are Fernet(json.dumps({"u": username, "c": session_cookie})) with a
    30-day TTL. Legacy non-JSON tokens are rejected.
    """
    master_key = os.getenv("MASTER_ENCRYPTION_KEY")
    if not master_key:
        raise HTTPException(status_code=500, detail={"code": "server_misconfigured"})
    try:
        raw = decrypt_session_token(x_session_token, master_key)
    except Exception:
        raise HTTPException(status_code=401, detail={"code": "invalid_session"})

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=401, detail={"code": "invalid_session"})
    return data.get("u", "")


def verify_extension_user(
    request: Request,
    x_session_token: str | None = Header(default=None, alias="X-Session-Token"),
) -> str:
    """Require a session token that resolves to a concrete username.

    Extension endpoints should never accept API-key style shared auth or
    legacy anonymous session payloads. The read-only progress probe answers a
    missing token with 401 rather than FastAPI's 422 so unauthenticated callers
    get a uniform auth failure.
    """
    if not x_session_token:
        status_code = 401 if request.url.path == "/ingest/progress" else 422
        raise HTTPException(status_code=status_code, detail={"code": "invalid_extension_session"})
    verified_user = verify_session(x_session_token)
    if not verified_user:
        raise HTTPException(status_code=401, detail={"code": "invalid_extension_session"})
    return verified_user


class AuthSessionRequest(BaseModel):
    session_cookie: str = Field(min_length=1)


class AuthSessionResponse(BaseModel):
    status: Literal["ok"]
    username: str
    encrypted_session_cookie: str


class PublicSessionRequest(BaseModel):
    username: str = Field(min_length=1, max_length=40)


class PublicSessionResponse(BaseModel):
    status: Literal["ok"]
    username: str
    encrypted_session_cookie: str
    mode: Literal["public"] = "public"


class PublicSyncRequest(BaseModel):
    user_id: str = Field(min_length=1)


class IngestStartRequest(BaseModel):
    user_id: str = Field(min_length=1)
    source: str = Field(default="trending", min_length=1)
    depth_pages: int = Field(default=2, ge=1, le=50)


class SwipeActionRequest(BaseModel):
    user_id: str = Field(min_length=1)
    movie_slug: str = Field(min_length=1)
    action: Literal["watchlist", "dismiss", "log"]


class ExtensionBatchRequest(BaseModel):
    user_id: str = Field(min_length=1)
    slugs: list[str] = Field(default_factory=list)
    page: int | None = None
    total_pages: int | None = None


class ExtensionSyncStatusRequest(BaseModel):
    user_id: str = Field(min_length=1)
    phase: Literal["watchlist", "diary", "list", "movies", "idle", "complete", "error"]
    current_page: int | None = None
    total_pages: int | None = None
    slugs_found: int | None = None
    message: str | None = None


class ExtensionRegisterRequest(BaseModel):
    letterboxd_session_cookie: str = Field(min_length=1)
    username: str | None = None


class ExtensionRegisterResponse(BaseModel):
    status: Literal["ok"]
    username: str
    session_token: str
    api_base: str


class ExtensionMoviePayload(BaseModel):
    slug: str = Field(min_length=1)
    title: str = ""
    poster_url: str = ""
    rating: float = 0.0
    popularity: int = 0
    genres: list[str] = Field(default_factory=list)
    synopsis: str = ""
    cast: list[str] = Field(default_factory=list)
    year: int | None = None
    director: str | None = None
    lb_film_id: str = ""


class ExtensionBatchMoviesRequest(BaseModel):
    movies: list[ExtensionMoviePayload] = Field(default_factory=list)


class ExtensionBatchListMoviesRequest(BaseModel):
    list_id: str = Field(min_length=1)
    list_url: str | None = None
    title: str | None = None
    owner_slug: str | None = None
    owner_name: str | None = None
    description: str | None = None
    film_count: int | None = None
    slugs: list[str] = Field(default_factory=list)
    page: int | None = None
    total_pages: int | None = None
    replace_memberships: bool = False


class ExtensionListSummaryPayload(BaseModel):
    list_id: str = Field(min_length=1)
    slug: str = ""
    url: str = ""
    title: str = ""
    owner_name: str = ""
    owner_slug: str = ""
    description: str = ""
    film_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    is_official: bool = False
    tags: list[str] = Field(default_factory=list)


class ExtensionBatchListSummariesRequest(BaseModel):
    lists: list[ExtensionListSummaryPayload] = Field(default_factory=list)
    source: str = "popular"
    page: int | None = None


class CacheLbIdRequest(BaseModel):
    movie_slug: str = Field(min_length=1)
    lb_film_id: str = Field(min_length=1)


# Split modules: handlers read shared state through this module (app_module) so
# monkeypatching src.api.app.store/scraper affects every route.
from .workers.sync import _run_user_history_sync, _filter_first_pipeline, _run_ingest_worker
from .routers import auth as auth_router, lists as lists_router, extension as extension_router

app.include_router(auth_router.router)
app.include_router(lists_router.router)
app.include_router(extension_router.router)
