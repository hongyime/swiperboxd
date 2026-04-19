# SPEC: Metadata Sync Fix - Eliminate Placeholder Pattern

**Status:** Draft  
**Priority:** P0 (Critical UX Issue)  
**Estimated Effort:** 2-3 days  
**Target Version:** 0.7.0

---

## Problem Statement

### Current Broken Flow

```
User Syncs Watchlist (500 movies)
    ↓
Store slugs only → Create 500 placeholders
    ↓
User loads deck → Sees movies with no posters, no ratings, no genres
    ↓
Hours later: Backfill runs → Fetches metadata
    ↓
User refreshes → Now sees complete data
```

**Issues:**
1. **Poor UX:** Users see incomplete data immediately after sync
2. **Unreliable:** Backfill fails on Vercel (IP blocked by Letterboxd)
3. **Technical Debt:** Placeholder pattern creates unnecessary complexity
4. **Data Integrity:** Database contains incomplete records by design

### Root Cause

The system was designed to optimize for **sync speed** over **data completeness**:
- Storing slugs is fast (< 1 second for 500 items)
- Fetching metadata is slow (500 movies × 0.6s = 5 minutes)
- Original assumption: "Backfill later to avoid timeouts"

**This assumption is wrong** because:
- Extension has no timeout (runs in browser)
- Server-side sync should fetch metadata for non-Vercel deployments
- User experience > sync speed

---

## Solution Overview

### New Correct Flow

```
User Syncs Watchlist (500 movies)
    ↓
Scrape slugs from watchlist pages
    ↓
Immediately fetch metadata for all slugs
    ↓
Store complete movie records
    ↓
Store watchlist/diary references
    ↓
User loads deck → Sees complete data with posters, ratings, genres
```

**Benefits:**
1. ✅ Users see complete data immediately
2. ✅ No reliance on unreliable backfill
3. ✅ Simpler codebase (remove placeholder logic)
4. ✅ Better data integrity

---

## Implementation Plan

### Phase 1: Immediate Fixes (Ship in 0.7.0)

#### 1.1 Extension: Fetch Metadata During Sync

**File:** `extension/background.js`

**Current Code:**
```javascript
async function scrapeUserHistory(cfg, settings = {}) {
  // Scrapes watchlist → stores slugs only
  // Scrapes diary → stores slugs only
  return { watchlist: wl, diary, stopped: false };
}
```

**New Code:**
```javascript
async function scrapeUserHistory(cfg, settings = {}) {
  const doWatchlist = settings.syncWatchlist !== false;
  const doDiary = settings.syncDiary !== false;
  let wl = 0, diary = 0;
  const allSlugs = new Set();

  if (doWatchlist) {
    syncState.phase = "watchlist";
    syncState.currentPage = 0;
    syncState.totalPages = 0;
    broadcast();
    wl = await scrapeListType({
      cfg,
      pathFn: (p) => p === 1
        ? `/${encodeURIComponent(cfg.username)}/watchlist/`
        : `/${encodeURIComponent(cfg.username)}/watchlist/page/${p}/`,
      batchEndpoint: "/api/extension/batch/watchlist",
      phaseName: "watchlist",
      onFound: (n) => {
        syncState.watchlistFound = n;
        syncState.percent = Math.min(33, Math.floor((syncState.currentPage / Math.max(1, syncState.totalPages)) * 30));
        broadcast();
      },
      onSlugsCollected: (slugs) => {
        slugs.forEach(s => allSlugs.add(s));
      },
    });
    if (syncState.stopRequested) return { watchlist: wl, diary: 0, stopped: true };
  }

  if (doDiary) {
    syncState.phase = "diary";
    syncState.currentPage = 0;
    syncState.totalPages = 0;
    broadcast();
    diary = await scrapeListType({
      cfg,
      pathFn: (p) => p === 1
        ? `/${encodeURIComponent(cfg.username)}/diary/`
        : `/${encodeURIComponent(cfg.username)}/diary/page/${p}/`,
      batchEndpoint: "/api/extension/batch/diary",
      phaseName: "diary",
      onFound: (n) => {
        syncState.diaryFound = n;
        syncState.percent = 33 + Math.min(33, Math.floor((syncState.currentPage / Math.max(1, syncState.totalPages)) * 30));
        broadcast();
      },
      onSlugsCollected: (slugs) => {
        slugs.forEach(s => allSlugs.add(s));
      },
    });
    if (syncState.stopRequested) return { watchlist: wl, diary, stopped: true };
  }

  // NEW: Fetch metadata for all collected slugs
  const slugsArray = Array.from(allSlugs);
  if (slugsArray.length > 0) {
    syncState.phase = "metadata";
    syncState.percent = 66;
    broadcast();
    log(`Fetching metadata for ${slugsArray.length} movies...`);
    
    try {
      const result = await scrapeMoviesMetadata(cfg, slugsArray);
      log(`Metadata fetch complete: ${result.processed} movies processed`);
    } catch (e) {
      log(`ERROR: Metadata fetch failed: ${e.message}`);
      // Non-fatal: slugs are already stored, metadata can be retried
    }
  }

  syncState.percent = 100;
  broadcast();
  return { watchlist: wl, diary, stopped: false, metadata_fetched: slugsArray.length };
}
```

