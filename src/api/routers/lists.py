"""List catalog, detail, deck, discovery, and manual refresh routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import app as app_module
from ..store import normalize_movie_record

router = APIRouter()

@router.get("/discovery/profiles")
def discovery_profiles():
    return {"profiles": list(app_module.PROFILES.keys())}

@router.get("/lists/catalog")
def list_catalog(
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    """Return lists from Supabase. Data is populated by the Chrome extension sync."""
    offset = (page - 1) * page_size
    items = app_module.store.get_lists(offset=offset, limit=page_size, q=q)

    # Add is_ready flag based on whether the list is fully scraped
    # Allow a small margin (0.95) for slight discrepancies between listed and actual count
    for item in items:
        film_count = item.get("film_count", 0)
        scraped = item.get("scraped_film_count", 0)
        item["is_ready"] = (film_count == 0) or (scraped >= film_count * 0.95)

    return {
        "status": "ok",
        "query": q or "",
        "page": page,
        "results": items
    }

@router.get("/lists/{list_id}")
def list_detail(list_id: str):
    summary = app_module.store.get_list_summary(list_id)
    if not summary:
        raise HTTPException(status_code=404, detail={"code": "list_not_found"})

    movie_slugs = app_module.store.get_list_memberships(list_id)

    preview = [app_module.store.get_movie(slug) for slug in movie_slugs[:4]]
    preview = [movie for movie in preview if movie]
    return {
        "status": "ok",
        "list": summary,
        "movie_slugs": movie_slugs,
        "preview": preview,
    }

@router.get("/lists/{list_id}/deck")
def list_deck(
    list_id: str,
    user_id: str = Query(min_length=1),
    include_seen: bool = Query(default=False),
):
    summary = app_module.store.get_list_summary(list_id)
    if not summary:
        raise HTTPException(status_code=404, detail={"code": "list_not_found"})

    try:
        movie_slugs = app_module.store.get_list_memberships(list_id)
    except Exception as exc:
        print(f"[deck] get_list_memberships failed: {exc}", flush=True)
        raise HTTPException(status_code=500, detail={"code": "store_error", "reason": str(exc)})

    # We completely rely on the extension's background worker and cron jobs
    # for metadata enrichment. Do not fetch synchronously here to prevent
    # blocking the API for 60+ seconds on cold starts.

    # Filter out movies the user has already watchlisted, logged, or dismissed
    watchlist, diary, exclusions = set(), set(), set()
    uid = None
    if not include_seen:
        try:
            uid = app_module.store.resolve_user_id(user_id)
            watchlist = app_module.store.get_watchlist(user_id, _user_id=uid)
            diary = app_module.store.get_diary(user_id, _user_id=uid)
            exclusions = app_module.store.get_exclusions(user_id, _user_id=uid)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "history_unavailable", "message": "Unable to load viewing history. Please retry."},
                headers={"Cache-Control": "private, no-store"},
            ) from exc
    seen = watchlist | diary | exclusions

    try:
        movies = app_module.store.get_deck_movies(list_id, seen, sample_size=200)
    except Exception as exc:
        print(f"[deck] failed to load deck movies from store: {exc}", flush=True)
        raise HTTPException(status_code=500, detail={"code": "store_error", "reason": str(exc)})

    movies = [m for m in movies if m and m.get("slug") not in seen]
    try:
        movies = app_module.store.weighted_shuffle(user_id, movies, _user_id=uid)
    except Exception as exc:
        print(f"[deck] weighted_shuffle failed: {exc} — using unshuffled", flush=True)
        import random as _random
        _random.shuffle(movies)

    print(
        f"[deck] list={list_id} total_in_list={len(movie_slugs)} "
        f"with_metadata={len([m for m in movies if m])} "
        f"after_filter={len(movies)} "
        f"include_seen={include_seen} "
        f"seen={len(seen)} "
        f"watchlist={len(watchlist)} diary={len(diary)} exclusions={len(exclusions)} "
        f"returned={len(movies[:20])}",
        flush=True,
    )
    return {
        "status": "ok",
        "list": summary,
        "results": movies[:20],
    }

@router.post("/lists/refresh")
async def manual_refresh_lists(verified_user: str = Depends(app_module.verify_session)):
    """Manually refresh lists from Letterboxd (user-triggered).
    
    Requires authenticated session and rate limits to prevent abuse.
    """
    # Rate limit: 1 refresh per 5 minutes per user
    allowed, retry_after = app_module.store.allow_scrape_request(verified_user, min_interval_seconds=300)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={"code": "rate_limited", "retry_after": retry_after}
        )
    
    try:
        # Fetch lists from Letterboxd
        lists_data = app_module.scraper.discover_site_lists(page=1)
        print(f"[lists] manual refresh fetched {len(lists_data)} lists", flush=True)
        
        updated_count = 0
        for item in lists_data:
            existing = app_module.store.get_list_summary(item.list_id)
            # Update only if data changed
            if not existing or existing['like_count'] != item.like_count or existing['film_count'] != item.film_count:
                app_module.store.upsert_list_summary(item.__dict__)
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

@router.get("/discovery/deck")
async def get_discovery_deck(
    user_id: str = Query(min_length=1),
    profile: str = Query(default="gold-standard")
):
    """Get a deck of movies for discovery."""
    if profile not in app_module.PROFILES:
        raise HTTPException(status_code=400, detail={"code": "invalid_profile"})

    matched = []
    skipped_invalid = 0
    for movie in app_module.store.get_movies():
        normalized = normalize_movie_record(movie)
        before_slug = normalized.get("slug", "")
        if app_module._matches_profile(profile, normalized):
            matched.append(normalized)
        elif not before_slug:
            skipped_invalid += 1

    try:
        watchlist = app_module.store.get_watchlist(user_id)
        diary = app_module.store.get_diary(user_id)
        exclusions = app_module.store.get_exclusions(user_id)
    except Exception as exc:
        print(f"[deck] failed to load user filters: {exc}", flush=True)
        watchlist, diary, exclusions = set(), set(), set()
    
    seen = watchlist | diary | exclusions

    movies = app_module.store.weighted_shuffle(user_id, [m for m in matched if m.get("slug") not in seen])
    return {
        "status": "ok",
        "profile": profile,
        "results": movies[:20],
        "meta": {
            "matched_count": len(matched),
            "skipped_invalid_count": skipped_invalid,
        },
    }

@router.get("/discovery/details")
def get_discovery_details(slug: str = Query(min_length=1)):
    """Get movie details."""
    movie = app_module.store.get_movie(slug)
    if not movie:
        raise HTTPException(status_code=404, detail={"code": "movie_not_found"})
    return {
        "status": "ok",
        "slug": slug,
        "synopsis": movie.get("synopsis", ""),
        "cast": movie.get("cast", []),
        "genres": movie.get("genres", []),
    }
