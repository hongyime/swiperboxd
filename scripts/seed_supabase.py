"""
Local seed script: scrape Letterboxd from this machine and upload to Supabase.

Why local? Vercel's AWS IPs are blocked by Letterboxd. Your home IP is not.

Auth flow (tried in order):
  1. LETTERBOXD_SESSION_COOKIE in .env  → fastest, skip login entirely
  2. Headless login with LETTERBOXD_USERNAME + LETTERBOXD_PASSWORD
  3. Opens a real Chromium browser → you log in manually → cookie extracted automatically
     (requires: pip install playwright && playwright install chromium)

Resume logic:
  - Lists whose memberships are already in Supabase are skipped (slug scraping)
  - Movies already in the movies table are skipped (metadata fetch)
  - Kill the script any time — restart picks up where it left off

Usage:
    python scripts/seed_supabase.py
    python scripts/seed_supabase.py --lists-pages 5
    python scripts/seed_supabase.py --skip-movies     # lists + memberships only
    python scripts/seed_supabase.py --movies-only     # fill in missing movie metadata
    python scripts/seed_supabase.py --no-resume       # re-scrape everything (ignore cache)
    python scripts/seed_supabase.py --dry-run         # scrape but don't write to Supabase
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

from dotenv import load_dotenv, set_key
load_dotenv(repo_root / ".env")

ENV_FILE = repo_root / ".env"
MOVIE_BATCH = 5      # fetch this many movie pages at once before saving
FAIL_BAIL_THRESHOLD = 10  # consecutive all-tiers-failed errors before aborting


# ── Auth ──────────────────────────────────────────────────────────────────────

def _get_session_cookie(scraper) -> str | None:
    """Return a Letterboxd session cookie, trying env → headless login → browser."""

    # 1. Pre-set cookie in .env
    cookie = os.getenv("LETTERBOXD_SESSION_COOKIE", "").strip()
    if cookie:
        print("[auth] Using LETTERBOXD_SESSION_COOKIE from .env")
        return cookie

    # 2. Headless login
    username = os.getenv("LETTERBOXD_USERNAME", "").strip()
    password = os.getenv("LETTERBOXD_PASSWORD", "").strip()
    if username and password:
        print(f"[auth] Trying headless login as {username}...")
        try:
            cookie = scraper.login(username, password)
            print("[auth] Headless login OK")
            return cookie
        except Exception as exc:
            print(f"[auth] Headless login failed ({exc}) — falling back to browser login")

    # 3. Browser login via Playwright
    return _browser_login()


def _browser_login() -> str | None:
    """Open a real Chromium browser, wait for the user to log in, return the session cookie."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "\n[auth] Playwright not installed.\n"
            "  Run:  pip install playwright && playwright install chromium\n"
            "  Then re-run this script.\n"
        )
        return None

    print("\n[auth] Opening Chromium for manual login...")
    print("[auth] Log in to Letterboxd in the browser — the script will continue automatically.\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=50)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://letterboxd.com/sign-in/")

        # Poll until letterboxd.user.CURRENT cookie appears (up to 3 minutes)
        cookie_value = None
        for _ in range(180):
            for c in context.cookies():
                if c["name"] == "letterboxd.user.CURRENT":
                    cookie_value = c["value"]
                    break
            if cookie_value:
                print("[auth] Login detected — cookie extracted")
                break
            time.sleep(1)
        else:
            print("[auth] Timed out waiting for login (3 min)")

        browser.close()

    if cookie_value:
        # Persist to .env so future runs skip the browser step
        try:
            set_key(str(ENV_FILE), "LETTERBOXD_SESSION_COOKIE", cookie_value)
            print(f"[auth] Saved cookie to {ENV_FILE} as LETTERBOXD_SESSION_COOKIE")
        except Exception as exc:
            print(f"[auth] Could not save cookie to .env: {exc}")
        return cookie_value

    return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Supabase with Letterboxd data")
    parser.add_argument("--lists-pages", type=int, default=3, help="Pages of popular lists to scrape (default: 3)")
    parser.add_argument("--skip-movies", action="store_true", help="Save lists + memberships only, skip movie metadata")
    parser.add_argument("--movies-only", action="store_true", help="Fill in missing movie metadata; skip list scraping")
    parser.add_argument("--backfill-lb-ids", action="store_true", help="Fetch lb_film_id for all movies missing it")
    parser.add_argument("--no-resume", action="store_true", help="Re-scrape everything, ignore existing DB data")
    parser.add_argument("--dry-run", action="store_true", help="Scrape but don't write to Supabase")
    args = parser.parse_args()

    from src.api.providers.letterboxd import HttpLetterboxdScraper
    from src.api.database import is_supabase_configured

    scraper = HttpLetterboxdScraper()

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
        _fetch_missing_movies(scraper, store, args)
        return

    if args.backfill_lb_ids:
        _backfill_lb_film_ids(scraper, store, args)
        return

    # ── Step 1: Discover lists page by page ────────────────────────────────
    print(f"\n[seed] Scraping {args.lists_pages} page(s) of popular lists...")
    seen_ids: set[str] = set()
    unique_lists = []

    for page in range(1, args.lists_pages + 1):
        print(f"  [lists] page {page}/{args.lists_pages}...", end=" ", flush=True)
        try:
            results = scraper.discover_site_lists(page=page)
            new = [r for r in results if r.list_id not in seen_ids]
            for r in new:
                seen_ids.add(r.list_id)
                unique_lists.append(r)
            print(f"got {len(results)} ({len(new)} new, {len(unique_lists)} total unique)")
        except Exception as exc:
            print(f"FAILED: {exc}")
        time.sleep(0.5)

    if not unique_lists:
        print("[seed] No lists scraped. Check your connection.")
        sys.exit(1)

    print(f"\n[seed] Processing {len(unique_lists)} unique lists...\n")

    # ── Step 2: Per-list: save summary → get slugs → save memberships → fetch movies
    total_movies_written = 0
    total_movies_skipped = 0
    total_movies_failed = 0

    for list_idx, lst in enumerate(unique_lists, 1):
        print(f"[{list_idx}/{len(unique_lists)}] {lst.title[:70]}")

        # Save list summary immediately
        if not args.dry_run:
            try:
                store.upsert_list_summary(lst.__dict__)
            except Exception as exc:
                print(f"  [warn] Failed to save list summary: {exc}")

        # Get movie slugs — resume from DB if possible
        slugs = _get_slugs_for_list(lst, scraper, store, args)
        if slugs is None:
            print(f"  [skip] Could not get slugs — skipping")
            continue

        print(f"  slugs: {len(slugs)}", end="")

        if args.skip_movies:
            print(" (--skip-movies: not fetching metadata)")
            continue

        # Which movies need fetching?
        if not args.dry_run and not args.no_resume:
            existing = {row["slug"] for row in store.client.table("movies").select("slug").in_("slug", slugs).execute().data} if slugs else set()
            to_fetch = [s for s in slugs if s not in existing]
            print(f" | already in DB: {len(existing)} | to fetch: {len(to_fetch)}")
        else:
            to_fetch = list(slugs)
            print(f" | to fetch: {len(to_fetch)}")

        if not to_fetch:
            print(f"  [resume] All movies already in DB — skipping")
            continue

        # Fetch + save in small batches so every movie is persisted immediately
        w, sk, f = _fetch_and_save_movies(to_fetch, lst.title, scraper, store, args)
        total_movies_written += w
        total_movies_skipped += sk
        total_movies_failed += f

        print()  # blank line between lists

    _print_summary(store, args, total_movies_written, total_movies_skipped, total_movies_failed)


