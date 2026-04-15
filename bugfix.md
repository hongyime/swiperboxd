# bugfix.md — Bug & Vulnerability Register

> Source of truth for all identified defects. Updated in-place as fixes are applied.
> Status: `Open` | `Fixed` | `Deferred`

---

## SECURITY

### BUG-S01 — Live Credentials in `.env`
- **Status:** Open
- **Root Cause:** `.env` contains plaintext production secrets (Fernet master key, Supabase service role key, Letterboxd password, Upstash/QStash tokens). The file exists in the working tree and `.gitignore` may not have prevented historical commits.
- **Impact:** Full database compromise, session hijack, infrastructure takeover. CRITICAL.
- **Evidence:** `e:/01 REPOSITORIES/swiperboxd/.env` — all fields populated with live values.

### BUG-S02 — JWT Signature Verification Disabled
- **Status:** Open
- **Root Cause:** `auth.py:verify_token()` calls `jwt.decode()` with `options={"verify_signature": False}`. Only expiry and issuer are checked manually.
- **Impact:** Any token with a valid expiry and issuer field passes verification, regardless of signature. Auth bypass.
- **Evidence:** `src/api/auth.py` — `verify_token()` method.

### BUG-S03 — No Authentication on Any API Endpoint
- **Status:** Open
- **Root Cause:** Zero endpoints in `app.py` use `Depends(get_authenticated_user)`. `user_id` is passed as an unvalidated query param or request body field.
- **Impact:** Any caller can read or mutate any user's data by supplying an arbitrary `user_id`.
- **Evidence:** `src/api/app.py` — all endpoint signatures.

### BUG-S04 — RLS Policies Are Cosmetic
- **Status:** Open
- **Root Cause:** `008_rls_user_based.sql` defines policies using `USING (user_id IS NOT NULL)`. This passes for every non-null row — it does not bind rows to the authenticated user.
- **Impact:** Database-level access control is illusory. Any authenticated Supabase client can read/write all rows.
- **Evidence:** `db/migrations/008_rls_user_based.sql`.

### BUG-S05 — `app_patch.py` Silently Skips Webhook Signature Verification
- **Status:** Open
- **Root Cause:** `ingest_webhook()` wraps `QStashQueue()` init in a `try/except` with bare `pass`. If env vars are missing, the queue is `None` and signature verification is skipped entirely.
- **Impact:** Unauthenticated callers can trigger ingest jobs via webhook endpoint.
- **Evidence:** `src/api/app_patch.py` — `ingest_webhook()`.

### BUG-S06 — `/db/migrate` Endpoint is Unauthenticated and Production-Exposed
- **Status:** Open
- **Root Cause:** The `/db/migrate` POST endpoint runs SQL migration files with no auth guard, no environment check.
- **Impact:** Any caller can re-run or roll forward migrations in production.
- **Evidence:** `src/api/app.py` — `migrate_database()`.

### BUG-S07 — Production Supabase Project ID Hardcoded in Script
- **Status:** Open
- **Root Cause:** `scripts/print_migrations.py` line 38 contains a hardcoded Supabase project URL string.
- **Impact:** Infrastructure enumeration risk if script is published or shared.
- **Evidence:** `scripts/print_migrations.py:38`.

---

## LOGIC

### BUG-L01 — `hidden-gems` Profile Filter Is Broken
- **Status:** Open
- **Root Cause:** `HttpLetterboxdScraper.metadata_for_slugs()` hardcodes `popularity=0` for every movie. The `hidden-gems` filter `popularity <= 50` always passes, making it identical in behaviour to a rating-only filter.
- **Impact:** Profile differentiation is non-functional. Users see the same results regardless of profile selection.
- **Evidence:** `src/api/providers/letterboxd.py:296` — `popularity=0`.

### BUG-L02 — Ingest Progress Reporting Is Fake
- **Status:** Open
- **Root Cause:** `_run_ingest_worker()` emits hardcoded progress checkpoints (`5, 20, 35, 50, 70`) before the actual pipeline runs. Progress resets to `-1` on failure after showing 70%.
- **Impact:** UI progress bar is decorative. User sees 70% completion even when the backend has done nothing yet.
- **Evidence:** `src/api/app.py` — `_run_ingest_worker()`.

### BUG-L03 — 24-Hour Session Suppression Never Applied
- **Status:** Open
- **Root Cause:** `state.js` exports `createSuppressionStore` which implements the 24-hour dismissed-film suppression. `app.js` never imports or calls this module. Dismissed films can reappear in the same session deck.
- **Impact:** FR 4.2.3 is entirely dead. Swipe-left has no local durability.
- **Evidence:** `src/web/state.js`, `src/web/app.js` — no import of `state.js`.

