# Product Requirements Document: Movie Discovery Platform

**Version:** 0.6.0  
**Status:** Production Deployed  
**Last Updated:** 2026-04-19

---

## 1. Executive Summary

This application is a serverless movie discovery platform that transforms Letterboxd community lists into an interactive, swipe-based decision interface. The system solves choice paralysis by programmatically filtering out content the user has already seen or queued, presenting only unseen titles from curated lists. Movie metadata is cached globally in a PostgreSQL database (Supabase), accelerating subsequent users while minimizing scraping overhead.

The discovery surface is built on Letterboxd community and official lists rather than static rating profiles. Lists are scraped periodically via Vercel Cron jobs and on-demand via a Chrome extension that leverages the user's browser session to bypass IP-based rate limiting.

---

## 2. System Architecture

### 2.1 Technology Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| **Frontend** | Vanilla JavaScript (ES modules) | - |
| **Backend** | FastAPI (Python) | ≥0.135.3 |
| **Runtime** | Python | ≥3.11 |
| **Database** | Supabase (PostgreSQL) | SDK ≥2.28.3 |
| **Deployment** | Vercel Serverless (@vercel/python) | - |
| **Scraping** | httpx + BeautifulSoup4 | ≥0.28.1, ≥4.14.3 |
| **Auth Crypto** | Fernet (AES-128-CBC) | cryptography ≥45.0.0 |
| **Extension** | Chrome MV3 Service Worker | - |

### 2.2 Deployment Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Vercel Serverless                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │  FastAPI App │  │  Cron Jobs   │  │  Static Web  │      │
│  │  (Python)    │  │  (Scheduled) │  │  (HTML/JS)   │      │
│  └──────┬───────┘  └──────┬───────┘  └──────────────┘      │
│         │                  │                                 │
└─────────┼──────────────────┼─────────────────────────────────┘
          │                  │
          ▼                  ▼
    ┌─────────────────────────────┐
    │   Supabase (PostgreSQL)     │
    │  - movies                   │
    │  - users                    │
    │  - watchlist / diary        │
    │  - list_summaries           │
    │  - list_memberships         │
    │  - genre_preferences        │
    └─────────────────────────────┘
          ▲
          │
    ┌─────┴──────────────────┐
    │  Chrome Extension      │
    │  (Browser-based sync)  │
    └────────────────────────┘
