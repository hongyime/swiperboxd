"""Core, auth/session, ingest, and action routes."""

from __future__ import annotations

import json
import os
import re
import threading

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from .. import app as app_module
from ..app import (
    AuthSessionRequest,
    AuthSessionResponse,
    CacheLbIdRequest,
    ExtensionRegisterRequest,
    ExtensionRegisterResponse,
    IngestStartRequest,
    PublicSessionRequest,
    PublicSessionResponse,
    PublicSyncRequest,
    SwipeActionRequest,
)
from ..database import is_supabase_configured, run_migrations
from ..security import decrypt_session_cookie, encrypt_session_cookie

router = APIRouter()

@router.get("/health")
def health():
    store_type = "SupabaseStore" if is_supabase_configured() else "InMemoryStore"
    return {"status": "ok", "app": "swiperboxd", "store": store_type}

@router.post("/db/migrate")
def migrate_database(verified_user: str = Depends(app_module.verify_session)):
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

@router.get("/")
def root():
    return FileResponse(str(app_module._WEB_DIR / "index.html"))

@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(str(app_module._WEB_DIR / "logo.svg"), media_type="image/svg+xml")

@router.get("/web/{path:path}")
def web_assets(path: str):
    # Resolve and verify the path stays within the web directory
    target = (app_module._WEB_DIR / path).resolve()
    if not str(target).startswith(str(app_module._WEB_DIR.resolve())):
        raise HTTPException(status_code=404)
    return FileResponse(str(target))

@router.get("/users/{username}/sync-status")
def user_sync_status(username: str, verified_user: str = Depends(app_module.verify_extension_user)):
    """Return read-only membership counts for the authenticated extension user."""
    headers = {"Cache-Control": "private, no-store"}
    if username != verified_user:
        raise HTTPException(status_code=403, detail={"code": "user_id_mismatch"}, headers=headers)
    try:
        return JSONResponse(app_module.store.get_sync_status(username), headers=headers)
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"code": "sync_status_unavailable"},
                            headers=headers) from exc

@router.post("/auth/session", response_model=AuthSessionResponse)
def create_auth_session(payload: AuthSessionRequest):
    """
    Authenticate with Letterboxd and return encrypted session cookie.
    The username is used as the user_id throughout the app.
    """
    master_key = os.getenv("MASTER_ENCRYPTION_KEY")
    if not master_key:
        raise HTTPException(status_code=500, detail={"code": "missing_master_key"})

    try:
        username = app_module._extract_username_from_cookie(payload.session_cookie)
    except Exception as exc:
        print(f"[auth] session validation failed: {exc}", flush=True)
        raise HTTPException(status_code=401, detail={"code": "invalid_session_cookie", "reason": str(exc)}) from exc

    if not username:
        raise HTTPException(status_code=401, detail={"code": "invalid_session_cookie", "reason": "could not resolve username from cookie — is it expired?"})

    print(f"[auth] session cookie valid for user={username}", flush=True)
    token_payload = json.dumps({"u": username, "c": payload.session_cookie})
    encrypted_cookie = encrypt_session_cookie(token_payload, master_key)

    try:
        app_module.store.save_user_session(username, encrypted_cookie)
        print(f"[auth] session stored for user={username}", flush=True)
    except Exception as exc:
        print(f"[auth] WARNING: failed to store session: {exc}", flush=True)

    return AuthSessionResponse(status="ok", username=username, encrypted_session_cookie=encrypted_cookie)

@router.post("/auth/public-session", response_model=PublicSessionResponse)
def create_public_session(payload: PublicSessionRequest):
    """Register or start a public session using only a Letterboxd username.

    No session cookie required. Generates an encrypted token with {"u": username, "public": True}.
    """
    clean_username = payload.username.strip().lstrip("@")
    if not clean_username or not re.match(r"^[a-zA-Z0-9_-]{1,40}$", clean_username):
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_username", "reason": "Username must be 1-40 alphanumeric characters, underscores, or hyphens."}
        )

    master_key = os.getenv("MASTER_ENCRYPTION_KEY")
    if not master_key:
        raise HTTPException(status_code=500, detail={"code": "missing_master_key"})

    token_payload = json.dumps({"u": clean_username, "public": True})
    encrypted_cookie = encrypt_session_cookie(token_payload, master_key)

    try:
        if hasattr(app_module.store, "_get_or_create_user_id"):
            app_module.store._get_or_create_user_id(clean_username)
    except Exception as exc:
        print(f"[auth] Notice: could not pre-seed user {clean_username}: {exc}", flush=True)

    return PublicSessionResponse(
        status="ok",
        username=clean_username,
        encrypted_session_cookie=encrypted_cookie,
        mode="public",
    )