**Changes to `scrapeListType`:**
```javascript
async function scrapeListType({ cfg, pathFn, batchEndpoint, phaseName, onFound, onSlugsCollected }) {
  let page = 1;
  let totalPages = null;
  let totalFound = 0;
  let buffer = [];

  while (page <= MAX_PAGES_HARD_CAP) {
    // ... existing scraping logic ...
    
    buffer.push(...slugs);
    totalFound += slugs.length;
    onFound(totalFound);
    
    // NEW: Callback to collect slugs for metadata fetching
    if (onSlugsCollected) {
      onSlugsCollected(slugs);
    }

    // ... rest of existing logic ...
  }
  
  return totalFound;
}
```

**Testing:**
```javascript
// Test that metadata is fetched during sync
1. Clear database
2. Run extension sync
3. Verify watchlist/diary tables populated
4. Verify movies table has COMPLETE records (not placeholders)
5. Check: poster_url NOT NULL, genres NOT [], rating > 0
```

---

#### 1.2 Server-Side: Fetch Metadata During Sync

**File:** `src/api/app.py`

**Function:** `_run_user_history_sync`

**Current Code:**
```python
def _run_user_history_sync(
    user_id: str,
    session_cookie: str | None,
    username: str | None,
    max_watchlist_pages: int = 5,
    max_diary_pages: int = 5,
) -> dict:
    # Scrapes watchlist → stores slugs only
    # Scrapes diary → stores slugs only
    return sync_stats
```

