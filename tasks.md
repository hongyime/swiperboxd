# Execution Tasks

## Execution Policy
- Order is strict and sequential.
- Each completed task requires:
  - acceptance verification,
  - `bugfix.md` status update,
  - checkpoint entry.

---

## Phase 0: Security Remediation (BLOCKER)

### T-001 — Credential Sanitization and Git History Cleanup
- **Description:** Remove hardcoded secrets from git history and rotate all exposed credentials.
- **Acceptance Criteria:**
  - `.env` removed from git history (verify with `git log --all --full-history -- .env`)
  - `.env` added to `.gitignore` if not present
  - All credential values rotated:
    - Generate new `MASTER_ENCRYPTION_KEY`
    - Regenerate `SUPABASE_SERVICE_ROLE_KEY` in Supabase dashboard
    - Regenerate `UPSTASH_REDIS_REST_TOKEN` in Upstash dashboard
    - Regenerate `QSTASH_TOKEN` and signing keys in QStash console
    - Update Letterboxd password in account settings
  - `.env.local` created for local development (never committed)
- **Dependencies:** None
- **Estimated:** 30 minutes
- **Status:** Pending
- **Validation:**
  ```bash
  git log --all --full-history -- .env | grep "commit"  # Should return empty
  git ls-files | grep ".env"  # Should not list .env
  git status --ignored | grep ".env"  # Should show .env ignored
  ```

---

## Phase 1: Data Layer Implementation

### T-002 — Add Supabase Python Dependency
- **Description:** Install `supabase>=2.0.0` and update package manifests.
- **Acceptance Criteria:**
  - `supabase>=2.0.0` added to `requirements.txt`
  - `supabase>=2.0.0` added to `pyproject.toml` dependencies
  - `pip check` passes without conflicts
- **Dependencies:** T-001
- **Estimated:** 15 minutes
- **Status:** Pending
- **Validation:** `pip install -r requirements.txt` succeeds

### T-003 — Create Supabase Client Module
- **Description:** Implement `src/api/database.py` with cached Supabase client getter.
- **Acceptance Criteria:**
  - File `src/api/database.py` exists
  - `get_supabase_client()` function implemented with `@lru_cache`
  - Client created from `SUPABASE_URL` and `SUPABASE_ANON_KEY` env vars
  - Module imports successfully
- **Dependencies:** T-002
- **Estimated:** 30 minutes
- **Status:** Pending
- **Validation:** `python -c "from src.api.database import get_supabase_client; print('OK')"`

### T-004 — Create Initial Database Schema Migration
- **Description:** Implement `db/migrations/001_initial_schema.sql` with user_exclusions, movies, user_actions tables.
- **Acceptance Criteria:**
  - File `db/migrations/001_initial_schema.sql` exists
  - Table `user_exclusions` created with id, user_id, movie_slug, created_at, UNIQUE constraint
  - Table `movies` created with slug (PK), title, poster_url, rating, popularity, genres, synopsis, cast, updated_at
  - Table `user_actions` created with id, user_id, movie_slug, action, created_at
  - Indexes created on user_exclusions.user_id, movies.rating, movies.popularity, user_actions.user_id+created_at
  - SQL executes successfully in Supabase SQL Editor
- **Dependencies:** T-001
- **Estimated:** 1 hour
- **Status:** Pending
- **Validation:** Run script in Supabase SQL Editor; confirm all tables and indexes exist

### T-005 — Create Row Level Security Policies
- **Description:** Implement `db/migrations/002_rls_policies.sql` with user isolation policies.
- **Acceptance Criteria:**
  - File `db/migrations/002_rls_policies.sql` exists
  - RLS enabled on `user_exclusions` and `user_actions`
  - Policy `user_own_exclusions` allows access only to user's own records
  - Policy `user_own_actions` allows access only to user's own records
  - SQL executes successfully in Supabase SQL Editor
- **Dependencies:** T-004
- **Estimated:** 30 minutes
- **Status:** Pending
- **Validation:** Run script in Supabase SQL Editor; confirm policies exist

