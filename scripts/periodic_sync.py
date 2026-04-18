"""
Local periodic sync: scrape Letterboxd from this machine and upload to Supabase
for ALL users that have a stored `letterboxd_session` blob, plus backfill
placeholder movies and under-scraped lists.

Why local? Letterboxd blocks Vercel's AWS IPs (403). This script runs the same
logic as /api/cron/sync-users and /api/cron/backfill-scrapes but from a home IP
so the scrape actually succeeds.

Usage:
    python scripts/periodic_sync.py                 # users + movies + lists
    python scripts/periodic_sync.py --users-only
    python scripts/periodic_sync.py --backfill-only
    python scripts/periodic_sync.py --max-users 50 --max-pages 10
    python scripts/periodic_sync.py --max-movies 200 --max-lists 25
    python scripts/periodic_sync.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

from dotenv import load_dotenv
load_dotenv(repo_root / ".env")


def _decrypt_session(encrypted: str) -> str | None:
    from src.api.security import decrypt_session_cookie
    master_key = os.getenv("MASTER_ENCRYPTION_KEY")
    if not master_key:
        print("[sync] MASTER_ENCRYPTION_KEY not set — cannot decrypt user sessions", flush=True)
        return None
    try:
        raw = decrypt_session_cookie(encrypted, master_key)
    except Exception as exc:
        print(f"[sync] decrypt failed: {exc}")
        return None
    try:
        return json.loads(raw).get("c")
    except (json.JSONDecodeError, ValueError):
        return raw or None


def sync_all_users(scraper, store, args) -> dict:
    """Iterate every user with a stored session and refresh watchlist + diary."""
    try:
        sessions = store.get_all_user_sessions()
    except Exception as exc:
        print(f"[sync/users] failed to load sessions: {exc}")
        return {"users": 0, "watchlist": 0, "diary": 0}

    sessions = sessions[: args.max_users]
    print(f"\n[sync/users] processing {len(sessions)} user(s) (max_pages={args.max_pages})")

    total_wl = total_diary = 0
    for idx, entry in enumerate(sessions, 1):
        username = entry.get("username")
        user_id = entry.get("user_id") or username
        encrypted = entry.get("encrypted_session")
        if not username or not encrypted:
            continue
        cookie = _decrypt_session(encrypted)
        if not cookie:
            print(f"  [{idx}/{len(sessions)}] {username}: skip (no usable cookie)")
            continue

        print(f"  [{idx}/{len(sessions)}] {username}")
        try:
            slugs = scraper.pull_watchlist_slugs(cookie, username=username, max_pages=args.max_pages)
            added = 0
            for slug in slugs:
                try:
                    if not args.dry_run:
                        store.add_watchlist(user_id, slug)
                    added += 1
                except Exception as exc:
                    print(f"      wl {slug}: {exc}")
            total_wl += added
            print(f"      watchlist: scraped={len(slugs)} added={added}")
        except Exception as exc:
            print(f"      watchlist failed: {exc}")

        try:
            slugs = scraper.pull_diary_slugs(cookie, username=username, max_pages=args.max_pages)
            added = 0
            for slug in slugs:
                try:
                    if not args.dry_run:
                        store.add_diary(user_id, slug)
                    added += 1
                except Exception as exc:
                    print(f"      diary {slug}: {exc}")
            total_diary += added
            print(f"      diary: scraped={len(slugs)} added={added}")
        except Exception as exc:
            print(f"      diary failed: {exc}")

        time.sleep(0.5)

    return {"users": len(sessions), "watchlist": total_wl, "diary": total_diary}


def backfill_movies(scraper, store, args) -> dict:
    """Fetch metadata for movies that only have the placeholder row."""
    try:
        slugs = store.get_placeholder_movie_slugs(limit=args.max_movies)
    except Exception as exc:
        print(f"[sync/movies] placeholder query failed: {exc}")
        return {"targeted": 0, "fetched": 0, "failed": 0}

    print(f"\n[sync/movies] {len(slugs)} placeholder movies to enrich")
    if not slugs:
        return {"targeted": 0, "fetched": 0, "failed": 0}

    written = failed = 0
    batch = 5
    for start in range(0, len(slugs), batch):
        chunk = slugs[start: start + batch]
        print(f"  [{start + 1}-{start + len(chunk)}/{len(slugs)}]...", end=" ", flush=True)
        try:
            movies = scraper.metadata_for_slugs(chunk)
        except Exception as exc:
            failed += len(chunk)
            print(f"FAILED: {exc}")
            time.sleep(1)
            continue
        batch_ok = 0
        for movie in movies:
            try:
                if not args.dry_run:
                    store.upsert_movie(movie.__dict__)
                batch_ok += 1
            except Exception as exc:
                failed += 1
                print(f"\n      upsert {movie.slug}: {exc}", end="")
        written += batch_ok
        print(f"saved {batch_ok}/{len(chunk)}")
        time.sleep(0.4)

    return {"targeted": len(slugs), "fetched": written, "failed": failed}


def backfill_lists(scraper, store, args) -> dict:
    """Re-scrape memberships for lists that are under 50% scraped."""
    try:
        lists = store.get_underscraped_lists(limit=args.max_lists)
    except Exception as exc:
        print(f"[sync/lists] underscraped query failed: {exc}")
        return {"targeted": 0, "scraped": 0, "failed": 0}

    print(f"\n[sync/lists] {len(lists)} under-scraped lists to refresh")
    scraped = failed = 0
    for idx, lst in enumerate(lists, 1):
        list_id = lst.get("list_id")
        title = (lst.get("title") or "")[:60]
        print(f"  [{idx}/{len(lists)}] {title} ({lst.get('scraped_film_count', 0)}/{lst.get('film_count', 0)})...", end=" ", flush=True)
        try:
            slugs = scraper.fetch_list_movie_slugs(list_id, list_url=lst.get("url"))
        except Exception as exc:
            failed += 1
            print(f"FAILED: {exc}")
            time.sleep(0.6)
            continue
        if slugs:
            if not args.dry_run:
                try:
                    store.replace_list_memberships(list_id, slugs)
                    store.update_list_scrape_count(list_id, len(slugs))
                except Exception as exc:
                    failed += 1
                    print(f"write failed: {exc}")
                    continue
            scraped += 1
            print(f"scraped {len(slugs)}")
        else:
            failed += 1
            print("no slugs returned")
        time.sleep(0.5)

    return {"targeted": len(lists), "scraped": scraped, "failed": failed}


def main() -> None:
    parser = argparse.ArgumentParser(description="Periodically sync all Swiperboxd users + backfill scrapes")
    parser.add_argument("--users-only", action="store_true", help="Skip movie + list backfill")
    parser.add_argument("--backfill-only", action="store_true", help="Skip user sync; only movies + lists")
    parser.add_argument("--max-users", type=int, default=25)
    parser.add_argument("--max-pages", type=int, default=10, help="Pages per user per list (watchlist/diary)")
    parser.add_argument("--max-movies", type=int, default=200)
    parser.add_argument("--max-lists", type=int, default=25)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from src.api.providers.letterboxd import HttpLetterboxdScraper
    from src.api.database import is_supabase_configured

    if not is_supabase_configured():
        print("[sync] ERROR: Supabase not configured — set SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY in .env")
        sys.exit(1)
    from src.api.store import SupabaseStore

    scraper = HttpLetterboxdScraper()
    store = SupabaseStore()
    print(f"[sync] dry_run={args.dry_run}")

    summary: dict = {}
    if not args.backfill_only:
        summary["users"] = sync_all_users(scraper, store, args)
    if not args.users_only:
        summary["movies"] = backfill_movies(scraper, store, args)
        summary["lists"] = backfill_lists(scraper, store, args)

    print("\n[sync] SUMMARY")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