**New Code:**
```python
def _run_user_history_sync(
    user_id: str,
    session_cookie: str | None,
    username: str | None,
    max_watchlist_pages: int = 5,
    max_diary_pages: int = 5,
) -> dict:
    sync_stats: dict = {"watchlist_count": 0, "diary_count": 0, "metadata_count": 0, "errors": []}
    print(f"[ingest/sync] starting user history sync for user_id={user_id} username={username}", flush=True)
    store.set_ingest_progress(user_id, 10)

    if not session_cookie:
        msg = "no session cookie provided — diary/watchlist sync skipped entirely"
        print(f"[ingest/sync] WARNING: {msg}", flush=True)
        sync_stats["errors"].append(msg)
        store.set_ingest_progress(user_id, 100)
        store.ingest_running.discard(user_id)
        return sync_stats

    all_slugs = set()

    # Scrape watchlist
    print(f"[ingest/sync] fetching watchlist (max_pages={max_watchlist_pages})...", flush=True)
    try:
        live_watchlist = scraper.pull_watchlist_slugs(
            session_cookie, username=username, max_pages=max_watchlist_pages
        )
        print(f"[ingest/sync] watchlist scraper returned {len(live_watchlist)} slugs", flush=True)
        stored = 0
        for slug in live_watchlist:
            try:
                store.add_watchlist(user_id, slug)
                all_slugs.add(slug)
                stored += 1
            except Exception as slug_exc:
                sync_stats["errors"].append(f"wl {slug}: {slug_exc}")
        sync_stats["watchlist_count"] = stored
    except Exception as exc:
        msg = f"watchlist fetch failed: {type(exc).__name__}: {exc}"
        print(f"[ingest/sync] ERROR: {msg}", flush=True)
        sync_stats["errors"].append(msg)

    store.set_ingest_progress(user_id, 40)

    # Scrape diary
    print(f"[ingest/sync] fetching diary (max_pages={max_diary_pages})...", flush=True)
    try:
        live_diary = scraper.pull_diary_slugs(
            session_cookie, username=username, max_pages=max_diary_pages
        )
        print(f"[ingest/sync] diary scraper returned {len(live_diary)} slugs", flush=True)
        stored = 0
        for slug in live_diary:
            try:
                store.add_diary(user_id, slug)
                all_slugs.add(slug)
                stored += 1
            except Exception as slug_exc:
                sync_stats["errors"].append(f"diary {slug}: {slug_exc}")
        sync_stats["diary_count"] = stored
    except Exception as exc:
        msg = f"diary fetch failed: {type(exc).__name__}: {exc}"
        print(f"[ingest/sync] ERROR: {msg}", flush=True)
        sync_stats["errors"].append(msg)

    store.set_ingest_progress(user_id, 70)

    # NEW: Fetch metadata for all collected slugs
    if all_slugs:
        print(f"[ingest/sync] fetching metadata for {len(all_slugs)} movies...", flush=True)
        missing = [slug for slug in all_slugs if not store.get_movie(slug)]
        print(f"[ingest/sync] {len(missing)} movies need metadata", flush=True)
        
        if missing:
            try:
                movies = scraper.metadata_for_slugs(missing)
                fetched = 0
                for movie in movies:
                    try:
                        store.upsert_movie(movie.__dict__)
                        fetched += 1
                    except Exception as exc:
                        sync_stats["errors"].append(f"metadata {movie.slug}: {exc}")
                sync_stats["metadata_count"] = fetched
                print(f"[ingest/sync] metadata fetched: {fetched} ok", flush=True)
            except Exception as exc:
                msg = f"metadata fetch failed: {type(exc).__name__}: {exc}"
                print(f"[ingest/sync] ERROR: {msg}", flush=True)
                sync_stats["errors"].append(msg)

    store.set_ingest_progress(user_id, 100)
    store.ingest_running.discard(user_id)
    print(
        f"[ingest/sync] DONE for {username}: "
        f"watchlist={sync_stats['watchlist_count']} diary={sync_stats['diary_count']} "
        f"metadata={sync_stats['metadata_count']} errors={len(sync_stats['errors'])}",
        flush=True,
    )
    return sync_stats
```

**Testing:**
```python
# Test server-side metadata fetch
def test_ingest_fetches_metadata():
    # Mock scraper to return slugs + metadata
    # Run ingest
    # Verify movies table has complete records
    assert store.get_movie("test-slug")["poster_url"] is not None
    assert len(store.get_movie("test-slug")["genres"]) > 0
```

---

#### 1.3 Update Backfill to Only Handle Edge Cases

**File:** `src/api/cron.py`

**Function:** `backfill_scrapes_cron`

**Changes:**
```python
@router.post("/backfill-scrapes")
async def backfill_scrapes_cron(
    x_cron_secret: str = Header(...),
    max_movies: int = 60,
    max_lists: int = 10,
):
    """Backfill ONLY:
    1. Old placeholder movies (from before metadata-during-sync fix)
    2. Movies with missing LIDs
    3. Under-scraped lists
    
    This is now a cleanup job, not the primary metadata source.
    """
    _require_cron_secret(x_cron_secret)

    scraper = HttpLetterboxdScraper()
    store = _get_store()

    # ── Movies: Only placeholders (legacy cleanup) ────────────────────────
    movie_stats = {"targeted": 0, "fetched": 0, "failed": 0}
    try:
        placeholder_slugs = store.get_placeholder_movie_slugs(limit=max_movies)
    except Exception as exc:
        print(f"[cron/backfill] placeholder query failed: {exc}", flush=True)
        placeholder_slugs = []
    
    movie_stats["targeted"] = len(placeholder_slugs)
    
    if placeholder_slugs:
        print(
            f"[cron/backfill] WARNING: Found {len(placeholder_slugs)} placeholder movies. "
            f"These should not exist after metadata-during-sync fix. Backfilling...",
            flush=True
        )
        try:
            movies = scraper.metadata_for_slugs(placeholder_slugs)
            for movie in movies:
                try:
                    store.upsert_movie(movie.__dict__)
                    movie_stats["fetched"] += 1
                except Exception as exc:
                    movie_stats["failed"] += 1
                    print(f"  upsert failed {movie.slug}: {exc}", flush=True)
        except Exception as exc:
            movie_stats["failed"] += len(placeholder_slugs)
            print(f"[cron/backfill] movie scrape failed: {exc}", flush=True)

    # ── Lists: Under-scraped only ──────────────────────────────────────────
    # ... existing list backfill logic unchanged ...

    print(f"[cron/backfill] DONE movies={movie_stats} lists={list_stats}", flush=True)
    return {"status": "ok", "movies": movie_stats, "lists": list_stats}
```