```

### 2.3 Data Flow

**User Authentication:**
1. User provides Letterboxd username + session cookie
2. Backend validates cookie against `letterboxd.com/settings/`
3. Session encrypted with Fernet (MASTER_ENCRYPTION_KEY)
4. Encrypted token returned to client, stored in localStorage

**List Discovery:**
1. Cron job scrapes `/lists/popular/` every 24 hours (02:00 UTC)
2. List summaries stored in `list_summaries` table
3. Frontend fetches catalog via `GET /lists/catalog`
4. User selects list → `GET /lists/{list_id}/deck` returns filtered movies

**User History Sync:**
- **Vercel (Production):** Extension scrapes watchlist/diary in browser, pushes batches to API
- **Local Dev:** Background thread scrapes using stored session cookie

**Movie Metadata:**
- Extension scrapes `/film/{slug}/` pages, extracts JSON-LD + HTML fallbacks
- Batch uploads via `POST /api/extension/batch/movies`
- Letterboxd Film ID (LID) cached for API write-back operations

---

## 3. Feature Matrix

### 3.1 Core Features

| Feature | Status | Implementation |
|---------|--------|----------------|
| **List-Based Discovery** | ✅ Active | Primary discovery surface; replaces profile-based filtering |
| **Swipe Interface** | ✅ Active | Touch/mouse drag, keyboard shortcuts (←/→/↑/Space) |
| **User History Sync** | ✅ Active | Watchlist + Diary sync via extension or server-side |
| **Genre Preference Learning** | ✅ Active | Accumulates weights on watchlist swipes; weighted shuffle |
| **24h Suppression** | ✅ Active | Client-side Map with expiry; dismiss action |
| **Letterboxd Write-Back** | ✅ Active | Direct API calls from browser using live session cookie |
| **Chrome Extension Sync** | ✅ Active | MV3 service worker; scrapes watchlist/diary/lists/metadata |
| **Cron-Based Refresh** | ✅ Active | 3 scheduled jobs (lists, users, backfill) |

### 3.2 Legacy Features (Deprecated)

| Feature | Status | Notes |
|---------|--------|-------|
| **Profile-Based Discovery** | ⚠️ Deprecated | `gold-standard`, `hidden-gems`, `fresh-picks` still in code but superseded by lists |
| **Server-Side Ingest Threads** | ⚠️ Limited | Works on long-running servers; killed on Vercel serverless |

---

## 4. API Contract

### 4.1 System Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | None | Liveness check; returns store type |
| `GET` | `/` | None | Serves `src/web/index.html` |
| `GET` | `/web/{path}` | None | Serves static assets (path-traversal protected) |

### 4.2 Authentication

| Method | Path | Request | Response |
|--------|------|---------|----------|
| `POST` | `/auth/session` | `{username, session_cookie}` | `{status, encrypted_session_cookie}` |

**Behavior:**
- Validates `session_cookie` against `letterboxd.com/settings/`
- Encrypts JSON payload `{"u": username, "c": session_cookie}` with Fernet
- Persists encrypted session to database for server-side operations

### 4.3 List Discovery

| Method | Path | Query Params | Response |
|--------|------|--------------|----------|
| `GET` | `/lists/catalog` | `q` (search), `page` | `{status, query, page, results: [LetterboxdListSummary]}` |
| `GET` | `/lists/{list_id}` | - | `{status, list, movie_slugs, preview}` |
| `GET` | `/lists/{list_id}/deck` | `user_id` | `{status, list, results: [Movie]}` |
| `POST` | `/lists/refresh` | - | `{status, fetched, updated}` (Auth required, rate-limited) |

**Behavior:**
- `/lists/catalog`: Attempts fresh scrape, falls back to cached data on rate-limit
- `/lists/{list_id}/deck`: Filters out user's watchlist/diary/exclusions; returns weighted-shuffled deck (max 20)
- Vercel: Skips live scraping (relies on cron + extension); uses cached memberships

### 4.4 User History Sync

| Method | Path | Request | Response |
|--------|------|---------|----------|
| `POST` | `/ingest/start` | `{user_id, source, depth_pages}` | `{status, user_id, sync_stats?}` |
| `GET` | `/ingest/progress` | `user_id` | `{status, user_id, progress, running, error}` |

**Behavior:**
- **Vercel:** Runs sync inline (awaited) within 55s timeout; returns `status: "completed"` with stats
- **Long-running server:** Spawns background thread; returns `status: "queued"`
- Extracts session cookie from `X-Session-Token` header for live scraping

### 4.5 Discovery (Legacy Profile-Based)

| Method | Path | Query Params | Response |
|--------|------|--------------|----------|
| `GET` | `/discovery/profiles` | - | `{profiles: [str]}` |
| `GET` | `/discovery/deck` | `user_id`, `profile` | `{status, profile, results, meta}` |
| `GET` | `/discovery/details` | `slug` | `{status, slug, synopsis, cast, genres}` |

### 4.6 Swipe Actions

| Method | Path | Request | Response |
|--------|------|---------|----------|
| `POST` | `/actions/swipe` | `{user_id, movie_slug, action}` | `{status, action, movie_slug}` |
| `POST` | `/actions/cache-lb-id` | `{movie_slug, lb_film_id}` | `{status}` |

**Actions:** `watchlist`, `dismiss`, `log`  
**Rate Limit:** 500ms per user (in-memory lock)  
**Duplicate Handling:** Returns 409 with `already_in_watchlist` / `already_in_diary` codes

### 4.7 Extension Endpoints

| Method | Path | Request | Description |
|--------|------|---------|-------------|
| `POST` | `/api/extension/register` | `{letterboxd_session_cookie}` | Self-register; returns `{username, session_token, api_base}` |
| `POST` | `/api/extension/batch/watchlist` | `{user_id, slugs, page?, total_pages?}` | Batch upload watchlist slugs |
| `POST` | `/api/extension/batch/diary` | `{user_id, slugs, page?, total_pages?}` | Batch upload diary slugs |
| `POST` | `/api/extension/batch/movies` | `{movies: [ExtensionMoviePayload]}` | Batch upload movie metadata |
| `POST` | `/api/extension/batch/list-summaries` | `{lists: [ExtensionListSummaryPayload]}` | Batch upload list catalog |
| `POST` | `/api/extension/batch/list-movies` | `{list_id, slugs, ...}` | Batch upload list memberships |
| `GET` | `/api/extension/movies-missing-lb-id` | `limit` | Returns slugs needing LID backfill |
| `GET` | `/api/extension/lists-needing-scrape` | `limit` | Returns under-scraped lists (<50% complete) |
| `POST` | `/api/extension/sync-status` | `{user_id, phase, ...}` | Report extension sync progress |

**Batch Limit:** 500 items per request  
**Auth:** Requires `X-Session-Token` header (session token or `EXTENSION_API_KEY`)

### 4.8 Cron Endpoints (Internal)

| Method | Path | Header | Description |
|--------|------|--------|-------------|
| `POST` | `/api/cron/refresh-lists` | `X-Cron-Secret` | Refresh list catalog from Letterboxd (daily 02:00 UTC) |
| `POST` | `/api/cron/sync-users` | `X-Cron-Secret` | Sync all users' watchlist/diary (daily 04:00 UTC) |
| `POST` | `/api/cron/backfill-scrapes` | `X-Cron-Secret` | Backfill placeholder movies + under-scraped lists (daily 03:30 UTC) |
| `GET` | `/api/cron/health` | None | Cron health check |

**Protection:** All cron endpoints require `X-Cron-Secret` header matching `VERCEL_CRON_SECRET` env var

### 4.9 Database (Development Only)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/db/migrate` | Required | Run database migrations (403 in production) |

