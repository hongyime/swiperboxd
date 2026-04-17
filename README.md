# swiperboxd

Swipe-based movie discovery backed by Letterboxd lists. Authenticates with a Letterboxd session cookie, syncs the user's watch history, then presents unseen films from curated lists as swipeable cards.

---

## Prerequisites

- Python 3.11+
- Node.js 18+ (for frontend tests only)
- A Letterboxd account with a valid session cookie

---

## Environment Variables

Copy `.env.template` to `.env` and populate:

| Variable | Required | Description |
| --- | --- | --- |
| `MASTER_ENCRYPTION_KEY` | Yes | Fernet key for session token encryption. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `SUPABASE_URL` | Production | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Production | Supabase service role key — bypasses RLS, required for server-side writes |
| `SUPABASE_ANON_KEY` | Optional | Supabase anon key — fallback when service role key is absent (local dev only) |
| `SCRAPER_BACKEND` | No | `http` (default) or `mock` (dev/test) |
| `APP_ENV` | No | `development` (default) or `production` |
| `TARGET_PLATFORM_BASE_URL` | No | Override scrape target, default `https://letterboxd.com` |
| `VERCEL_CRON_SECRET` | Production | Shared secret for `X-Cron-Secret` header on cron endpoints |

Without `SUPABASE_URL` the app falls back to `InMemoryStore` (data is wiped on restart). The backend must use `SUPABASE_SERVICE_ROLE_KEY` — the anon key is blocked by Row Level Security on writes.

---

## Local Setup

```bash
pip install -e ".[dev]"
```

Run the API server:

```bash
uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload
```

Open `http://localhost:8000` — the frontend is served directly by FastAPI from `src/web/`.

---

## Authentication

The app does not accept a username/password. Obtain your Letterboxd session cookie from your browser:

1. Log in to letterboxd.com
2. Open DevTools → Application → Cookies
3. Copy the value of `letterboxd.user.CURRENT`

Enter your username and that cookie value in the login form.

---

## Database Migrations

With Supabase configured, run migrations in development:

```bash
curl -X POST http://localhost:8000/db/migrate
```

Migrations run in order from `db/migrations/001_movies.sql` through `007_lists.sql`. Files prefixed with `LEGACY_` are skipped.

---

## Testing

Python tests:

```bash
pytest tests/ -q
```

JavaScript tests:

```bash
npm run test:web
```

Run a single Python test file:

```bash
pytest tests/test_api.py -q
```

Supabase integration tests are skipped automatically when `SUPABASE_ANON_KEY` is unset.

---

## Deployment (Vercel)

1. Set all production environment variables in the Vercel dashboard.
2. Set `APP_ENV=production`.
3. Add a Vercel Cron job: `POST /api/cron/refresh-lists` on your desired schedule (e.g. every 3 hours). Set `VERCEL_CRON_SECRET` and configure the cron to send `X-Cron-Secret: <secret>`.

The `POST /db/migrate` endpoint is blocked in production (`APP_ENV=production`). Run migrations locally against the production Supabase URL before deploying schema changes.

Note: background ingest threads (`POST /ingest/start`) run as daemon threads. On Vercel serverless, threads may be killed between requests. For reliable list catalog freshness, the cron job is the primary refresh path.

---

## Project Structure

```text
src/
  api/
    app.py              FastAPI application, all route handlers
    cron.py             Vercel cron endpoint (/api/cron/)
    store.py            Store protocol, InMemoryStore, SupabaseStore
    database.py         Supabase client + migration runner
    security.py         Fernet encrypt/decrypt helpers
    providers/
      letterboxd.py     Scraper protocol, MockLetterboxdScraper, HttpLetterboxdScraper
  web/
    index.html          Single-page app shell
    app.js              All frontend logic (vanilla JS, ES modules)
    state.js            Suppression store and ingest polling state
db/
  migrations/           SQL migration files (001–007)
tests/                  pytest suite
```