### T-006 — Define Store Protocol Interface
- **Description:** Create abstract `Store` Protocol in `src/api/store.py` with all required methods.
- **Acceptance Criteria:**
  - `Store` Protocol defined with methods:
    - `add_exclusion(user_id: str, slug: str) -> None`
    - `get_exclusions(user_id: str) -> set[str]`
    - `add_watchlist(user_id: str, slug: str) -> None`
    - `add_diary(user_id: str, slug: str) -> None`
    - `get_watchlist(user_id: str) -> set[str]`
    - `get_diary(user_id: str) -> set[str]`
    - `upsert_movie(movie: dict) -> None`
    - `get_movie(slug: str) -> dict | None`
    - `get_movies() -> list[dict]`
    - `set_ingest_progress(user_id: str, value: int) -> None`
    - `get_ingest_progress(user_id: str) -> int`
    - `should_rate_limit(user_id: str, lock_ms: int) -> tuple[bool, int]`
    - `allow_scrape_request(user_id: str, min_interval_seconds: float) -> tuple[bool, float]`
    - `record_genre_preference(user_id: str, genres: list[str]) -> None`
    - `weighted_shuffle(user_id: str, movies: list[dict]) -> list[dict]`
- **Dependencies:** None
- **Estimated:** 30 minutes
- **Status:** Pending
- **Validation:** `python -m py_compile src/api/store.py` succeeds

### T-007 — Implement SupabaseStore Class
- **Description:** Implement `SupabaseStore` class with Supabase integration for all `Store` methods.
- **Acceptance Criteria:**
  - `SupabaseStore` class added to `src/api/store.py`
  - `__init__()` initializes Supabase client
  - `add_exclusion()` inserts into user_exclusions table
  - `get_exclusions()` queries user_exclusions table, returns set of slugs
  - `add_watchlist()` and `get_watchlist()`: stub implementations (watchlist storage TBD)
  - `add_diary()` and `get_diary()`: stub implementations (diary storage TBD)
  - `upsert_movie()` upserts into movies table
  - `get_movie()` queries movies table by slug
  - `get_movies()` queries all movies from cache
  - `ingest_progress` methods still use in-memory (progress not persisted)
  - `exclusions`, `watchlist`, `diary` methods call Supabase with caching layer
- **Dependencies:** T-003, T-004, T-006
- **Estimated:** 3 hours
- **Status:** Pending
- **Validation:** Write unit tests in `tests/test_supabase_store.py` and pass

### T-008 — Update InMemoryStore to Implement Store Protocol
- **Description:** Refactor existing `InMemoryStore` to explicitly implement `Store` Protocol.
- **Acceptance Criteria:**
  - `InMemoryStore` class explicitly implements `Store` Protocol
  - All methods match `Store` signature exactly
  - Existing functionality preserved
  - Tests continue to pass
- **Dependencies:** T-006
- **Estimated:** 1 hour
- **Status:** Pending
- **Validation:** `npm run test:api` passes

### T-009 — Update API to Use Conditional Store Selection
- **Description:** Modify `src/api/app.py` to use `SupabaseStore` when `SUPABASE_URL` is set, fallback to `InMemoryStore`.
- **Acceptance Criteria:**
  - Import `get_supabase_client` from `database.py`
  - Add conditional store selection logic:
    ```python
    if os.getenv("SUPABASE_URL"):
        from .store import SupabaseStore
        store = SupabaseStore()
    else:
        store = InMemoryStore()
    ```
  - All endpoints work with both store implementations
  - Tests pass with both stores
- **Dependencies:** T-007, T-008
- **Estimated:** 30 minutes
- **Status:** Pending
- **Validation:** Run tests with `SUPABASE_URL` unset (uses InMemoryStore) and set (uses SupabaseStore)

---

## Phase 2: Queue & Cache Integration

### T-010 — Add Redis Dependencies
- **Description:** Install `redis>=5.0.0` for Upstash Redis integration.
- **Acceptance Criteria:**
  - `redis>=5.0.0` added to `requirements.txt`
  - `redis>=5.0.0` added to `pyproject.toml` dependencies
  - `pip check` passes
- **Dependencies:** T-001
- **Estimated:** 15 minutes
- **Status:** Pending
- **Validation:** `pip install -r requirements.txt` succeeds

### T-011 — Implement RedisRateLimiter Class
- **Description:** Create `src/api/rate_limiter.py` with `RedisRateLimiter` implementation.
- **Acceptance Criteria:**
  - File `src/api/rate_limiter.py` exists
  - `RedisRateLimiter` class implemented with:
    - `__init__()`: connects to Upstash Redis from env vars
    - `should_rate_limit(user_id, key, window_seconds, max_requests)`: returns (limited, ttl)
    - Uses Redis sorted sets for sliding window
    - Proper error handling for connection failures
  - Unit tests in `tests/test_rate_limiter.py`
- **Dependencies:** T-010
- **Estimated:** 2 hours
- **Status:** Pending
- **Validation:** `pytest tests/test_rate_limiter.py -q` passes