---

## 5. Data Models

### 5.1 LetterboxdListSummary

```python
{
  "list_id": str,           # "{owner_slug}-{list_slug}"
  "slug": str,              # list slug component
  "url": str,               # canonical letterboxd.com URL
  "title": str,
  "owner_name": str,
  "owner_slug": str,
  "description": str,
  "film_count": int,
  "like_count": int,
  "comment_count": int,
  "is_official": bool,      # true if owner_slug in {"letterboxd", "official"}
  "tags": list[str],
  "scraped_film_count": int # tracking scrape completeness
}
```

### 5.2 LetterboxdMovie

```python
{
  "slug": str,              # unique film identifier
  "title": str,
  "poster_url": str,
  "rating": float,          # 0.0-5.0
  "popularity": int,        # rating count
  "genres": list[str],
  "synopsis": str,
  "cast": list[str],
  "year": int | None,
  "director": str | None,
  "lb_film_id": str         # Letterboxd internal ID (for API operations)
}
```

### 5.3 Session Token Format

**New Format (Current):**
```
Fernet(json.dumps({"u": username, "c": session_cookie}))
```

**Old Format (Backward Compatible):**
```
Fernet(raw_session_cookie)
```

Identity binding is enforced for new-format tokens; old-format tokens bypass the check.

---

## 6. Security & Performance

### 6.1 Security

**Session Token Encryption:**
- `MASTER_ENCRYPTION_KEY` (env) is required for all encryption/decryption
- Fernet provides authenticated encryption (AES-128-CBC + HMAC)
- Tampering is detected and rejected

