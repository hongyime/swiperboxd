from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, Literal

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .queue import InMemoryQueue
from .security import decrypt_session_cookie, encrypt_session_cookie
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

# Conditional store selection
if is_supabase_configured():
    from .store import SupabaseStore
    store = SupabaseStore()
    print("[startup] store=SupabaseStore", flush=True)
else:
    from .store import InMemoryStore
    store = InMemoryStore()
    print("[startup] store=InMemoryStore (Supabase not configured)", flush=True)

try:
    for entry in scraper.discover_site_lists(page=1):
        store.upsert_list_summary(entry.__dict__)
except NotImplementedError:
    print(
        "[startup] WARNING: scraper does not support list discovery; "
        "catalog will be empty until first /lists/catalog request",
        flush=True,
    )

print(f"[startup] scraper={SCRAPER_BACKEND} web_dir={_WEB_DIR}", flush=True)

# Include cron router for scheduled tasks
app.include_router(cron_router, prefix="/api/cron", tags=["cron"])

queue = InMemoryQueue()


_SESSION_COOKIE_NAME = "letterboxd.user.CURRENT"


def _matches_profile(profile: str, movie: dict) -> bool:
    movie = normalize_movie_record(movie)
    try:
        return PROFILES[profile](movie)
    except Exception as exc:
        print(f"[deck] skipping invalid movie slug={movie.get('slug', 'unknown')} profile={profile} error={exc}", flush=True)
        return False


def _validate_letterboxd_session(username: str, session_cookie: str) -> None:
    """Validate a Letterboxd session cookie by hitting the settings page (auth-gated).

    Uses letterboxd.user.CURRENT as the cookie name — this is the session token
    visible in DevTools after login. A valid cookie gets a 200 on /settings/;
    an invalid/expired one gets redirected to /sign-in/.

    Raises RuntimeError if the cookie is invalid or the request fails.
    """
    import httpx as _httpx
    base_url = os.getenv("TARGET_PLATFORM_BASE_URL", "https://letterboxd.com").rstrip("/")
    url = f"{base_url}/settings/"
    try:
        with _httpx.Client(
            cookies={_SESSION_COOKIE_NAME: session_cookie},
            timeout=15.0,
            follow_redirects=False,
        ) as client:
            resp = client.get(url)
            print(f"[auth] settings check status={resp.status_code} url={url}", flush=True)
            if resp.status_code in {301, 302}:
                location = resp.headers.get("location", "")
                print(f"[auth] redirect location={location}", flush=True)
                if "sign-in" in location or "login" in location:
                    raise RuntimeError("session_expired_or_invalid")
                # Other redirects (canonical URL) — treat as OK
            elif resp.status_code == 200:
                pass  # authenticated
            else:
                raise RuntimeError(f"unexpected_status: {resp.status_code}")
    except _httpx.RequestError as exc:
        raise RuntimeError(f"network_error: {exc}") from exc


def verify_session(x_session_token: str = Header(..., alias="X-Session-Token")) -> str:
    """Decrypt X-Session-Token and return the verified username.

    New token format: Fernet(json.dumps({"u": username, "c": session_cookie}))
    Old token format: Fernet(raw_session_cookie)  — returns "" for backward compat.
    """
    master_key = os.getenv("MASTER_ENCRYPTION_KEY")
    if not master_key:
        raise HTTPException(status_code=500, detail={"code": "server_misconfigured"})
    try:
        raw = decrypt_session_cookie(x_session_token, master_key)
    except Exception:
        raise HTTPException(status_code=401, detail={"code": "invalid_session"})

    try:
        data = json.loads(raw)
        return data.get("u", "")
    except (json.JSONDecodeError, ValueError):
        # Old-format token (raw cookie string) — identity unknown, allow through
        return ""


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


