"""Cron job handlers for scheduled tasks."""

from __future__ import annotations

import json
import os
from fastapi import APIRouter, Header, HTTPException
from typing import Literal

from .providers.letterboxd import HttpLetterboxdScraper
from .security import decrypt_session_cookie

router = APIRouter()

# Cron secret to prevent unauthorized access
# Set VERCEL_CRON_SECRET in environment variables
CRON_SECRET = os.getenv("VERCEL_CRON_SECRET")


def _require_cron_secret(x_cron_secret: str | None) -> None:
    if not CRON_SECRET or x_cron_secret != CRON_SECRET:
        raise HTTPException(status_code=403, detail="Unauthorized: Invalid cron secret")


def _get_store():
    """Lazy import to avoid circular dep and pick up env-configured backend."""
    from .database import is_supabase_configured
    if is_supabase_configured():
        from .store import SupabaseStore
        return SupabaseStore()
    from .store import InMemoryStore
    return InMemoryStore()


@router.post("/refresh-lists")
async def refresh_lists_cron(x_cron_secret: str = Header(...)):
    """Vercel Chron cron job endpoint to refresh Letterboxd lists.

    Protected by VERCEL_CRON_SECRET header to ensure only authorized calls.

    Returns:
        JSON response with refresh stats
    """
    _require_cron_secret(x_cron_secret)

    scraper = HttpLetterboxdScraper()
    store = _get_store()
        
    print("\n[cron] Starting scheduled list refresh...", flush=True)
    
    try:
        # Fetch lists from Letterboxd (page 1 for most popular)
        lists_data = scraper.discover_site_lists(page=1)
        print(f"[cron] Fetched {len(lists_data)} lists from Letterboxd", flush=True)
        
        # Track changes
        updated_count = 0
        skipped_count = 0
        error_count = 0
        
        # Update only if data changed
        for item in lists_data:
            try:
                existing = store.get_list_summary(item.list_id)
                
                # Update if:
                # - List doesn't exist, OR
                # - Like count changed (indicates activity), OR
                # - Film count changed
                needs_update = (
                    not existing or
                    existing.get('like_count') != item.like_count or
                    existing.get('film_count') != item.film_count
                )
                
                if needs_update:
                    store.upsert_list_summary(item.__dict__)
                    updated_count += 1
                    print(f"  ✓ Updated: {item.title}")
                else:
                    skipped_count += 1
                    print(f"  ⊘ Skipped: {item.title} (no change)")
                    
            except Exception as e:
                error_count += 1
                print(f"  ✗ Error processing {item.title}: {e}")
        
        print(f"[cron] Refresh complete: {updated_count} updated, {skipped_count} skipped, {error_count} errors", flush=True)

        # Prune stale ingest progress entries (older than 1 hour) to prevent
        # unbounded dict growth in long-running processes.
        store.cleanup_expired_progress()

        return {
            "status": "ok",
            "fetched": len(lists_data),
            "updated": updated_count,
            "skipped": skipped_count,
            "errors": error_count
        }
        
    except RuntimeError as e:
        error_msg = str(e)
        if "rate_limited" in error_msg.lower():
            print(f"[cron] Rate limited by Letterboxd, using cached data", flush=True)
            return {
                "status": "rate_limited",
                "message": "Letterboxd rate limiting - cached data returned"
            }
        else:
            print(f"[cron] Scraper error: {error_msg}", flush=True)
            raise HTTPException(
                status_code=500,
                detail={"code": "scraper_failed", "reason": error_msg}
            )
    except Exception as e:
        print(f"[cron] Unexpected error: {e}", flush=True)
        raise HTTPException(
            status_code=500,
            detail={"code": "internal_error", "reason": str(e)}
        )


@router.get("/health")
async def cron_health():
    """Health check for cron endpoints."""
    return {
        "status": "ok",
        "service": "cron_scheduler",
        "configured": bool(CRON_SECRET)
    }


def _decrypt_user_session(encrypted_session: str) -> str | None:
    """Decrypt a stored session blob and return the raw Letterboxd cookie, or None."""
    master_key = os.getenv("MASTER_ENCRYPTION_KEY")
    if not master_key:
        print("[cron] MASTER_ENCRYPTION_KEY not set — cannot decrypt sessions", flush=True)
        return None
    try:
        raw = decrypt_session_cookie(encrypted_session, master_key)
    except Exception as exc:
        print(f"[cron] session decrypt failed: {type(exc).__name__}: {exc}", flush=True)
        return None
    try:
        data = json.loads(raw)
        return data.get("c")
    except (json.JSONDecodeError, ValueError):
        # Old-format token stored as raw cookie
        return raw or None


