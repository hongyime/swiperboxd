# tasks.md — Execution Task List (Cycle 2)

> Sequential atomic tasks derived from bugfix.md + design.md.
> Status: `[ ]` Pending | `[x]` Complete | `[~]` In Progress | `[!]` Blocked

---

## GROUP A — Critical Crash & Data Integrity (No-Behavior-Change Fixes)

### T-A01 — Guard startup list-discovery call
- **Status:** [x]
- **File:** `src/api/app.py:45–47`
- **Action:** Wrap `scraper.discover_site_lists(page=1)` call in `try/except NotImplementedError` with warning log.
- **Fixes:** BUG-C01
- **Acceptance:** App starts with `SCRAPER_BACKEND=http`. Warning logged. No crash.

### T-A02 — Filter LEGACY files from migration runner
- **Status:** [x]
- **File:** `src/api/database.py:66`
- **Action:** Change `glob("*.sql")` to filter files whose name starts with `LEGACY_`. Improve error message on `exec_sql` failure to reference the required custom RPC.
- **Fixes:** BUG-H01, BUG-H02 (partial)
- **Acceptance:** `sorted([f.name for f in ...])` returns only `001_movies.sql` through `007_lists.sql`.

### T-A03 — Fix `smoke_test_app.py` import and auth signature
- **Status:** [x]
- **File:** `scripts/smoke_test_app.py`
- **Action:** Fix import path (`api.app` → `src.api.app`). Replace `password` field with `session_cookie` in the `/auth/session` call. Add a clear comment that the smoke test requires a pre-obtained session cookie.
- **Fixes:** BUG-M01
- **Acceptance:** `python -c "from scripts.smoke_test_app import main"` runs without ImportError.

### T-A04 — Fix `test_store.py` Supabase skip condition
- **Status:** [x]
- **File:** `tests/test_store.py:251–253`
- **Action:** Replace `SUPABASE_KEY` with `SUPABASE_ANON_KEY` in both `skipif` decorator conditions.
- **Fixes:** BUG-M02
- **Acceptance:** With `SUPABASE_ANON_KEY` unset, both Supabase integration tests are skipped.

### T-A05 — Make `ingest_running` check-and-set atomic
- **Status:** [x]
- **File:** `src/api/app.py:269–278`
- **Action:** Acquire `store.lock` around the `ingest_running` membership check and `.add()` call.
- **Fixes:** BUG-H05
- **Acceptance:** Two simultaneous POST requests to `/ingest/start` for the same user: exactly one returns `"queued"`, the other returns `"already_running"`.

### T-A06 — Remove dead `queue.enqueue` call from `start_ingest`
- **Status:** [x]
- **File:** `src/api/app.py:275`
- **Action:** Delete the `queue.enqueue(...)` line. Retain `queue = InMemoryQueue()` instantiation (non-breaking, kept for future use).
- **Fixes:** BUG-M05
- **Acceptance:** `start_ingest` no longer references `queue`. All existing tests pass.

---

## GROUP B — HTTP Scraper Implementation

### T-B01 — Update `Scraper` protocol: add `list_url` to `fetch_list_movie_slugs`
- **Status:** [x]
- **File:** `src/api/providers/letterboxd.py` — `Scraper` protocol class
- **Action:** Add `list_url: str | None = None` keyword parameter to `fetch_list_movie_slugs` signature.
- **Fixes:** BUG-M03 (prerequisite)
- **Acceptance:** Protocol definition updated; no callers broken yet.

### T-B02 — Update `MockLetterboxdScraper.fetch_list_movie_slugs` signature
- **Status:** [x]
- **File:** `src/api/providers/letterboxd.py` — `MockLetterboxdScraper`
- **Action:** Add `list_url: str | None = None` parameter. Ignore it (mock uses `list_id` mapping).
- **Fixes:** BUG-M03 (prerequisite)
- **Acceptance:** Existing mock behavior unchanged; all current tests pass.

### T-B03 — Implement `HttpLetterboxdScraper.discover_site_lists`
- **Status:** [x]
- **File:** `src/api/providers/letterboxd.py` — `HttpLetterboxdScraper`
- **Action:** Replace `raise NotImplementedError` with real scraper. Fetch `{base_url}/lists/` with `?page=N`. Parse `section.list-set` elements: extract `list_id` from href path, title, owner, description, film count, like count. Stop on empty page. Apply `query` filter post-scrape. Return `list[LetterboxdListSummary]`.
- **Fixes:** BUG-C02
- **Acceptance:** With `SCRAPER_BACKEND=http`, `GET /lists/catalog` returns a non-empty list (or empty list with no exception). No `NotImplementedError` raised.

### T-B04 — Implement `HttpLetterboxdScraper.fetch_list_movie_slugs`
- **Status:** [x]
- **File:** `src/api/providers/letterboxd.py` — `HttpLetterboxdScraper`
- **Action:** Replace `raise NotImplementedError` with real scraper. Use `list_url` if provided; raise `ValueError("list_url required for HTTP scraper")` if not. Paginate `list_url?page=N`, extract `div.film-poster[data-film-slug]` (fallback: `a[href^="/film/"]`). Cap at 20 pages. Return `list[str]`.
- **Fixes:** BUG-C03, BUG-M03
- **Acceptance:** With a valid list URL, returns a list of slug strings. With `list_url=None`, raises `ValueError`. No `NotImplementedError`.

