# bugfix.md — Bug & Vulnerability Register

> Source of truth for all identified defects. Updated in-place as fixes are applied.
> Status: `Open` | `Fixed` | `Deferred`
> Last audit: 2026-04-16 (Multi-Agent Reconciliation)

---

## CRITICAL — Runtime Failures

### BUG-C01 — App startup crash in production
- **Status:** Fixed
- **Root Cause:** `app.py:45` calls `scraper.discover_site_lists(page=1)` at module load with no guard. `HttpLetterboxdScraper.discover_site_lists()` raises `NotImplementedError`. With `SCRAPER_BACKEND=http` (the production default) the process terminates before any route is registered.
- **Impact:** Production app cannot start. Zero-downtime deploy is impossible until fixed.
- **Evidence:** `src/api/app.py:45–47`; `src/api/providers/letterboxd.py:484`

### BUG-C02 — `HttpLetterboxdScraper.discover_site_lists` not implemented
- **Status:** Fixed
- **Root Cause:** Method body is `raise NotImplementedError(...)`. Entire Phase 2 list catalog feature is blocked.
- **Impact:** `GET /lists/catalog` always errors with HTTP scraper. BUG-C01 is a direct consequence.
- **Evidence:** `src/api/providers/letterboxd.py:484`

### BUG-C03 — `HttpLetterboxdScraper.fetch_list_movie_slugs` not implemented
- **Status:** Fixed
- **Root Cause:** Method body is `raise NotImplementedError(...)`. No URL is passed from caller so HTTP scraper cannot know which page to scrape.
- **Impact:** `GET /lists/{id}` and `GET /lists/{id}/deck` both error with HTTP scraper.
- **Evidence:** `src/api/providers/letterboxd.py:487`

---

## HIGH — Data Integrity & Security

### BUG-H01 — `database.run_migrations()` executes LEGACY files
- **Status:** Fixed
- **Root Cause:** `migrations_dir.glob("*.sql")` returns all `.sql` files. `LEGACY_` prefix sorts after `0–9` in ASCII, so the 8 deprecated migrations run after the 6 canonical ones, causing duplicate-table and FK-type conflicts.
- **Impact:** `POST /db/migrate` corrupts schema in any environment where it is called.
- **Evidence:** `src/api/database.py:66`

### BUG-H02 — `run_migrations()` calls non-existent `exec_sql` RPC
- **Status:** Fixed
- **Root Cause:** `client.rpc('exec_sql', {'sql': sql})` requires a custom Supabase stored procedure that does not exist in any migration file. Every migration call silently fails with a `404` from the Supabase RPC layer.
- **Impact:** `/db/migrate` endpoint is entirely non-functional against a real Supabase instance.
- **Evidence:** `src/api/database.py:78`

### BUG-H03 — No SQL schema for list tables; list data lost on cold start
- **Status:** Fixed
- **Root Cause:** `list_summaries` and `list_memberships` are in-memory Python dicts on both `InMemoryStore` and `SupabaseStore`. No migration creates these tables. Vercel cold starts wipe the list catalog.
- **Impact:** Every cold start serves an empty list catalog until the startup `discover_site_lists` call repopulates it (which itself crashes — see BUG-C01).
- **Evidence:** `src/api/store.py` — `SupabaseStore` class; `db/migrations/` — no list tables

### BUG-H04 — Session token identity not bound to `user_id` in request
- **Status:** Fixed
- **Root Cause:** `verify_session` decrypts the `X-Session-Token` header and returns the raw Letterboxd session cookie, not the username. POST endpoint bodies accept an arbitrary `user_id` string. A caller with a valid token for user A can submit `user_id=userB` and mutate another user's data.
- **Impact:** Cross-user data mutation (watchlist, diary, exclusions) possible from any authenticated session.
- **Evidence:** `src/api/app.py` — `verify_session()`, `start_ingest()`, `submit_swipe_action()`

### BUG-H05 — `ingest_running` check-then-add is not atomic
- **Status:** Fixed
- **Root Cause:** The guard `if payload.user_id in store.ingest_running` and the subsequent `store.ingest_running.add()` are two separate operations not wrapped in the store's lock. Concurrent requests can both pass the guard.
- **Impact:** Double ingest workers launched for the same user; duplicate scrape load and progress corruption.
- **Evidence:** `src/api/app.py:269–278`

---

