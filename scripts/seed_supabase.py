"""
Local seed script: scrape Letterboxd from this machine and upload to Supabase.

Why local? Vercel's AWS IPs are blocked by Letterboxd. Your home IP is not.
This script scrapes lists + movies locally then writes them straight to production.

Usage:
    python scripts/seed_supabase.py
    python scripts/seed_supabase.py --lists-pages 5
    python scripts/seed_supabase.py --movies-only
    python scripts/seed_supabase.py --skip-movies
    python scripts/seed_supabase.py --dry-run

Options:
    --lists-pages N   Pages of popular lists to scrape (default: 3, ~18 lists/page)
    --skip-movies     Upload list metadata + memberships only, skip movie metadata
    --movies-only     Skip list scraping; fetch metadata for slugs already in DB memberships
    --dry-run         Scrape but don't write to Supabase (useful for debugging)

Auth:
    Reads LETTERBOXD_USERNAME + LETTERBOXD_PASSWORD from .env and logs in to get a
    session cookie automatically. This authenticates every scrape request (tier 1),
    which is much harder for Letterboxd to block than anonymous requests.
    Set LETTERBOXD_SESSION_COOKIE in .env to skip the login step and use a
    pre-obtained cookie directly (faster).
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


def _get_session_cookie(scraper) -> str | None:
    """Try to obtain a Letterboxd session cookie for authenticated scraping.

    Checks (in order):
    1. LETTERBOXD_SESSION_COOKIE env var (pre-obtained cookie, fastest)
    2. Login with LETTERBOXD_USERNAME + LETTERBOXD_PASSWORD
    """
    # Option 1: pre-set cookie
    cookie = os.getenv("LETTERBOXD_SESSION_COOKIE", "").strip()
    if cookie:
        print(f"[seed] Using pre-set LETTERBOXD_SESSION_COOKIE")
        return cookie

    # Option 2: login with credentials
    username = os.getenv("LETTERBOXD_USERNAME", "").strip()
    password = os.getenv("LETTERBOXD_PASSWORD", "").strip()
    if username and password:
        print(f"[seed] Logging in as {username} to get session cookie...")
        try:
            cookie = scraper.login(username, password)
            print(f"[seed] Login OK — session cookie obtained")
            return cookie
        except Exception as exc:
            print(f"[seed] Login failed ({exc}), continuing without auth cookie")
            return None

    print("[seed] No LETTERBOXD_SESSION_COOKIE or credentials found — scraping without auth")
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Supabase with Letterboxd data")
    parser.add_argument("--lists-pages", type=int, default=3, help="Pages of popular lists to scrape (default: 3)")
    parser.add_argument("--skip-movies", action="store_true", help="Skip movie metadata fetch")
    parser.add_argument("--movies-only", action="store_true", help="Skip list scraping; only fetch missing movie metadata")
    parser.add_argument("--dry-run", action="store_true", help="Scrape only, don't write to Supabase")
    args = parser.parse_args()

    from src.api.providers.letterboxd import HttpLetterboxdScraper
    from src.api.database import is_supabase_configured

    scraper = HttpLetterboxdScraper()

    # Get session cookie — uses auth credentials from .env for tier-1 requests
    session_cookie = _get_session_cookie(scraper)
    if session_cookie:
        scraper.session_cookie = session_cookie

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

    if args.movies_only:
        # Skip list scraping — go straight to fetching missing movie metadata
        _fetch_missing_movies(scraper, store, args)
        return

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
        print("[seed] No lists scraped. Check your connection.")
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
        _print_summary(store, args)
        return

    _fetch_movies_for_slugs(unique_slugs, scraper, store, args)
    _print_summary(store, args)


def _fetch_missing_movies(scraper, store, args) -> None:
    """--movies-only: pull all slugs from DB memberships, fetch metadata for missing ones."""
    if args.dry_run or store is None:
        print("[seed] --movies-only requires a live Supabase connection (not --dry-run)")
        return

    print("\n[seed] --movies-only: loading slugs from DB list memberships...")
    membership_rows = store.client.table("list_memberships").select("movie_slug").execute().data
    all_slugs = list({row["movie_slug"] for row in membership_rows if row.get("movie_slug")})
    print(f"[seed] {len(all_slugs)} unique slugs in memberships")

    existing = {row["slug"] for row in store.client.table("movies").select("slug").execute().data}
    missing = [s for s in all_slugs if s not in existing]
    print(f"[seed] {len(existing)} already in movies table, {len(missing)} need fetching")

    if not missing:
        print("[seed] All movies already seeded. Done.")
        return

    _fetch_movies_for_slugs(missing, scraper, store, args)
    _print_summary(store, args)


def _fetch_movies_for_slugs(slugs: list[str], scraper, store, args) -> None:
    """Fetch + upsert metadata for the given slug list."""
    if not slugs:
        return

    # Check which ones are already in DB (skip if not --movies-only, already filtered upstream)
    missing_slugs = slugs
    if not args.dry_run and store is not None and not args.movies_only:
        print("\n[seed] Checking which movies are already in Supabase...")
        existing = {row["slug"] for row in store.client.table("movies").select("slug").execute().data}
        missing_slugs = [s for s in slugs if s not in existing]
        print(f"[seed] {len(existing)} already in DB, {len(missing_slugs)} need fetching")

    if not missing_slugs:
        print("[seed] All movies already seeded.")
        return

    BATCH = 20
    total = len(missing_slugs)
    fetched = 0
    failed = 0

    print(f"\n[seed] Fetching metadata for {total} movies in batches of {BATCH}...")
    for batch_start in range(0, total, BATCH):
        batch = missing_slugs[batch_start: batch_start + BATCH]
        batch_end = min(batch_start + BATCH, total)
        print(f"  [movies] {batch_start + 1}-{batch_end}/{total}...", end=" ", flush=True)

        try:
            movies = scraper.metadata_for_slugs(batch)
        except Exception as exc:
            failed += len(batch)
            print(f"SCRAPE FAILED: {exc}")
            time.sleep(0.5)
            continue

        batch_fetched = 0
        batch_failed = 0
        if not args.dry_run and store is not None:
            for movie in movies:
                try:
                    store.upsert_movie(movie.__dict__)
                    batch_fetched += 1
                except Exception as exc:
                    batch_failed += 1
                    print(f"\n    [warn] upsert failed for {movie.slug}: {exc}", end="")
        else:
            batch_fetched = len(movies)

        fetched += batch_fetched
        failed += batch_failed + (len(batch) - len(movies))
        print(f"fetched {batch_fetched}/{len(batch)}" + (f" ({batch_failed} upsert errors)" if batch_failed else ""))
        time.sleep(0.5)

    print(f"\n[seed] Movie metadata: {fetched} written, {failed} failed/skipped")


def _print_summary(store, args) -> None:
    print(f"\n[seed] Done. Supabase now has:")
    if not args.dry_run and store is not None:
        lists_count = len(store.get_lists())
        movies_count = len(store.client.table("movies").select("slug").execute().data)
        print(f"  {lists_count} lists")
        print(f"  {movies_count} movies")
    else:
        print(f"  (dry run — nothing written)")


if __name__ == "__main__":
    main()