### T-B05 — Update `app.py` list endpoint callers to pass `list_url`
- **Status:** [x]
- **File:** `src/api/app.py` — `list_detail()` and `list_deck()`
- **Action:** After fetching `summary = store.get_list_summary(list_id)`, pass `list_url=summary.get("url")` to every `scraper.fetch_list_movie_slugs(...)` call.
- **Fixes:** BUG-C03, BUG-M03
- **Acceptance:** `GET /lists/official-best-picture` and `GET /lists/official-best-picture/deck` pass `list_url` through. Mock scraper ignores it; HTTP scraper uses it.

---

## GROUP C — List Persistence

### T-C01 — Create `007_lists.sql` migration
- **Status:** [x]
- **File:** `db/migrations/007_lists.sql` (new file)
- **Action:** Create `list_summaries` (PK: `list_id TEXT`) and `list_memberships` (`list_id` + `movie_slug` + `position`) tables as specified in `design.md §6.1`.
- **Fixes:** BUG-H03 (schema)
- **Acceptance:** SQL file is syntactically valid. Contains `CREATE TABLE IF NOT EXISTS` for both tables. No `LEGACY_` prefix.

### T-C02 — Wire `SupabaseStore` list methods to Supabase tables
- **Status:** [x]
- **File:** `src/api/store.py` — `SupabaseStore` class
- **Action:** Replace in-memory dict operations in `upsert_list_summary`, `get_list_summary`, `get_lists`, `replace_list_memberships`, `get_list_memberships` with Supabase table calls. Remove `list_summaries` and `list_memberships` dict fields from `SupabaseStore.__init__`. `InMemoryStore` is unchanged.
- **Fixes:** BUG-H03 (runtime)
- **Acceptance:** `SupabaseStore` no longer has in-memory list state. Methods call `self.client.table(...)`. `InMemoryStore` tests pass unchanged.

---

## GROUP D — Session Identity Binding

### T-D01 — Embed username in encrypted session token
- **Status:** [x]
- **File:** `src/api/app.py` — `create_auth_session()`
- **Action:** Change `encrypt_session_cookie(payload.session_cookie, master_key)` to encrypt `json.dumps({"u": payload.username, "c": payload.session_cookie})`.
- **Fixes:** BUG-H04 (token side)
- **Acceptance:** `/auth/session` returns a token that when decrypted contains JSON with `"u"` and `"c"` keys.

### T-D02 — Update `verify_session` to extract and return username
- **Status:** [x]
- **File:** `src/api/app.py` — `verify_session()`
- **Action:** After decryption, attempt `json.loads(raw)` to extract `data["u"]` as the verified username. On `JSONDecodeError` (old-format token), return `""`. Return type changes from `str` (raw cookie) to `str` (username).
- **Fixes:** BUG-H04 (verification side)
- **Acceptance:** New-format token returns correct username. Old-format token returns `""` without raising.

### T-D03 — Assert `user_id` matches verified identity on guarded endpoints
- **Status:** [x]
- **File:** `src/api/app.py` — `start_ingest()`, `submit_swipe_action()`
- **Action:** After `verified_user: str = Depends(verify_session)`, add: `if verified_user and payload.user_id != verified_user: raise HTTPException(403, ...)`. The `if verified_user` guard maintains backward compatibility with old tokens.
- **Fixes:** BUG-H04
- **Acceptance:** New token + mismatched `user_id` → 403. Old token (empty username) + any `user_id` → passes through. Correct match → passes through.

---

## GROUP E — Dead Module Tombstoning

### T-E01 — Add tombstone headers to unused infrastructure modules
- **Status:** [x]
- **Files:** `src/api/auth.py`, `src/api/auth_deps.py`, `src/api/rate_limiter.py`, `src/api/qstash_queue.py`
- **Action:** Insert a 3-line comment block at the top of each file (below the module docstring if present) marking status as "IMPLEMENTED BUT NOT WIRED".
- **Fixes:** BUG-M04
- **Acceptance:** Each file has the tombstone comment. No functional code changed.

---

## GROUP F — Validation

### T-F01 — Run Python test suite
- **Status:** [x]
- **Action:** `pytest -q`. All tests must pass. Zero failures. Expected: 14+ passing (existing) plus any new tests added.
- **Acceptance:** Exit code 0.

### T-F02 — Run JavaScript test suite
- **Status:** [x]
- **Action:** `npm run test:web`. All tests must pass.
- **Acceptance:** Exit code 0.

---

## CHECKPOINT LOG

| Checkpoint | Tasks Complete | Notes |
|---|---|---|
| Phase 1 complete | 0 / 17 | Artifacts generated. Awaiting execution approval. |
| Cycle 2 complete | 17 / 17 | All tasks executed. 35 Python + 4 JS tests passing. |