@router.post("/ingest/public-sync")
async def public_sync(
    payload: PublicSyncRequest,
    verified_user: str = Depends(app_module.verify_session),
):
    """Non-blocking public sync: pull public RSS feed and public watchlist for a user."""
    if verified_user and payload.user_id != verified_user:
        raise HTTPException(status_code=403, detail={"code": "user_id_mismatch"})

    diary_added = 0
    watchlist_added = 0

    # 1. Pull public RSS feed (diary/watched)
    try:
        if hasattr(app_module.scraper, "pull_rss_diary_slugs"):
            rss_slugs = app_module.scraper.pull_rss_diary_slugs(payload.user_id)
            if rss_slugs:
                res = app_module.store.batch_add_diary(payload.user_id, rss_slugs)
                diary_added = res.get("added", len(rss_slugs))
    except Exception as exc:
        print(f"[ingest] public rss sync failed for {payload.user_id}: {exc}", flush=True)

    # 2. Pull page 1 of public watchlist if supported
    try:
        if hasattr(app_module.scraper, "pull_watchlist_slugs"):
            wl_slugs = list(app_module.scraper.pull_watchlist_slugs("", username=payload.user_id))
            if wl_slugs:
                res = app_module.store.batch_add_watchlist(payload.user_id, wl_slugs)
                watchlist_added = res.get("added", len(wl_slugs))
    except Exception as exc:
        print(f"[ingest] public watchlist sync failed for {payload.user_id}: {exc}", flush=True)

    return {
        "status": "ok",
        "user_id": payload.user_id,
        "diary_added": diary_added,
        "watchlist_added": watchlist_added,
    }

@router.post("/ingest/start")
async def start_ingest(
    payload: IngestStartRequest,
    verified_user: str = Depends(app_module.verify_session),
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

    allowed, retry_after = app_module.store.allow_scrape_request(payload.user_id, min_interval_seconds=1.0)
    if not allowed:
        print(f"[ingest] rate limited for user, retry_after={retry_after:.1f}s", flush=True)
        raise HTTPException(status_code=429, detail={"code": "scrape_rate_limited", "retry_after": retry_after})

    if not app_module.store.claim_ingest(payload.user_id):
        print("[ingest] already running, skipping duplicate start", flush=True)
        return {"status": "already_running", "user_id": payload.user_id}

    app_module.store.set_ingest_error(payload.user_id, None)
    app_module.store.set_ingest_progress(payload.user_id, 0)
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
                    app_module._run_user_history_sync,
                    payload.user_id, session_cookie, ingest_username,
                ),
                timeout=55.0,  # stay within Vercel's 60 s function limit
            )
        except _asyncio.TimeoutError:
            print("[ingest] Vercel inline sync timed out — partial sync saved", flush=True)
            app_module.store.set_ingest_progress(payload.user_id, 100)
            sync_stats["errors"].append("sync timed out (55s limit)")
        except Exception as exc:
            print(f"[ingest] Vercel inline sync error: {type(exc).__name__}: {exc}", flush=True)
            app_module.store.set_ingest_error(payload.user_id, {"code": "ingest_worker_failed", "reason": str(exc)})
            app_module.store.set_ingest_progress(payload.user_id, -1)
            sync_stats["errors"].append(str(exc))
        finally:
            app_module.store.release_ingest(payload.user_id)

        # Always include current DB counts so the UI can render cards even when
        # the live scrape failed (expected on Vercel — Letterboxd blocks AWS IPs).
        try:
            db_watchlist = len(app_module.store.get_watchlist(payload.user_id))
            db_diary = len(app_module.store.get_diary(payload.user_id))
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
        target=app_module._run_ingest_worker,
        args=(payload.user_id, payload.source, payload.depth_pages, session_cookie, ingest_username),
        daemon=True,
    ).start()
    return {"status": "queued", "user_id": payload.user_id}