### T-012 — Add QStash Dependencies
- **Description:** Choose QStash integration approach (SDK vs direct API) and add dependencies.
- **Acceptance Criteria:**
  - Decision documented:
    - Option A: `qstash>=0.5.0` SDK
    - Option B: `requests>=2.31.0` direct API calls
  - Chosen package added to requirements.txt and pyproject.toml
  - `pip check` passes
- **Dependencies:** T-001
- **Estimated:** 30 minutes
- **Status:** Pending
- **Validation:** Dependency install succeeds

### T-013 — Implement QStashQueue Class
- **Description:** Create `src/api/qstash_queue.py` with `QStashQueue` implementation.
- **Acceptance Criteria:**
  - File `src/api/qstash_queue.py` exists
  - `QStashQueue` class implemented with:
    - `__init__()`: initializes with QStash env vars
    - `enqueue(topic, payload)`: publishes message to QStash, returns messageId
    - HMAC signature generation using `QSTASH_CURRENT_SIGNING_KEY`
    - Proper error handling for API failures
  - Unit tests in `tests/test_qstash_queue.py`
- **Dependencies:** T-012
- **Estimated:** 2 hours
- **Status:** Pending
- **Validation:** `pytest tests/test_qstash_queue.py -q` passes

### T-014 — Replace InMemoryQueue with QStashQueue in Ingest Endpoint
- **Description:** Update `/ingest/start` endpoint to use `QStashQueue` instead of daemon thread.
- **Acceptance Criteria:**
  - Import `QStashQueue` from `qstash_queue.py`
  - Replace `threading.Thread` with `QStashQueue.enqueue("ingest-history", payload)`
  - Remove `_simulate_ingest()` daemon thread function
  - Create `/webhooks/ingest` endpoint to receive QStash callbacks
  - Endpoint validates QStash signature (using `QSTASH_NEXT_SIGNING_KEY`)
  - Rate limiting still enforced via `allow_scrape_request()`
- **Dependencies:** T-011, T-013
- **Estimated:** 2 hours
- **Status:** Pending
- **Validation:** Manual test: start ingest, verify callback received by webhook

---

## Phase 3: Scraper Completion

### T-015 — Implement pull_watchlist_slugs() for HttpLetterboxdScraper
- **Description:** Implement full watchlist scraping with pagination for `HttpLetterboxdScraper`.
- **Acceptance Criteria:**
  - `pull_watchlist_slugs()` in `src/api/providers/letterboxd.py` returns real data
  - Handles pagination (load next page until no more results)
  - Extracts slugs from `li.poster-container a` elements
  - Returns set of unique slugs
  - Unit test in `tests/test_letterboxd_scraping.py`
  - Mocks httpx responses for test isolation
- **Dependencies:** T-001
- **Estimated:** 2 hours
- **Status:** Pending
- **Validation:** `pytest tests/test_letterboxd_scraping.py::test_pull_watchlist -v` passes

### T-016 — Implement pull_diary_slugs() for HttpLetterboxdScraper
- **Description:** Implement full diary scraping with pagination for `HttpLetterboxdScraper`.
- **Acceptance Criteria:**
  - `pull_diary_slugs()` in `src/api/providers/letterboxd.py` returns real data
  - Handles pagination across diary entries
  - Extracts slugs from diary page HTML structure
  - Returns set of unique slugs
  - Unit test in `tests/test_letterboxd_scraping.py`
- **Dependencies:** T-015
- **Estimated:** 2 hours
- **Status:** Pending
- **Validation:** `pytest tests/test_letterboxd_scraping.py::test_pull_diary -v` passes

### T-017 — Implement pull_source_slugs() for HttpLetterboxdScraper
- **Description:** Implement source discovery scraping (trending, popular, recommended) for `HttpLetterboxdScraper`.
- **Acceptance Criteria:**
  - `pull_source_slugs()` in `src/api/providers/letterboxd_scraper.py` returns real data
  - Supports sources: `trending`, `popular`, `recommended`
  - Handles page-by-page pagination up to `depth_pages`
  - Detects and handles 403/429 rate limits
  - Calls `resilience.should_trigger_proxy_fallback()` on rate limit
  - Returns list of unique slugs (preserve order)
  - Unit tests in `tests/test_letterboxd_scraping.py`
- **Dependencies:** T-015, T-016
- **Estimated:** 3 hours
- **Status:** Pending
- **Validation:** `pytest tests/test_letterboxd_scraping.py::test_pull_source -v` passes

