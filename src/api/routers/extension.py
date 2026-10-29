"""Chrome-extension synchronization routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from .. import app as app_module
from ..app import (
    ExtensionBatchListMoviesRequest,
    ExtensionBatchListSummariesRequest,
    ExtensionBatchMoviesRequest,
    ExtensionBatchRequest,
    ExtensionSyncStatusRequest,
)
from ..store import normalize_movie_record

router = APIRouter()

@router.get("/api/extension/user-history")
def extension_user_history(
    verified_user: str = Depends(app_module.verify_extension_user),
    user_id: str | None = Query(default=None),
    include_watchlist: bool = Query(default=True),
    include_diary: bool = Query(default=True),
    limit: int = Query(default=10000, ge=1, le=50000),
):
    """Return user watchlist/diary slug sets for extension-side cross-sync.
    """
    if user_id and user_id != verified_user:
        raise HTTPException(status_code=403, detail={"code": "user_id_mismatch"})

    try:
        watchlist_slugs = sorted(app_module.store.get_watchlist(verified_user)) if include_watchlist else []
        diary_slugs = sorted(app_module.store.get_diary(verified_user)) if include_diary else []
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"code": "store_error", "reason": str(exc)}) from exc

    return {
        "status": "ok",
        "user_id": verified_user,
        "watchlist_slugs": watchlist_slugs[:limit],
        "diary_slugs": diary_slugs[:limit],
        "watchlist_count": len(watchlist_slugs),
        "diary_count": len(diary_slugs),
    }

_EXTENSION_BATCH_LIMIT = 500


def _extension_batch_response(payload: ExtensionBatchRequest, result: dict) -> JSONResponse:
    failed = bool(result.get("errors"))
    return JSONResponse(status_code=503 if failed else 200, content={
        "status": "error" if failed else "ok",
        "user_id": payload.user_id,
        "page": payload.page,
        "total_pages": payload.total_pages,
        "result": result,
    })

@router.post("/api/extension/batch/watchlist")
async def extension_batch_watchlist(
    payload: ExtensionBatchRequest,
    verified_user: str = Depends(app_module.verify_extension_user),
):
    """Push a batch of watchlist slugs scraped by the Chrome extension.
    
    EXPECTS: Metadata for these slugs should already be in the database.
    If metadata is missing, logs warning but doesn't fail the request.
    """
    if verified_user and payload.user_id != verified_user:
        raise HTTPException(status_code=403, detail={"code": "user_id_mismatch"})
    if len(payload.slugs) > _EXTENSION_BATCH_LIMIT:
        raise HTTPException(status_code=413, detail={"code": "batch_too_large", "limit": _EXTENSION_BATCH_LIMIT})

    print(
        f"[extension] watchlist batch user={payload.user_id} "
        f"page={payload.page}/{payload.total_pages} slugs={len(payload.slugs)}",
        flush=True,
    )
    result = app_module.store.batch_add_watchlist(payload.user_id, payload.slugs)
    
    # Warn if metadata is missing (shouldn't happen after Phase 1 fix)
    if result.get("missing_metadata"):
        print(
            f"[extension] WARNING: {len(result['missing_metadata'])} movies missing metadata. "
            f"Extension should fetch metadata before pushing watchlist.",
            flush=True
        )
    
    return _extension_batch_response(payload, result)

@router.post("/api/extension/batch/diary")
async def extension_batch_diary(
    payload: ExtensionBatchRequest,
    verified_user: str = Depends(app_module.verify_extension_user),
):
    """Push a batch of diary slugs scraped by the Chrome extension.
    
    EXPECTS: Metadata for these slugs should already be in the database.
    If metadata is missing, logs warning but doesn't fail the request.
    """
    if verified_user and payload.user_id != verified_user:
        raise HTTPException(status_code=403, detail={"code": "user_id_mismatch"})
    if len(payload.slugs) > _EXTENSION_BATCH_LIMIT:
        raise HTTPException(status_code=413, detail={"code": "batch_too_large", "limit": _EXTENSION_BATCH_LIMIT})

    print(
        f"[extension] diary batch user={payload.user_id} "
        f"page={payload.page}/{payload.total_pages} slugs={len(payload.slugs)}",
        flush=True,
    )
    result = app_module.store.batch_add_diary(payload.user_id, payload.slugs)
    
    # Warn if metadata is missing (shouldn't happen after Phase 1 fix)
    if result.get("missing_metadata"):
        print(
            f"[extension] WARNING: {len(result['missing_metadata'])} movies missing metadata. "
            f"Extension should fetch metadata before pushing diary.",
            flush=True
        )
    
    return _extension_batch_response(payload, result)

@router.post("/api/extension/batch/movies")
async def extension_batch_movies(
    payload: ExtensionBatchMoviesRequest,
    verified_user: str = Depends(app_module.verify_extension_user),
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
            app_module.store.upsert_movie(record)
            stored += 1
        except Exception as exc:
            failed.append({"slug": movie.slug, "error": str(exc)})

    print(
        f"[extension] movies batch user={verified_user or '?'} "
        f"stored={stored} failed={len(failed)}",
        flush=True,
    )
    return {"status": "ok", "stored": stored, "failed": failed}

@router.post("/api/extension/batch/list-summaries")
async def extension_batch_list_summaries(
    payload: ExtensionBatchListSummariesRequest,
    verified_user: str = Depends(app_module.verify_extension_user),
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
            existing = app_module.store.get_list_summary(record["list_id"])
            if existing and existing.get("scraped_film_count"):
                record["scraped_film_count"] = existing["scraped_film_count"]
            else:
                record["scraped_film_count"] = 0
            app_module.store.upsert_list_summary(record)
            stored += 1
        except Exception as exc:
            failed.append({"list_id": summary.list_id, "error": str(exc)})

    print(
        f"[extension] list-summaries batch source={payload.source} page={payload.page} "
        f"stored={stored} failed={len(failed)}",
        flush=True,
    )
    return {"status": "ok", "stored": stored, "failed": failed}

@router.get("/api/extension/movies-needing-backfill")
def extension_movies_needing_backfill(
    limit: int = Query(default=500, ge=1, le=2000),
    mode: str = Query(default="core", pattern="^(core|full)$"),
    verified_user: str = Depends(app_module.verify_extension_user),
):
    """Return slugs of movies with incomplete metadata.

    mode=core => title/poster/lb_film_id required
    mode=full => core + year/director/synopsis/genres/cast/rating expected
    """
    try:
        response = (
            app_module.store.client.table("movies")
            .select("slug,title,poster_url,lb_film_id,year,director,synopsis,genres,cast,rating")
            .limit(min(5000, max(limit * 3, limit)))
            .execute()
        )
        rows = response.data or []

        def _missing_core(row: dict) -> bool:
            return (
                not (row.get("title") or "").strip()
                or not (row.get("poster_url") or "").strip()
                or not (row.get("lb_film_id") or "").strip()
            )

        def _missing_full(row: dict) -> bool:
            if _missing_core(row):
                return True
            synopsis = (row.get("synopsis") or "").strip()
            director = (row.get("director") or "").strip()
            year = row.get("year")
            genres = row.get("genres") if isinstance(row.get("genres"), list) else []
            cast = row.get("cast") if isinstance(row.get("cast"), list) else []
            rating = row.get("rating")
            return (
                not synopsis
                or not director
                or year in (None, "")
                or not genres
                or not cast
                or rating in (None, "")
            )

        predicate = _missing_full if mode == "full" else _missing_core
        slugs = [r["slug"] for r in rows if r.get("slug") and predicate(r)][:limit]
    except Exception as exc:
        print(f"[extension] movies-needing-backfill failed: {exc}", flush=True)
        return {"status": "error", "slugs": [], "reason": str(exc)}
    return {"status": "ok", "mode": mode, "count": len(slugs), "slugs": slugs}

@router.delete("/api/extension/lists/{list_id}")
def extension_delete_list(list_id: str, verified_user: str = Depends(app_module.verify_extension_user)):
    """Delete a list that no longer exists on Letterboxd (404)."""
    try:
        app_module.store.delete_list(list_id)
        return {"status": "ok", "deleted": list_id}
    except Exception as exc:
        print(f"[extension] delete_list failed: {exc}", flush=True)
        return {"status": "error", "reason": str(exc)}

@router.get("/api/extension/lists-needing-scrape")
def extension_lists_needing_scrape(
    limit: int = Query(default=25, ge=1, le=200),
    min_coverage: float = Query(default=0.96, ge=0.0, le=1.0),
    verified_user: str = Depends(app_module.verify_extension_user),
):
    """Return list_summaries rows that are under 50% scraped (or brand-new).

    Used by the extension to decide which lists still need their films fetched.
    Returns minimal fields — list_id, url, title, film_count, scraped_film_count.
    """
    try:
        rows = app_module.store.get_underscraped_lists(limit=limit, min_coverage=min_coverage)
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

@router.post("/api/extension/batch/list-movies")
async def extension_batch_list_movies(
    payload: ExtensionBatchListMoviesRequest,
    verified_user: str = Depends(app_module.verify_extension_user),
):
    """Push film slugs scraped from a Letterboxd list page.

    If the list_summaries row is missing, upserts a skeleton row from the
    provided metadata. Accumulates scraped_film_count across paginated batches.
    """
    if len(payload.slugs) > _EXTENSION_BATCH_LIMIT:
        raise HTTPException(status_code=413, detail={"code": "batch_too_large", "limit": _EXTENSION_BATCH_LIMIT})

    existing = app_module.store.get_list_summary(payload.list_id) if hasattr(app_module.store, "get_list_summary") else None
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
            app_module.store.upsert_list_summary(summary)
        except Exception as exc:
            print(f"[extension] list skeleton upsert failed {payload.list_id}: {exc}", flush=True)

    try:
        if payload.replace_memberships:
            app_module.store.replace_list_memberships(payload.list_id, payload.slugs)
        else:
            app_module.store.add_list_memberships(payload.list_id, payload.slugs)
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"code": "membership_write_failed", "reason": str(exc)}) from exc

    try:
        count = len(app_module.store.get_list_memberships(payload.list_id))
        app_module.store.update_list_scrape_count(payload.list_id, count)
    except Exception as exc:
        print(f"[extension] scrape_count update failed {payload.list_id}: {exc}", flush=True)
        count = len(payload.slugs)
    print(
        f"[extension] list-movies batch list_id={payload.list_id} "
        f"page={payload.page}/{payload.total_pages} pushed={len(payload.slugs)} total={count}",
        flush=True,
    )

    # Detect slugs in this batch that are missing full metadata
    missing_metadata = []
    try:
        movies_by_slug = app_module.store.get_movies_by_slugs(payload.slugs)
        for slug in payload.slugs:
            movie = movies_by_slug.get(slug)
            if not movie or not movie.get("synopsis") or not movie.get("poster_url"):
                missing_metadata.append(slug)
    except Exception as exc:
        print(f"[extension] missing_metadata detection failed: {exc}", flush=True)

    return {
        "status": "ok",
        "list_id": payload.list_id,
        "page": payload.page,
        "total_pages": payload.total_pages,
        "added": len(payload.slugs),
        "total_scraped": count,
        "missing_metadata": missing_metadata,
    }

@router.post("/api/extension/sync-status")
async def extension_sync_status(
    payload: ExtensionSyncStatusRequest,
    verified_user: str = Depends(app_module.verify_extension_user),
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
    app_module.store.set_ingest_progress(payload.user_id, pct)
    if payload.phase == "error" and payload.message:
        app_module.store.set_ingest_error(payload.user_id, {"code": "extension_error", "reason": payload.message})
    print(
        f"[extension] sync-status user={payload.user_id} phase={payload.phase} "
        f"page={payload.current_page}/{payload.total_pages} slugs_found={payload.slugs_found} pct={pct}",
        flush=True,
    )
    return {"status": "ok", "progress": pct}