---

### Phase 2: Long-Term Refactoring (Ship in 0.8.0)

#### 2.1 Remove Placeholder Pattern Entirely

**Goal:** Make movie metadata **required** before storing watchlist/diary references.

**File:** `src/api/store.py`

**Current Code:**
```python
def add_watchlist(self, user_id: str, slug: str) -> None:
    try:
        self.client.table("watchlist").insert({...}).execute()
    except Exception as e:
        if "foreign key" in err:
            self._ensure_movie_placeholder(slug)  # ← REMOVE THIS
            # retry insert
```

**New Code:**
```python
def add_watchlist(self, user_id: str, slug: str) -> None:
    """Add a movie to user's watchlist.
    
    REQUIRES: Movie must exist in movies table with complete metadata.
    If movie doesn't exist, this will raise an exception.
    Caller is responsible for fetching metadata first.
    """
    actual_user_id = self._get_or_create_user_id(user_id)
    try:
        self.client.table("watchlist").insert({
            "user_id": actual_user_id,
            "movie_slug": slug
        }).execute()
    except Exception as e:
        err = str(e).lower()
        if "duplicate" in err or "unique" in err:
            return  # Already in watchlist, OK
        
        if "foreign key" in err or "23503" in err:
            # Movie doesn't exist - this is now an ERROR, not auto-fixed
            raise ValueError(
                f"Cannot add {slug} to watchlist: movie metadata not found. "
                f"Fetch metadata first using scraper.metadata_for_slugs(['{slug}'])"
            ) from e
        
        # Other errors
        print(f"[store] ERROR: watchlist insert failed for {slug}: {e}", flush=True)
        raise
```

**Same changes for:**
- `add_diary()`
- `add_exclusion()`

**Remove entirely:**
- `_ensure_movie_placeholder()` method
- `get_placeholder_movie_slugs()` method (or mark deprecated)

---

#### 2.2 Update Batch Operations

**File:** `src/api/store.py`

**Function:** `batch_add_watchlist`, `batch_add_diary`

**New Code:**
```python
def batch_add_watchlist(self, user_id: str, slugs: list[str]) -> dict:
    """Add many watchlist slugs with per-slug error handling.
    
    REQUIRES: All movies must exist in movies table.
    Missing movies will be logged as errors, not auto-created.
    """
    added = 0
    errors: list[str] = []
    missing_metadata: list[str] = []
    
    for slug in slugs:
        if not slug or not slug.strip():
            continue
        try:
            self.add_watchlist(user_id, slug)
            added += 1
        except ValueError as exc:
            # Movie metadata missing
            missing_metadata.append(slug)
            errors.append(f"{slug}: metadata_missing")
        except Exception as exc:
            errors.append(f"{slug}: {exc}")
            print(f"[store] batch_add_watchlist error for {slug}: {exc}", flush=True)
    
    if missing_metadata:
        print(
            f"[store] WARNING: {len(missing_metadata)} movies missing metadata. "
            f"These should have been fetched during sync. Slugs: {missing_metadata[:10]}",
            flush=True
        )
    
    print(
        f"[store] batch_add_watchlist: added={added} missing_metadata={len(missing_metadata)} "
        f"errors={len(errors)} total={len(slugs)}",
        flush=True
    )
    return {
        "added": added,
        "errors": errors,
        "missing_metadata": missing_metadata,
        "total": len(slugs)
    }
```