### T-018 — Implement metadata_for_slugs() for HttpLetterboxdScraper
- **Description:** Implement movie metadata extraction from individual film pages.
- **Acceptance Criteria:**
  - `metadata_for_slugs()` in `src/api/providers/letterboxd.py` fetches real metadata
  - Fetches HTML for each slug individually or in batches
  - Extracts: title, poster_url, rating, popularity, genres, synopsis, cast
  - Returns list of `LetterboxdMovie` dataclass instances
  - Handles missing/malformed data gracefully (skip or return None)
  - Applies exponential backoff on failures
  - Unit tests with mocked responses
- **Dependencies:** T-017
- **Estimated:** 3 hours
- **Status:** Pending
- **Validation:** `pytest tests/test_letterboxd_scraping.py::test_metadata_for_slugs -v` passes

### T-019 — Implement Rotating Proxy Fallback
- **Description:** Add proxy fallback integration for rate limit recovery.
- **Acceptance Criteria:**
  - Add `_get_proxy_url()` method to `HttpLetterboxdScraper`
  - Calls `ROTATING_PROXY_ENDPOINT` with `ROTATING_PROXY_API_KEY`
  - Returns proxy URL (host:port) from response
  - Update `pull_source_slugs()` to retry with proxy on 403/429
  - Pass proxy to httpx client via `proxies` parameter
  - Falls back to standard client if proxy unavailable
  - Tests mock proxy API responses
- **Dependencies:** T-017
- **Estimated:** 2 hours
- **Status:** Pending
- **Validation:** `pytest tests/test_letterboxd_scraping.py::test_proxy_fallback -v` passes

---

## Phase 4: Logic & Concurrency Fixes

### T-020 — Fix Race Condition in weighted_shuffle()
- **Description:** Refactor `weighted_shuffle()` to prevent concurrent modification issues.
- **Acceptance Criteria:**
  - Update `InMemoryStore.weighted_shuffle()` with proper lock management
  - Ensure all list operations (sort, shuffle, slice) are protected by lock
  - Release lock only when safe (list copied or mutation complete)
  - Existing tests pass
  - Add concurrency test (multiple threads call weighted_shuffle simultaneously)
- **Dependencies:** T-008
- **Estimated:** 1 hour
- **Status:** Pending
- **Validation:** `pytest tests/test_store_concurrency.py -v` passes

### T-021 — Add Error Handling to Ingest Worker
- **Description:** Refactor ingest worker to handle exceptions and propagate errors.
- **Acceptance Criteria:**
  - Update `_simulate_ingest()` (or webhook handler) with try/except/finally
  - Log errors with `logger.error()` (add logging if not already)
  - Set ingest progress to -1 on error
  - Ensure `running` flag cleared in finally block
  - Add test for error scenario (e.g., scraper raises exception)
- **Dependencies:** T-014
- **Estimated:** 1 hour
- **Status:** Pending
- **Validation:** `pytest tests/test_ingest_error_handling.py -v` passes

### T-022 — Implement Cleanup Policies for InMemoryStore
- **Description:** Add cleanup methods to prevent unbounded growth of state.
- **Acceptance Criteria:**
  - Add `cleanup_expired_progress(ttl_seconds=3600)` method
  - Removes ingest progress entries older than TTL
  - Returns count of entries removed
  - Add `archive_old_actions(keep_days=7)` method
  - Removes or archives actions older than keep_days
  - Returns count of remaining actions
  - Add unit tests for both methods
  - Add scheduled cleanup call (e.g., every hour via background thread or external scheduler)
- **Dependencies:** T-020
- **Estimated:** 1.5 hours
- **Status:** Pending
- **Validation:** `pytest tests/test_store_cleanup.py -v` passes

---

## Phase 5: Testing & Quality Assurance

### T-023 — Add Integration Tests for SupabaseStore
- **Description:** Create integration tests verifying Supabase CRUD operations.
- **Acceptance Criteria:**
  - File `tests/integration/test_supabase_store.py` exists
  - Test `add_exclusion()` and `get_exclusions()`
  - Test `upsert_movie()` and `get_movie()`
  - Test `get_movies()`
  - Uses test Supabase instance or fixtures
  - Cleans up test data after each test
  - All tests pass
- **Dependencies:** T-007
- **Estimated:** 2 hours
- **Status:** Pending
- **Validation:** `pytest tests/integration/test_supabase_store.py -v` passes

