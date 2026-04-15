# design.md — Implementation Design

> Architecture decisions and interface contracts for the remediation cycle.
> Read this before executing any task in `tasks.md`.

---

## 1. SCOPE

This document covers remediations for all `Open` items in `bugfix.md`. It is organized by subsystem. Items marked **DEFERRED** are acknowledged but not scheduled for this cycle.

---

## 2. SECURITY SUBSYSTEM

### 2.1 Credential Hygiene (BUG-S01, BUG-S07)

**Decision:** `.env` must be removed from git tracking. All secrets must be referenced only via `.env.template` (documentation) and loaded from environment at runtime.

**Changes:**
- Verify `.env` is excluded by `.gitignore` (it is — confirmed in file). No code change needed.
- Remove hardcoded Supabase project URL from `scripts/print_migrations.py:38` — replace with `os.getenv("SUPABASE_URL", "<YOUR_SUPABASE_URL>")` in the instruction string.
- User must rotate all credentials externally (out of scope for automated fix).

### 2.2 JWT Signature Verification (BUG-S02)

**Decision:** Restore proper HS256 signature verification in `verify_token()`.

**Change in `src/api/auth.py`:**
```python
# Before (broken)
payload = jwt.decode(token, options={"verify_signature": False}, algorithms=["HS256"])

# After
payload = jwt.decode(token, key=self.jwt_secret, algorithms=["HS256"])
```
`self.jwt_secret` is already available as `AuthService.jwt_secret` (set from `SUPABASE_JWT_SECRET` env var).

### 2.3 Endpoint Authentication (BUG-S03)

**Decision:** For this cycle, implement a lightweight **shared-secret guard** on state-mutating endpoints. Full JWT auth wiring (`Depends(get_authenticated_user)`) is architecturally correct but requires resolving the dual-auth system first (see §5). The shared secret is a net security improvement over the current zero-auth state.

**Approach:**
- Add `API_SECRET_KEY` env var (document in `.env.template`).
- Add `verify_api_key(x_api_key: str = Header(...))` FastAPI dependency in `app.py`.
- Apply to: `/ingest/start`, `/actions/swipe`, `/db/migrate`.
- `/discovery/deck`, `/discovery/details`, `/ingest/progress`, `/auth/session` remain open (read-only or auth-initiation flows).
- `/db/migrate` gets an additional `APP_ENV != "production"` guard.

**Note:** This is not a substitute for user-scoped auth. It prevents anonymous abuse from external callers.

### 2.4 `/db/migrate` Production Guard (BUG-S06)

**Decision:** Gate the endpoint behind both the API secret key (§2.3) and an explicit dev-only environment check.

**Change in `src/api/app.py` — `migrate_database()`:**
```python
if os.getenv("APP_ENV", "development") == "production":
    raise HTTPException(status_code=403, detail="Not available in production")
```

### 2.5 `app_patch.py` Webhook Signature Fix (BUG-S05)

**Decision:** Replace the bare `except: pass` with a proper fail-closed guard. If `QStashQueue` cannot be initialized, return HTTP 503. Do not proceed.

---

## 3. LOGIC SUBSYSTEM

### 3.1 Suppression Store Integration (BUG-L03)

**Decision:** Import `createSuppressionStore` from `state.js` into `app.js`. Wire `dismiss()` on swipe-left and `isSuppressed()` as a pre-render filter before building the card stack.

**Interface contract (no changes to `state.js`):**
```js
import { createSuppressionStore } from './state.js';
const suppression = createSuppressionStore(Date.now);

// On dismiss swipe:
suppression.dismiss(slug);

// In loadDeck(), filter deck before rendering:
state.deck = state.deck.filter(m => !suppression.isSuppressed(m.slug));
```

### 3.2 Popularity Scraping (BUG-L01)

**Decision:** Scrape member-count from Letterboxd film page as a popularity proxy. The member count appears in `a.has-icon.icon-watched` or `a[href$="/members/"]` — text like "1.2M" needs normalisation to an integer.

**Change in `src/api/providers/letterboxd.py` — `metadata_for_slugs()`:**
```python
# Parse member count from film page
members_tag = soup.select_one('a.has-icon.icon-watched span') or \
              soup.select_one('[data-original-title*="members"]')
popularity = _parse_member_count(members_tag.get_text()) if members_tag else 0

def _parse_member_count(text: str) -> int:
    # "1.2M" -> 1200000, "45K" -> 45000, "1,234" -> 1234
    text = text.strip().replace(',', '')
    if text.endswith('M'): return int(float(text[:-1]) * 1_000_000)
    if text.endswith('K'): return int(float(text[:-1]) * 1_000)
    return int(text) if text.isdigit() else 0
```

**Note:** The exact CSS selector must be verified against live Letterboxd HTML before committing. The mock scraper is unaffected.

### 3.3 Ingest Progress (BUG-L02)

**Decision:** Replace hardcoded progress checkpoints with real event-driven counters. The pipeline knows: total slugs fetched, slugs filtered, metadata batches completed. Emit actual percentages.

