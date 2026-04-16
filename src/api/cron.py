"""Cron job handlers for scheduled tasks."""

from __future__ import annotations

import os
from fastapi import APIRouter, Header, HTTPException
from typing import Literal

from .store import Store
from .providers.letterboxd import HttpLetterboxdScraper

router = APIRouter()

# Cron secret to prevent unauthorized access
# Set VERCEL_CRON_SECRET in environment variables
CRON_SECRET = os.getenv("VERCEL_CRON_SECRET")


@router.post("/refresh-lists")
async def refresh_lists_cron(x_cron_secret: str = Header(...)):
    """Vercel Chron cron job endpoint to refresh Letterboxd lists.
    
    Protected by VERCEL_CRON_SECRET header to ensure only authorized calls.
    
    Returns:
        JSON response with refresh stats
    """
    if CRON_SECRET and x_cron_secret != CRON_SECRET:
        raise HTTPException(
            status_code=403, 
            detail="Unauthorized: Invalid cron secret"
        )
    
    scraper = HttpLetterboxdScraper()
    store = Store()  # Will use SupabaseStore or InMemoryStore
        
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
