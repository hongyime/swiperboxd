from __future__ import annotations

import json
import os
import threading
import time
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .queue import InMemoryQueue
from .qstash_queue import QStashQueue
from .security import encrypt_session_cookie
from .store import SupabaseStore
from .providers.letterboxd import HttpLetterboxdScraper, MockLetterboxdScraper
from .database import is_supabase_configured
from .auth import get_auth_service

PROFILES = {
    "gold-standard": lambda m: m["rating"] >= 4.5,
    "hidden-gems": lambda m: m["rating"] >= 4.0 and m["popularity"] <= 50,
    "fresh-picks": lambda m: m["rating"] >= 3.8,
}

SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "mock").lower()
scraper = HttpLetterboxdScraper() if SCRAPER_BACKEND == "http" else MockLetterboxdScraper()
app = FastAPI(title="CineSwipe API", version="0.3.0")

# Conditional store selection: SupabaseStore if configured, otherwise InMemoryStore (for fallback and tests)
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


# Supabase Auth models
class UserRegisterRequest(BaseModel):
    email: str = Field(min_length=1, regex=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=8, max_length=100)


class UserLoginRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)


class AuthTokenResponse(BaseModel):
    status: Literal["ok"]
    access_token: str
    user_id: str
    email: str


@app.get("/health")
def health():
    return {"status": "ok", "app": "cineswipe"}


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
    master_key = os.getenv("MASTER_ENCRYPTION_KEY")
    if not master_key:
        raise HTTPException(status_code=500, detail={"code": "missing_master_key"})

    try:
        upstream_session_cookie = scraper.login(payload.username, payload.password)
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"code": "upstream_login_failed", "reason": str(exc)}) from exc

    encrypted_cookie = encrypt_session_cookie(upstream_session_cookie, master_key)
    return AuthSessionResponse(status="ok", encrypted_session_cookie=encrypted_cookie)


# Supabase Auth endpoints
@app.post("/auth/register", response_model=AuthTokenResponse)
async def register_user(payload: UserRegisterRequest):
    """Register a new user with Supabase Auth."""
    auth_service = get_auth_service()

    try:
        result = await auth_service.register_user(payload.email, payload.password)

        return AuthTokenResponse(
            status="ok",
            access_token=result["access_token"],
            user_id=result["user_id"],
            email=result["email"]
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "registration_failed", "reason": str(exc)}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"code": "server_error", "reason": str(exc)}) from exc


@app.post("/auth/login", response_model=AuthTokenResponse)
async def login_user(payload: UserLoginRequest):
    """Login a user with Supabase Auth."""
    auth_service = get_auth_service()

    try:
        result = await auth_service.login_user(payload.email, payload.password)

        return AuthTokenResponse(
            status="ok",
            access_token=result["access_token"],
            user_id=result["user_id"],
            email=result["email"]
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail={"code": "login_failed", "reason": str(exc)}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"code": "server_error", "reason": str(exc)}) from exc


@app.get("/auth/me")
async def get_current_user(request: Request):
    """Get current authenticated user info."""
    auth_service = get_auth_service()

    user = await auth_service.get_user_from_request(request)

    if not user:
        raise HTTPException(status_code=401, detail={"code": "unauthorized"})

    return {
        "status": "ok",
        "user_id": user["user_id"],
        "email": user["email"]
    }


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

        # Import scraper here to avoid circular dependency
        _filter_first_pipeline(user_id=user_id, source=source, depth_pages=depth_pages)

        store.set_ingest_progress(user_id, 100)

    except Exception as exc:
        # Log error and set progress to error state
        store.set_ingest_progress(user_id, -1)
        # In production, this would go to a proper logging system
        print(f"Ingest worker error for user {user_id}: {exc}")
    finally:
        store.ingest_running.discard(user_id)


async def ingest_webhook(request: Request):
    """Webhook endpoint for QStash callbacks when ingest jobs complete."""
    body_bytes = await request.body()
    body = body_bytes.decode()

    try:
        from .qstash_queue import QStashQueue
        queue_client = QStashQueue()
        is_valid = queue_client.verify_webhook(dict(request.headers), body)
        if not is_valid:
            raise HTTPException(status_code=401, detail={"code": "invalid_signature"})
    except ValueError:
        pass  # QStash not configured
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"code": "signature_verification_failed", "reason": str(exc)}) from exc

    try:
        payload = json.loads(body)
        user_id = payload.get("user_id")
        source = payload.get("source", "trending")
        depth_pages = payload.get("depth_pages", 2)
        if not user_id:
            raise HTTPException(status_code=400, detail={"code": "invalid_payload"})
        
        # Call existing _run_ingest_worker (need to rename _simulate_ingest first)
        _run_ingest_worker(user_id, source, depth_pages)
        return {"status": "accepted", "user_id": user_id}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_json"}) from exc


@app.post("/webhooks/ingest")
async def handle_ingest_webhook(request: Request):
    return await ingest_webhook(request)






@app.post("/ingest/start")
def start_ingest(payload: IngestStartRequest):
    allowed, retry_after = store.allow_scrape_request(payload.user_id, min_interval_seconds=1.0)
    if not allowed:
        raise HTTPException(status_code=429, detail={"code": "scrape_rate_limited", "retry_after": retry_after})

    if payload.user_id in store.ingest_running:
        return {"status": "already_running", "user_id": payload.user_id}

    store.ingest_running.add(payload.user_id)
    queue.enqueue("ingest-history", payload.model_dump())
    threading.Thread(target=_run_ingest_worker, args=(payload.user_id, payload.source, payload.depth_pages), daemon=True).start()
    return {"status": "queued", "user_id": payload.user_id}


@app.get("/ingest/progress")
def ingest_progress(user_id: str = Query(min_length=1)):
    return {
        "status": "ok",
        "user_id": user_id,
        "progress": store.get_ingest_progress(user_id),
        "running": user_id in store.ingest_running,
    }


@app.get("/discovery/deck")
def get_discovery_deck(
    user_id: str = Query(min_length=1),
    profile: str = Query(default="gold-standard"),
):
    if profile not in PROFILES:
        raise HTTPException(status_code=400, detail={"code": "invalid_profile"})

    movies = [m for m in store.get_movies() if PROFILES[profile](m)]
    movies = store.weighted_shuffle(user_id, movies)
    return {"status": "ok", "profile": profile, "results": movies[:20]}


@app.get("/discovery/details")
def get_discovery_details(slug: str = Query(min_length=1)):
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
def submit_swipe_action(payload: SwipeActionRequest):
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
