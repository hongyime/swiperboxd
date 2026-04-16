from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .queue import InMemoryQueue
from .security import decrypt_session_cookie, encrypt_session_cookie
from .providers.letterboxd import HttpLetterboxdScraper, MockLetterboxdScraper
from .database import is_supabase_configured, run_migrations

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
app = FastAPI(title="Swiperboxd API", version="0.5.0")

# Conditional store selection
if is_supabase_configured():
    from .store import SupabaseStore
    store = SupabaseStore()
    print("[startup] store=SupabaseStore", flush=True)
else:
    from .store import InMemoryStore
    store = InMemoryStore()
    print("[startup] store=InMemoryStore (Supabase not configured)", flush=True)

print(f"[startup] scraper={SCRAPER_BACKEND} web_dir={_WEB_DIR}", flush=True)

queue = InMemoryQueue()


def _validate_letterboxd_session(username: str, session_cookie: str) -> None:
    """Validate a Letterboxd session cookie by hitting the user's profile page.

    Raises RuntimeError if the cookie is invalid or the request fails.
    """
    import httpx as _httpx
    base_url = os.getenv("TARGET_PLATFORM_BASE_URL", "https://letterboxd.com").rstrip("/")
    url = f"{base_url}/{username}/"
    try:
        with _httpx.Client(
            cookies={"letterboxd.session": session_cookie},
            timeout=15.0,
            follow_redirects=False,
        ) as client:
            resp = client.get(url)
            print(f"[auth] profile check status={resp.status_code} url={url}", flush=True)
            # A valid session loads the profile (200) or may redirect to the same page (301/302 to same path).
            # An invalid/expired session redirects to /sign-in/.
            if resp.status_code in {301, 302}:
                location = resp.headers.get("location", "")
                if "sign-in" in location:
                    raise RuntimeError("session_expired_or_invalid")
                # Some other redirect — treat as OK (e.g. www → non-www canonical)
            elif resp.status_code == 404:
                raise RuntimeError(f"username_not_found: {username}")
            elif resp.status_code >= 400:
                raise RuntimeError(f"unexpected_status: {resp.status_code}")
    except _httpx.RequestError as exc:
        raise RuntimeError(f"network_error: {exc}") from exc


def verify_session(x_session_token: str = Header(..., alias="X-Session-Token")) -> str:
    """Require a valid encrypted session token on mutating endpoints."""
    master_key = os.getenv("MASTER_ENCRYPTION_KEY")
    if not master_key:
        raise HTTPException(status_code=500, detail={"code": "server_misconfigured"})
    try:
        return decrypt_session_cookie(x_session_token, master_key)
    except Exception:
        raise HTTPException(status_code=401, detail={"code": "invalid_session"})


class AuthSessionRequest(BaseModel):
    username: str = Field(min_length=1)
    session_cookie: str = Field(min_length=1)


class AuthSessionResponse(BaseModel):
    status: Literal["ok"]
    encrypted_session_cookie: str


class IngestStartRequest(BaseModel):
    user_id: str = Field(min_length=1)
    source: str = Field(default="trending", min_length=1)
    depth_pages: int = Field(default=2, ge=1, le=50)


class SwipeActionRequest(BaseModel):
    user_id: str = Field(min_length=1)
    movie_slug: str = Field(min_length=1)
    action: Literal["watchlist", "dismiss", "log"]


@app.get("/health")
def health():
    store_type = "SupabaseStore" if is_supabase_configured() else "InMemoryStore"
    return {"status": "ok", "app": "swiperboxd", "store": store_type}


@app.post("/db/migrate")
def migrate_database():
    """
    Run database migrations. Development only — blocked in production.
    """
    if os.getenv("APP_ENV", "development") == "production":
        raise HTTPException(status_code=403, detail={"code": "not_available_in_production"})

    if not is_supabase_configured():
        raise HTTPException(
            status_code=500, 
            detail="Supabase not configured. Cannot run migrations."
        )
    
    try:
        run_migrations()
        return {"status": "ok", "message": "Migrations completed successfully"}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"code": "migration_failed", "reason": str(e)}
        )


@app.get("/")
def root():
    return FileResponse(str(_WEB_DIR / "index.html"))


@app.get("/web/{path:path}")
def web_assets(path: str):
    # Resolve and verify the path stays within the web directory
    target = (_WEB_DIR / path).resolve()
    if not str(target).startswith(str(_WEB_DIR.resolve())):
        raise HTTPException(status_code=404)
    return FileResponse(str(target))


@app.get("/discovery/profiles")
def discovery_profiles():
    return {"profiles": list(PROFILES.keys())}


@app.post("/auth/session", response_model=AuthSessionResponse)
def create_auth_session(payload: AuthSessionRequest):
    """
    Authenticate with Letterboxd and return encrypted session cookie.
    The username is used as the user_id throughout the app.
    """
    master_key = os.getenv("MASTER_ENCRYPTION_KEY")
    if not master_key:
        raise HTTPException(status_code=500, detail={"code": "missing_master_key"})

    print(f"[auth] validating session cookie for user={payload.username}", flush=True)
    try:
        _validate_letterboxd_session(payload.username, payload.session_cookie)
        print("[auth] session cookie valid", flush=True)
    except Exception as exc:
        print(f"[auth] session validation failed: {exc}", flush=True)
        raise HTTPException(status_code=401, detail={"code": "invalid_session_cookie", "reason": str(exc)}) from exc

    encrypted_cookie = encrypt_session_cookie(payload.session_cookie, master_key)
    return AuthSessionResponse(status="ok", encrypted_session_cookie=encrypted_cookie)


