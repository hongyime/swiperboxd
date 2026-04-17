"""
Local seed script: scrape Letterboxd from this machine and upload to Supabase.

Why local? Vercel's AWS IPs are blocked by Letterboxd. Your home IP is not.
This script scrapes lists + movies locally then writes them straight to production.

Usage:
    python scripts/seed_supabase.py
    python scripts/seed_supabase.py --lists-pages 5 --direct
    python scripts/seed_supabase.py --lists-pages 2 --skip-movies

Options:
    --lists-pages N   How many pages of popular lists to scrape (default: 3, ~36 lists)
    --direct          Force direct requests, skip WebShare/ScrapeDo proxies
    --skip-movies     Upload list metadata only, skip movie metadata fetch
    --dry-run         Scrape but don't write to Supabase
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Ensure the repo root is on the path so src.api imports work
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

from dotenv import load_dotenv
load_dotenv(repo_root / ".env")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Supabase with Letterboxd data")
    parser.add_argument("--lists-pages", type=int, default=3, help="Pages of popular lists to scrape (default: 3)")
    parser.add_argument("--direct", action="store_true", help="Use direct requests only (no proxies)")
    parser.add_argument("--skip-movies", action="store_true", help="Skip movie metadata fetch")
    parser.add_argument("--dry-run", action="store_true", help="Scrape only, don't write to Supabase")
    args = parser.parse_args()

    if args.direct:
        print("[seed] --direct: clearing proxy env vars so scraper uses direct requests")
        os.environ.pop("WEBSHARE_PROXIES", None)
        os.environ.pop("SCRAPEDO_TOKEN", None)

    # Import after env manipulation so ProxyManager reads the right values
    from src.api.providers.letterboxd import HttpLetterboxdScraper
    from src.api.database import is_supabase_configured

    scraper = HttpLetterboxdScraper()

    if not args.dry_run:
        if not is_supabase_configured():
            print("[seed] ERROR: Supabase not configured. Check SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env")
            sys.exit(1)
        from src.api.store import SupabaseStore
        store = SupabaseStore()
        print("[seed] Supabase connected")
    else:
        store = None
        print("[seed] DRY RUN — no writes to Supabase")

    # ── Step 1: Scrape list catalog ─────────────────────────────────────────
    print(f"\n[seed] Scraping {args.lists_pages} page(s) of popular lists...")
    all_lists = []
    for page in range(1, args.lists_pages + 1):
        print(f"  [lists] page {page}/{args.lists_pages}...", end=" ", flush=True)
        try:
            page_results = scraper.discover_site_lists(page=page)
            all_lists.extend(page_results)
            print(f"got {len(page_results)} lists (total: {len(all_lists)})")
        except Exception as exc:
            print(f"FAILED: {exc}")
        time.sleep(0.5)

    if not all_lists:
        print("[seed] No lists scraped. Check your connection or try --direct.")
        sys.exit(1)

    # Deduplicate by list_id
    seen_ids: set[str] = set()
    unique_lists = []
    for lst in all_lists:
        if lst.list_id not in seen_ids:
            seen_ids.add(lst.list_id)
            unique_lists.append(lst)

    print(f"\n[seed] {len(unique_lists)} unique lists found")

    if not args.dry_run:
        print("[seed] Writing list summaries to Supabase...")
        for lst in unique_lists:
            store.upsert_list_summary(lst.__dict__)
        print(f"[seed] {len(unique_lists)} list summaries written")

    # ── Step 2: Scrape movie slugs for each list ────────────────────────────
    all_slugs: list[str] = []
    list_slug_map: dict[str, list[str]] = {}

    print(f"\n[seed] Scraping movie slugs for each list...")
    for i, lst in enumerate(unique_lists, 1):
        print(f"  [{i}/{len(unique_lists)}] {lst.title[:60]}...", end=" ", flush=True)
        try:
            slugs = scraper.fetch_list_movie_slugs(lst.list_id, list_url=lst.url)
            list_slug_map[lst.list_id] = slugs
            all_slugs.extend(slugs)
            print(f"{len(slugs)} films")
        except Exception as exc:
            print(f"FAILED: {exc}")
            list_slug_map[lst.list_id] = []
        time.sleep(0.3)

    # Write memberships to Supabase
    if not args.dry_run:
        print("\n[seed] Writing list memberships to Supabase...")
        written_memberships = 0
        for list_id, slugs in list_slug_map.items():
            if slugs:
                store.replace_list_memberships(list_id, slugs)
                written_memberships += 1
        print(f"[seed] {written_memberships} list membership sets written")

    # Deduplicate all movie slugs across lists
    seen_slugs: set[str] = set()
    unique_slugs: list[str] = []
    for slug in all_slugs:
        if slug and slug not in seen_slugs:
            seen_slugs.add(slug)
            unique_slugs.append(slug)

    print(f"\n[seed] {len(unique_slugs)} unique movie slugs across all lists")

    if args.skip_movies:
        print("[seed] --skip-movies: skipping movie metadata fetch")
        print("\n[seed] Done.")
        return

    # ── Step 3: Check which slugs we already have in Supabase ───────────────
    missing_slugs = unique_slugs
    if not args.dry_run:
        print("\n[seed] Checking which movies are already in Supabase...")
        existing = {row["slug"] for row in store.client.table("movies").select("slug").execute().data}
        missing_slugs = [s for s in unique_slugs if s not in existing]
        print(f"[seed] {len(existing)} already in DB, {len(missing_slugs)} need fetching")

    if not missing_slugs:
        print("[seed] All movies already seeded. Done.")
        return

    # ── Step 4: Fetch metadata in batches ───────────────────────────────────
    BATCH = 20
    total = len(missing_slugs)
    fetched = 0
    failed = 0

    print(f"\n[seed] Fetching metadata for {total} movies in batches of {BATCH}...")
    for batch_start in range(0, total, BATCH):
        batch = missing_slugs[batch_start: batch_start + BATCH]
        batch_end = min(batch_start + BATCH, total)
        print(f"  [movies] {batch_start + 1}–{batch_end}/{total}...", end=" ", flush=True)

        try:
            movies = scraper.metadata_for_slugs(batch)
            if not args.dry_run:
                for movie in movies:
                    store.upsert_movie(movie.__dict__)
            fetched += len(movies)
            failed += len(batch) - len(movies)
            print(f"fetched {len(movies)}/{len(batch)}")
        except Exception as exc:
            failed += len(batch)
            print(f"FAILED: {exc}")

        time.sleep(0.5)  # polite pause between batches

    print(f"\n[seed] Movie metadata: {fetched} fetched, {failed} failed/skipped")
    print(f"[seed] Done. Supabase now has:")
    if not args.dry_run:
        lists_count = len(store.get_lists())
        movies_count = len(store.client.table("movies").select("slug").execute().data)
        print(f"  {lists_count} lists")
        print(f"  {movies_count} movies")
    else:
        print(f"  (dry run — nothing written)")


if __name__ == "__main__":
    main()
