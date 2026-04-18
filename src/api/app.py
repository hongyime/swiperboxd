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

# Lists are now refreshed via cron job (every 3 hours on Vercel)
# No startup scraping - reads from database
print("[startup] lists loaded from database (refreshed via cron job)", flush=True)

print(f"[startup] scraper={SCRAPER_BACKEND} web_dir={_WEB_DIR}", flush=True)

# Include cron router for scheduled tasks
app.include_router(cron_router, prefix="/api/cron", tags=["cron"])


_SESSION_COOKIE_NAME = "letterboxd.user.CURRENT"


def _push_to_letterboxd(action: str, movie_slug: str, session_cookie: str) -> bool:
    """Write a watchlist or diary entry back to Letterboxd using the user's session cookie.

    Returns True on success, False on failure (non-fatal — local DB is already updated).
    """
    import httpx as _httpx
    base_url = os.getenv("TARGET_PLATFORM_BASE_URL", "https://letterboxd.com").rstrip("/")

    try:
        with _httpx.Client(
            cookies={_SESSION_COOKIE_NAME: session_cookie},
            timeout=15.0,
            follow_redirects=True,
        ) as client:
            # First fetch the film page to get the CSRF token
            film_resp = client.get(f"{base_url}/film/{movie_slug}/")
            if film_resp.status_code != 200:
                print(f"[lb-write] film page fetch failed status={film_resp.status_code} slug={movie_slug}", flush=True)
                return False

            # Extract __csrf token from the page
            import re as _re
            csrf_match = _re.search(r'name="__csrf"\s+value="([^"]+)"', film_resp.text)
            if not csrf_match:
                # Try meta tag
                csrf_match = _re.search(r'<meta[^>]+name="csrf-token"[^>]+content="([^"]+)"', film_resp.text)
            if not csrf_match:
                print(f"[lb-write] could not find CSRF token for slug={movie_slug}", flush=True)
                return False
            csrf_token = csrf_match.group(1)

            if action == "watchlist":
                resp = client.post(
                    f"{base_url}/film/{movie_slug}/watchlist/",
                    data={"__csrf": csrf_token},
                    headers={"Referer": f"{base_url}/film/{movie_slug}/"},
                )
                success = resp.status_code in {200, 201, 302}
                print(f"[lb-write] watchlist slug={movie_slug} status={resp.status_code}", flush=True)
                return success

            elif action == "log":
                # Add to diary via the /diary/save/ endpoint
                resp = client.post(
                    f"{base_url}/diary/save/",
                    data={
                        "__csrf": csrf_token,
                        "filmSlug": movie_slug,
                        "specifiedDate": "false",
                        "rating": "",
                        "review": "",
                        "containsSpoilers": "false",
                        "rewatch": "false",
                    },
                    headers={"Referer": f"{base_url}/film/{movie_slug}/"},
                )
                success = resp.status_code in {200, 201, 302}
                print(f"[lb-write] diary slug={movie_slug} status={resp.status_code}", flush=True)
                return success

    except Exception as exc:
        print(f"[lb-write] ERROR action={action} slug={movie_slug}: {exc}", flush=True)
        return False

    return False


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


def _extract_username_from_cookie(session_cookie: str) -> str | None:
    """Fetch the Letterboxd settings page with the given cookie and parse out the
    signed-in username. Returns None if the cookie is invalid or parsing fails.

    Letterboxd exposes the username in several stable places on every
    authenticated page:
      • `<body class="signed-in" data-owner="<username>" ...>`
      • `<a class="navitem account" href="/<username>/" ...>`
      • `<meta name="twitter:creator" content="@<username>">`
    We try each in order.
    """
    import re as _re
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
    except _httpx.RequestError as exc:
        print(f"[auth] username extraction network error: {exc}", flush=True)
        return None

    if resp.status_code in {301, 302}:
        loc = resp.headers.get("location", "")
        if "sign-in" in loc or "login" in loc:
            return None
    elif resp.status_code != 200:
        return None

    html = resp.text
    for pattern in (
        r'data-owner="([a-zA-Z0-9_-]+)"',
        r'data-current-user="([a-zA-Z0-9_-]+)"',
        r'data-username="([a-zA-Z0-9_-]+)"',
        r'<a[^>]+class="[^"]*navitem[^"]*account[^"]*"[^>]+href="/([a-zA-Z0-9_-]+)/"',
        r'<meta\s+name="twitter:creator"\s+content="@([a-zA-Z0-9_-]+)"',
    ):
        m = _re.search(pattern, html)
        if m:
            return m.group(1)
    return None


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