def _get_slugs_for_list(lst, scraper, store, args) -> list[str] | None:
    """Return movie slugs for a list — resume from DB cache when available."""
    # Resume: memberships already in DB?
    if not args.dry_run and not args.no_resume:
        cached = store.get_list_memberships(lst.list_id)
        if cached:
            print(f"  [resume] memberships cached ({len(cached)} slugs)")
            return cached

    # Scrape fresh
    try:
        slugs = scraper.fetch_list_movie_slugs(lst.list_id, list_url=lst.url)
    except Exception as exc:
        print(f"  [error] slug scrape failed: {exc}")
        return None

    if not args.dry_run and slugs:
        try:
            store.replace_list_memberships(lst.list_id, slugs)
        except Exception as exc:
            print(f"  [warn] Failed to save memberships: {exc}")

    time.sleep(0.3)
    return slugs


def _fetch_and_save_movies(
    slugs: list[str], list_title: str, scraper, store, args
) -> tuple[int, int, int]:
    """Fetch movie metadata in small batches and save each movie immediately.

    Returns (written, skipped, failed).
    Aborts early when FAIL_BAIL_THRESHOLD consecutive all-tiers-failed errors occur.
    """
    written = skipped = failed = 0
    total = len(slugs)
    consecutive_all_failed = 0

    for batch_start in range(0, total, MOVIE_BATCH):
        batch = slugs[batch_start: batch_start + MOVIE_BATCH]
        batch_end = min(batch_start + MOVIE_BATCH, total)
        print(f"  [movies] {batch_start + 1}-{batch_end}/{total}...", end=" ", flush=True)

        try:
            movies = scraper.metadata_for_slugs(batch)
            consecutive_all_failed = 0  # reset on any non-exception result
        except RuntimeError as exc:
            if "all_tiers_failed" in str(exc):
                consecutive_all_failed += len(batch)
                failed += len(batch)
                print(f"ALL TIERS FAILED ({consecutive_all_failed} consecutive)")
                if consecutive_all_failed >= FAIL_BAIL_THRESHOLD:
                    print(
                        f"\n[seed] aborting early — {consecutive_all_failed} consecutive "
                        "all-tiers-failed errors. Saving what we have.",
                        flush=True,
                    )
                    return written, skipped, failed
            else:
                failed += len(batch)
                print(f"SCRAPE FAILED: {exc}")
            time.sleep(1)
            continue
        except Exception as exc:
            failed += len(batch)
            print(f"SCRAPE FAILED: {exc}")
            time.sleep(1)
            continue

        fetched_slugs = {m.slug for m in movies}
        skipped += len(batch) - len(fetched_slugs)  # slugs that came back empty

        batch_ok = batch_fail = 0
        if not args.dry_run and store is not None:
            for movie in movies:
                try:
                    store.upsert_movie(movie.__dict__)
                    batch_ok += 1
                except Exception as exc:
                    batch_fail += 1
                    print(f"\n    [warn] upsert failed for {movie.slug}: {exc}", end="")
        else:
            batch_ok = len(movies)

        written += batch_ok
        failed += batch_fail
        print(f"saved {batch_ok}/{len(batch)}" + (f" ({batch_fail} errors)" if batch_fail else ""))
        time.sleep(0.4)

    return written, skipped, failed