---

#### 2.3 Update Extension Batch Endpoints

**File:** `src/api/app.py`

**Endpoints:** `/api/extension/batch/watchlist`, `/api/extension/batch/diary`

**New Behavior:**
```python
@app.post("/api/extension/batch/watchlist")
async def extension_batch_watchlist(
    payload: ExtensionBatchRequest,
    verified_user: str = Depends(verify_session),
):
    """Push a batch of watchlist slugs scraped by the Chrome extension.
    
    EXPECTS: Metadata for these slugs should already be in the database.
    If metadata is missing, logs warning but doesn't fail the request.
    """
    if verified_user and payload.user_id != verified_user:
        raise HTTPException(status_code=403, detail={"code": "user_id_mismatch"})
    if len(payload.slugs) > _EXTENSION_BATCH_LIMIT:
        raise HTTPException(status_code=413, detail={"code": "batch_too_large", "limit": _EXTENSION_BATCH_LIMIT})

    print(
        f"[extension] watchlist batch user={payload.user_id} "
        f"page={payload.page}/{payload.total_pages} slugs={len(payload.slugs)}",
        flush=True,
    )
    result = store.batch_add_watchlist(payload.user_id, payload.slugs)
    
    # Warn if metadata is missing (shouldn't happen after fix)
    if result.get("missing_metadata"):
        print(
            f"[extension] WARNING: {len(result['missing_metadata'])} movies missing metadata. "
            f"Extension should fetch metadata before pushing watchlist.",
            flush=True
        )
    
    return {
        "status": "ok",
        "user_id": payload.user_id,
        "page": payload.page,
        "total_pages": payload.total_pages,
        "result": result,
    }
```

---

## Migration Strategy

### Step 1: Deploy Phase 1 (Immediate Fixes)

1. **Deploy extension update:**
   - Users update extension from Chrome Web Store
   - New syncs fetch metadata immediately
   - Old placeholder records remain (cleaned by backfill)

2. **Deploy server update:**
   - Server-side sync now fetches metadata
   - Backfill continues to clean old placeholders
   - No breaking changes

3. **Monitor:**
   - Check logs for "placeholder movie" messages
   - Should decrease over time as users re-sync
   - Track `metadata_count` in sync stats

### Step 2: Deploy Phase 2 (Long-Term Refactoring)

**Prerequisites:**
- Phase 1 deployed for 2+ weeks
- Backfill has cleaned most placeholders
- `get_placeholder_movie_slugs()` returns < 100 records

**Deployment:**
1. Run final backfill to clean remaining placeholders
2. Deploy code that removes `_ensure_movie_placeholder`
3. Monitor for FK violation errors
4. If errors occur, roll back and run more backfill

---

## Success Metrics

### Phase 1 Success Criteria

- ✅ New syncs create 0 placeholder records
- ✅ Users see complete movie data immediately after sync
- ✅ `metadata_count` in sync stats > 0
- ✅ Backfill finds fewer placeholders each day

### Phase 2 Success Criteria

- ✅ `_ensure_movie_placeholder` removed from codebase
- ✅ No FK violation errors in production logs
- ✅ All movies in database have complete metadata
- ✅ Simplified codebase (less technical debt)

---

## Rollback Plan

### If Phase 1 Causes Issues

**Symptoms:**
- Sync takes too long (> 10 minutes)
- Sync fails with timeout errors
- Users report sync never completes

**Rollback:**
1. Revert extension to previous version
2. Revert server code to previous version
3. Backfill continues to work as before

### If Phase 2 Causes Issues

**Symptoms:**
- FK violation errors in logs
- Users can't add movies to watchlist
- Batch operations failing

**Rollback:**
1. Revert to Phase 1 code (keep placeholder creation)
2. Run backfill to clean up
3. Investigate why metadata wasn't fetched

---

## Testing Plan

### Unit Tests

