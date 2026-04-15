# tasks.md — Execution Task List

> Sequential atomic tasks. Execute in order. Do not skip ahead.
>
> Status: `[ ]` Pending | `[x]` Complete | `[~]` In Progress | `[!]` Blocked

---

## PHASE A — Dependency & Build Fixes

### T-A01 — Add `python-dotenv` to dependencies

- **Status:** [x]
- **Files:** `requirements.txt`, `pyproject.toml`
- **Action:** Add `python-dotenv>=1.0.0` to both files.
- **Acceptance Criteria:** `pip install -r requirements.txt` completes without error. `from dotenv import load_dotenv` resolves.

### T-A02 — Fix `package.json` start script

- **Status:** [x]
- **Files:** `package.json`
- **Action:** Changed `"start"` from `"uvicorn api.app:app ..."` to `"uvicorn src.api.app:app --host 0.0.0.0 --port 8000"`.
- **Acceptance Criteria:** `npm start` launches the FastAPI server without module errors.

---

## PHASE B — Dead Code Removal

### T-B01 — Delete `app_patch.py`

- **Status:** [x]
- **Files:** `src/api/app_patch.py` (deleted)
- **Action:** Deleted file. Verified no other file imports from it.
- **Acceptance Criteria:** No import errors. No dangling references.

### T-B02 — Delete `auth.html`

- **Status:** [x]
- **Files:** `src/web/auth.html` (deleted)
- **Action:** Deleted file. Confirmed no links to it in `index.html` or `app.js`.
- **Acceptance Criteria:** No broken links.

---

## PHASE C — Security Fixes

### T-C01 — Fix JWT signature verification

- **Status:** [x]
- **Files:** `src/api/auth.py`
- **Action:** Replaced `jwt.decode(..., options={"verify_signature": False})` with proper HS256 verification using `self.supabase_jwt_secret`.
- **Acceptance Criteria:** Tampered tokens raise 401. Valid tokens pass.

### T-C02 — Add session-token guard to mutating endpoints

- **Status:** [x]
- **Files:** `src/api/app.py`, `src/web/app.js`
- **Action:** Added `verify_session` FastAPI dependency that decrypts `X-Session-Token` header using `MASTER_ENCRYPTION_KEY`. Applied to `/ingest/start` and `/actions/swipe`. Frontend `api()` function now sends this header automatically.
- **Acceptance Criteria:** Missing/invalid token returns 401. Valid token passes. Two new tests confirm behaviour.

### T-C03 — Gate `/db/migrate` in production

- **Status:** [x]
- **Files:** `src/api/app.py`
- **Action:** Added `APP_ENV == "production"` → 403 guard at top of `migrate_database()`.
- **Acceptance Criteria:** Returns 403 when `APP_ENV=production`. Works normally in development.

### T-C04 — Remove hardcoded project ID from script

- **Status:** [x]
- **Files:** `scripts/print_migrations.py`
- **Action:** Replaced hardcoded Supabase project URL with `os.getenv("SUPABASE_URL")` extraction.
- **Acceptance Criteria:** No literal project ID in source files.

---

## PHASE D — Logic Fixes

### T-D01 — Wire suppression store in frontend

- **Status:** [x]
- **Files:** `src/web/app.js`
- **Action:** Imported `createSuppressionStore` from `state.js`. Instantiated at module level. `dismiss()` called on swipe-left. Deck filtered through `isSuppressed()` after fetch.
- **Acceptance Criteria:** Dismissed films do not reappear within 24 hours. JS tests pass.

### T-D02 — Fix `SCRAPER_BACKEND` default

- **Status:** [x]
- **Files:** `src/api/app.py`
- **Action:** Changed default from `"mock"` to `"http"`. Added warning log when mock is used outside development.
- **Acceptance Criteria:** Unset `SCRAPER_BACKEND` in production uses real HTTP scraper.

### T-D03 — Implement real ingest progress reporting

- **Status:** [x]
- **Files:** `src/api/app.py`
- **Action:** Added `progress_callback` parameter to `_filter_first_pipeline()`. Emits real milestones: slug fetch = 20%, filter = 40%, metadata batches = 40–95% proportionally, upsert complete = 100%. Removed hardcoded `[5, 20, 35, 50, 70]` checkpoints.
- **Acceptance Criteria:** Progress values reflect actual pipeline stages. Error immediately emits -1.

### T-D04 — Implement popularity scraping

- **Status:** [x]
- **Files:** `src/api/providers/letterboxd.py`
- **Action:** Added `_parse_member_count()` helper (handles M/K/numeric suffixes). Replaced `popularity=0` with multi-selector scrape of Letterboxd member count stats with graceful fallback.
- **Acceptance Criteria:** `popularity` field is non-zero for films with known view counts. Mock scraper unchanged.

### T-D05 — Consolidate migration files

- **Status:** [x]
- **Files:** `db/migrations/` (8 legacy files renamed), `db/migrations/README.md`, `scripts/run_migrations.py`, `scripts/print_migrations.py`
- **Action:** Renamed all 8 conflicting legacy files with `LEGACY_` prefix. Updated both scripts to skip `LEGACY_*` files. Rewrote README with canonical 6-file execution order.
- **Acceptance Criteria:** Scripts run only the 6 canonical migrations. No table conflicts.

---

## PHASE E — Validation

### T-E01 — Run full test suite

- **Status:** [x]
- **Result:** 30 Python tests pass, 5 skipped (Redis — correct). 2 JS tests pass. 0 failures.
- **Note:** Also fixed pre-existing `pytest.config` deprecation in `test_rate_limiter.py` and corrected wrong import paths in `test_api.py` and `test_letterboxd_provider.py`.

### T-E02 — Verify no remaining references to deleted files

- **Status:** [x]
- **Result:** `grep -rn "app_patch\|auth\.html" src/ scripts/ tests/` — no matches.

### T-E03 — Verify no hardcoded credentials in source

- **Status:** [x]
- **Result:** `grep -rn "ppluujxuevublgdgmzcq\|bryanseah234\|8ry@nL3tt3rb0xd" src/ scripts/ db/` — no matches.

---

## CHECKPOINT LOG

| Checkpoint | Tasks Completed | Notes |
| ---------- | --------------- | ----- |
| Initial | 0 / 18 | Artifacts generated. |
| Post-Phase-A | 2 / 18 | Dependencies fixed. |
| Post-Phase-B | 4 / 18 | Dead code removed. |
| Post-Phase-C | 8 / 18 | Security guards applied. |
| Post-Phase-D | 13 / 18 | Logic fixes applied. |
| **Final** | **18 / 18** | All phases complete. 30 Python + 2 JS tests passing. |