def _fetch_missing_movies(scraper, store, args) -> None:
    """--movies-only: fetch metadata for slugs in DB memberships that have no movie record."""
    if args.dry_run or store is None:
        print("[seed] --movies-only requires a live Supabase connection (not --dry-run)")
        return

    print("\n[seed] --movies-only: loading slugs from DB list memberships...")
    membership_rows = store.client.table("list_memberships").select("movie_slug").execute().data
    all_slugs = list({row["movie_slug"] for row in membership_rows if row.get("movie_slug")})
    print(f"[seed] {len(all_slugs)} unique slugs in memberships")

    existing = {row["slug"] for row in store.client.table("movies").select("slug").execute().data}
    missing = [s for s in all_slugs if s not in existing]
    print(f"[seed] {len(existing)} already in movies table, {len(missing)} to fetch")

    if not missing:
        print("[seed] All movies already seeded.")
        return

    w, sk, f = _fetch_and_save_movies(missing, "all lists", scraper, store, args)
    _print_summary(store, args, w, sk, f)


def _backfill_lb_film_ids(scraper, store, args) -> None:
    """--backfill-lb-ids: fetch lb_film_id for every movie that doesn't have one yet.

    The LID comes from the x-letterboxd-identifier response header on the film page.
    Only makes one HTTP request per movie — no HTML parsing needed.
    """
    if args.dry_run or store is None:
        print("[backfill] requires a live Supabase connection (not --dry-run)")
        return

    print("\n[backfill] Loading movies missing lb_film_id...")
    rows = (
        store.client.table("movies")
        .select("slug")
        .is_("lb_film_id", "null")
        .execute()
        .data
    )
    slugs = [r["slug"] for r in rows if r.get("slug")]
    print(f"[backfill] {len(slugs)} movies need lb_film_id")

    if not slugs:
        print("[backfill] Nothing to do.")
        return

    import httpx as _httpx
    updated = failed = 0
    for i, slug in enumerate(slugs, 1):
        url = f"{scraper.base_url}/film/{slug}/"
        try:
            # HEAD request is enough — we only need the response header
            with _httpx.Client(
                timeout=10.0,
                follow_redirects=True,
                headers=scraper._BROWSER_HEADERS,
            ) as client:
                resp = client.head(url)
            lb_film_id = resp.headers.get("x-letterboxd-identifier", "")
            if not lb_film_id:
                # HEAD may not return custom headers on all CDN configs — fall back to GET
                resp = scraper._fetch(url)
                lb_film_id = resp.headers.get("x-letterboxd-identifier", "")

            if lb_film_id:
                store.client.table("movies").update(
                    {"lb_film_id": lb_film_id}
                ).eq("slug", slug).execute()
                updated += 1
                print(f"  [{i}/{len(slugs)}] {slug} → {lb_film_id}")
            else:
                failed += 1
                print(f"  [{i}/{len(slugs)}] {slug} → no LID in headers")
        except Exception as exc:
            failed += 1
            print(f"  [{i}/{len(slugs)}] {slug} → ERROR: {exc}")

        time.sleep(0.3)  # be polite

    print(f"\n[backfill] Done. updated={updated} failed/missing={failed}")


def _print_summary(store, args, written=0, skipped=0, failed=0) -> None:
    print(f"\n[seed] Done.")
    print(f"  Movies written: {written} | skipped (no data): {skipped} | failed: {failed}")
    if not args.dry_run and store is not None:
        try:
            lists_resp = store.client.table("lists").select("list_id", count="exact").execute()  # type: ignore[union-attr]
            movies_resp = store.client.table("movies").select("slug", count="exact").execute()  # type: ignore[union-attr]
            print(f"  Supabase totals: {lists_resp.count} lists, {movies_resp.count} movies")
        except Exception as exc:
            print(f"  (Could not fetch totals: {exc})")
    else:
        print("  (dry run — nothing written)")


if __name__ == "__main__":
    main()