@app.get("/lists/catalog")
def list_catalog(q: str | None = None, page: int = Query(default=1, ge=1)):
    """Fetch lists from Letterboxd with fallback to cached data."""
    
    # Try to fetch fresh lists from Letterboxd
    try:
        discovered = scraper.discover_site_lists(query=q, page=page)
        if discovered:
            # Store fresh data
            for entry in discovered:
                store.upsert_list_summary(entry.__dict__)
            print(f"[lists] Fetched {len(discovered)} fresh lists from Letterboxd", flush=True)
        else:
            print(f"[lists] No lists returned from Letterboxd", flush=True)
    except RuntimeError as exc:
        # Rate limited or other scraper errors - fall back to cached
        print(f"[lists] Letterboxd fetch failed ({str(exc)}), using cached data", flush=True)
    except Exception as exc:
        print(f"[lists] Unexpected error fetching lists: {exc}", flush=True)
    
    # Always return cached data
    items = store.get_lists()
    
    # Apply search filter if query provided
    if q:
        q_lower = q.lower()
        items = [
            item for item in items
            if q_lower in item.get("title", "").lower() or 
               q_lower in item.get("description", "").lower()
        ]
    
    # Sort: official first, then by like count, then by title
    items.sort(key=lambda item: (
        not item.get("is_official", False), 
        -item.get("like_count", 0), 
        item.get("title", "")
    ))
    
    return {
        "status": "ok",
        "query": q or "",
        "page": page,
        "results": items
    }


@app.get("/lists/{list_id}")
def list_detail(list_id: str):
    summary = store.get_list_summary(list_id)
    if not summary:
        discovered = scraper.discover_site_lists(page=1)
        for entry in discovered:
            store.upsert_list_summary(entry.__dict__)
        summary = store.get_list_summary(list_id)

    if not summary:
        raise HTTPException(status_code=404, detail={"code": "list_not_found"})

    movie_slugs = scraper.fetch_list_movie_slugs(list_id, list_url=summary.get("url"))
    store.replace_list_memberships(list_id, movie_slugs)
    preview = [store.get_movie(slug) for slug in movie_slugs[:4]]
    preview = [movie for movie in preview if movie]
    return {
        "status": "ok",
        "list": summary,
        "movie_slugs": movie_slugs,
        "preview": preview,
    }


@app.get("/lists/{list_id}/deck")
def list_deck(list_id: str, user_id: str = Query(min_length=1)):
    summary = store.get_list_summary(list_id)
    if not summary:
        raise HTTPException(status_code=404, detail={"code": "list_not_found"})

    movie_slugs = scraper.fetch_list_movie_slugs(list_id, list_url=summary.get("url"))
    store.replace_list_memberships(list_id, movie_slugs)

    missing = [slug for slug in movie_slugs if not store.get_movie(slug)]
    for movie in scraper.metadata_for_slugs(missing):
        store.upsert_movie(movie.__dict__)

    movies = [store.get_movie(slug) for slug in store.get_list_memberships(list_id)]
    movies = [movie for movie in movies if movie]
    movies = store.weighted_shuffle(user_id, movies)
    return {
        "status": "ok",
        "list": summary,
        "results": movies[:20],
    }


@app.post("/lists/refresh")
async def manual_refresh_lists(verified_user: str = Depends(verify_session)):
    """Manually refresh lists from Letterboxd (user-triggered).
    
    Requires authenticated session and rate limits to prevent abuse.
    """
    # Rate limit: 1 refresh per 5 minutes per user
    allowed, retry_after = store.allow_scrape_request(verified_user, min_interval_seconds=300)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={"code": "rate_limited", "retry_after": retry_after}
        )
    
    try:
        # Fetch lists from Letterboxd
        lists_data = scraper.discover_site_lists(page=1)
        print(f"[lists] manual refresh fetched {len(lists_data)} lists", flush=True)
        
        updated_count = 0
        for item in lists_data:
            existing = store.get_list_summary(item.list_id)
            # Update only if data changed
            if not existing or existing['like_count'] != item.like_count or existing['film_count'] != item.film_count:
                store.upsert_list_summary(item.__dict__)
                updated_count += 1
        
        return {
            "status": "ok",
            "fetched": len(lists_data),
            "updated": updated_count
        }
    except RuntimeError as e:
        if "rate_limited" in str(e).lower():
            raise HTTPException(
                status_code=503,
                detail={"code": "letterboxd_rate_limited", "message": "Letterboxd is rate limiting our requests"}
            )
        raise
    except Exception as e:
        print(f"[lists] manual refresh error: {e}", flush=True)
        raise HTTPException(
            status_code=500,
            detail={"code": "refresh_failed", "reason": str(e)}
        )


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

    token_payload = json.dumps({"u": payload.username, "c": payload.session_cookie})
    encrypted_cookie = encrypt_session_cookie(token_payload, master_key)
    return AuthSessionResponse(status="ok", encrypted_session_cookie=encrypted_cookie)


