# Environment Secrets Guide (Vercel + Letterboxd)

Copy `.env.template` to `.env` for local runs, and copy the same keys into Vercel Project Settings → Environment Variables.

## Runtime toggles

- `SCRAPER_BACKEND`: use `mock` for local tests, `http` for live Letterboxd login flow.

## Values you can generate yourself

- `MASTER_ENCRYPTION_KEY`:
  - `python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"`
- `SESSION_TTL_SECONDS`: `86400` recommended.
- `TARGET_PLATFORM_TIMEOUT_SECONDS`: `20` recommended.

## Values you must get from providers

### Letterboxd
- `LETTERBOXD_USERNAME`
- `LETTERBOXD_PASSWORD`
- `TARGET_PLATFORM_BASE_URL` (`https://letterboxd.com`)

### Supabase
- `SUPABASE_URL` (Project Settings → API)
- `SUPABASE_ANON_KEY` (Project Settings → API)
- `SUPABASE_SERVICE_ROLE_KEY` (Project Settings → API, keep secret)
- `SUPABASE_DB_URL` (Project Settings → Database)

### Upstash (Redis + QStash)
- `UPSTASH_REDIS_REST_URL`
- `UPSTASH_REDIS_REST_TOKEN`
- `QSTASH_URL`
- `QSTASH_TOKEN`
- `QSTASH_CURRENT_SIGNING_KEY`
- `QSTASH_NEXT_SIGNING_KEY`

### Proxy provider (for 429/403 fallback)
- `ROTATING_PROXY_ENDPOINT`
- `ROTATING_PROXY_API_KEY`

## Vercel-specific

Set all keys above for at least `Preview` and `Production` environments.
