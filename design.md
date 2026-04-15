# Implementation Design Document

## Objective
Define the technical architecture and implementation strategy to remediate critical security vulnerabilities, complete missing integrations, and align the codebase with the documented architecture from the PRD.

---

## Current State Assessment

### Architecture Drift
| Documented Stack | Actual Implementation | Gap Severity |
|------------------|----------------------|--------------|
| Next.js + React | Vanilla JS + Static HTML | Medium |
| Zustand state management | Plain JS module `state.js` | Medium |
| Supabase PostgreSQL | InMemoryStore (ephemeral) | Critical |
| Upstash Redis | No Redis integration | High |
| QStash async queue | InMemoryQueue stub | High |
| Real Letterboxd scraper | Login implemented, pull methods empty | High |

### Data Flow Reality
```
Current (In-Memory Only):
Browser → FastAPI → InMemoryStore ← MockLetterboxdScraper
                      ↑
                   (State lost on restart)

Intended (Persistent):
Browser → FastAPI → Supabase ← RealHttpLetterboxdScraper
                    ↓           ↓
                Redis ←── QStash (async jobs)
```

---

## Phase 0: Security Remediation (CRITICAL - IMMEDIATE)

### 0.1 Credential Sanitization
**Priority**: P0 - Blocker for all other work
**Estimated**: 30 minutes

**Changes:**
1. Delete `.env` from git history using `git filter-branch` or BFG
2. Add `.env` to `.gitignore` if not already present
3. Rotate all exposed credentials:
   - Supabase `SERVICE_ROLE_KEY` (regenerate in Supabase dashboard)
   - Letterboxd password (change account password)
   - Upstash tokens (regenerate in Upstash dashboard)
   - QStash signing keys (rotate in QStash console)
4. Create `.env.local` for local development (never commit)

**Verification:**
```bash
git log --all --full-history -- .env | grep "commit"  # Should return nothing
git ls-files | grep ".env"  # Should not list .env
git status --ignored | grep ".env"  # Should show .env in ignored files
```

**Impact:** Prevents credential exposure to anyone with repository access.

---

## Phase 1: Data Layer Implementation (HIGH PRIORITY)

### 1.1 Supabase Client Setup
**File**: `src/api/database.py` (new)
**Dependencies**: `supabase>=2.0.0`
**Estimated**: 2 hours

**Interface:**
```python
from supabase import create_client, Client
from functools import lru_cache

@lru_cache
def get_supabase_client() -> Client:
    return create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_ANON_KEY")
    )
```

### 1.2 Database Schema & Migrations
**Files**: `db/migrations/001_initial_schema.sql`
**Estimated**: 2 hours

**Schema:**
```sql
-- user_exclusions table
CREATE TABLE IF NOT EXISTS user_exclusions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    movie_slug TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, movie_slug)
);

CREATE INDEX idx_user_exclusions_user_id ON user_exclusions(user_id);

-- movies table (cache-aside)
CREATE TABLE IF NOT EXISTS movies (
    slug TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    poster_url TEXT NOT NULL,
    rating FLOAT NOT NULL,
    popularity INTEGER NOT NULL,
    genres JSONB NOT NULL,
    synopsis TEXT,
    cast JSONB,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_movies_rating ON movies(rating);
CREATE INDEX idx_movies_popularity ON movies(popularity);

-- user_actions table (audit log)
CREATE TABLE IF NOT EXISTS user_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    movie_slug TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('watchlist', 'dismiss', 'log')),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_user_actions_user_id ON user_actions(user_id, created_at DESC);
```

### 1.3 Row Level Security Policies
**File**: `db/migrations/002_rls_policies.sql`
**Estimated**: 1 hour

**Policies:**
```sql
-- Enable RLS
ALTER TABLE user_exclusions ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_actions ENABLE ROW LEVEL SECURITY;

-- User can only access their own exclusions
CREATE POLICY user_own_exclusions ON user_exclusions
    FOR ALL USING (user_id = auth.uid()::TEXT);

-- User can only access their own actions
CREATE POLICY user_own_actions ON user_actions
    FOR ALL USING (user_id = auth.uid()::TEXT);
```

