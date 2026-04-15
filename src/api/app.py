from __future__ import annotations

import json
import os
import threading
import time
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .queue import InMemoryQueue
from .security import encrypt_session_cookie
from .providers.letterboxd import HttpLetterboxdScraper, MockLetterboxdScraper
from .database import is_supabase_configured

PROFILES = {
    "gold-standard": lambda m: m["rating"] >= 4.5,
    "hidden-gems": lambda m: m["rating"] >= 4.0 and m["popularity"] <= 50,
    "fresh-picks": lambda m: m["rating"] >= 3.8,
}

SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "mock").lower()
scraper = HttpLetterboxdScraper() if SCRAPER_BACKEND == "http" else MockLetterboxdScraper()
app = FastAPI(title="Swiperboxd API", version="0.4.0")

# Conditional store selection
if is_supabase_configured():
    from .store import SupabaseStore
    store = SupabaseStore()
else:
    from .store import InMemoryStore
    store = InMemoryStore()

queue = InMemoryQueue()


class AuthSessionRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


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
    return {"status": "ok", "app": "swiperboxd"}


@app.get("/")
def root():
    return FileResponse("src/web/index.html")


@app.get("/web/{path:path}")
def web_assets(path: str):
    return FileResponse(f"src/web/{path}")


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

    try:
        upstream_session_cookie = scraper.login(payload.username, payload.password)
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"code": "upstream_login_failed", "reason": str(exc)}) from exc

    encrypted_cookie = encrypt_session_cookie(upstream_session_cookie, master_key)
    return AuthSessionResponse(status="ok", encrypted_session_cookie=encrypted_cookie)


@app.post("/ingest/start")
async def start_ingest(payload: IngestStartRequest):
    """Start ingest process for a user (username)."""
    allowed, retry_after = store.allow_scrape_request(payload.user_id, min_interval_seconds=1.0)
    if not allowed:
        raise HTTPException(status_code=429, detail={"code": "scrape_rate_limited", "retry_after": retry_after})

    if payload.user_id in store.ingest_running:
        return {"status": "already_running", "user_id": payload.user_id}

    store.ingest_running.add(payload.user_id)
    queue.enqueue("ingest-history", {"user_id": payload.user_id, "source": payload.source, "depth_pages": payload.depth_pages})
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
async def submit_swipe_action(payload: SwipeActionRequest):
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


def _filter_first_pipeline(user_id: str, source: str, depth_pages: int) -> list[dict]:
    """Filter pipeline: pull source → exclude seen → fetch metadata → upsert to cache."""
    source_slugs = scraper.pull_source_slugs(source=source, depth_pages=depth_pages)
    watchlist = store.get_watchlist(user_id)
    diary = store.get_diary(user_id)
    exclusions = store.get_exclusions(user_id)

    unique = [slug for slug in source_slugs if slug not in watchlist]
    unique = [slug for slug in unique if slug not in diary]
    unique = [slug for slug in unique if slug not in exclusions]

    metadata = [m.__dict__ for m in scraper.metadata_for_slugs(unique)]
    for movie in metadata:
        store.upsert_movie(movie)

    return metadata


def _run_ingest_worker(user_id: str, source: str, depth_pages: int) -> None:
    """Background worker for ingest processing with error handling."""

    try:
        store.set_ingest_progress(user_id, 5)
        for value in [20, 35, 50, 70]:
            time.sleep(0.1)
            store.set_ingest_progress(user_id, value)

        # Filter pipeline
        _filter_first_pipeline(user_id=user_id, source=source, depth_pages=depth_pages)

        store.set_ingest_progress(user_id, 100)

    except Exception as exc:
        store.set_ingest_progress(user_id, -1)
        print(f"Ingest worker error for user {user_id}: {exc}")
    finally:
        store.ingest_running.discard(user_id)
