# design.md — Implementation Design (Cycle 2)

> Architecture decisions and interface contracts for this execution cycle.
> Read this before executing any task in `tasks.md`.
> Bugs addressed: BUG-C01 through BUG-M05

---

## 1. SCOPE

This cycle fixes all `Open` items from `bugfix.md`. Items marked **DEFERRED** are acknowledged but not scheduled.

---

## 2. STARTUP CRASH FIX (BUG-C01)

**Decision:** Wrap the startup `discover_site_lists` call in a `try/except` that catches `NotImplementedError` and logs a warning. Do not remove the call — it remains valid for mock mode and will be valid once BUG-C02 is fixed.

**Change in `src/api/app.py`:**
```python
try:
    for entry in scraper.discover_site_lists(page=1):
        store.upsert_list_summary(entry.__dict__)
except NotImplementedError:
    print("[startup] WARNING: scraper does not support list discovery; catalog will be empty until first /lists/catalog request", flush=True)
```

---

## 3. HTTP SCRAPER — LIST CATALOG (BUG-C02)

**Decision:** Scrape `https://letterboxd.com/lists/` with `?page=N` for pagination.

**HTML target (Letterboxd public list browse page):**
- List container: `section.list-set` — one per list entry
- Title + URL: `h2.title-headline a` — href is the canonical list path e.g. `/username/list/list-slug/`
- Description: `div.body-text p` (first `p`) — may be absent
- Film count: `small.poster-count` text — e.g. "52 films"
- Like count: `a.icon-like` adjacent text, OR `span.icon-likes-count`
- Owner: extracted from the href path component (first segment = owner_slug)

**`list_id` derivation from URL path:**
Given href `/alice/list/my-favorites/`:
- `owner_slug = "alice"`
- `list_slug = "my-favorites"`
- `list_id = "alice-my-favorites"`
- `url = f"{self.base_url}/alice/list/my-favorites/"`

**Empty page detection:** if no `section.list-set` elements found → stop pagination.

**Signature (no change to protocol):**
```python
def discover_site_lists(self, query: str | None = None, page: int = 1) -> list[LetterboxdListSummary]
```
Note: `query` filtering is done post-scrape (same as mock) since Letterboxd's list browse does not expose a search URL in the public interface.

---

## 4. HTTP SCRAPER — LIST MOVIE SLUGS (BUG-C03 + BUG-M03)

**Problem:** `fetch_list_movie_slugs(list_id)` receives only the list ID. The HTTP scraper needs a URL. The `list_id` string (e.g. `"official-best-picture"`) cannot be reliably reversed to a URL.

**Decision:** Add an optional `list_url` keyword argument to the `Scraper` protocol and both implementations. Callers in `app.py` already hold the `LetterboxdListSummary` (which contains `url`) and will pass it.

**Updated protocol:**
```python
def fetch_list_movie_slugs(self, list_id: str, list_url: str | None = None) -> list[str]: ...
```

- `MockLetterboxdScraper`: ignores `list_url`, uses existing `list_id` mapping.
- `HttpLetterboxdScraper`: uses `list_url` if provided; raises `ValueError` if neither is derivable.

**HTML target (Letterboxd list detail page):**
- Film slugs: `div.film-poster[data-film-slug]` attribute — most reliable selector.
- Fallback: `li.poster-container a[href^="/film/"]` — extract slug from href.
- Pagination: `?page=N`, stop when no poster containers found.
- Cap: 20 pages maximum (≈ 240 films) to prevent runaway scraping.

**`app.py` call-site update (two locations):**
```python
summary = store.get_list_summary(list_id)
movie_slugs = scraper.fetch_list_movie_slugs(list_id, list_url=summary.get("url") if summary else None)
```

---

## 5. MIGRATION FIXES (BUG-H01, BUG-H02)

### 5.1 LEGACY file filter
**Change in `src/api/database.py`:**
```python
migration_files = sorted(f for f in migrations_dir.glob("*.sql") if not f.name.startswith("LEGACY_"))
```

### 5.2 `exec_sql` RPC
**Decision:** The `exec_sql` RPC approach requires a custom Supabase function that cannot be auto-provisioned. Replace with a direct `postgrest-py` raw SQL approach using the service role key if available; otherwise log a clear error and skip.

**Revised approach:** Use `supabase-py`'s `client.postgrest.schema("public")` raw query path. Since supabase-py v2 does not expose arbitrary SQL via the REST client, the safest correct fix is:
1. Keep the `rpc('exec_sql', ...)` call as-is (it works if the user provisions it).
2. Improve the error message to explicitly say the `exec_sql` function is required.
3. Add a note to `db/migrations/README.md`.

This is the minimum-change correct fix: do not redesign the migration runner; make the failure mode explicit.

---

## 6. LIST PERSISTENCE — SQL + SUPABASE STORE (BUG-H03)

### 6.1 New migration: `007_lists.sql`
```sql
CREATE TABLE IF NOT EXISTS list_summaries (
    list_id TEXT PRIMARY KEY,
    slug TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    owner_name TEXT DEFAULT '',
    owner_slug TEXT DEFAULT '',
    description TEXT DEFAULT '',
    film_count INTEGER DEFAULT 0,
    like_count INTEGER DEFAULT 0,
    comment_count INTEGER DEFAULT 0,
    is_official BOOLEAN DEFAULT FALSE,
    tags JSONB DEFAULT '[]',
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS list_memberships (
    id BIGSERIAL PRIMARY KEY,
    list_id TEXT NOT NULL REFERENCES list_summaries(list_id) ON DELETE CASCADE,
    movie_slug TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    UNIQUE(list_id, movie_slug)
);

CREATE INDEX IF NOT EXISTS idx_list_memberships_list_id_pos ON list_memberships(list_id, position);
```