@app.post("/ingest/start")
async def start_ingest(payload: IngestStartRequest, verified_user: str = Depends(verify_session)):
    """Start ingest process for a user (username)."""
    if verified_user and payload.user_id != verified_user:
        raise HTTPException(status_code=403, detail={"code": "user_id_mismatch"})

    allowed, retry_after = store.allow_scrape_request(payload.user_id, min_interval_seconds=1.0)
    if not allowed:
        print(f"[ingest] rate limited for user, retry_after={retry_after:.1f}s", flush=True)
        raise HTTPException(status_code=429, detail={"code": "scrape_rate_limited", "retry_after": retry_after})

    with store.lock:
        if payload.user_id in store.ingest_running:
            print("[ingest] already running, skipping duplicate start", flush=True)
            return {"status": "already_running", "user_id": payload.user_id}
        store.ingest_running.add(payload.user_id)

    store.set_ingest_error(payload.user_id, None)
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
        "error": store.get_ingest_error(user_id),
    }


@app.get("/discovery/deck")
async def get_discovery_deck(
    user_id: str = Query(min_length=1),
    profile: str = Query(default="gold-standard")
):
    """Get a deck of movies for discovery."""
    if profile not in PROFILES:
        raise HTTPException(status_code=400, detail={"code": "invalid_profile"})

    matched = []
    skipped_invalid = 0
    for movie in store.get_movies():
        normalized = normalize_movie_record(movie)
        before_slug = normalized.get("slug", "")
        if _matches_profile(profile, normalized):
            matched.append(normalized)
        elif not before_slug:
            skipped_invalid += 1

    movies = store.weighted_shuffle(user_id, matched)
    return {
        "status": "ok",
        "profile": profile,
        "results": movies[:20],
        "meta": {
            "matched_count": len(matched),
            "skipped_invalid_count": skipped_invalid,
        },
    }


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
async def submit_swipe_action(payload: SwipeActionRequest, verified_user: str = Depends(verify_session)):
    """Submit a swipe action."""
    if verified_user and payload.user_id != verified_user:
        raise HTTPException(status_code=403, detail={"code": "user_id_mismatch"})
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

    print(f"[ingest] stage=source_fetch source={source} depth_pages={depth_pages}", flush=True)
    source_slugs = scraper.pull_source_slugs(source=source, depth_pages=depth_pages)
    print(f"[ingest] stage=source_fetch_complete count={len(source_slugs)}", flush=True)
    _emit(20)

    watchlist = store.get_watchlist(user_id)
    diary = store.get_diary(user_id)
    exclusions = store.get_exclusions(user_id)

    unique = [slug for slug in source_slugs if slug not in watchlist]
    unique = [slug for slug in unique if slug not in diary]
    unique = [slug for slug in unique if slug not in exclusions]
    print(f"[ingest] stage=filter_seen remaining={len(unique)} watchlist={len(watchlist)} diary={len(diary)} exclusions={len(exclusions)}", flush=True)
    _emit(40)

    print(f"[ingest] stage=metadata_fetch count={len(unique)}", flush=True)
    movies_raw = scraper.metadata_for_slugs(unique)
    print(f"[ingest] stage=metadata_fetch_complete count={len(movies_raw)}", flush=True)
    n = max(len(movies_raw), 1)
    metadata = []
    for i, m in enumerate(movies_raw):
        movie = m.__dict__
        store.upsert_movie(movie)
        metadata.append(movie)
        print(f"[ingest] stage=store_upsert slug={movie.get('slug', 'unknown')} index={i + 1}/{n}", flush=True)
        _emit(40 + int((i + 1) / n * 55))

    return metadata


def _run_ingest_worker(user_id: str, source: str, depth_pages: int) -> None:
    """Background worker for ingest processing with real progress events."""

    def _set_progress(pct: int) -> None:
        store.set_ingest_progress(user_id, pct)

    try:
        store.set_ingest_error(user_id, None)
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
        store.set_ingest_error(user_id, {"code": "ingest_worker_failed", "reason": str(exc)})
        print(f"[ingest] worker_error user_id={user_id} error={exc}", flush=True)
    finally:
        store.ingest_running.discard(user_id)