### 1.4 Replace InMemoryStore with SupabaseStore
**File**: `src/api/store.py` (refactor)
**Strategy**: Abstract `Store` Protocol, implement `InMemoryStore` and `SupabaseStore`
**Estimated**: 4 hours

**Interface:**
```python
from typing import Protocol

class Store(Protocol):
    def add_exclusion(self, user_id: str, slug: str) -> None: ...
    def get_exclusions(self, user_id: str) -> set[str]: ...
    def upsert_movie(self, movie: dict) -> None: ...
    def get_movie(self, slug: str) -> dict | None: ...
    def get_movies(self) -> list[dict]: ...
    # ... other methods

class SupabaseStore:
    def __init__(self):
        self.client = get_supabase_client()

    def add_exclusion(self, user_id: str, slug: str) -> None:
        self.client.table("user_exclusions").insert({
            "user_id": user_id,
            "movie_slug": slug
        }).execute()

    # ... implementation of other methods
```

**Backward Compatibility:** Keep `InMemoryStore` for tests, use `SupabaseStore` when `SUPABASE_URL` is set.

---

## Phase 2: Queue & Cache Integration (HIGH PRIORITY)

### 2.1 Upstash Redis for Rate Limiting
**File**: `src/api/rate_limiter.py` (new)
**Dependencies**: `redis>=5.0.0`, `upstash>=1.0.0` (optional, use redis-py)
**Estimated**: 2 hours

**Interface:**
```python
import os
import time
import redis

class RedisRateLimiter:
    def __init__(self):
        self.redis = redis.Redis(
            host=os.getenv("UPSTASH_REDIS_HOST"),
            port=os.getenv("UPSTASH_REDIS_PORT", "6379"),
            password=os.getenv("UPSTASH_REDIS_REST_TOKEN"),
            ssl=True
        )

    def should_rate_limit(self, user_id: str, key: str, window_seconds: int, max_requests: int) -> tuple[bool, float]:
        pipe = self.redis.pipeline()
        now = time.time()
        window_start = now - window_seconds

        pipe.delete(f"rate:{key}:{user_id}:old")
        pipe.zremrangebyscore(f"rate:{key}:{user_id}", 0, window_start)
        pipe.zadd(f"rate:{key}:{user_id}", {str(now): now})
        pipe.zcard(f"rate:{key}:{user_id}")
        pipe.expire(f"rate:{key}:{user_id}", window_seconds)

        results = pipe.execute()
        count = results[3]

        if count >= max_requests:
            ttl = self.redis.ttl(f"rate:{key}:{user_id}")
            return True, ttl

        return False, 0.0
```

### 2.2 QStash for Background Ingestion
**File**: `src/api/qstash_queue.py` (new)
**Dependencies**: `qstash>=0.5.0` or `requests` with direct API calls
**Estimated**: 2 hours

**Interface:**
```python
import os
import hmac
import hashlib
import base64
import requests

class QStashQueue:
    def __init__(self):
        self.url = os.getenv("QSTASH_URL")
        self.token = os.getenv("QSTASH_TOKEN")
        self.current_key = os.getenv("QSTASH_CURRENT_SIGNING_KEY")

    def enqueue(self, topic: str, payload: dict) -> str:
        body = json.dumps({"topic": topic, "payload": payload})

        # Sign request
        signature = hmac.new(
            self.current_key.encode(),
            body.encode(),
            hashlib.sha256
        ).digest()

        response = requests.post(
            f"{self.url}/v2/publish/{topic}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Upstash-Signature": f"v1,{base64.b64encode(signature).decode()}",
                "Content-Type": "application/json"
            },
            data=body
        )

        response.raise_for_status()
        return response.json()["messageId"]
```

### 2.3 Update Ingest Endpoint
**File**: `src/api/app.py` (modify)
**Estimated**: 1 hour