@app.post("/ingest/start")
async def start_ingest(payload: IngestStartRequest, _session: str = Depends(verify_session)):
    """Start ingest process for a user (username)."""
    allowed, retry_after = store.allow_scrape_request(payload.user_id, min_interval_seconds=1.0)
    if not allowed:
        print(f"[ingest] rate limited for user, retry_after={retry_after:.1f}s", flush=True)
        raise HTTPException(status_code=429, detail={"code": "scrape_rate_limited", "retry_after": retry_after})

    if payload.user_id in store.ingest_running:
        print("[ingest] already running, skipping duplicate start", flush=True)
        return {"status": "already_running", "user_id": payload.user_id}

    store.ingest_running.add(payload.user_id)
    queue.enqueue("ingest-history", {"user_id": payload.user_id, "source": payload.source, "depth_pages": payload.depth_pages})
    print(f"[ingest] starting worker source={payload.source} depth={payload.depth_pages}", flush=True)
    threading.Thread(target=_run_ingest_worker, args=(payload.user_id, payload.source, payload.depth_pages), daemon=True).start()
    return {"status": "queued", "user_id": payload.user_id}


@app.get("/ingest/progress")
def ingest_progress(user_id: str = Query(min_length=1)):
    """Get ingest progress for a user."""
    return {
        "status": "ok",
        "user_id": user_id,
        "progress": store.get_ingest_progress(user_id),
        "running": user_id in store.ingest_running,
    }


@app.get("/discovery/deck")
async def get_discovery_deck(
    user_id: str = Query(min_length=1),
    profile: str = Query(default="gold-standard")
):
    """Get a deck of movies for discovery."""
    if profile not in PROFILES:
        raise HTTPException(status_code=400, detail={"code": "invalid_profile"})

    movies = [m for m in store.get_movies() if PROFILES[profile](m)]
    movies = store.weighted_shuffle(user_id, movies)
    return {"status": "ok", "profile": profile, "results": movies[:20]}


@app.get("/discovery/details")
def get_discovery_details(slug: str = Query(min_length=1)):
    """Get movie details."""
    movie = store.get_movie(slug)
    if not movie:
        raise HTTPException(status_code=404, detail={"code": "movie_not_found"})
    return {
        "status": "ok",
        "slug": slug,
        "synopsis": movie.get("synopsis", ""),
        "cast": movie.get("cast", []),
        "genres": movie.get("genres", []),
    }


@app.post("/actions/swipe")
async def submit_swipe_action(payload: SwipeActionRequest, _session: str = Depends(verify_session)):
    """Submit a swipe action."""
    limited, retry_after_ms = store.should_rate_limit(payload.user_id, lock_ms=500)
    if limited:
        raise HTTPException(status_code=429, detail={"code": "sync_lock", "retry_after_ms": retry_after_ms})

    movie = store.get_movie(payload.movie_slug)
    
    if payload.action == "dismiss":
        store.add_exclusion(payload.user_id, payload.movie_slug)
    elif payload.action == "watchlist":
        store.add_watchlist(payload.user_id, payload.movie_slug)
        if movie:
            store.record_genre_preference(payload.user_id, movie.get("genres", []))
    elif payload.action == "log":
        store.add_diary(payload.user_id, payload.movie_slug)

    return {"status": "accepted", "action": payload.action, "movie_slug": payload.movie_slug}


def _filter_first_pipeline(
    user_id: str,
    source: str,
    depth_pages: int,
    progress_callback: Callable[[int], None] | None = None,
) -> list[dict]:
    """Filter pipeline: pull source → exclude seen → fetch metadata → upsert to cache."""
    def _emit(pct: int) -> None:
        if progress_callback:
            progress_callback(pct)

    source_slugs = scraper.pull_source_slugs(source=source, depth_pages=depth_pages)
    _emit(20)

    watchlist = store.get_watchlist(user_id)
    diary = store.get_diary(user_id)
    exclusions = store.get_exclusions(user_id)

    unique = [slug for slug in source_slugs if slug not in watchlist]
    unique = [slug for slug in unique if slug not in diary]
    unique = [slug for slug in unique if slug not in exclusions]
    _emit(40)

    movies_raw = scraper.metadata_for_slugs(unique)
    n = max(len(movies_raw), 1)
    metadata = []
    for i, m in enumerate(movies_raw):
        movie = m.__dict__
        store.upsert_movie(movie)
        metadata.append(movie)
        _emit(40 + int((i + 1) / n * 55))

    return metadata


def _run_ingest_worker(user_id: str, source: str, depth_pages: int) -> None:
    """Background worker for ingest processing with real progress events."""

    def _set_progress(pct: int) -> None:
        store.set_ingest_progress(user_id, pct)

    try:
        _set_progress(5)
        _filter_first_pipeline(
            user_id=user_id,
            source=source,
            depth_pages=depth_pages,
            progress_callback=_set_progress,
        )
        _set_progress(100)

    except Exception as exc:
        store.set_ingest_progress(user_id, -1)
        print(f"Ingest worker error for user {user_id}: {exc}")
    finally:
        store.ingest_running.discard(user_id)