@router.post("/sync-users")
async def sync_users_cron(
    x_cron_secret: str = Header(...),
    max_users: int = 25,
    max_pages: int = 5,
):
    """Iterate every user with a stored Letterboxd session and refresh their
    watchlist + diary. Runs from Vercel even though Letterboxd may 403 AWS IPs —
    the extension is the reliable channel, but we keep this as best-effort so
    the catalog stays fresh when a given proxy tier does make it through.
    """
    _require_cron_secret(x_cron_secret)

    scraper = HttpLetterboxdScraper()
    store = _get_store()

    try:
        sessions = store.get_all_user_sessions()
    except Exception as exc:
        print(f"[cron/sync-users] failed to load sessions: {exc}", flush=True)
        raise HTTPException(status_code=500, detail={"code": "sessions_unavailable", "reason": str(exc)})

    sessions = sessions[:max_users]
    print(f"[cron/sync-users] processing {len(sessions)} users (max_pages={max_pages})", flush=True)

    per_user = []
    for idx, entry in enumerate(sessions, 1):
        username = entry.get("username")
        user_id = entry.get("user_id") or username
        encrypted = entry.get("encrypted_session")
        if not username or not encrypted:
            continue
        cookie = _decrypt_user_session(encrypted)
        if not cookie:
            per_user.append({"username": username, "status": "decrypt_failed"})
            continue

        print(f"[cron/sync-users] [{idx}/{len(sessions)}] user={username}", flush=True)
        user_stats: dict = {"username": username, "watchlist": 0, "diary": 0, "errors": []}

        try:
            slugs = scraper.pull_watchlist_slugs(cookie, username=username, max_pages=max_pages)
            added = 0
            for slug in slugs:
                try:
                    store.add_watchlist(user_id, slug)
                    added += 1
                except Exception as exc:
                    user_stats["errors"].append(f"wl {slug}: {exc}")
            user_stats["watchlist"] = added
            print(f"  watchlist: scraped={len(slugs)} added={added}", flush=True)
        except Exception as exc:
            user_stats["errors"].append(f"watchlist fetch: {type(exc).__name__}: {exc}")
            print(f"  watchlist failed: {exc}", flush=True)

        try:
            slugs = scraper.pull_diary_slugs(cookie, username=username, max_pages=max_pages)
            added = 0
            for slug in slugs:
                try:
                    store.add_diary(user_id, slug)
                    added += 1
                except Exception as exc:
                    user_stats["errors"].append(f"diary {slug}: {exc}")
            user_stats["diary"] = added
            print(f"  diary: scraped={len(slugs)} added={added}", flush=True)
        except Exception as exc:
            user_stats["errors"].append(f"diary fetch: {type(exc).__name__}: {exc}")
            print(f"  diary failed: {exc}", flush=True)

        per_user.append(user_stats)

    total_wl = sum(u.get("watchlist", 0) for u in per_user)
    total_diary = sum(u.get("diary", 0) for u in per_user)
    print(f"[cron/sync-users] DONE users={len(per_user)} watchlist={total_wl} diary={total_diary}", flush=True)
    return {
        "status": "ok",
        "users_processed": len(per_user),
        "watchlist_total": total_wl,
        "diary_total": total_diary,
        "per_user": per_user,
    }


@router.post("/backfill-scrapes")
async def backfill_scrapes_cron(
    x_cron_secret: str = Header(...),
    max_movies: int = 60,
    max_lists: int = 10,
):
    """Backfill ONLY:
    1. Old placeholder movies (from before metadata-during-sync fix)
    2. Under-scraped lists
    
    This is now a cleanup job, not the primary metadata source.
    """
    _require_cron_secret(x_cron_secret)

    scraper = HttpLetterboxdScraper()
    store = _get_store()

    # ── Movies: Only placeholders (legacy cleanup) ────────────────────────
    movie_stats = {"targeted": 0, "fetched": 0, "failed": 0}
    try:
        placeholder_slugs = store.get_placeholder_movie_slugs(limit=max_movies)
    except Exception as exc:
        print(f"[cron/backfill] placeholder query failed: {exc}", flush=True)
        placeholder_slugs = []
    
    movie_stats["targeted"] = len(placeholder_slugs)
    
    if placeholder_slugs:
        print(
            f"[cron/backfill] WARNING: Found {len(placeholder_slugs)} placeholder movies. "
            f"These should not exist after metadata-during-sync fix. Backfilling...",
            flush=True
        )
        try:
            movies = scraper.metadata_for_slugs(placeholder_slugs)
            for movie in movies:
                try:
                    store.upsert_movie(movie.__dict__)
                    movie_stats["fetched"] += 1
                except Exception as exc:
                    movie_stats["failed"] += 1
                    print(f"  upsert failed {movie.slug}: {exc}", flush=True)
        except Exception as exc:
            movie_stats["failed"] += len(placeholder_slugs)
            print(f"[cron/backfill] movie scrape failed: {exc}", flush=True)

    # ── Lists: Under-scraped only ──────────────────────────────────────────
    list_stats = {"targeted": 0, "scraped": 0, "failed": 0}
    try:
        lists = store.get_underscraped_lists(limit=max_lists)
    except Exception as exc:
        print(f"[cron/backfill] underscraped query failed: {exc}", flush=True)
        lists = []
    list_stats["targeted"] = len(lists)
    for lst in lists:
        list_id = lst.get("list_id")
        if not list_id:
            continue
        try:
            slugs = scraper.fetch_list_movie_slugs(list_id, list_url=lst.get("url"))
            if slugs:
                store.replace_list_memberships(list_id, slugs)
                store.update_list_scrape_count(list_id, len(slugs))
                list_stats["scraped"] += 1
                print(f"  list={list_id} scraped={len(slugs)}", flush=True)
            else:
                list_stats["failed"] += 1
                print(f"  list={list_id} returned no slugs", flush=True)
        except Exception as exc:
            list_stats["failed"] += 1
            print(f"  list={list_id} failed: {exc}", flush=True)

    print(f"[cron/backfill] DONE movies={movie_stats} lists={list_stats}", flush=True)
    return {"status": "ok", "movies": movie_stats, "lists": list_stats}