@app.get("/health")
def health():
    store_type = "SupabaseStore" if is_supabase_configured() else "InMemoryStore"
    return {"status": "ok", "app": "swiperboxd", "store": store_type}


@app.post("/db/migrate")
def migrate_database(verified_user: str = Depends(verify_session)):
    """
    Run database migrations. Development only — blocked in production.
    Requires a valid session token (X-Session-Token header).
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
    
    # Filter out lists that have a partial scrape: we started but captured
    # less than 50% of the film_count. scraped_film_count == 0 is treated as
    # "not yet attempted" and the list stays visible (the list-detail endpoint
    # scrapes memberships on demand).
    before_filter = len(items)
    items = [
        item for item in items
        if item.get("film_count", 0) == 0
        or item.get("scraped_film_count", 0) == 0
        or item.get("scraped_film_count", 0) >= item.get("film_count", 1) * 0.5
    ]
    if len(items) < before_filter:
        print(f"[lists] filtered {before_filter - len(items)} partially scraped lists", flush=True)

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
        try:
            discovered = scraper.discover_site_lists(page=1)
            for entry in discovered:
                store.upsert_list_summary(entry.__dict__)
        except Exception as exc:
            print(f"[list_detail] discover_site_lists failed: {exc}", flush=True)
        summary = store.get_list_summary(list_id)

    if not summary:
        raise HTTPException(status_code=404, detail={"code": "list_not_found"})

    movie_slugs: list[str] = []
    try:
        movie_slugs = scraper.fetch_list_movie_slugs(list_id, list_url=summary.get("url")) or []
    except Exception as exc:
        print(f"[list_detail] scrape failed for {list_id}: {exc} — falling back to cache", flush=True)

    if movie_slugs:
        try:
            store.replace_list_memberships(list_id, movie_slugs)
            try:
                store.update_list_scrape_count(list_id, len(movie_slugs))
            except Exception as exc:
                print(f"[list_detail] update_list_scrape_count skipped: {exc}", flush=True)
        except Exception as exc:
            print(f"[list_detail] replace_list_memberships failed: {exc}", flush=True)
    else:
        movie_slugs = store.get_list_memberships(list_id)

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

    movie_slugs: list[str] = []
    # On Vercel, skip the live list scrape — it times out and the cache is
    # kept fresh by the cron job. Only scrape on long-running servers.
    if not os.getenv("VERCEL"):
        try:
            movie_slugs = scraper.fetch_list_movie_slugs(list_id, list_url=summary.get("url")) or []
        except Exception as exc:
            print(f"[deck] scrape failed for {list_id}: {exc} — falling back to cache", flush=True)

        if movie_slugs:
            try:
                store.replace_list_memberships(list_id, movie_slugs)
                try:
                    store.update_list_scrape_count(list_id, len(movie_slugs))
                except Exception as exc:
                    print(f"[deck] update_list_scrape_count skipped: {exc}", flush=True)
            except Exception as exc:
                print(f"[deck] replace_list_memberships failed: {exc}", flush=True)

    # Always fall back to cached memberships
    if not movie_slugs:
        try:
            movie_slugs = store.get_list_memberships(list_id)
        except Exception as exc:
            print(f"[deck] get_list_memberships failed: {exc}", flush=True)
            raise HTTPException(status_code=500, detail={"code": "store_error", "reason": str(exc)})
        print(f"[deck] using {len(movie_slugs)} cached slugs for {list_id}", flush=True)

    # Only fetch missing metadata on non-Vercel (too slow for serverless)
    if not os.getenv("VERCEL"):
        missing = [slug for slug in movie_slugs if not store.get_movie(slug)]
        try:
            for movie in scraper.metadata_for_slugs(missing):
                store.upsert_movie(movie.__dict__)
        except Exception as exc:
            print(f"[deck] metadata_for_slugs failed: {exc} — continuing with existing movies", flush=True)

    # Filter out movies the user has already watchlisted, logged, or dismissed
    try:
        watchlist = store.get_watchlist(user_id)
        diary = store.get_diary(user_id)
        exclusions = store.get_exclusions(user_id)
    except Exception as exc:
        print(f"[deck] failed to load user filters: {exc}", flush=True)
        watchlist, diary, exclusions = set(), set(), set()
    seen = watchlist | diary | exclusions

    try:
        cached_slugs = store.get_list_memberships(list_id)
        movies_by_slug = store.get_movies_by_slugs(cached_slugs)
        movies = [movies_by_slug[slug] for slug in cached_slugs if slug in movies_by_slug]
    except Exception as exc:
        print(f"[deck] failed to load movies from store: {exc}", flush=True)
        raise HTTPException(status_code=500, detail={"code": "store_error", "reason": str(exc)})

    movies = [m for m in movies if m and m.get("slug") not in seen]
    try:
        movies = store.weighted_shuffle(user_id, movies)
    except Exception as exc:
        print(f"[deck] weighted_shuffle failed: {exc} — using unshuffled", flush=True)
        import random as _random
        _random.shuffle(movies)

    print(f"[deck] list={list_id} total={len(movie_slugs)} after_filter={len(movies)} seen={len(seen)}", flush=True)
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

    # Persist encrypted session in Supabase for future server-side operations
    try:
        store.save_user_session(payload.username, encrypted_cookie)
        print(f"[auth] encrypted session stored in DB for user={payload.username}", flush=True)
    except Exception as exc:
        print(f"[auth] WARNING: failed to store session in DB: {exc}", flush=True)

    return AuthSessionResponse(status="ok", encrypted_session_cookie=encrypted_cookie)


@app.post("/ingest/start")
async def start_ingest(
    payload: IngestStartRequest,
    verified_user: str = Depends(verify_session),
    x_session_token: str = Header(None, alias="X-Session-Token"),
):
    """Start ingest process for a user (username).

    On Vercel (detected via VERCEL env var): runs the user-history sync
    synchronously within this request so the background thread isn't killed.
    On long-running servers: spawns a background thread as before.
    """
    if verified_user and payload.user_id != verified_user:
        raise HTTPException(status_code=403, detail={"code": "user_id_mismatch"})

    # Extract the raw Letterboxd session cookie from the encrypted token.
    session_cookie: str | None = None
    master_key = os.getenv("MASTER_ENCRYPTION_KEY")
    if not master_key:
        print("[ingest] WARNING: MASTER_ENCRYPTION_KEY not set — cannot decrypt session token", flush=True)
    elif not x_session_token:
        print("[ingest] WARNING: no X-Session-Token header received — diary/watchlist sync will be skipped", flush=True)
    else:
        try:
            raw = decrypt_session_cookie(x_session_token, master_key)
            data = json.loads(raw)
            session_cookie = data.get("c")
            if session_cookie:
                print(f"[ingest] session cookie extracted OK (length={len(session_cookie)})", flush=True)
            else:
                print("[ingest] WARNING: decrypted token has no 'c' field — session cookie missing from payload", flush=True)
        except Exception as exc:
            print(f"[ingest] ERROR: failed to decrypt session token: {type(exc).__name__}: {exc}", flush=True)

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
    store.set_ingest_progress(payload.user_id, 0)
    ingest_username = verified_user or payload.user_id

    # ── Vercel serverless: threads are killed when the response is sent, so run
    #    the sync inline (awaited) within this request's lifetime.
    #    We only sync watchlist/diary (capped pages) — the movie catalog is
    #    already seeded via the seed script, so full metadata fetch is skipped.
    if os.getenv("VERCEL"):
        print(
            f"[ingest] Vercel mode — running user-history sync inline "
            f"(watchlist+diary, capped) username={ingest_username} "
            f"session_cookie_present={session_cookie is not None}",
            flush=True,
        )
        import asyncio as _asyncio
        sync_stats: dict = {"watchlist_count": 0, "diary_count": 0, "errors": []}
        try:
            sync_stats = await _asyncio.wait_for(
                _asyncio.to_thread(
                    _run_user_history_sync,
                    payload.user_id, session_cookie, ingest_username,
                ),
                timeout=55.0,  # stay within Vercel's 60 s function limit
            )
        except _asyncio.TimeoutError:
            print("[ingest] Vercel inline sync timed out — partial sync saved", flush=True)
            store.set_ingest_progress(payload.user_id, 100)
            sync_stats["errors"].append("sync timed out (55s limit)")
        except Exception as exc:
            print(f"[ingest] Vercel inline sync error: {type(exc).__name__}: {exc}", flush=True)
            store.set_ingest_error(payload.user_id, {"code": "ingest_worker_failed", "reason": str(exc)})
            store.set_ingest_progress(payload.user_id, -1)
            sync_stats["errors"].append(str(exc))
        finally:
            store.ingest_running.discard(payload.user_id)

        # Always include current DB counts so the UI can render cards even when
        # the live scrape failed (expected on Vercel — Letterboxd blocks AWS IPs).
        try:
            db_watchlist = len(store.get_watchlist(payload.user_id))
            db_diary = len(store.get_diary(payload.user_id))
        except Exception as exc:
            print(f"[ingest] could not read DB counts: {exc}", flush=True)
            db_watchlist = 0
            db_diary = 0
        sync_stats["db_watchlist_count"] = db_watchlist
        sync_stats["db_diary_count"] = db_diary
        print(
            f"[ingest] Vercel done: live_watchlist={sync_stats.get('watchlist_count', 0)} "
            f"live_diary={sync_stats.get('diary_count', 0)} "
            f"db_watchlist={db_watchlist} db_diary={db_diary} "
            f"errors={len(sync_stats.get('errors', []))}",
            flush=True,
        )
        return {
            "status": "completed",
            "user_id": payload.user_id,
            "sync_stats": sync_stats,
        }

    # ── Long-running server: background thread ──────────────────────────────
    print(
        f"[ingest] starting worker source={payload.source} "
        f"depth={payload.depth_pages} username={ingest_username}",
        flush=True,
    )
    threading.Thread(
        target=_run_ingest_worker,
        args=(payload.user_id, payload.source, payload.depth_pages, session_cookie, ingest_username),
        daemon=True,
    ).start()
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
async def submit_swipe_action(
    payload: SwipeActionRequest,
    verified_user: str = Depends(verify_session),
    x_session_token: str = Header(None, alias="X-Session-Token"),
):
    """Submit a swipe action and write back to Letterboxd."""
    if verified_user and payload.user_id != verified_user:
        raise HTTPException(status_code=403, detail={"code": "user_id_mismatch"})
    limited, retry_after_ms = store.should_rate_limit(payload.user_id, lock_ms=500)
    if limited:
        raise HTTPException(status_code=429, detail={"code": "sync_lock", "retry_after_ms": retry_after_ms})

    movie = store.get_movie(payload.movie_slug)

    # Pre-check for duplicates so we can return a distinct 409 the frontend can handle
    if payload.action == "watchlist":
        if payload.movie_slug in store.get_watchlist(payload.user_id):
            return JSONResponse(
                status_code=409,
                content={"code": "already_in_watchlist", "action": payload.action, "movie_slug": payload.movie_slug},
            )
        store.add_watchlist(payload.user_id, payload.movie_slug)
        if movie:
            store.record_genre_preference(payload.user_id, movie.get("genres", []))
    elif payload.action == "log":
        if payload.movie_slug in store.get_diary(payload.user_id):
            return JSONResponse(
                status_code=409,
                content={"code": "already_in_diary", "action": payload.action, "movie_slug": payload.movie_slug},
            )
        store.add_diary(payload.user_id, payload.movie_slug)
    elif payload.action == "dismiss":
        store.add_exclusion(payload.user_id, payload.movie_slug)

    # Write back to Letterboxd for watchlist/log actions
    lb_synced = False
    if payload.action in {"watchlist", "log"} and x_session_token:
        master_key = os.getenv("MASTER_ENCRYPTION_KEY")
        if master_key:
            try:
                raw = decrypt_session_cookie(x_session_token, master_key)
                data = json.loads(raw)
                session_cookie = data.get("c")
                if session_cookie:
                    import asyncio as _asyncio
                    lb_synced = await _asyncio.wait_for(
                        _asyncio.to_thread(_push_to_letterboxd, payload.action, payload.movie_slug, session_cookie),
                        timeout=12.0,
                    )
            except Exception as exc:
                print(f"[swipe] lb write-back failed: {exc}", flush=True)

    return {
        "status": "accepted",
        "action": payload.action,
        "movie_slug": payload.movie_slug,
        "lb_synced": lb_synced,
    }


_EXTENSION_BATCH_LIMIT = 500


@app.post("/api/extension/batch/watchlist")
async def extension_batch_watchlist(
    payload: ExtensionBatchRequest,
    verified_user: str = Depends(verify_session),
):
    """Push a batch of watchlist slugs scraped by the Chrome extension."""
    if verified_user and payload.user_id != verified_user:
        raise HTTPException(status_code=403, detail={"code": "user_id_mismatch"})
    if len(payload.slugs) > _EXTENSION_BATCH_LIMIT:
        raise HTTPException(status_code=413, detail={"code": "batch_too_large", "limit": _EXTENSION_BATCH_LIMIT})

    print(
        f"[extension] watchlist batch user={payload.user_id} "
        f"page={payload.page}/{payload.total_pages} slugs={len(payload.slugs)}",
        flush=True,
    )
    result = store.batch_add_watchlist(payload.user_id, payload.slugs)
    return {
        "status": "ok",
        "user_id": payload.user_id,
        "page": payload.page,
        "total_pages": payload.total_pages,
        "result": result,
    }


@app.post("/api/extension/batch/diary")
async def extension_batch_diary(
    payload: ExtensionBatchRequest,
    verified_user: str = Depends(verify_session),
):
    """Push a batch of diary slugs scraped by the Chrome extension."""
    if verified_user and payload.user_id != verified_user:
        raise HTTPException(status_code=403, detail={"code": "user_id_mismatch"})
    if len(payload.slugs) > _EXTENSION_BATCH_LIMIT:
        raise HTTPException(status_code=413, detail={"code": "batch_too_large", "limit": _EXTENSION_BATCH_LIMIT})

    print(
        f"[extension] diary batch user={payload.user_id} "
        f"page={payload.page}/{payload.total_pages} slugs={len(payload.slugs)}",
        flush=True,
    )
    result = store.batch_add_diary(payload.user_id, payload.slugs)
    return {
        "status": "ok",
        "user_id": payload.user_id,
        "page": payload.page,
        "total_pages": payload.total_pages,
        "result": result,
    }


@app.post("/api/extension/register", response_model=ExtensionRegisterResponse)
def extension_register(payload: ExtensionRegisterRequest, request: Request):
    """Self-register an extension install using the user's Letterboxd cookie.

    Flow:
      1. Validate the cookie against letterboxd.com/settings/
      2. Parse the signed-in username out of the HTML response
      3. Encrypt the cookie with MASTER_ENCRYPTION_KEY + persist to Supabase
      4. Return a Swiperboxd session token + the resolved username

    Needs no prior interaction with the web app.
    """
    master_key = os.getenv("MASTER_ENCRYPTION_KEY")
    if not master_key:
        raise HTTPException(status_code=500, detail={"code": "missing_master_key"})

    try:
        _validate_letterboxd_session("", payload.letterboxd_session_cookie)
    except Exception as exc:
        raise HTTPException(status_code=401, detail={"code": "invalid_letterboxd_cookie", "reason": str(exc)}) from exc

    username = _extract_username_from_cookie(payload.letterboxd_session_cookie)
    if not username:
        raise HTTPException(
            status_code=422,
            detail={"code": "username_unresolved", "message": "could not parse username from Letterboxd response"},
        )

    token_payload = json.dumps({"u": username, "c": payload.letterboxd_session_cookie})
    encrypted = encrypt_session_cookie(token_payload, master_key)

    try:
        store.save_user_session(username, encrypted)
    except Exception as exc:
        print(f"[extension/register] WARNING: failed to persist session: {exc}", flush=True)

    base_url = str(request.base_url).rstrip("/")
    print(f"[extension/register] registered username={username} api_base={base_url}", flush=True)
    return ExtensionRegisterResponse(status="ok", username=username, session_token=encrypted, api_base=base_url)


@app.post("/api/extension/batch/movies")
async def extension_batch_movies(
    payload: ExtensionBatchMoviesRequest,
    verified_user: str = Depends(verify_session),
):
    """Push metadata for films scraped directly from /film/<slug>/ pages."""
    if len(payload.movies) > _EXTENSION_BATCH_LIMIT:
        raise HTTPException(status_code=413, detail={"code": "batch_too_large", "limit": _EXTENSION_BATCH_LIMIT})

    stored = 0
    failed: list[dict] = []
    for movie in payload.movies:
        try:
            record = movie.model_dump()
            record = normalize_movie_record(record)
            store.upsert_movie(record)
            stored += 1
        except Exception as exc:
            failed.append({"slug": movie.slug, "error": str(exc)})

    print(
        f"[extension] movies batch user={verified_user or '?'} "
        f"stored={stored} failed={len(failed)}",
        flush=True,
    )
    return {"status": "ok", "stored": stored, "failed": failed}


@app.post("/api/extension/batch/list-summaries")
async def extension_batch_list_summaries(
    payload: ExtensionBatchListSummariesRequest,
    verified_user: str = Depends(verify_session),
):
    """Push an array of list summaries scraped from /lists/popular/."""
    if len(payload.lists) > _EXTENSION_BATCH_LIMIT:
        raise HTTPException(status_code=413, detail={"code": "batch_too_large", "limit": _EXTENSION_BATCH_LIMIT})

    stored = 0
    failed: list[dict] = []
    for summary in payload.lists:
        try:
            record = summary.model_dump()
            # Preserve existing scraped_film_count by not overriding with zero
            existing = store.get_list_summary(record["list_id"])
            if existing and existing.get("scraped_film_count"):
                record["scraped_film_count"] = existing["scraped_film_count"]
            else:
                record["scraped_film_count"] = 0
            store.upsert_list_summary(record)
            stored += 1
        except Exception as exc:
            failed.append({"list_id": summary.list_id, "error": str(exc)})

    print(
        f"[extension] list-summaries batch source={payload.source} page={payload.page} "
        f"stored={stored} failed={len(failed)}",
        flush=True,
    )
    return {"status": "ok", "stored": stored, "failed": failed}


@app.get("/api/extension/lists-needing-scrape")
def extension_lists_needing_scrape(
    limit: int = Query(default=25, ge=1, le=200),
    verified_user: str = Depends(verify_session),
):
    """Return list_summaries rows that are under 50% scraped (or brand-new).

    Used by the extension to decide which lists still need their films fetched.
    Returns minimal fields — list_id, url, title, film_count, scraped_film_count.
    """
    try:
        rows = store.get_underscraped_lists(limit=limit)
    except Exception as exc:
        print(f"[extension] lists-needing-scrape failed: {exc}", flush=True)
        return {"status": "error", "lists": [], "reason": str(exc)}

    out = []
    for row in rows:
        out.append({
            "list_id": row.get("list_id"),
            "url": row.get("url"),
            "title": row.get("title"),
            "owner_slug": row.get("owner_slug"),
            "film_count": int(row.get("film_count", 0) or 0),
            "scraped_film_count": int(row.get("scraped_film_count", 0) or 0),
        })
    return {"status": "ok", "lists": out, "count": len(out)}


@app.post("/api/extension/batch/list-movies")
async def extension_batch_list_movies(
    payload: ExtensionBatchListMoviesRequest,
    verified_user: str = Depends(verify_session),
):
    """Push film slugs scraped from a Letterboxd list page.

    If the list_summaries row is missing, upserts a skeleton row from the
    provided metadata. Accumulates scraped_film_count across paginated batches.
    """
    if len(payload.slugs) > _EXTENSION_BATCH_LIMIT:
        raise HTTPException(status_code=413, detail={"code": "batch_too_large", "limit": _EXTENSION_BATCH_LIMIT})

    existing = store.get_list_summary(payload.list_id) if hasattr(store, "get_list_summary") else None
    if not existing and (payload.title or payload.list_url):
        summary = {
            "list_id": payload.list_id,
            "slug": payload.list_id.split("-", 1)[-1],
            "url": payload.list_url or "",
            "title": payload.title or payload.list_id,
            "owner_name": payload.owner_name or payload.owner_slug or "",
            "owner_slug": payload.owner_slug or "",
            "description": payload.description or "",
            "film_count": payload.film_count or len(payload.slugs),
            "like_count": 0,
            "comment_count": 0,
            "is_official": (payload.owner_slug or "").lower() in {"letterboxd", "official"},
            "tags": [],
            "scraped_film_count": 0,
        }
        try:
            store.upsert_list_summary(summary)
        except Exception as exc:
            print(f"[extension] list skeleton upsert failed {payload.list_id}: {exc}", flush=True)

    try:
        if payload.replace_memberships:
            store.replace_list_memberships(payload.list_id, payload.slugs)
        else:
            existing_slugs = set(store.get_list_memberships(payload.list_id))
            merged = list(existing_slugs) + [s for s in payload.slugs if s not in existing_slugs]
            store.replace_list_memberships(payload.list_id, merged)
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"code": "membership_write_failed", "reason": str(exc)}) from exc

    try:
        count = len(store.get_list_memberships(payload.list_id))
        store.update_list_scrape_count(payload.list_id, count)
    except Exception as exc:
        print(f"[extension] scrape_count update failed {payload.list_id}: {exc}", flush=True)
        count = len(payload.slugs)

    print(
        f"[extension] list-movies batch list_id={payload.list_id} "
        f"page={payload.page}/{payload.total_pages} pushed={len(payload.slugs)} total={count}",
        flush=True,
    )
    return {
        "status": "ok",
        "list_id": payload.list_id,
        "page": payload.page,
        "total_pages": payload.total_pages,
        "scraped_film_count": count,
    }


@app.post("/api/extension/sync-status")
async def extension_sync_status(
    payload: ExtensionSyncStatusRequest,
    verified_user: str = Depends(verify_session),
):
    """Report extension sync progress. Mirrors the state into ingest_progress for the UI."""
    if verified_user and payload.user_id != verified_user:
        raise HTTPException(status_code=403, detail={"code": "user_id_mismatch"})

    pct_map = {"idle": 0, "watchlist": 25, "diary": 75, "complete": 100, "error": -1}
    pct = pct_map.get(payload.phase, 0)
    if payload.current_page and payload.total_pages:
        base = 0 if payload.phase == "watchlist" else 50
        span = 45
        pct = base + int((payload.current_page / max(payload.total_pages, 1)) * span)
    store.set_ingest_progress(payload.user_id, pct)
    if payload.phase == "error" and payload.message:
        store.set_ingest_error(payload.user_id, {"code": "extension_error", "reason": payload.message})
    print(
        f"[extension] sync-status user={payload.user_id} phase={payload.phase} "
        f"page={payload.current_page}/{payload.total_pages} slugs_found={payload.slugs_found} pct={pct}",
        flush=True,
    )
    return {"status": "ok", "progress": pct}


def _run_user_history_sync(
    user_id: str,
    session_cookie: str | None,
    username: str | None,
    max_watchlist_pages: int = 5,
    max_diary_pages: int = 5,
) -> dict:
    """Sync a user's Letterboxd watchlist and diary into the store.

    Intentionally capped at a small number of pages so this can run within
    Vercel's function timeout.  The movie catalog itself is not fetched here —
    it should already be populated by the seed script.

    Returns a sync_stats dict with counts for the frontend.
    """
    sync_stats: dict = {"watchlist_count": 0, "diary_count": 0, "errors": []}
    print(f"[ingest/sync] starting user history sync for user_id={user_id} username={username}", flush=True)
    store.set_ingest_progress(user_id, 10)

    if not session_cookie:
        msg = "no session cookie provided — diary/watchlist sync skipped entirely"
        print(f"[ingest/sync] WARNING: {msg}", flush=True)
        sync_stats["errors"].append(msg)
        store.set_ingest_progress(user_id, 100)
        store.ingest_running.discard(user_id)
        return sync_stats

    print(f"[ingest/sync] session cookie present (length={len(session_cookie)}), fetching watchlist (max_pages={max_watchlist_pages})...", flush=True)

    try:
        live_watchlist = scraper.pull_watchlist_slugs(
            session_cookie, username=username, max_pages=max_watchlist_pages
        )
        print(f"[ingest/sync] watchlist scraper returned {len(live_watchlist)} slugs", flush=True)
        stored = 0
        failed = 0
        for slug in live_watchlist:
            try:
                store.add_watchlist(user_id, slug)
                stored += 1
            except Exception as slug_exc:
                failed += 1
                print(f"[ingest/sync] watchlist slug failed: {slug}: {slug_exc}", flush=True)
        sync_stats["watchlist_count"] = stored
        if failed:
            sync_stats["errors"].append(f"watchlist: {failed}/{len(live_watchlist)} slugs failed to store")
        print(f"[ingest/sync] watchlist stored: {stored} ok, {failed} failed for user_id={user_id}", flush=True)
    except Exception as exc:
        msg = f"watchlist fetch failed: {type(exc).__name__}: {exc}"
        print(f"[ingest/sync] ERROR: {msg}", flush=True)
        sync_stats["errors"].append(msg)

    store.set_ingest_progress(user_id, 55)

    print(f"[ingest/sync] fetching diary (max_pages={max_diary_pages})...", flush=True)
    try:
        live_diary = scraper.pull_diary_slugs(
            session_cookie, username=username, max_pages=max_diary_pages
        )
        print(f"[ingest/sync] diary scraper returned {len(live_diary)} slugs", flush=True)
        stored = 0
        failed = 0
        for slug in live_diary:
            try:
                store.add_diary(user_id, slug)
                stored += 1
            except Exception as slug_exc:
                failed += 1
                print(f"[ingest/sync] diary slug failed: {slug}: {slug_exc}", flush=True)
        sync_stats["diary_count"] = stored
        if failed:
            sync_stats["errors"].append(f"diary: {failed}/{len(live_diary)} slugs failed to store")
        print(f"[ingest/sync] diary stored: {stored} ok, {failed} failed for user_id={user_id}", flush=True)
    except Exception as exc:
        msg = f"diary fetch failed: {type(exc).__name__}: {exc}"
        print(f"[ingest/sync] ERROR: {msg}", flush=True)
        sync_stats["errors"].append(msg)

    store.set_ingest_progress(user_id, 100)
    store.ingest_running.discard(user_id)
    print(
        f"[ingest/sync] DONE for {username}: "
        f"watchlist={sync_stats['watchlist_count']} diary={sync_stats['diary_count']} "
        f"errors={len(sync_stats['errors'])}",
        flush=True,
    )
    return sync_stats


def _filter_first_pipeline(
    user_id: str,
    source: str,
    depth_pages: int,
    session_cookie: str | None = None,
    username: str | None = None,
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

    # Augment with live Letterboxd history when a session cookie is available.
    # Persist to store so deck filtering and future ingests use the real data.
    # Failures are non-fatal — ingest continues with store-only data.
    if session_cookie:
        print(f"[ingest] session cookie present (length={len(session_cookie)}), fetching live history...", flush=True)
        _emit(22)
        try:
            live_watchlist = scraper.pull_watchlist_slugs(session_cookie, username=username)
            for slug in live_watchlist:
                store.add_watchlist(user_id, slug)
            watchlist = watchlist | live_watchlist
            print(f"[ingest] live_watchlist={len(live_watchlist)} persisted to store", flush=True)
        except Exception as exc:
            print(f"[ingest] live watchlist fetch failed: {type(exc).__name__}: {exc}", flush=True)
        _emit(31)
        try:
            live_diary = scraper.pull_diary_slugs(session_cookie, username=username)
            for slug in live_diary:
                store.add_diary(user_id, slug)
            diary = diary | live_diary
            print(f"[ingest] live_diary={len(live_diary)} persisted to store", flush=True)
        except Exception as exc:
            print(f"[ingest] live diary fetch failed: {type(exc).__name__}: {exc}", flush=True)
        _emit(39)
    else:
        print("[ingest] WARNING: no session cookie — skipping live watchlist/diary fetch", flush=True)

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


def _run_ingest_worker(
    user_id: str,
    source: str,
    depth_pages: int,
    session_cookie: str | None = None,
    username: str | None = None,
) -> None:
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
            session_cookie=session_cookie,
            username=username,
            progress_callback=_set_progress,
        )
        _set_progress(100)

    except Exception as exc:
        store.set_ingest_progress(user_id, -1)
        store.set_ingest_error(user_id, {"code": "ingest_worker_failed", "reason": str(exc)})
        print(f"[ingest] worker_error user_id={user_id} error={exc}", flush=True)
    finally:
        store.ingest_running.discard(user_id)