```python
# Test: Sync fetches metadata
def test_sync_fetches_metadata_for_all_slugs():
    scraper = MockLetterboxdScraper()
    store = InMemoryStore()
    
    # Mock watchlist with 10 movies
    scraper.pull_watchlist_slugs = lambda *args: {"film-1", "film-2", ..., "film-10"}
    
    # Run sync
    stats = _run_user_history_sync("user1", "cookie", "user1")
    
    # Verify metadata was fetched
    assert stats["metadata_count"] == 10
    
    # Verify movies have complete data
    for i in range(1, 11):
        movie = store.get_movie(f"film-{i}")
        assert movie is not None
        assert movie["poster_url"] is not None
        assert len(movie["genres"]) > 0
        assert movie["rating"] > 0

# Test: Phase 2 - FK violation when metadata missing
def test_add_watchlist_requires_metadata():
    store = SupabaseStore()
    
    # Try to add movie that doesn't exist
    with pytest.raises(ValueError, match="metadata not found"):
        store.add_watchlist("user1", "nonexistent-movie")
```

### Integration Tests

```javascript
// Extension test: Metadata fetched during sync
async function testExtensionSync() {
  // 1. Clear database
  await clearDatabase();
  
  // 2. Run extension sync
  await chrome.runtime.sendMessage({ type: "START_SYNC" });
  
  // 3. Wait for completion
  await waitForSyncComplete();
  
  // 4. Verify movies table has complete records
  const movies = await fetchMoviesFromAPI();
  assert(movies.every(m => m.poster_url !== null));
  assert(movies.every(m => m.genres.length > 0));
  assert(movies.every(m => m.rating > 0));
}
```

---

## Documentation Updates

### Update README.md

**Section: "How It Works"**

Add:
```markdown
### Data Sync Flow

When you sync your Letterboxd history:

1. **Scrape Slugs:** Extension scrapes your watchlist/diary pages to get movie slugs
2. **Fetch Metadata:** Immediately fetches complete metadata for all movies (poster, rating, genres, cast, synopsis)
3. **Store Complete Records:** Saves full movie data to database
4. **Link to User:** Creates watchlist/diary references

**Result:** You see complete movie data immediately, no waiting for backfill.
```

### Update PRD.md

**Section 6.2: Performance**

Update:
```markdown
**Sync Performance:**
- Watchlist/diary sync: ~1-2 seconds per page
- Metadata fetch: ~0.6 seconds per movie (batched)
- Total sync time: ~5-10 minutes for 500 movies
- All metadata fetched during initial sync (no backfill needed)
```

---

## Timeline

| Phase | Task | Duration | Owner |
|-------|------|----------|-------|
| **Phase 1** | Extension: Add metadata fetch to sync | 1 day | Dev |
| | Server: Add metadata fetch to sync | 1 day | Dev |
| | Update backfill to log warnings | 0.5 day | Dev |
| | Testing + QA | 1 day | QA |
| | Deploy to production | 0.5 day | DevOps |
| **Phase 2** | Remove placeholder pattern | 1 day | Dev |
| | Update batch operations | 0.5 day | Dev |
| | Update error handling | 0.5 day | Dev |
| | Testing + QA | 1 day | QA |
| | Deploy to production | 0.5 day | DevOps |

**Total:** 7-8 days

---

## Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Sync takes too long | High | Medium | Batch metadata fetching, show progress |
| Metadata fetch fails | Medium | Low | Log error, continue with slugs only, retry later |
| Vercel timeout on server sync | Low | High | Already handled (extension is primary) |
| Users have slow connections | Medium | Medium | Show progress, allow pause/resume |
| Letterboxd rate limits | High | Low | Respect delays, exponential backoff |

---

## Appendix: Code Locations

### Files to Modify (Phase 1)

- `extension/background.js` - Add metadata fetch to `scrapeUserHistory()`
- `src/api/app.py` - Add metadata fetch to `_run_user_history_sync()`
- `src/api/cron.py` - Update `backfill_scrapes_cron()` to log warnings

### Files to Modify (Phase 2)

- `src/api/store.py` - Remove `_ensure_movie_placeholder()`, update `add_watchlist()`, `add_diary()`, `add_exclusion()`
- `src/api/app.py` - Update batch endpoints to handle missing metadata errors
- `tests/test_store.py` - Add tests for FK violation errors

### Files to Update (Documentation)

- `README.md` - Add "How It Works" section
- `PRD.md` - Update performance section
- `CHANGELOG.md` - Document breaking changes in 0.8.0