@router.get("/ingest/progress")
def ingest_progress(user_id: str = Query(min_length=1), verified_user: str = Depends(app_module.verify_extension_user)):
    """Get ingest progress for the authenticated extension user."""
    headers = {"Cache-Control": "private, no-store"}
    if user_id != verified_user:
        raise HTTPException(status_code=403, detail={"code": "user_id_mismatch"}, headers=headers)
    return JSONResponse({
        "status": "ok",
        "user_id": user_id,
        "progress": app_module.store.get_ingest_progress(user_id),
        # Progress 0/partial means a worker holds the ingest claim; -1 and 100 are terminal.
        "running": app_module.store.get_ingest_progress(user_id) not in (-1, 100),
        "error": app_module.store.get_ingest_error(user_id),
    }, headers=headers)

@router.post("/actions/cache-lb-id")
async def cache_lb_film_id(payload: CacheLbIdRequest, verified_user: str = Depends(app_module.verify_session)):
    """Persist the Letterboxd film LID discovered client-side."""
    try:
        app_module.store.upsert_movie({"slug": payload.movie_slug, "lb_film_id": payload.lb_film_id})
    except Exception as exc:
        print(f"[cache-lb-id] failed {payload.movie_slug}: {exc}", flush=True)
    return {"status": "ok"}

@router.post("/actions/swipe")
async def submit_swipe_action(
    payload: SwipeActionRequest,
    verified_user: str = Depends(app_module.verify_session),
):
    """Submit a swipe action — saves to Supabase. Letterboxd write-back is handled
    client-side by the browser extension using the user's live session cookie."""
    if verified_user and payload.user_id != verified_user:
        raise HTTPException(status_code=403, detail={"code": "user_id_mismatch"})
    limited, retry_after_ms = app_module.store.should_rate_limit(payload.user_id, lock_ms=500)
    if limited:
        raise HTTPException(status_code=429, detail={"code": "sync_lock", "retry_after_ms": retry_after_ms})

    movie = app_module.store.get_movie(payload.movie_slug)

    if payload.action == "watchlist":
        if payload.movie_slug in app_module.store.get_watchlist(payload.user_id):
            return JSONResponse(
                status_code=409,
                content={"code": "already_in_watchlist", "action": payload.action, "movie_slug": payload.movie_slug},
            )
        app_module.store.add_watchlist(payload.user_id, payload.movie_slug)
        if movie:
            app_module.store.record_genre_preference(payload.user_id, movie.get("genres", []))
    elif payload.action == "log":
        if payload.movie_slug in app_module.store.get_diary(payload.user_id):
            return JSONResponse(
                status_code=409,
                content={"code": "already_in_diary", "action": payload.action, "movie_slug": payload.movie_slug},
            )
        app_module.store.add_diary(payload.user_id, payload.movie_slug)
    elif payload.action == "dismiss":
        app_module.store.add_exclusion(payload.user_id, payload.movie_slug)

    return {"status": "accepted", "action": payload.action, "movie_slug": payload.movie_slug}

@router.post("/api/extension/register", response_model=ExtensionRegisterResponse)
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

    # payload.username is ignored on purpose: trusting it was an account takeover.
    # Route through the scraper proxy chain when available (Vercel IPs are blocked).
    fetcher = getattr(app_module.scraper, "_fetch", None)
    try:
        app_module._validate_letterboxd_session("", payload.letterboxd_session_cookie, fetch=fetcher)
    except Exception as exc:
        reason = str(exc)
        if "all_tiers_failed" in reason or "network_error" in reason:
            reason = f"proxy_unavailable: {reason}"
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_letterboxd_cookie", "reason": reason},
        ) from exc

    username = app_module._extract_username_from_cookie(payload.letterboxd_session_cookie, fetch=fetcher)
    if not username:
        raise HTTPException(
            status_code=422,
            detail={"code": "username_unresolved", "message": "could not parse username from Letterboxd response"},
        )
    token_payload = json.dumps({"u": username, "c": payload.letterboxd_session_cookie})
    encrypted = encrypt_session_cookie(token_payload, master_key)

    try:
        app_module.store.save_user_session(username, encrypted)
    except Exception as exc:
        print(f"[extension/register] WARNING: failed to persist session: {exc}", flush=True)

    base_url = str(request.base_url).rstrip("/")
    print(f"[extension/register] registered username={username} api_base={base_url}", flush=True)
    return ExtensionRegisterResponse(status="ok", username=username, session_token=encrypted, api_base=base_url)