**Identity Binding:**
- `POST /ingest/start` and `POST /actions/swipe` extract username from token
- Reject requests where `payload.user_id` differs from token username (HTTP 403)
- Bypassed for old-format tokens (empty username) for backward compatibility

**Cron Endpoint Protection:**
- All cron endpoints require `X-Cron-Secret` header
- If `VERCEL_CRON_SECRET` is unset, all requests are rejected (fail-closed)

**Path Traversal Guard:**
- `GET /web/{path}` resolves path and verifies it stays within `src/web/` directory

**Ingest Atomicity:**
- `ingest_running` check-and-add wrapped in `store.lock` to prevent concurrent ingests

### 6.2 Performance

**Rate Limiting:**
- **Swipe actions:** 500ms minimum interval per user (in-memory, resets on restart)
- **Manual refresh:** 300s minimum interval per user (in-memory)
- **Ingest:** 1s minimum interval per user (in-memory)

**Deck Ordering:**
- `weighted_shuffle`: Top 8 movies by summed genre preference score, then random tail
- Genre weights accumulate per watchlist swipe, persisted to `genre_preferences` table

**Frontend Suppression:**
- Dismiss swipes suppress movie for 24 hours in client-side Map (not persisted across reloads)
- Suppressed movies filtered from deck results before rendering

**List Catalog Sorting:**
- Official lists first, then descending `like_count`, then alphabetical `title`

**Serverless Constraints:**
- Background threads killed on Vercel between requests
- Cron jobs are primary refresh path for list data freshness
- Extension is primary sync path for user history (bypasses Vercel IP blocking)

---

## 7. Non-Functional Requirements

### 7.1 Error Handling

**Ingest Worker Failures:**
- Exceptions caught and stored in `ingest_errors` dict
- Progress set to `-1` to signal error state
- `GET /ingest/progress` returns error details with `{code, reason}`

**Deck Filtering Robustness:**
- Movies with `None` rating/popularity are normalized to `0.0` / `0`
- Invalid movies skipped during profile matching
- Defensive filtering prevents 500 errors from partial data

**List Catalog Fallback:**
- Fresh scrape attempted first
- On rate-limit or error, falls back to cached `store.get_lists()`
- Partial scrapes (<50% of `film_count`) filtered from results

### 7.2 Logging

**Structured Logging:**
- All log statements use `flush=True` for immediate output
- Prefixes: `[startup]`, `[auth]`, `[ingest]`, `[deck]`, `[lists]`, `[extension]`, `[cron]`
- Includes context: `user_id`, `list_id`, `slug`, `phase`, `page`, `count`

**Progress Tracking:**
- Ingest progress: 0-100 (percentage), -1 (error)
- Extension sync: phase-based progress mapping (`idle`, `watchlist`, `diary`, `complete`, `error`)

### 7.3 Scalability

**Database:**
- Supabase (PostgreSQL) with connection pooling
- Indexes on: `movies.slug`, `movies.rating`, `movies.popularity`, `movies.lb_film_id`
- Indexes on: `list_memberships(list_id, position)`

**Caching:**
- List summaries cached in database, refreshed daily
- Movie metadata cached globally (shared across users)
- List memberships cached per list

**Batch Operations:**
- Extension batch endpoints accept up to 500 items per request
- Batch add operations for watchlist/diary (single DB round-trip)

---

## 8. Environment Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MASTER_ENCRYPTION_KEY` | **Yes** | - | Fernet key for session token encryption |
| `SUPABASE_URL` | Production | - | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Production | - | Supabase service role key (bypasses RLS) |
| `SUPABASE_ANON_KEY` | Optional | - | Supabase anon key (fallback for local dev) |
| `VERCEL_CRON_SECRET` | Production | - | Shared secret for cron endpoint protection |
| `SCRAPER_BACKEND` | No | `http` | `http` or `mock` (dev/test) |
| `APP_ENV` | No | `development` | `development` or `production` |
| `TARGET_PLATFORM_BASE_URL` | No | `https://letterboxd.com` | Override scrape target |
| `TARGET_PLATFORM_TIMEOUT_SECONDS` | No | `20.0` | HTTP timeout for scraping |
| `EXTENSION_API_KEY` | Optional | - | API key for extension auth (alternative to session tokens) |
| `VERCEL` | Auto-set | - | Detected by Vercel; triggers serverless-specific behavior |