**Changes:**
1. Replace `threading.Thread` with `QStashQueue.enqueue()`
2. Remove `_simulate_ingest()` daemon thread
3. Create `/webhooks/ingest` endpoint for QStash callback

---

## Phase 3: Scraper Completion (HIGH PRIORITY)

### 3.1 Implement Watchlist Scraping
**File**: `src/api/providers/letterboxd.py`
**Estimated**: 3 hours

**Implementation:**
```python
def pull_watchlist_slugs(self, session_cookie: str) -> set[str]:
    with httpx.Client(
        cookies={"letterboxd.session": session_cookie},
        timeout=self.timeout_seconds
    ) as client:
        page = 1
        slugs = set()

        while True:
            response = client.get(
                f"{self.base_url}/watchlist/",
                params={"page": page}
            )

            soup = BeautifulSoup(response.text, "html.parser")
            film_links = soup.select("li poster-container a")

            if not film_links:
                break

            for link in film_links:
                href = link.get("href", "")
                if href.startswith("/film/"):
                    slug = href.split("/")[2]
                    slugs.add(slug)

            page += 1

        return slugs
```

### 3.2 Implement Diary Scraping
**Similar to watchlist scraping**
**Estimated**: 2 hours

### 3.3 Implement Source Discovery Scraping
**Estimated**: 3 hours

**Implementation:**
```python
def pull_source_slugs(self, source: str, depth_pages: int = 2) -> list[str]:
    url_map = {
        "trending": "/films/trending/",
        "popular": "/films/popular/",
        "recommended": "/films/reception/recommended/"
    }

    if source not in url_map:
        raise ValueError(f"Unknown source: {source}")

    slugs = []

    for page in range(1, depth_pages + 1):
        try:
            response = self._http_client.get(
                f"{self.base_url}{url_map[source]}",
                params={"page": page}
            )

            # Check for rate limiting
            if response.status_code in {403, 429}:
                if should_trigger_proxy_fallback(response.status_code):
                    # TODO: Implement rotating proxy fallback
                    raise RuntimeError("rate_limit_require_proxy")

            soup = BeautifulSoup(response.text, "html.parser")
            film_links = soup.select("li poster-container a")

            for link in film_links:
                href = link.get("href", "")
                if href.startswith("/film/"):
                    slug = href.split("/")[2]
                    if slug not in slugs:
                        slugs.append(slug)

        except httpx.TimeoutException:
            # Apply exponential backoff
            sleep(exponential_backoff_seconds(page))

    return slugs
```

### 3.4 Implement Proxy Fallback
**File**: `src/api/providers/letterboxd.py` (extend)
**Dependencies**: `requests` with proxy support
**Estimated**: 2 hours

**Implementation:**
```python
def _get_proxy_url(self) -> str | None:
    proxy_endpoint = os.getenv("ROTATING_PROXY_ENDPOINT")
    proxy_key = os.getenv("ROTATING_PROXY_API_KEY")

    if not proxy_endpoint or not proxy_key:
        return None

    response = requests.get(
        proxy_endpoint,
        headers={"X-API-Key": proxy_key},
        timeout=5
    )

    return response.json().get("proxy_url")

def pull_source_slugs(self, source: str, depth_pages: int = 2) -> list[str]:
    # ... existing code ...

    if should_trigger_proxy_fallback(response.status_code):
        proxy_url = self._get_proxy_url()
        if proxy_url:
            # Retry with proxy
            response = self._http_client.get(
                f"{self.base_url}{url_map[source]}",
                params={"page": page},
                proxies={"http://": proxy_url, "https://": proxy_url}
            )
        else:
            raise RuntimeError("rate_limit_no_proxy_available")
```

---

## Phase 4: Frontend Evolution (MEDIUM PRIORITY)

### 4.1 Add TypeScript Migration
**Estimated**: 6 hours

**Changes:**
1. Add `tsconfig.json`: strict mode, ES2022 target
2. Rename `src/web/app.js` → `src/web/app.ts`
3. Rename `src/web/state.js` → `src/web/state.ts`
4. Add type annotations to all functions
5. Update `vercel.json` build process (if using Next.js)