### 6.2 `SupabaseStore` list methods
All five list methods (`upsert_list_summary`, `get_list_summary`, `get_lists`, `replace_list_memberships`, `get_list_memberships`) are updated to use Supabase tables instead of in-memory dicts. The in-memory `list_summaries`/`list_memberships` dict fields are removed from `SupabaseStore.__init__`.

`InMemoryStore` list methods are unchanged (dicts remain correct for dev/test).

---

## 7. SESSION IDENTITY BINDING (BUG-H04)

**Decision:** Embed the username inside the Fernet-encrypted token at auth time. `verify_session` dependency returns the verified username string. POST endpoints assert `payload.user_id == verified_username`.

**Token format change:**
- Old: `encrypt(session_cookie)`
- New: `encrypt(json.dumps({"u": username, "c": session_cookie}))`

**Backward compatibility:** `verify_session` tries JSON parse; if it fails (old-format token), it returns `""` as username and skips the binding check. This allows existing sessions to remain valid.

**`create_auth_session` change:**
```python
import json
token_payload = json.dumps({"u": payload.username, "c": payload.session_cookie})
encrypted_cookie = encrypt_session_cookie(token_payload, master_key)
```

**`verify_session` change:**
```python
def verify_session(x_session_token: str = Header(..., alias="X-Session-Token")) -> str:
    ...
    raw = decrypt_session_cookie(x_session_token, master_key)
    try:
        data = json.loads(raw)
        return data.get("u", "")
    except (json.JSONDecodeError, ValueError):
        return ""  # old token format — identity unknown, allow through
```

**Binding assertion in guarded endpoints:**
```python
async def start_ingest(payload: IngestStartRequest, verified_user: str = Depends(verify_session)):
    if verified_user and payload.user_id != verified_user:
        raise HTTPException(status_code=403, detail={"code": "user_id_mismatch"})
```

The `if verified_user` guard ensures old-format tokens still work during rollover.

---

## 8. INGEST RACE CONDITION FIX (BUG-H05)

**Decision:** Move both the `ingest_running` check and the `add()` inside a single store lock acquisition. Add a `check_and_start_ingest` method to `InMemoryStore` (and `SupabaseStore`) that performs the check-and-set atomically.

**Alternative (simpler):** Since the `store.lock` is already available, acquire it directly in `app.py` around the check+add:

```python
with store.lock:
    if payload.user_id in store.ingest_running:
        return {"status": "already_running", "user_id": payload.user_id}
    store.ingest_running.add(payload.user_id)
```

This is the minimum change. Do not add new store methods.

---

## 9. SMOKE TEST + TEST FIXES (BUG-M01, BUG-M02)

### 9.1 `smoke_test_app.py`
- Fix import: `from api.app import app` → `from src.api.app import app`
- Fix auth call: remove `password` field, use `session_cookie` with a placeholder noting manual pre-requisite

### 9.2 `test_store.py`
- Fix skip condition: `SUPABASE_KEY` → `SUPABASE_ANON_KEY` in both `skipif` decorators

---

## 10. DEAD MODULE TOMBSTONING (BUG-M04)

**Decision:** Do NOT delete these modules — they contain real, correct implementations that may be wired in future. Add a tombstone header to each file making their status explicit.

Files: `src/api/auth.py`, `src/api/auth_deps.py`, `src/api/rate_limiter.py`, `src/api/qstash_queue.py`

Header to add at top of each:
```python
# STATUS: IMPLEMENTED BUT NOT WIRED
# This module is not imported by app.py. It is retained for future integration.
# Do not assume this code is active or enforced.
```

---

## 11. QUEUE CLEANUP (BUG-M05)

**Decision:** Remove the `queue.enqueue(...)` call from `start_ingest`. The `InMemoryQueue` serves no functional purpose. The queue object and class are retained (not deleted) since they may be replaced by QStash in future.

---

## 12. DEPENDENCY COMPATIBILITY

| Component | Requirement | Notes |
|-----------|-------------|-------|
| `supabase-py` | `>=2.0.0` | `upsert(on_conflict=...)` syntax requires v2 |
| `httpx` | `>=0.24.0` | Already in requirements; no change |
| `beautifulsoup4` | `>=4.12` | Already in requirements; no change |
| `cryptography` | `>=45.0.0` | Fernet unchanged |
| Python `json` | stdlib | No new dependency for token payload change |

---

## 13. FILE CHANGE MANIFEST

| File | Action | Bugs Fixed |
|------|--------|------------|
| `src/api/app.py` | Modify | C01, H04, H05, M05 |
| `src/api/providers/letterboxd.py` | Modify | C02, C03, M03 |
| `src/api/database.py` | Modify | H01, H02 |
| `src/api/store.py` | Modify | H03 |
| `db/migrations/007_lists.sql` | Create | H03 |
| `scripts/smoke_test_app.py` | Modify | M01 |
| `tests/test_store.py` | Modify | M02 |
| `src/api/auth.py` | Modify (header only) | M04 |
| `src/api/auth_deps.py` | Modify (header only) | M04 |
| `src/api/rate_limiter.py` | Modify (header only) | M04 |
| `src/api/qstash_queue.py` | Modify (header only) | M04 |