**Notes:**
- Without `SUPABASE_URL`, app falls back to `InMemoryStore` (data wiped on restart)
- Backend must use `SUPABASE_SERVICE_ROLE_KEY` — anon key is blocked by RLS on writes
- `POST /db/migrate` is blocked when `APP_ENV=production`

---

## 9. Deployment

### 9.1 Vercel Configuration

**Build:**
- Entry point: `api/index.py` (exports FastAPI app)
- Builder: `@vercel/python`
- Static files: Served from `src/web/` via FastAPI

**Cron Jobs:**
```json
{
  "crons": [
    {"path": "/api/cron/refresh-lists", "schedule": "0 2 * * *"},
    {"path": "/api/cron/sync-users", "schedule": "0 4 * * *"},
    {"path": "/api/cron/backfill-scrapes", "schedule": "30 3 * * *"}
  ]
}
```

**Environment Variables:**
- Set all production variables in Vercel dashboard
- Set `APP_ENV=production`
- Configure `VERCEL_CRON_SECRET` and add to cron job headers

### 9.2 Database Migrations

**Production:**
- Migrations blocked via API (`POST /db/migrate` returns 403)
- Run migrations locally against production Supabase URL before deploying schema changes

**Development:**
```bash
curl -X POST http://localhost:8000/db/migrate \
  -H "X-Session-Token: <your-token>"
```

---

## 10. Chrome Extension

### 10.1 Architecture

**Manifest V3 Service Worker:**
- Scrapes watchlist/diary using `credentials: "include"` (user's browser session)
- Scrapes public lists from `/lists/popular/`
- Scrapes individual `/film/{slug}/` pages for metadata
- Batch-pushes to API with retries + exponential backoff

**Self-Registration:**
- No prior web app interaction required
- `POST /api/extension/register` exchanges Letterboxd cookie for session token
- Parses username from HTML response (`data-owner` attribute)

**Periodic Sync:**
- Alarm-based background sync every 6 hours (configurable)
- Discovers under-scraped lists via `GET /api/extension/lists-needing-scrape`
- Backfills missing LIDs via `GET /api/extension/movies-missing-lb-id`

### 10.2 Letterboxd Write-Back

**Direct API Integration:**
- Extension fetches CSRF token from film page HTML
- Calls Letterboxd's internal AJAX endpoints:
  - `POST /s/save-film-watch` (watchlist toggle)
  - `POST /s/save-film-watch` (diary log with date)
- Uses `credentials: "include"` to send user's live session cookie

**LID Caching:**
- Extracts `x-letterboxd-identifier` response header
- Caches via `POST /actions/cache-lb-id` for future operations

---

## 11. Known Limitations

1. **Vercel IP Blocking:** Letterboxd blocks AWS IP ranges with 403; server-side scraping fails in production
2. **Serverless Thread Killing:** Background threads terminated between requests; cron + extension are primary sync paths
3. **In-Memory Rate Limiting:** Rate limits reset on server restart (not distributed)
4. **Client-Side Suppression:** 24h dismiss suppression not persisted across page reloads
5. **Profile-Based Discovery:** Legacy feature still in code but superseded by list-based discovery

---

## 12. Future Considerations

- Distributed locking for ingest atomicity in multi-instance deployments
- Persistent suppression storage (database-backed)
- Migration path to force old-format tokens to re-authenticate
- Removal of deprecated profile-based discovery code
- Enhanced error observability (structured logging, metrics)