**Note:** This is optional for the baseline; can be deferred if schedule constrained.

### 4.2 Add Linting
**File**: `.eslintrc.json`, `package.json` added scripts
**Estimated**: 1 hour

**Configuration:**
```json
{
  "extends": ["eslint:recommended"],
  "env": {
    "browser": true,
    "es2022": true
  },
  "parserOptions": {
    "ecmaVersion": "latest",
    "sourceType": "module"
  }
}
```

### 4.3 PWA Enhancement (Optional)
**File**: `src/web/manifest.json`, `src/web/sw.js`
**Estimated**: 4 hours

**Features:**
- App installation (manifest)
- Offline caching (service worker)
- Network-first image caching

**Note:** Defer this to a future release if time-constrained.

---

## Phase 5: Logic & Concurrency Fixes (HIGH PRIORITY)

### 5.1 Fix Race Condition in weighted_shuffle()
**File**: `src/api/store.py`
**Estimated**: 30 minutes

**Fix:**
```python
def weighted_shuffle(self, user_id: str, movies: list[dict]) -> list[dict]:
    with self.lock:
        weights = self.genre_weights.get(user_id, {})

    # Release lock before mutation - this is intentional
    if not weights:
        # Critical section: must protect shuffle
        with self.lock:
            random.shuffle(movies)
            return movies

    def score(movie: dict) -> int:
        return sum(weights.get(g, 0) for g in movie.get("genres", []))

    # Critical section: protect sort and slice
    with self.lock:
        boosted = sorted(movies, key=score, reverse=True)
        head = boosted[:8]
        tail = boosted[8:]

    # Release lock before shuffle
    random.shuffle(tail)
    return head + tail
```

### 5.2 Add Error Handling to _simulate_ingest()
**File**: `src/api/app.py`
**Estimated**: 1 hour

**Fix:**
```python
def _simulate_ingest(user_id: str, source: str, depth_pages: int) -> None:
    try:
        store.set_ingest_progress(user_id, 5)
        for value in [20, 35, 50, 70]:
            time.sleep(0.1)
            store.set_ingest_progress(user_id, value)

        _filter_first_pipeline(user_id=user_id, source=source, depth_pages=depth_pages)
        store.set_ingest_progress(user_id, 100)

    except Exception as exc:
        # Log error and mark as failed
        logger.error(f"Ingest failed for user {user_id}: {exc}")
        store.set_ingest_progress(user_id, -1)  # -1 indicates error

    finally:
        store.ingest_running.discard(user_id)
```

### 5.3 Implement Cleanup Policies
**File**: `src/api/store.py`
**Estimated**: 1 hour

**Implementation:**
```python
def cleanup_expires_progress(self, ttl_seconds: int = 3600) -> int:
    """Remove ingest progress entries older than TTL."""
    cutoff = time.time() - ttl_seconds
    removed = 0

    with self.lock:
        to_remove = [
            user_id for user_id, last_updated
            in self.ingest_progress.items()
            if last_updated < cutoff
        ]

        for user_id in to_remove:
            del self.ingest_progress[user_id]
            removed += 1

    return removed

def archive_old_actions(self, keep_days: int = 7) -> int:
    """Archive or remove old actions to prevent unbounded growth."""
    cutoff = time.time() - (keep_days * 86400)

    with self.lock:
        # Filter out old actions
        self.actions = [
            action for action in self.actions
            if action.get("timestamp", 0) >= cutoff
        ]

    return len(self.actions)
```

---

## Phase 6: Testing & Quality Assurance (MEDIUM PRIORITY)

### 6.1 Add Integration Tests
**File**: `tests/integration/test_supabase_store.py` (new)
**Estimated**: 3 hours

**Coverage:**
- SupabaseStore CRUD operations
- End-to-end ingest flow with real Supabase
- Rate limiting with Redis