## MEDIUM — Correctness & Maintainability

### BUG-M01 — `smoke_test_app.py` is permanently broken
- **Status:** Fixed
- **Root Cause (import):** `from api.app import app` — module path should be `src.api.app`.
- **Root Cause (auth):** Calls `/auth/session` with `{"username": ..., "password": ...}` — current endpoint requires `{"username": ..., "session_cookie": ...}`.
- **Impact:** Smoke test cannot run; acts as dead CI coverage.
- **Evidence:** `scripts/smoke_test_app.py:7`, `:57`

### BUG-M02 — `test_store.py` Supabase skip condition checks wrong env var
- **Status:** Fixed
- **Root Cause:** `pytest.mark.skipif` checks `not os.getenv("SUPABASE_KEY")` but `database.py` checks `SUPABASE_ANON_KEY`. Skip condition never triggers via intended mechanism.
- **Impact:** Supabase integration tests attempt live DB connection in CI, fail with misleading error.
- **Evidence:** `tests/test_store.py:251–253`

### BUG-M03 — `fetch_list_movie_slugs` receives only `list_id`; HTTP scraper needs URL
- **Status:** Fixed
- **Root Cause:** The `Scraper` protocol defines `fetch_list_movie_slugs(self, list_id: str)`. The mock maps `list_id` to slugs directly. The HTTP scraper needs a URL to make a request, and `list_id` alone (e.g. `"official-best-picture"`) cannot be reliably converted back to a URL without the stored summary.
- **Impact:** Even after BUG-C03 is fixed, the HTTP scraper cannot determine which Letterboxd URL to fetch.
- **Evidence:** `src/api/providers/letterboxd.py:487`; `src/api/app.py:205`, `:223`

### BUG-M04 — Dead infrastructure modules create false security surface
- **Status:** Fixed
- **Root Cause:** `auth.py`, `auth_deps.py`, `rate_limiter.py`, `qstash_queue.py` are fully implemented but never imported by `app.py`. Future developers reading these files may assume JWT auth or Redis rate limiting is active.
- **Impact:** Maintenance confusion; incorrect security threat model.
- **Evidence:** `src/api/auth.py`, `src/api/auth_deps.py`, `src/api/rate_limiter.py`, `src/api/qstash_queue.py` — no import in `src/api/app.py`

### BUG-M05 — `InMemoryQueue` is a write-only blackhole
- **Status:** Fixed
- **Root Cause:** `app.py` calls `queue.enqueue(...)` but the `InMemoryQueue.messages` list is never consumed. Ingest work is done directly by `threading.Thread` below the enqueue call.
- **Impact:** Queue grows unboundedly; adds cognitive overhead with zero functional benefit.
- **Evidence:** `src/api/app.py:275–277`; `src/api/queue.py`

---

## PREVIOUSLY FIXED (Phase 1 Remediation)

### BUG-S02 — JWT Signature Verification Disabled → **Fixed**
- `auth.py:verify_token()` now uses proper HS256 verification with `self.supabase_jwt_secret`.

### BUG-S03 — No auth on mutating endpoints → **Partially Fixed**
- `verify_session` Fernet guard applied to `/ingest/start` and `/actions/swipe`. BUG-H04 (identity binding) remains open.

### BUG-S06 — `/db/migrate` unauthenticated → **Fixed**
- `APP_ENV == "production"` → 403 guard applied.

### BUG-L02 — Ingest progress fake → **Fixed**
- `_filter_first_pipeline()` emits real milestones via `progress_callback`.

### BUG-L03 — Suppression store not wired → **Fixed**
- `app.js` imports `createSuppressionStore`, calls `dismiss()` on swipe-left, filters deck via `isSuppressed()`.

### BUG-L10 — `SCRAPER_BACKEND` defaults to `"mock"` → **Fixed**
- Default changed to `"http"`.

### BUG-D02 — `package.json` start script wrong → **Fixed**
- Corrected to `uvicorn src.api.app:app --host 0.0.0.0 --port 8000`.

### BUG-D03 — Dual conflicting migration series → **Partially Fixed**
- LEGACY files renamed with `LEGACY_` prefix. BUG-H01 (glob still picks them up) remains open.

### BUG-S05 / BUG-L04 — `app_patch.py` crashes on import → **Fixed**
- File deleted.

### BUG-L07 — `auth.html` dead page → **Fixed**
- File deleted.
