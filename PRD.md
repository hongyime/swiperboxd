Product Requirements Document: Media Discovery PWA

Version: 2.0.0 | Status: Reflects deployed implementation

---

1. Executive Summary

This application is a serverless, discovery-driven engine that transforms Letterboxd movie lists into an interactive, swipe-based decision loop. It solves choice paralysis by programmatically filtering out content the user has already seen or queued, presenting only unseen titles from curated lists. Discovered movie metadata is cached globally in Supabase, accelerating subsequent users while minimising target platform scraping.

The discovery surface is built on Letterboxd community and official lists rather than static rating profiles. Lists are scraped periodically via a Vercel Cron job; individual list film rosters are fetched on demand when a user loads a deck.

---

2. System Architecture

2.1 Stack

  Frontend:     Vanilla JavaScript (ES modules, no framework), served as static files by FastAPI
  Backend:      FastAPI (Python 3.11+) deployed via @vercel/python
  Database:     Supabase (PostgreSQL) with supabase-py v2 client; InMemoryStore fallback for dev/test
  Scraping:     httpx + BeautifulSoup4; target platform is letterboxd.com
  Auth crypto:  Python cryptography (Fernet / AES-128-CBC) for session token encryption
  Scheduling:   Vercel Cron (POST /api/cron/refresh-lists, every 3 hours)

2.2 Scraper Backends

  SCRAPER_BACKEND=http   HttpLetterboxdScraper  (production default)
  SCRAPER_BACKEND=mock   MockLetterboxdScraper  (development / testing)

Both backends implement the Scraper protocol defined in src/api/providers/letterboxd.py.

2.3 Store Backends

  Supabase configured   SupabaseStore   all persistent data in PostgreSQL tables
  Supabase absent       InMemoryStore   in-process dicts + sets; wiped on restart

Selection is automatic at startup: if SUPABASE_URL and SUPABASE_ANON_KEY are set, SupabaseStore is used.

2.4 Request Flow

  User loads app → POST /auth/session → encrypted token stored in localStorage
  User selects list → GET /lists/catalog → list metadata from store
  User loads deck  → POST /ingest/start → background thread scrapes user history
                   → poll GET /ingest/progress until 100%
                   → GET /lists/{list_id}/deck → weighted-shuffled movie cards
  User swipes      → POST /actions/swipe → persist action; dismiss adds 24h suppression

---

3. API Contract

All endpoints are hosted at the root path. Auth-guarded endpoints require the X-Session-Token header.

3.1 System

  GET  /health                      Liveness check; reports store type
  GET  /                            Serves src/web/index.html
  GET  /web/{path}                  Serves static frontend assets (path-traversal guarded)

3.2 Authentication

  POST /auth/session
    Body:     { "username": str, "session_cookie": str }
    Response: { "status": "ok", "encrypted_session_cookie": str }
    Behaviour: validates session_cookie against letterboxd.com/settings/; encrypts
              JSON payload {"u": username, "c": session_cookie} with Fernet using
              MASTER_ENCRYPTION_KEY; returns encrypted token.

3.3 Ingest

  POST /ingest/start                [auth required]
    Body:     { "user_id": str, "source": str, "depth_pages": int (1–50) }
    Response: { "status": "queued" | "already_running", "user_id": str }
    Behaviour: launches daemon thread; check-and-add to ingest_running is atomic
              (held under store.lock). Identity binding: user_id must match the
              username in the decrypted session token.

  GET  /ingest/progress?user_id=    Returns { progress: int (-1=error, 0–100) }

3.4 Discovery

  GET  /discovery/profiles          Lists available deck filter profiles
  GET  /discovery/deck?user_id=&profile=   Returns up to 20 weighted-shuffled movies
  GET  /discovery/details?slug=     Returns synopsis, cast, genres for one movie

3.5 Lists

  GET  /lists/catalog?q=&page=
    Behaviour: attempts fresh scrape of letterboxd.com/lists/popular/; on rate-limit
              or error falls back to store.get_lists(); applies q filter post-fetch;
              sorts official-first then by like_count desc.
    Response: { "status": "ok", "query": str, "page": int, "results": [LetterboxdListSummary] }

  GET  /lists/{list_id}
    Behaviour: fetches film roster via HTTP scraper (requires summary.url); stores
              memberships; returns summary + movie_slugs + 4-movie preview.

  GET  /lists/{list_id}/deck?user_id=
    Behaviour: fetches film roster; backfills missing movie metadata; returns up to
              20 weighted-shuffled movies.

  POST /lists/refresh               [auth required]
    Rate limit: 1 request per 5 minutes per user (store.allow_scrape_request)
    Response:  { "status": "ok", "fetched": int, "updated": int }