### 6.2 Add E2E Tests
**File**: `tests/e2e/test_swipe_flow.spec.ts` (new)
**Dependencies**: `@playwright/test`
**Estimated**: 4 hours

**Scenarios:**
- Load deck → swipe right → verify in watchlist
- Load deck → swipe left → verify in exclusions
- Rate limiting enforcement
- Progress indicator accuracy

### 6.3 Add Property-Based Tests
**File**: `tests/test_store_properties.py` (new)
**Dependencies**: `hypothesis>=6.0.0`
**Estimated**: 2 hours

**Properties:**
- `weighted_shuffle()` returns same number of items
- `add_exclusion()` is idempotent
- `get_exclusions()` returns set (unique items)

---

## Dependency Compatibility Matrix

| Component | Current Version | Target Version | Conflict Risk | Action |
|-----------|----------------|---------------|---------------|--------|
| Python | 3.11+ | 3.11+ | None | Keep |
| FastAPI | 0.135.3+ | 0.135.3+ | None | Keep |
| Supabase JS | Not installed | 2.x | None | Add |
| Supabase Python | Not installed | 2.x | None | Add |
| Redis | Not installed | 5.x | None | Add |
| QStash | Not installed | 0.5.x | None | Add |
| requests | Not installed | 2.31+ | None | Add |
| httpx | 0.28.1+ | 0.28.1+ | None | Keep |

**Verification Command:**
```bash
pip check  # Verify no dependency conflicts
```

---

## Deployment Strategy

### Vercel Configuration
**File**: `vercel.json`

**Current Routes:**
```json
{
  "functions": {
    "api/index.py": {
      "runtime": "python3.11"
    }
  },
  "routes": [
    { "src": "/(.*)", "dest": "/api/index.py" }
  ]
}
```

**Additions Needed:**
- Environment variables in Vercel dashboard
- Build command: `echo "No build step required"`
- Output directory: `.` (root)

### Environment Variables Checklist
- `SCRAPER_BACKEND` (mock/http)
- `MASTER_ENCRYPTION_KEY` (generate new)
- `TARGET_PLATFORM_BASE_URL`
- `TARGET_PLATFORM_TIMEOUT_SECONDS`
- `SUPABASE_URL` (rotate)
- `SUPABASE_ANON_KEY` (rotate)
- `SUPABASE_SERVICE_ROLE_KEY` (rotate) — DO NOT commit
- `UPSTASH_REDIS_HOST` (extract from REST URL)
- `UPSTASH_REDIS_PORT` (usually 6379)
- `UPSTASH_REDIS_REST_TOKEN` (rotate)
- `QSTASH_URL`
- `QSTASH_TOKEN` (rotate)
- `QSTASH_CURRENT_SIGNING_KEY`
- `QSTASH_NEXT_SIGNING_KEY`
- `ROTATING_PROXY_ENDPOINT` (optional)
- `ROTATING_PROXY_API_KEY` (optional)

---

## Rollback Plan

If critical issues arise during deployment:

1. **Immediate**: Revert Vercel deployment to previous version
2. **Database**: Supabase changes are additive (CREATE TABLE IF NOT EXISTS), safe
3. **Redis/Queue**: If QStash integration fails, revert to `InMemoryQueue` by setting `SCRAPER_BACKEND=mock`
4. **Rollback Command:**
   ```bash
   vercel rollback --token <VERCEL_TOKEN> --scope <PROJECT_ID>
   ```

---

## Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Security vulnerabilities | 0 critical | `trufflehog` scan clean |
| Integration coverage | >80% | `pytest coverage` |
| E2E test pass rate | 100% | `playwright test` |
| API response time (p95) | <500ms | Vercel logs |
| Cache hit rate | >70% (after 1 month) | Supabase query stats |
| Rate limit blocks | <1% | Vercel error logs |

---

## State Summary (Pre-Phase 3)

This document provides the complete implementation roadmap. All artifacts are ready for surgical execution in Phase 3. Phase 0 (security remediation) must be completed immediately before any other implementation work begins.
