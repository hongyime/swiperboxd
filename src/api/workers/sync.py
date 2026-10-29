"""Background ingest workers (user-history sync and filter pipeline)."""

from __future__ import annotations

import os
from typing import Callable

from .. import app as app_module

def _run_user_history_sync(
    user_id: str,
    session_cookie: str | None,
    username: str | None,
    max_watchlist_pages: int = 5,
    max_diary_pages: int = 5,
) -> dict:
    """Sync a user's Letterboxd watchlist and diary into persistent storage.

    Intentionally capped at a small number of pages so this can run within
    Vercel's function timeout.  The movie catalog itself is not fetched here —
    it should already be populated by the seed script.

    Returns a sync_stats dict with counts for the frontend.
    """
    sync_stats: dict = {"watchlist_count": 0, "diary_count": 0, "metadata_count": 0, "errors": []}
    print(f"[ingest/sync] starting user history sync for user_id={user_id} username={username}", flush=True)
    app_module.store.set_ingest_progress(user_id, 10)

    if not session_cookie:
        msg = "no session cookie provided — diary/watchlist sync skipped entirely"
        print(f"[ingest/sync] WARNING: {msg}", flush=True)
        sync_stats["errors"].append(msg)
        app_module.store.set_ingest_progress(user_id, 100)
        app_module.store.release_ingest(user_id)
        return sync_stats

    all_slugs = set()

    print(f"[ingest/sync] session cookie present (length={len(session_cookie)}), fetching watchlist (max_pages={max_watchlist_pages})...", flush=True)

    try:
        live_watchlist = app_module.scraper.pull_watchlist_slugs(
            session_cookie, username=username, max_pages=max_watchlist_pages
        )
        print(f"[ingest/sync] watchlist scraper returned {len(live_watchlist)} slugs", flush=True)
        result = app_module.store.batch_add_watchlist(user_id, list(live_watchlist))
        all_slugs.update(live_watchlist)
        sync_stats["watchlist_count"] = result.get("added", len(live_watchlist))
        sync_stats["errors"].extend(result.get("errors") or [])
        print(f"[ingest/sync] watchlist stored: {sync_stats['watchlist_count']} slugs for user_id={user_id}", flush=True)
    except Exception as exc:
        msg = f"watchlist fetch failed: {type(exc).__name__}: {exc}"
        print(f"[ingest/sync] ERROR: {msg}", flush=True)
        sync_stats["errors"].append(msg)

    app_module.store.set_ingest_progress(user_id, 40)

    print(f"[ingest/sync] fetching diary (max_pages={max_diary_pages})...", flush=True)
    try:
        live_diary = app_module.scraper.pull_diary_slugs(
            session_cookie, username=username, max_pages=max_diary_pages
        )
        print(f"[ingest/sync] diary scraper returned {len(live_diary)} slugs", flush=True)
        result = app_module.store.batch_add_diary(user_id, list(live_diary))
        all_slugs.update(live_diary)
        sync_stats["diary_count"] = result.get("added", len(live_diary))
        sync_stats["errors"].extend(result.get("errors") or [])
        print(f"[ingest/sync] diary stored: {sync_stats['diary_count']} slugs for user_id={user_id}", flush=True)
    except Exception as exc:
        msg = f"diary fetch failed: {type(exc).__name__}: {exc}"
        print(f"[ingest/sync] ERROR: {msg}", flush=True)
        sync_stats["errors"].append(msg)

    app_module.store.set_ingest_progress(user_id, 70)

    # NEW: Fetch metadata for all collected slugs
    if all_slugs and os.getenv("VERCEL"):
        # Catalog is seed-populated on Vercel; scraping here would blow the timeout.
        print("[ingest/sync] VERCEL set — skipping metadata scrape", flush=True)
    elif all_slugs:
        existing = app_module.store.get_movies_by_slugs(list(all_slugs))
        missing = [slug for slug in all_slugs if slug not in existing]
        missing = missing[:50]
        print(f"[ingest/sync] {len(missing)} movies need metadata (capped at 50)", flush=True)

        if missing:
            try:
                movies = app_module.scraper.metadata_for_slugs(missing)
                fetched = 0
                for movie in movies:
                    try:
                        app_module.store.upsert_movie(movie.__dict__)
                        fetched += 1
                    except Exception as exc:
                        sync_stats["errors"].append(f"metadata {movie.slug}: {exc}")
                sync_stats["metadata_count"] = fetched
                print(f"[ingest/sync] metadata fetched: {fetched} ok", flush=True)
            except Exception as exc:
                msg = f"metadata fetch failed: {type(exc).__name__}: {exc}"
                print(f"[ingest/sync] ERROR: {msg}", flush=True)
                sync_stats["errors"].append(msg)

    app_module.store.set_ingest_progress(user_id, 100)
    app_module.store.release_ingest(user_id)
    print(
        f"[ingest/sync] DONE for {username}: "
        f"watchlist={sync_stats['watchlist_count']} diary={sync_stats['diary_count']} "
        f"metadata={sync_stats['metadata_count']} errors={len(sync_stats['errors'])}",
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
    source_slugs = app_module.scraper.pull_source_slugs(source=source, depth_pages=depth_pages)
    print(f"[ingest] stage=source_fetch_complete count={len(source_slugs)}", flush=True)
    _emit(20)

    watchlist = app_module.store.get_watchlist(user_id)
    diary = app_module.store.get_diary(user_id)
    exclusions = app_module.store.get_exclusions(user_id)

    # Augment with live Letterboxd history when a session cookie is available.
    # Persist to store so deck filtering and future ingests use the real data.
    # Failures are non-fatal — ingest continues with store-only data.
    if session_cookie:
        print(f"[ingest] session cookie present (length={len(session_cookie)}), fetching live history...", flush=True)
        _emit(22)
        try:
            live_watchlist = app_module.scraper.pull_watchlist_slugs(session_cookie, username=username)
            for slug in live_watchlist:
                app_module.store.add_watchlist(user_id, slug)
            watchlist = watchlist | live_watchlist
            print(f"[ingest] live_watchlist={len(live_watchlist)} persisted to store", flush=True)
        except Exception as exc:
            print(f"[ingest] live watchlist fetch failed: {type(exc).__name__}: {exc}", flush=True)
        _emit(31)
        try:
            live_diary = app_module.scraper.pull_diary_slugs(session_cookie, username=username)
            for slug in live_diary:
                app_module.store.add_diary(user_id, slug)
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
    movies_raw = app_module.scraper.metadata_for_slugs(unique)
    print(f"[ingest] stage=metadata_fetch_complete count={len(movies_raw)}", flush=True)
    n = max(len(movies_raw), 1)
    metadata = []
    for i, m in enumerate(movies_raw):
        movie = m.__dict__
        app_module.store.upsert_movie(movie)
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
        app_module.store.set_ingest_progress(user_id, pct)

    try:
        app_module.store.set_ingest_error(user_id, None)
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
        app_module.store.set_ingest_progress(user_id, -1)
        app_module.store.set_ingest_error(user_id, {"code": "ingest_worker_failed", "reason": str(exc)})
        print(f"[ingest] worker_error user_id={user_id} error={exc}", flush=True)
    finally:
        app_module.store.release_ingest(user_id)
