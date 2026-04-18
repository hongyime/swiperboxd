"""
Local periodic sync: scrape Letterboxd from this machine and upload to Supabase
for ALL users that have a stored `letterboxd_session` blob, plus backfill
placeholder movies and under-scraped lists.

Why local? Letterboxd blocks Vercel's AWS IPs (403). This script runs the same
logic as /api/cron/sync-users and /api/cron/backfill-scrapes but from a home IP
so the scrape actually succeeds.

Idempotency
-----------
- Movies: only placeholder rows (no metadata) are ever refetched.
- Lists: only lists with scraped_film_count < 50% of film_count are refetched.
- Users: writes are upserts so duplicates are dropped at insert time, BUT the
  script does re-fetch each user's watchlist/diary HTML every run. Use
  --skip-recent-users <minutes> to skip users synced within a window.

Cookie expiry
-------------
Letterboxd rotates session cookies periodically. When the stored cookie 403s,
re-login with Playwright and push the fresh cookie back into Supabase:

    python scripts/periodic_sync.py --refresh-my-session bryanseah234

This opens Chromium, waits for you to log in, extracts the cookie, encrypts
it with MASTER_ENCRYPTION_KEY, and writes it to users.letterboxd_session.

Usage
-----
    python scripts/periodic_sync.py
    python scripts/periodic_sync.py --users-only
    python scripts/periodic_sync.py --backfill-only
    python scripts/periodic_sync.py --only-user bryanseah234
    python scripts/periodic_sync.py --max-watchlist-pages 50 --max-diary-pages 200
    python scripts/periodic_sync.py --refresh-my-session bryanseah234
    python scripts/periodic_sync.py --refresh-my-session bryanseah234 --manual-cookie
    python scripts/periodic_sync.py --dry-run

Cookie refresh modes
--------------------
Default: Playwright opens Chromium with a persistent profile at
`.playwright-letterboxd/`. If Cloudflare Turnstile blocks the automation
(error 600010), pass `--manual-cookie` to paste the cookie directly from
your real browser's DevTools.
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

from dotenv import load_dotenv, set_key
load_dotenv(repo_root / ".env")

ENV_FILE = repo_root / ".env"


# ── Session management ────────────────────────────────────────────────────────

def _decrypt_session(encrypted: str) -> str | None:
    from src.api.security import decrypt_session_cookie
    master_key = os.getenv("MASTER_ENCRYPTION_KEY")
    if not master_key:
        print("[sync] MASTER_ENCRYPTION_KEY not set — cannot decrypt user sessions")
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


def _encrypt_session_for_storage(username: str, raw_cookie: str) -> str | None:
    from src.api.security import encrypt_session_cookie
    master_key = os.getenv("MASTER_ENCRYPTION_KEY")
    if not master_key:
        print("[sync] MASTER_ENCRYPTION_KEY not set — cannot encrypt new session")
        return None
    payload = json.dumps({"u": username, "c": raw_cookie})
    return encrypt_session_cookie(payload, master_key)


def manual_paste_cookie(save_to_env: bool = True) -> str | None:
    """Prompt the operator to paste a cookie grabbed from a real browser.

    Letterboxd's sign-in page is now gated by Cloudflare Turnstile, which often
    flags Playwright's Chromium as automation (error 600010). The most reliable
    flow: log in with your real Chrome → open DevTools → Application ›
    Cookies › https://letterboxd.com → copy the value of
    `letterboxd.user.CURRENT` → paste here.
    """
    print(
        "\n[auth] Manual cookie paste:\n"
        "  1. Open https://letterboxd.com in your real browser, sign in normally.\n"
        "  2. DevTools → Application → Cookies → https://letterboxd.com\n"
        "  3. Copy the value of `letterboxd.user.CURRENT`.\n"
    )
    try:
        cookie_value = input("[auth] Paste cookie value (or blank to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        cookie_value = ""
    if not cookie_value:
        print("[auth] No cookie entered — aborting.")
        return None
    if cookie_value and save_to_env:
        try:
            set_key(str(ENV_FILE), "LETTERBOXD_SESSION_COOKIE", cookie_value)
            print(f"[auth] Saved cookie to {ENV_FILE}")
        except Exception as exc:
            print(f"[auth] Could not save cookie to .env: {exc}")
    return cookie_value


def browser_capture_cookie(save_to_env: bool = True, persistent: bool = True) -> str | None:
    """Open Chromium (persistent context by default) and wait for the user to
    sign in. Cookie captured automatically when Letterboxd sets
    `letterboxd.user.CURRENT`.

    Persistent mode stores profile data in `.playwright-letterboxd/` inside
    the repo root so Cloudflare Turnstile only has to be solved once. If
    Turnstile still blocks (error 600010), fall back to manual paste.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "\n[auth] Playwright not installed.\n"
            "  Run:  pip install playwright && playwright install chromium\n"
            "  …or use --manual-cookie to paste a cookie from your real browser.\n"
        )
        return None

    profile_dir = repo_root / ".playwright-letterboxd"
    print(f"\n[auth] Opening Chromium (profile: {profile_dir})")
    print(
        "[auth] Sign in in the browser. If Cloudflare Turnstile blocks you,\n"
        "       close the window and re-run with --manual-cookie instead.\n"
    )

    cookie_value: str | None = None
    try:
        with sync_playwright() as p:
            if persistent:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    headless=False,
                    slow_mo=50,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                browser = None
            else:
                browser = p.chromium.launch(
                    headless=False,
                    slow_mo=50,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                context = browser.new_context()

            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://letterboxd.com/sign-in/")

            for _ in range(300):  # 5 min
                for c in context.cookies():
                    if c["name"] == "letterboxd.user.CURRENT":
                        cookie_value = c["value"]
                        break
                if cookie_value:
                    print("[auth] Login detected — cookie extracted.")
                    break
                time.sleep(1)
            else:
                print("[auth] Timed out waiting for login (5 min).")

            if browser is not None:
                browser.close()
            else:
                context.close()
    except Exception as exc:
        print(f"[auth] Playwright flow failed: {exc}")
        return None

    if cookie_value and save_to_env:
        try:
            set_key(str(ENV_FILE), "LETTERBOXD_SESSION_COOKIE", cookie_value)
            print(f"[auth] Saved cookie to {ENV_FILE}")
        except Exception as exc:
            print(f"[auth] Could not save cookie to .env: {exc}")
    return cookie_value


def refresh_user_session(store, username: str, use_manual: bool = False) -> bool:
    """Capture a fresh cookie (browser or manual paste), encrypt it, store it."""
    if use_manual:
        raw_cookie = manual_paste_cookie(save_to_env=True)
    else:
        raw_cookie = browser_capture_cookie(save_to_env=True)
    if not raw_cookie:
        print("[sync] no cookie captured — aborting refresh")
        return False
    encrypted = _encrypt_session_for_storage(username, raw_cookie)
    if not encrypted:
        return False
    try:
        store.save_user_session(username, encrypted)
        print(f"[sync] stored refreshed session for {username}")
        return True
    except Exception as exc:
        print(f"[sync] save_user_session failed: {exc}")
        return False


# ── User sync ─────────────────────────────────────────────────────────────────

def _recent_sync_timestamps(store, minutes: int) -> set[str]:
    """Return usernames that already have rows written within the last N minutes.

    Uses watchlist.inserted_at as a proxy. If the column doesn't exist or the
    query fails, returns an empty set (fall-through — re-sync everyone).
    """
    if minutes <= 0:
        return set()
    try:
        import datetime as _dt
        cutoff = (_dt.datetime.utcnow() - _dt.timedelta(minutes=minutes)).isoformat()
        resp = (
            store.client.table("watchlist")
            .select("user_id")
            .gte("inserted_at", cutoff)
            .limit(10_000)
            .execute()
        )
        user_ids = {row["user_id"] for row in (resp.data or [])}
        if not user_ids:
            return set()
        users_resp = (
            store.client.table("users")
            .select("id, letterboxd_username")
            .in_("id", list(user_ids))
            .execute()
        )
        return {row["letterboxd_username"] for row in (users_resp.data or [])}
    except Exception as exc:
        print(f"[sync] recent-sync check skipped: {exc}")
        return set()


def sync_all_users(scraper, store, args) -> dict:
    """Iterate every user with a stored session and refresh watchlist + diary."""
    try:
        sessions = store.get_all_user_sessions()
    except Exception as exc:
        print(f"[sync/users] failed to load sessions: {exc}")
        return {"users": 0, "watchlist": 0, "diary": 0, "skipped": 0}

    if args.only_user:
        sessions = [s for s in sessions if s.get("username") == args.only_user]
    sessions = sessions[: args.max_users]

    recent = _recent_sync_timestamps(store, args.skip_recent_users)
    if recent:
        print(f"[sync/users] {len(recent)} user(s) synced within {args.skip_recent_users}m — skipping")

    print(
        f"\n[sync/users] processing {len(sessions)} user(s) "
        f"(watchlist_pages={args.max_watchlist_pages}, diary_pages={args.max_diary_pages})"
    )

    total_wl = total_diary = skipped = 0
    for idx, entry in enumerate(sessions, 1):
        username = entry.get("username")
        user_id = entry.get("user_id") or username
        encrypted = entry.get("encrypted_session")
        if not username or not encrypted:
            continue
        if username in recent:
            skipped += 1
            print(f"  [{idx}/{len(sessions)}] {username}: skip (recently synced)")
            continue
        cookie = _decrypt_session(encrypted)
        if not cookie:
            print(f"  [{idx}/{len(sessions)}] {username}: skip (no usable cookie)")
            continue

        print(f"  [{idx}/{len(sessions)}] {username}")
        try:
            slugs = scraper.pull_watchlist_slugs(cookie, username=username, max_pages=args.max_watchlist_pages)
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
            if "403" in str(exc) and args.auto_refresh_on_403 and username == args.refresh_my_session_on_fallback:
                print("      auto-refreshing session via browser…")
                if refresh_user_session(store, username):
                    print("      retry with fresh cookie — next run will pick it up")

        try:
            slugs = scraper.pull_diary_slugs(cookie, username=username, max_pages=args.max_diary_pages)
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

    return {"users": len(sessions), "watchlist": total_wl, "diary": total_diary, "skipped": skipped}


# ── Movie + list backfill ─────────────────────────────────────────────────────

def backfill_movies(scraper, store, args) -> dict:
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
        print(f"  [{start + 1}-{start + len(chunk)}/{len(slugs)}]…", end=" ", flush=True)
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
        print(
            f"  [{idx}/{len(lists)}] {title} "
            f"({lst.get('scraped_film_count', 0)}/{lst.get('film_count', 0)})…",
            end=" ",
            flush=True,
        )
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


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Periodically sync all Swiperboxd users + backfill scrapes")
    parser.add_argument("--users-only", action="store_true", help="Skip movie + list backfill")
    parser.add_argument("--backfill-only", action="store_true", help="Skip user sync; only movies + lists")
    parser.add_argument("--only-user", type=str, default=None, help="Restrict user sync to one username")
    parser.add_argument("--skip-recent-users", type=int, default=0,
                        help="Skip users whose watchlist was written within N minutes")
    parser.add_argument("--max-users", type=int, default=25)
    parser.add_argument("--max-watchlist-pages", type=int, default=50,
                        help="Cap watchlist pages per user (default: 50, ~1400 films)")
    parser.add_argument("--max-diary-pages", type=int, default=200,
                        help="Cap diary pages per user (default: 200, ~10k entries)")
    parser.add_argument("--max-movies", type=int, default=200)
    parser.add_argument("--max-lists", type=int, default=25)
    parser.add_argument("--refresh-my-session", type=str, default=None, metavar="USERNAME",
                        help="Capture a fresh cookie (Playwright by default), encrypt it, "
                             "and save to users.letterboxd_session for USERNAME")
    parser.add_argument("--manual-cookie", action="store_true",
                        help="With --refresh-my-session: paste cookie from a real browser "
                             "instead of launching Playwright (bypasses Cloudflare Turnstile "
                             "error 600010)")
    parser.add_argument("--auto-refresh-on-403", action="store_true",
                        help="(with --refresh-my-session) retry once if the stored cookie 403s")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    # carry the refresh target through to the sync loop so auto-refresh-on-403 knows who to re-login
    args.refresh_my_session_on_fallback = args.refresh_my_session

    from src.api.providers.letterboxd import HttpLetterboxdScraper
    from src.api.database import is_supabase_configured

    if not is_supabase_configured():
        print("[sync] ERROR: Supabase not configured — set SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY in .env")
        sys.exit(1)
    from src.api.store import SupabaseStore

    scraper = HttpLetterboxdScraper()
    store = SupabaseStore()
    print(f"[sync] dry_run={args.dry_run}")

    if args.refresh_my_session:
        ok = refresh_user_session(store, args.refresh_my_session, use_manual=args.manual_cookie)
        if not ok:
            sys.exit(1)
        if args.users_only or args.backfill_only or args.only_user:
            # If the operator only wanted to refresh, don't continue to full sync
            if args.users_only and not args.only_user:
                pass
            elif args.backfill_only:
                pass
            else:
                return
        else:
            # Default: after refresh, run a full sync for that user
            args.only_user = args.refresh_my_session

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
