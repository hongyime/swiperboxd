# Movie Discovery Platform

A serverless movie discovery application that transforms Letterboxd community lists into an interactive, swipe-based interface. Built with FastAPI, deployed on Vercel, with a Chrome extension for browser-based sync.

---

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Environment Configuration](#environment-configuration)
- [Installation & Setup](#installation--setup)
- [Usage](#usage)
- [Testing](#testing)
- [Deployment](#deployment)
- [Chrome Extension](#chrome-extension)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)

---

## Overview

This application provides a Tinder-like swipe interface for discovering movies from Letterboxd lists. Key features:

- **List-Based Discovery:** Browse movies from Letterboxd community and official lists
- **Smart Filtering:** Automatically excludes movies you've already watched or added to your watchlist
- **Genre Learning:** Learns your preferences and prioritizes similar movies
- **Browser-Based Sync:** Chrome extension syncs your Letterboxd history directly from your browser
- **Serverless Architecture:** Deployed on Vercel with scheduled cron jobs for data freshness

**Tech Stack:**
- Backend: FastAPI (Python 3.11+)
- Frontend: Vanilla JavaScript (ES modules)
- Database: Supabase (PostgreSQL)
- Deployment: Vercel Serverless
- Extension: Chrome Manifest V3

---

## Prerequisites

### Required

- **Python:** 3.11 or higher
- **pip:** Latest version
- **Letterboxd Account:** With a valid session cookie

### Optional (for development)

- **Node.js:** 18+ (for frontend tests only)
- **Supabase Account:** For production database (falls back to in-memory store without it)
- **Chrome Browser:** For extension development/testing

---

## Environment Configuration

Copy `.env.template` to `.env` and configure the following variables:

### Required Variables

| Variable | Description | How to Generate |
|----------|-------------|-----------------|
| `MASTER_ENCRYPTION_KEY` | Fernet key for encrypting session tokens | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

### Production Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SUPABASE_URL` | Yes | Your Supabase project URL (e.g., `https://xxx.supabase.co`) |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Supabase service role key (bypasses RLS for server-side writes) |
| `SUPABASE_ANON_KEY` | No | Supabase anon key (fallback for local dev, not recommended for production) |
| `VERCEL_CRON_SECRET` | Yes | Shared secret for protecting cron endpoints (generate a random string) |

### Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SCRAPER_BACKEND` | `http` | Set to `mock` for development/testing (uses mock data) |
| `APP_ENV` | `development` | Set to `production` to block migration endpoint |
| `TARGET_PLATFORM_BASE_URL` | `https://letterboxd.com` | Override Letterboxd base URL (for testing) |
| `TARGET_PLATFORM_TIMEOUT_SECONDS` | `20.0` | HTTP timeout for scraping requests |
| `EXTENSION_API_KEY` | - | API key for extension auth (alternative to session tokens) |

### Example `.env` File

```bash
# Required
MASTER_ENCRYPTION_KEY=your-fernet-key-here

# Production (Supabase)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
VERCEL_CRON_SECRET=your-random-secret-string

# Optional
SCRAPER_BACKEND=http
APP_ENV=development
```

**Important Notes:**
- Without `SUPABASE_URL`, the app uses an in-memory store (data is wiped on restart)
- The backend **must** use `SUPABASE_SERVICE_ROLE_KEY` — the anon key is blocked by Row Level Security on writes
- Generate `MASTER_ENCRYPTION_KEY` using the command above; never commit it to version control

---

## Installation & Setup

### 1. Clone the Repository

```bash
git clone <repository-url>
cd <repository-name>
```

### 2. Create Virtual Environment

```bash
python -m venv .venv
```

### 3. Activate Virtual Environment

**Linux/macOS:**
```bash
source .venv/bin/activate
```

**Windows (PowerShell):**
```powershell
.venv\Scripts\Activate.ps1
```

**Windows (CMD):**
```cmd
.venv\Scripts\activate.bat
```

### 4. Install Dependencies

```bash
pip install -e ".[dev]"
```

This installs:
- Core dependencies: FastAPI, uvicorn, httpx, beautifulsoup4, supabase, cryptography, etc.
- Development dependencies: pytest

### 5. Configure Environment

```bash
cp .env.template .env
# Edit .env with your configuration
```

### 6. Run Database Migrations (Optional)

If using Supabase, run migrations to set up the database schema:

```bash
# Start the development server first
uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload

# In another terminal, run migrations
curl -X POST http://localhost:8000/db/migrate \
  -H "X-Session-Token: <your-session-token>"
```

**Note:** You'll need a valid session token. Get one by logging in through the web interface first.

### 7. Start the Development Server

```bash
uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload
```

The application will be available at `http://localhost:8000`

---

## Usage

### Web Application

1. **Open the App:**
   ```
   http://localhost:8000
   ```

2. **Authenticate:**
   - Log in to [letterboxd.com](https://letterboxd.com) in your browser
   - Open DevTools → Application → Cookies
   - Copy the value of `letterboxd.user.CURRENT`
   - Enter your Letterboxd username and paste the cookie value in the login form

3. **Browse Lists:**
   - Click the list dropdown to see available Letterboxd lists
   - Search for specific lists using the search bar
   - Select a list to load movies

4. **Swipe Movies:**
   - **Swipe Right / →:** Add to watchlist
   - **Swipe Left / ←:** Dismiss (24h suppression)
   - **Swipe Up / ↑:** Log as watched
   - **Space:** Flip card to see details

### API Endpoints

**Health Check:**
```bash
curl http://localhost:8000/health
```

**List Catalog:**
```bash
curl http://localhost:8000/lists/catalog
```

**Get List Details:**
```bash
curl http://localhost:8000/lists/official-best-picture
```

**Get Deck for List:**
```bash
curl "http://localhost:8000/lists/official-best-picture/deck?user_id=your-username"
```

**Submit Swipe Action:**
```bash
curl -X POST http://localhost:8000/actions/swipe \
  -H "Content-Type: application/json" \
  -H "X-Session-Token: your-encrypted-token" \
  -d '{"user_id": "your-username", "movie_slug": "the-shawshank-redemption", "action": "watchlist"}'
```

### Using Mock Data (Development)

Set `SCRAPER_BACKEND=mock` in `.env` to use mock data instead of scraping Letterboxd:

```bash
SCRAPER_BACKEND=mock
```

This is useful for:
- Development without hitting Letterboxd's servers
- Testing without a Letterboxd account
- Running tests in CI/CD pipelines

---

## Testing

### Run All Tests

```bash
# Python tests
pytest tests/ -q

# JavaScript tests (requires Node.js)
npm run test:web

# Run both
npm test
```

### Run Specific Test File

```bash
pytest tests/test_api.py -q
```

### Run Tests with Coverage

```bash
pytest tests/ --cov=src --cov-report=html
```

### Test Configuration

- **Supabase Integration Tests:** Automatically skipped when `SUPABASE_URL` is not set
- **Mock Scraper:** Tests use `SCRAPER_BACKEND=mock` by default
- **In-Memory Store:** Tests use `InMemoryStore` to avoid database dependencies

---

## Deployment

### Vercel Deployment

1. **Install Vercel CLI:**
   ```bash
   npm install -g vercel
   ```

2. **Set Environment Variables:**
   - Go to your Vercel project dashboard
   - Navigate to Settings → Environment Variables
   - Add all production variables (see [Environment Configuration](#environment-configuration))
   - Set `APP_ENV=production`

3. **Configure Cron Jobs:**
   - Vercel automatically reads `vercel.json` for cron configuration
   - Ensure `VERCEL_CRON_SECRET` is set in environment variables
   - Cron jobs will run at:
     - **02:00 UTC:** Refresh list catalog
     - **04:00 UTC:** Sync all users' watchlist/diary
     - **03:30 UTC:** Backfill missing metadata

4. **Deploy:**
   ```bash
   vercel --prod
   ```

### Database Migrations (Production)

**Important:** The `POST /db/migrate` endpoint is blocked in production (`APP_ENV=production`).

Run migrations locally against the production Supabase URL **before** deploying schema changes:

```bash
# Set production Supabase credentials in .env
SUPABASE_URL=https://your-prod-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-prod-service-role-key

# Run migrations locally
python -c "from src.api.database import run_migrations; run_migrations()"
```

---

## Chrome Extension

### Installation (Developer Mode)

1. **Navigate to Extension Directory:**
   ```bash
   cd extension/
   ```

2. **Load in Chrome:**
   - Open `chrome://extensions`
   - Enable **Developer mode** (top-right toggle)
   - Click **Load unpacked**
   - Select the `extension/` directory

3. **Pin to Toolbar:**
   - Click the puzzle icon in Chrome toolbar
   - Pin "Swiperboxd Sync" for easy access

### Configuration

The extension can auto-configure or be manually configured:

**Auto-Configuration:**
1. Sign in to your Swiperboxd web app
2. The extension automatically detects your credentials via `postMessage`

**Manual Configuration:**
1. Click the extension icon
2. Enter:
   - API Base URL (e.g., `https://your-app.vercel.app`)
   - Your Letterboxd username
   - Your session token (from the web app)
3. Click **Save credentials**

### Usage

1. **Sign in to Letterboxd** in the same browser profile
2. **Open the extension popup**
3. **Click "Start sync"** to begin syncing:
   - Watchlist (all pages)
   - Diary (all pages)
   - List catalog (popular lists)
   - Movie metadata (missing films)

**Features:**
- Live progress tracking per page
- Stop/resume sync at any time
- Auto-sync every 6 hours (optional)
- Backfill missing Letterboxd Film IDs

### Extension Architecture

- **Service Worker:** Scrapes Letterboxd using your browser's session cookie
- **Content Script:** Forwards credentials from web app to extension
- **Batch Upload:** Pushes data to API in batches of 50 items
- **Retry Logic:** Exponential backoff on failures

---

## Production Readiness Checklist

This section covers what's been implemented for production stability:

### ✅ Error Handling & Recovery

- **Automatic Retries:** All API calls with exponential backoff (up to 3 attempts)
- **Watchlist Write Verification:** Verifies film was actually added after POST (multi-endpoint fallback)
- **Cross-Sync Retries:** Automatic retry on fetch failures with 2 attempts
- **Graceful Degradation:** App continues working even if one component fails
- **Detailed Logging:** All operations logged with request IDs for debugging

### ✅ Security

- **CORS Protection:** Configured for allowed origins only
- **Trusted Host Validation:** Rejects requests from unauthorized hosts
- **Security Headers:** Sets content type, frame, XSS protection headers
- **Input Validation:** All API inputs validated via Pydantic models
- **Session Encryption:** Fernet-based encryption for session tokens
- **HTTPS Only:** Enforced in production

### ✅ Monitoring & Diagnostics

- **Request Logging:** All API requests logged with status codes and response times
- **Error Context:** Error responses include path and error code for debugging
- **Performance Metrics:** Tracks sync phase, progress, and completion status
- **Extension Diagnostics:** Logs browser context, user agent, presence signals

### ✅ API Reliability

- **CORS Middleware:** Enables browser clients to communicate with API
- **Connection Pooling:** Reuses HTTP connections for better performance
- **Timeout Protection:** All Letterboxd fetches have 20s timeout
- **Rate Limiting Ready:** Framework supports adding rate limits per endpoint
- **Status Codes:** Proper HTTP status codes for all responses

### How to Deploy

1. **Set Environment Variables** in Vercel dashboard

   ```bash
   MASTER_ENCRYPTION_KEY=<generated-key>
   SUPABASE_URL=<your-url>
   SUPABASE_SERVICE_ROLE_KEY=<your-key>
   VERCEL_CRON_SECRET=<random-secret>
   APP_ENV=production
   ```

2. **Verify Production Settings**

   - Check that security middleware is active (HTTPS redirect enabled)
   - Check that CORS only allows production origins
   - Verify Supabase connection string

3. **Deploy**

   ```bash
   vercel deploy --prod
   ```

4. **Monitor**

   - Check Vercel logs for errors
   - Monitor API response times (target: <2s p95)
   - Track sync completion rates (target: >95%)
   - Watch for unhandled exceptions

### Known Limitations & Fixes

**Watchlist Write Verification:** The extension verifies that a film was actually added to Letterboxd after posting. This is necessary because Letterboxd sometimes accepts requests but doesn't recognize the form parameters. If verification fails after 3 endpoint attempts, the write is rejected and logged for debugging.

**Cross-Sync Timeouts:** If the extension doesn't get a response within 3 minutes, the cross-sync times out. This is by design to prevent long-running background tasks from consuming resources. The extension can be re-triggered from the popup.

**Missing Letterboxd Film IDs:** Some films may not have their Letterboxd Film ID available initially. The `backfill` cron job fills these in over time.

---

## Project Structure

```
.
├── api/
│   └── index.py                 # Vercel entry point
├── extension/
│   ├── background.js            # MV3 service worker (scraping logic)
│   ├── content.js               # Content script (credential forwarding)
│   ├── popup.html/js            # Extension UI
│   ├── manifest.json            # MV3 manifest
│   └── icons/                   # Extension icons
├── scripts/
│   ├── periodic_sync.py         # Local sync script (alternative to cron)
│   ├── seed_supabase.py         # Seed database with initial data
│   └── smoke_test_app.py        # End-to-end smoke tests
├── src/
│   ├── api/
│   │   ├── app.py               # FastAPI application (all routes)
│   │   ├── cron.py              # Cron job handlers
│   │   ├── store.py             # Store protocol + implementations
│   │   ├── database.py          # Supabase client + migrations
│   │   ├── security.py          # Fernet encryption helpers
│   │   ├── proxy_manager.py    # Proxy rotation (optional)
│   │   └── providers/
│   │       └── letterboxd.py    # Scraper implementations
│   └── web/
│       ├── index.html           # Single-page app shell
│       ├── app.js               # Frontend logic (vanilla JS)
│       ├── state.js             # Suppression store + ingest polling
│       └── styles.css           # Styles
├── tests/
│   ├── test_api.py              # API endpoint tests
│   ├── test_store.py            # Store implementation tests
│   ├── test_letterboxd_provider.py  # Scraper tests
│   └── web_state.test.js        # Frontend state tests
├── .env.template                # Environment variable template
├── pyproject.toml               # Python project configuration
├── requirements.txt             # Python dependencies (legacy)
├── package.json                 # Node.js scripts
├── vercel.json                  # Vercel deployment config
└── README.md                    # This file
```

---

## Troubleshooting

### Common Issues

#### 1. "MASTER_ENCRYPTION_KEY not set"

**Problem:** The encryption key is missing from your environment.

**Solution:**

```bash
# Generate a new key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Add to .env
echo "MASTER_ENCRYPTION_KEY=<generated-key>" >> .env
```

#### 2. "Supabase not configured" / Using InMemoryStore

**Problem:** Supabase credentials are missing or invalid.

**Solution:**

- Verify `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are set in `.env`
- Check that the URL format is correct: `https://xxx.supabase.co`
- Ensure the service role key (not anon key) is used

#### 3. Session Cookie Invalid / Expired

**Problem:** Letterboxd session cookie has expired or is invalid.

**Solution:**

1. Log out of Letterboxd and log back in
2. Get a fresh cookie from DevTools → Application → Cookies
3. Re-authenticate in the app with the new cookie

#### 4. Extension Not Syncing

**Problem:** Extension shows "Not signed in to Letterboxd" or sync fails.

**Solution:**

- Ensure you're signed in to letterboxd.com in the same browser profile
- Check that the extension has permission to access letterboxd.com
- Try manually configuring the extension with your credentials

#### 5. Vercel Deployment: Empty Watchlist/Diary

**Problem:** User history is empty after sync on Vercel.

**Solution:**

- Letterboxd blocks Vercel's AWS IP ranges with 403 errors
- **Use the Chrome extension** for reliable syncing (runs in your browser)
- Server-side sync only works on non-Vercel deployments

#### 6. Rate Limiting Errors

**Problem:** Getting 429 errors when refreshing lists or swiping.

**Solution:**

- **Swipe actions:** Wait 500ms between swipes
- **Manual refresh:** Wait 5 minutes between refresh requests
- **Ingest:** Wait 1 second between ingest requests

#### 7. Database Migration Fails

**Problem:** `POST /db/migrate` returns 403 or fails.

**Solution:**

- Ensure `APP_ENV` is not set to `production` (migrations are blocked in prod)
- Verify you have a valid session token in the `X-Session-Token` header
- Check Supabase credentials are correct

### Debug Mode

Enable verbose logging by setting the log level:

```bash
# In your terminal before starting the server
export PYTHONUNBUFFERED=1
uvicorn src.api.app:app --log-level debug
```

### Getting Help

1. Check the [PRD.md](PRD.md) for detailed technical specifications
2. Review the API contract in PRD.md Section 4
3. Check server logs for error messages (all logs use `flush=True`)
4. For extension issues, check the service worker console:
   - `chrome://extensions` → Swiperboxd Sync → "service worker" link

---

## Development Workflow

### Local Development

1. **Start the server with auto-reload:**

   ```bash
   uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload
   ```

2. **Run tests on file changes:**

   ```bash
   pytest tests/ --watch
   ```

3. **Use mock scraper for faster iteration:**

   ```bash
   SCRAPER_BACKEND=mock uvicorn src.api.app:app --reload
   ```

### Code Quality

**Linting:**

```bash
# Python
python -m compileall src

# Or use npm script
npm run lint
```

**Type Checking:**

```bash
# Install mypy
pip install mypy

# Run type checker
mypy src/
```

### Database Schema Changes

1. Create a new migration file in `db/migrations/` (if migrations directory exists)
2. Test locally against a development Supabase instance
3. Run migrations against production **before** deploying code changes
4. Never run migrations via the API in production

---

## License

[Add your license information here]

## Contributing

[Add contribution guidelines here]

## Support

[Add support contact information here]