3.6 Actions

  POST /actions/swipe               [auth required]
    Body:     { "user_id": str, "movie_slug": str, "action": "watchlist"|"dismiss"|"log" }
    Rate limit: 500 ms sync lock per user (store.should_rate_limit)

3.7 Database

  POST /db/migrate                  Development only (403 in production)

3.8 Cron (internal)

  POST /api/cron/refresh-lists      Protected by X-Cron-Secret header (VERCEL_CRON_SECRET)
  GET  /api/cron/health

---

4. Data Models

4.1 LetterboxdListSummary (scraper output / list_summaries table)

  list_id       TEXT PK   "{owner_slug}-{list_slug}" derived from href path
  slug          TEXT      list slug component
  url           TEXT      canonical letterboxd.com URL (required for deck fetching)
  title         TEXT
  owner_name    TEXT
  owner_slug    TEXT
  description   TEXT
  film_count    INTEGER
  like_count    INTEGER
  comment_count INTEGER
  is_official   BOOLEAN   true if owner_slug in {"letterboxd", "official"}
  tags          JSONB

4.2 list_memberships table

  id         BIGSERIAL PK
  list_id    TEXT FK → list_summaries(list_id) ON DELETE CASCADE
  movie_slug TEXT
  position   INTEGER
  UNIQUE(list_id, movie_slug)
  INDEX on (list_id, position)

4.3 LetterboxdMovie (scraper output / movies table)

  slug, title, poster_url, rating (float), popularity (int),
  genres (JSONB array), synopsis, cast (JSONB array), year?, director?

4.4 Session Token Format

  Fernet( json.dumps({"u": username, "c": session_cookie}) )
  Old format (raw cookie string) is accepted for backward compatibility; identity
  binding is skipped when the decrypted payload is not valid JSON.

---

5. Security Model

5.1 Session token encryption
  MASTER_ENCRYPTION_KEY (env) is required to encrypt and decrypt all session tokens.
  Fernet provides authenticated encryption; tampering is detected.

5.2 Identity binding
  POST /ingest/start and POST /actions/swipe extract the username from the decrypted
  session token and reject requests where payload.user_id differs (HTTP 403).
  Guard is bypassed for old-format tokens (empty username) for backward compat.

5.3 Cron endpoint
  POST /api/cron/refresh-lists requires X-Cron-Secret: <VERCEL_CRON_SECRET>.
  If VERCEL_CRON_SECRET is unset the endpoint rejects all requests (fail-closed).
  Set VERCEL_CRON_SECRET in all environments, including local dev, to enable the endpoint.

5.4 Path traversal guard
  GET /web/{path} resolves the path and checks it remains within _WEB_DIR before serving.

5.5 Ingest atomicity
  The ingest_running check-and-add is wrapped in store.lock to prevent double-ingest
  under concurrent requests.

5.6 Dead infrastructure
  auth.py, auth_deps.py, rate_limiter.py, qstash_queue.py are implemented but not
  imported. Each file carries a tombstone comment. Do not assume JWT auth or Redis
  rate limiting is active.

---

6. Non-Functional Requirements

6.1 Serverless constraints
  Background ingest uses daemon threads (threading.Thread). This works on long-running
  uvicorn processes but will be interrupted on Vercel serverless cold starts. The cron
  refresh endpoint is the reliable path for list data freshness.

6.2 Rate limiting
  Swipe actions:     500 ms minimum interval per user (in-memory, resets on restart)
  Manual refresh:    300 s minimum interval per user (in-memory)
  Ingest:            1 s minimum interval per user (in-memory)

6.3 Deck ordering
  weighted_shuffle: top 8 movies by summed genre preference score, then random tail.
  Genre weights accumulate per watchlist swipe and are persisted to Supabase in production.

6.4 Frontend suppression
  Dismiss swipes suppress the movie for 24 hours in the client-side Map (not persisted
  across page reloads). Suppressed movies are filtered from deck results before rendering.

6.5 List catalog sorting
  Official lists first, then descending like_count, then alphabetical title.