**Approach:**
- Extract `_filter_first_pipeline()` to accept a `progress_callback: Callable[[int], None]` parameter.
- Milestones: slugs fetched = 20%, filtered = 40%, each metadata batch = +40%/n_batches, upsert complete = 100%.
- `_run_ingest_worker()` passes a lambda that calls `store.set_ingest_progress(user_id, pct)`.

### 3.4 Fix `auth.html` Dead Endpoints (BUG-L07)

**Decision:** `auth.html` is a dead page with no backend. Two options:
- **Option A:** Remove `auth.html` entirely — login is handled in `index.html`/`app.js`.
- **Option B:** Wire `AuthService` login/register routes.

**Decision: Option A.** The current app flow uses `POST /auth/session` (Letterboxd proxy), not Supabase Auth. `auth.html` is orphaned UI with no use case. Delete it. Remove reference from any nav links.

### 3.5 Fix `app_patch.py` ImportError (BUG-L04)

**Decision:** `app_patch.py` is an abandoned integration draft. It cannot be salvaged without implementing QStash wiring (deferred). Delete the file.

### 3.6 Fix `SCRAPER_BACKEND` Default (BUG-L10)

**Decision:** Change default from `"mock"` to `"http"`. Add startup log warning if `SCRAPER_BACKEND` is `"mock"` in a non-development environment.

```python
SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "http")  # was "mock"
if SCRAPER_BACKEND == "mock" and os.getenv("APP_ENV", "development") != "development":
    print("WARNING: SCRAPER_BACKEND=mock in non-development environment", flush=True)
```

---

## 4. DEPENDENCY / BUILD SUBSYSTEM

### 4.1 Add `python-dotenv` (BUG-D01)

**Change:** Add `python-dotenv>=1.0.0` to `requirements.txt` and `pyproject.toml` `[project.dependencies]`.

### 4.2 Fix `package.json` Start Script (BUG-D02)

**Change:** Update `"start"` script to `"uvicorn src.api.app:app --host 0.0.0.0 --port 8000"`.

### 4.3 Consolidate Migrations (BUG-D03)

**Decision:** The `00X_name.sql` series (`001_movies.sql` through `006_genre_preferences.sql`) is the canonical series — it matches what `SupabaseStore` queries. The `00X_initial_schema.sql` series is a legacy draft.

**Approach:**
- Rename legacy files with `_LEGACY` suffix so they are clearly non-executable but preserved for history.
- Add a `README.md` in `db/migrations/` documenting the canonical order.
- Do NOT run the legacy series against any live database.

**Canonical migration order:**
```
001_movies.sql
002_users.sql
003_watchlist.sql
004_diary.sql
005_exclusions.sql
006_genre_preferences.sql
```

**Note:** `007_rate_limit_state.sql` and `008_rls_user_based.sql` remain but are flagged as needing RLS policy corrections (BUG-S04 — deferred full fix, but RLS policies will be updated in this cycle to use `auth.uid()` where applicable).

---

## 5. DEFERRED (OUT OF SCOPE THIS CYCLE)

| Item | Reason |
|------|--------|
| BUG-S04 Full RLS rewrite | Requires fully wired user auth system first |
| BUG-L05 QStash async ingest | Architectural lift; daemon thread is functional for local use |
| BUG-L08 Supabase persistence for ingest/rate-limit state | Tied to BUG-L05 |
| Full `Depends(get_authenticated_user)` on all endpoints | Requires resolving dual-auth system design |
| BUG-L06 Profile-driven ingest source | Feature enhancement, not a bug fix |
| BUG-L09 Column name validation | Requires knowing which migration series ran on the live DB |

---

## 6. DEPENDENCY COMPATIBILITY

| Package | Required Version | Notes |
|---------|-----------------|-------|
| `python-dotenv` | `>=1.0.0` | Stable; no known conflicts with existing deps |
| `PyJWT` | `>=2.8.0` | Already in `requirements.txt`; `jwt.decode` signature verified compatible |
| `cryptography` | `>=45.0.0` | Already present; Fernet unchanged |
| `fastapi` | `>=0.116.0` | `Header()` dependency already available |

---

## 7. FILE CHANGE MANIFEST

| File | Action | Bugs Addressed |
|------|--------|----------------|
| `src/api/app.py` | Modify | S03, S06, L10 |
| `src/api/auth.py` | Modify | S02 |
| `src/api/app_patch.py` | Delete | L04, S05 |
| `src/api/providers/letterboxd.py` | Modify | L01 |
| `src/web/app.js` | Modify | L03 |
| `src/web/auth.html` | Delete | L07 |
| `scripts/print_migrations.py` | Modify | S07 |
| `requirements.txt` | Modify | D01 |
| `pyproject.toml` | Modify | D01 |
| `package.json` | Modify | D02 |
| `db/migrations/` | Rename legacy files + update README | D03 |
| `.env.template` | Modify | S03 (document `API_SECRET_KEY`) |
