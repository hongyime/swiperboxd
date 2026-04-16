# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the dev server (http://localhost:8000)
npm start                          # uvicorn src.api.app:app --host 0.0.0.0 --port 8000

# Tests
npm test                           # run both suites
npm run test:web                   # Node built-in test runner, tests/web_state.test.js
npm run test:api                   # pytest -q, tests/test_api.py (and other test_*.py)
pytest tests/test_api.py::test_list_catalog_returns_mixed_lists  # single test

# Lint (Python syntax check only)
npm run lint                       # python -m compileall src
```

Tests require `MASTER_ENCRYPTION_KEY` and `SCRAPER_BACKEND=mock`. The test file sets both via `os.environ` at import time — no `.env` needed to run tests.

## Architecture

### Request flow

```
Browser → FastAPI (src/api/app.py)
            ├── selects Store (InMemoryStore or SupabaseStore based on env)
            ├── selects Scraper (HttpLetterboxdScraper or MockLetterboxdScraper)
            └── static files served from src/web/ via FileResponse
```

**Scraper selection** — controlled by `SCRAPER_BACKEND` env var (`"http"` default, `"mock"` for dev/tests). The mock reads from `src/api/providers/mock_catalog.json` for movies and returns hard-coded list stubs from `_load_mock_lists()`.

**Store selection** — `InMemoryStore` when Supabase env vars are absent, `SupabaseStore` in production. The `Store` protocol in `store.py` defines the shared interface both must satisfy. `SupabaseStore` persists movies/watchlist/diary/exclusions/genre preferences to Supabase but keeps ingest progress, rate-limit state, and list data in-memory.

### Key subsystems

**List discovery** (Phase 2 work-in-progress):
- `GET /lists/catalog` — calls `scraper.discover_site_lists()`, upserts to store, returns sorted results
- `GET /lists/{list_id}` — fetches movie slugs via `scraper.fetch_list_movie_slugs()`
- `GET /lists/{list_id}/deck` — on-demand: fetches slugs, fills missing metadata, returns weighted shuffle
- `HttpLetterboxdScraper.discover_site_lists()` and `fetch_list_movie_slugs()` currently raise `NotImplementedError` — only `MockLetterboxdScraper` has real implementations

**Ingest pipeline** (`POST /ingest/start` → background thread):
- `_run_ingest_worker` → `_filter_first_pipeline` → `scraper.pull_source_slugs` → `scraper.metadata_for_slugs` → `store.upsert_movie`
- Progress (0–100, -1 for error) tracked in-memory, polled via `GET /ingest/progress`
- Rate-limited per user via `store.allow_scrape_request` (1 s minimum interval)

**Session auth**: `POST /auth/session` validates a raw Letterboxd session cookie against `/settings/`, then returns it encrypted with Fernet/AES-256 derived from `MASTER_ENCRYPTION_KEY`. Mutating endpoints (`/ingest/start`, `/actions/swipe`) require `X-Session-Token: <encrypted>`.

**Frontend** (`src/web/`): Vanilla JS + CSS, no build step. `app.js` imports from `state.js`. State is plain objects. `createSuppressionStore` (state.js) handles 24-hour local dismiss suppression. The discovery UI uses a list-selector dropdown (not profiles) as the primary entry point.

### Environment variables

| Variable | Purpose |
|----------|---------|
| `MASTER_ENCRYPTION_KEY` | AES-256 key for Fernet session encryption — required |
| `SCRAPER_BACKEND` | `"http"` (default) or `"mock"` |
| `APP_ENV` | `"production"` blocks `/db/migrate` |
| `TARGET_PLATFORM_BASE_URL` | Letterboxd base URL (default `https://letterboxd.com`) |
| `TARGET_PLATFORM_TIMEOUT_SECONDS` | HTTP scraper timeout |
| `SUPABASE_URL` / `SUPABASE_KEY` | Activates `SupabaseStore` when both set |
| `ROTATING_PROXY_ENDPOINT` / `ROTATING_PROXY_API_KEY` | Optional proxy fallback on 429/403 |

### Deployment

Deployed on Vercel via `@vercel/python`. `vercel.json` routes all traffic to `src/api/app.py`. Supabase free-tier keep-alive runs via GitHub Actions cron (`.github/workflows/keep-alive.yml`).

### DB migrations

Canonical order (run in Supabase SQL editor or via `POST /db/migrate` in dev):
```
001_movies.sql → 002_users.sql → 003_watchlist.sql →
004_diary.sql → 005_exclusions.sql → 006_genre_preferences.sql
```
Files prefixed `_LEGACY` in `db/migrations/` are superseded drafts — do not run them.

### Conventions

- Commit messages: `type: description` (types: `feat fix docs refactor test chore perf style`)
- All scraper HTML parsing is in `src/api/providers/letterboxd.py`; CSS selector changes go there
- `normalize_movie_record()` in `store.py` is the single source of truth for coercing partial movie dicts — always run fetched movies through it before comparisons
- `design.md` contains architectural decisions for the current remediation cycle; `tasks.md` tracks task status