### BUG-L04 — `app_patch.py` Crashes on Import
- **Status:** Open
- **Root Cause:** `app_patch.py` contains `from src.api import _execute_filter_pipeline`. This symbol does not exist in `src/api/__init__.py` or `app.py`.
- **Impact:** Any attempt to load `app_patch.py` raises `ImportError`, making it permanently dead code that cannot be recovered without a fix.
- **Evidence:** `src/api/app_patch.py` — top-level import.

### BUG-L05 — Daemon Thread Ingest Incompatible with Serverless Runtime
- **Status:** Open
- **Root Cause:** `_run_ingest_worker()` runs on a `threading.Thread(daemon=True)`. Vercel serverless functions are stateless; the process is killed after each request, orphaning the thread mid-scrape.
- **Impact:** Ingest jobs silently drop on Vercel. Only works in local `uvicorn` mode.
- **Evidence:** `src/api/app.py` — `start_ingest()`.

### BUG-L06 — Ingest Source and Depth Hardcoded in Frontend
- **Status:** Open
- **Root Cause:** `app.js:loadDeck()` always calls `POST /ingest/start` with `{source: 'trending', depth_pages: 2}`. Profile selection does not influence ingest source.
- **Impact:** All profiles draw from the same trending source pool. "Hidden Gems" / "Gold Standard" differ only in post-fetch filter, not source diversity.
- **Evidence:** `src/web/app.js:232-233`.

### BUG-L07 — `auth.html` References Non-Existent Endpoints
- **Status:** Open
- **Root Cause:** `auth.html` POSTs to `/auth/login` and `/auth/register`. Neither endpoint is registered in `app.py`.
- **Impact:** The auth page returns 404 for all interactions. It is a dead UI surface.
- **Evidence:** `src/web/auth.html`, `src/api/app.py`.

### BUG-L08 — `SupabaseStore` `ingest_state` / `rate_limit_state` Not Persisted
- **Status:** Open
- **Root Cause:** Tables `ingest_state` and `rate_limit_state` are defined in migrations but `SupabaseStore` keeps both in in-memory Python dicts. On cold start, all progress and rate-limit state is lost.
- **Impact:** Re-opened Vercel functions lose ingest progress. Rate limits do not persist across requests.
- **Evidence:** `src/api/store.py` — `SupabaseStore` class; `db/migrations/006_ingest_state.sql`, `007_rate_limit_state.sql`.

### BUG-L09 — `genre_preferences` Column Name Mismatch
- **Status:** Open
- **Root Cause:** `SupabaseStore` queries `genre_preferences` for column `score`. Migration `005_genre_preferences.sql` (alternate series) defines column `weight`. If the wrong migration ran, all genre reads silently return empty results.
- **Impact:** Weighted shuffle degrades to random shuffle without error.
- **Evidence:** `src/api/store.py` — `get_genre_weights()`; `db/migrations/005_genre_preferences.sql` vs `006_genre_preferences.sql`.

### BUG-L10 — `SCRAPER_BACKEND` Defaults to `"mock"` in Production
- **Status:** Open
- **Root Cause:** `os.getenv("SCRAPER_BACKEND", "mock")` — if the env var is absent from Vercel config, the production deployment silently serves mock film data.
- **Impact:** Silent data failure. No error raised; users see hardcoded test films.
- **Evidence:** `src/api/app.py:24`.

---

## DEPENDENCY / BUILD

### BUG-D01 — `python-dotenv` Missing from `requirements.txt`
- **Status:** Open
- **Root Cause:** `src/api/auth.py` calls `from dotenv import load_dotenv` at module load. `python-dotenv` is not listed in `requirements.txt` or `pyproject.toml`.
- **Impact:** `ImportError` on cold start in any environment that installs from `requirements.txt`.
- **Evidence:** `src/api/auth.py:1`; `requirements.txt`.

### BUG-D02 — `package.json` Start Script Has Wrong Module Path
- **Status:** Open
- **Root Cause:** `"start": "uvicorn api.app:app"` — the `app` object lives at `src.api.app:app`, not `api.app:app`. `api/index.py` re-exports it but does not expose it as `app` directly.
- **Impact:** `npm start` fails with `AttributeError` or `ModuleNotFoundError`.
- **Evidence:** `package.json`; `api/index.py`; `src/api/app.py`.

### BUG-D03 — Dual Conflicting Migration Series
- **Status:** Open
- **Root Cause:** `db/migrations/` contains two parallel, incompatible schema series. Both define `movies`, `genre_preferences`, and related tables with different column names and foreign key types. No documented canonical execution order.
- **Impact:** Running all migrations in lexicographic order causes conflicts (duplicate table definitions, FK type mismatches).
- **Evidence:** `db/migrations/001_initial_schema.sql` vs `001_movies.sql`; `005_genre_preferences.sql` vs `006_genre_preferences.sql`.