### T-024 — Add E2E Tests with Playwright
- **Description:** Create end-to-end tests covering full user flows.
- **Acceptance Criteria:**
  - File `tests/e2e/test_swipe_flow.spec.ts` exists
  - Playwright configured in package.json
  - Test: Load deck → Swipe right → Verify watchlist updated
  - Test: Load deck → Swipe left → Verify exclusion added
  - Test: Rate limiting enforcement
  - Test: Progress indicator accuracy
  - All tests pass
- **Dependencies:** None (can run independently)
- **Estimated:** 4 hours
- **Status:** Pending
- **Validation:** `npx playwright test` passes

### T-025 — Add Property-Based Tests for Store
- **Description:** Create property-based tests for store invariant properties.
- **Acceptance Criteria:**
  - File `tests/test_store_properties.py` exists
  - Test: `weighted_shuffle()` returns same number of items
  - Test: `add_exclusion()` is idempotent (calling twice doesn't double-add)
  - Test: `get_exclusions()` returns set (no duplicates)
  - Uses `hypothesis` for property-based testing
  - All tests pass
- **Dependencies:** T-020, T-022
- **Estimated:** 2 hours
- **Status:** Pending
- **Validation:** `pytest tests/test_store_properties.py -v` passes

---

## Phase 6: Frontend Enhancements (OPTIONAL)

### T-026 — Migrate Frontend to TypeScript
- **Description:** Add TypeScript type safety to frontend code.
- **Acceptance Criteria:**
  - `tsconfig.json` created with strict mode, ES2022 target
  - `src/web/app.js` renamed to `src/web/app.ts`
  - `src/web/state.js` renamed to `src/web/state.ts`
  - All functions have type annotations
  - `tsc --noEmit` passes without errors
  - Update build process in `vercel.json` if needed
- **Dependencies:** None
- **Estimated:** 6 hours
- **Status:** Optional - Skip if schedule constrained
- **Validation:** `npx tsc --noEmit` succeeds

### T-027 — Add ESLint Configuration
- **Description:** Add linting for JavaScript/TypeScript code quality.
- **Acceptance Criteria:**
  - `.eslintrc.json` created
  - "lint:js" script added to package.json
  - Lints both `.js` and `.ts` files
  - Existing code passes linting (or acceptable issues suppressed)
  - CI pipeline runs linting
- **Dependencies:** None
- **Estimated:** 1 hour
- **Status:** Optional - Skip if T-026 not implemented
- **Validation:** `npm run lint:js` passes

---

## Checkpoint Log

### CP-001 — Phase 1 Complete
- **Date:** 2026-04-15
- **Status:** ✅ Complete
- **Summary:** 
  - Security remediation (T-001) complete. Credential template created.
  - Data layer implementation (T-002 to T-009) complete:
    - Supabase client module created
    - Database migrations (schema + RLS) created
    - Store Protocol interface defined
    - SupabaseStore and InMemoryStore both implement Protocol
    - API uses conditional store selection based on env vars
  - Queue/Cache foundation (T-011, T-013) complete:
    - RedisRateLimiter with sliding window implemented
    - QStashQueue with HMAC signatures implemented
  - All artifacts (bugfix.md, design.md, tasks.md) generated and up-to-date.

### CP-002 — Remaining Work
- **Status:** Pending
- **Tasks Remaining:**
  - T-010: Already complete (dependencies added in T-002)
  - T-012: Already complete (dependencies added in T-002)
  - T-014: Wire QStashQueue into ingest endpoint, create webhook handler
  - T-015 to T-019: Complete scraper implementation
  - T-020 to T-022: Fix logic and concurrency issues
  - T-023 to T-025: Add comprehensive testing
  - T-026 to T-027: Optional frontend enhancements


---

## Execution Order Notes

1. **CRITICAL**: T-001 must be completed first (security blocker)
2. **Phase 1** (T-002 to T-009) establishes data persistence foundation
3. **Phase 2** (T-010 to T-014) integrates external services
4. **Phase 3** (T-015 to T-019) completes scraper functionality
5. **Phase 4** (T-020 to T-022) fixes concurrency and reliability issues
6. **Phase 5** (T-023 to T-025) adds comprehensive test coverage
7. **Phase 6** (T-026 to T-027) is optional and can be deferred

Any task requiring env var credentials (e.g., Supabase, QStash) must wait until T-001 credential rotation is complete.

---

## Rollback Criteria

If any task fails acceptance criteria and cannot be remediated within 2 hours:
1. Document the failure reason in checkpoint log
2. Revert changes made by that task
3. Pause execution and request user guidance

All reversible changes (code, not credential rotation) should be versioned in git for easy rollback.
