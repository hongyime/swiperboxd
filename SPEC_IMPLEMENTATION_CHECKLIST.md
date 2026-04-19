# Implementation Checklist: Metadata Sync Fix

**Spec:** SPEC_METADATA_SYNC_FIX.md  
**Version:** 0.7.0 (Phase 1) → 0.8.0 (Phase 2)

---

## Phase 1: Immediate Fixes (v0.7.0)

### 1. Extension: Fetch Metadata During Sync

- [ ] **Modify `scrapeUserHistory()` function**
  - [ ] Add `allSlugs` Set to collect all scraped slugs
  - [ ] Add `onSlugsCollected` callback to `scrapeListType()` calls
  - [ ] After watchlist/diary scraping, call `scrapeMoviesMetadata(cfg, slugsArray)`
  - [ ] Update progress percentages (33% watchlist, 33% diary, 33% metadata)
  - [ ] Add `metadata_fetched` count to return value
  - [ ] Handle metadata fetch errors gracefully (log, don't fail sync)

- [ ] **Update `scrapeListType()` function**
  - [ ] Add `onSlugsCollected` parameter to function signature
  - [ ] Call `onSlugsCollected(slugs)` after extracting slugs from each page
  - [ ] Ensure callback is optional (backward compatible)

- [ ] **Update progress UI**
  - [ ] Add "Fetching metadata..." phase to popup
  - [ ] Show metadata fetch progress (X/Y movies)
  - [ ] Update progress bar to reflect 3-phase sync

- [ ] **Testing**
  - [ ] Test with empty watchlist/diary (should not crash)
  - [ ] Test with 10 movies (verify metadata fetched)
  - [ ] Test with 500 movies (verify all metadata fetched)
  - [ ] Test with stop button during metadata fetch (should stop gracefully)
  - [ ] Test with network error during metadata fetch (should log error, continue)

---

### 2. Server: Fetch Metadata During Sync

- [ ] **Modify `_run_user_history_sync()` function**
  - [ ] Add `all_slugs` Set to collect scraped slugs
  - [ ] Add slugs to set during watchlist loop
  - [ ] Add slugs to set during diary loop
  - [ ] After both loops, filter for missing metadata: `missing = [slug for slug in all_slugs if not store.get_movie(slug)]`
  - [ ] Call `scraper.metadata_for_slugs(missing)`
  - [ ] Upsert each movie with error handling
  - [ ] Add `metadata_count` to `sync_stats` return value
  - [ ] Update progress: 10% → 40% (watchlist) → 70% (diary) → 100% (metadata)

- [ ] **Update return value**
  - [ ] Add `metadata_count` field to `sync_stats` dict
  - [ ] Log metadata fetch stats: `metadata={sync_stats['metadata_count']}`

- [ ] **Testing**
  - [ ] Test with mock scraper (10 movies)
  - [ ] Verify `metadata_count` in response
  - [ ] Verify movies table has complete records
  - [ ] Test with metadata fetch failure (should log error, not crash)
  - [ ] Test on Vercel (should work but may timeout for large syncs)

---

### 3. Update Backfill to Log Warnings

- [ ] **Modify `backfill_scrapes_cron()` function**
  - [ ] Add warning log when placeholders found:
    ```python
    print(
        f"[cron/backfill] WARNING: Found {len(placeholder_slugs)} placeholder movies. "
        f"These should not exist after metadata-during-sync fix. Backfilling...",
        flush=True
    )
    ```
  - [ ] Keep existing backfill logic (don't break it)
  - [ ] Add comment: "This is now a cleanup job, not the primary metadata source"

- [ ] **Testing**
  - [ ] Verify backfill still works for old placeholders
  - [ ] Verify warning appears in logs when placeholders found
  - [ ] Verify backfill count decreases over time

---

### 4. Documentation Updates

- [ ] **Update README.md**
  - [ ] Add "How It Works" section explaining sync flow
  - [ ] Document that metadata is fetched during sync (not backfilled)
  - [ ] Update sync time estimates (5-10 minutes for 500 movies)

- [ ] **Update PRD.md**
  - [ ] Update Section 6.2 (Performance) with new sync flow
  - [ ] Remove references to "backfill as primary metadata source"
  - [ ] Add note about metadata fetch during sync

- [ ] **Create CHANGELOG.md entry**
  ```markdown
  ## [0.7.0] - 2026-04-XX
  
  ### Changed
  - **BREAKING:** Sync now fetches complete movie metadata immediately
  - Users see complete movie data (posters, ratings, genres) right after sync
  - Backfill is now only for cleanup of old placeholder records
  
  ### Improved
  - Sync reliability: No longer depends on unreliable backfill
  - User experience: Complete data shown immediately
  - Data integrity: Database contains complete records by design
  ```

---

### 5. Deployment

- [ ] **Pre-deployment**
  - [ ] Run all tests (unit + integration)
  - [ ] Test extension locally with production API
  - [ ] Test server-side sync on staging environment
  - [ ] Verify backfill still works

- [ ] **Deploy extension**
  - [ ] Build extension: `cd extension && zip -r extension.zip *`
  - [ ] Upload to Chrome Web Store
  - [ ] Submit for review
  - [ ] Wait for approval (1-3 days)

- [ ] **Deploy server**
  - [ ] Commit changes to git
  - [ ] Push to main branch
  - [ ] Vercel auto-deploys
  - [ ] Verify deployment successful
  - [ ] Check logs for errors

- [ ] **Post-deployment monitoring**
  - [ ] Monitor logs for "placeholder movie" warnings (should decrease)
  - [ ] Monitor sync completion rates
  - [ ] Monitor metadata fetch errors
  - [ ] Check user feedback for sync issues

---

## Phase 2: Long-Term Refactoring (v0.8.0)

### Prerequisites

- [ ] **Verify Phase 1 success**
  - [ ] Phase 1 deployed for 2+ weeks
  - [ ] Backfill finds < 100 placeholders
  - [ ] No major issues reported by users
  - [ ] Sync completion rate > 95%

- [ ] **Run final cleanup**
  - [ ] Manually trigger backfill cron job
  - [ ] Verify placeholder count near zero
  - [ ] Export list of remaining placeholders for investigation

---

### 1. Remove Placeholder Pattern

- [ ] **Modify `SupabaseStore.add_watchlist()`**
  - [ ] Remove `_ensure_movie_placeholder()` call
  - [ ] Change FK violation handling to raise `ValueError`
  - [ ] Update error message: "Cannot add {slug} to watchlist: movie metadata not found"
  - [ ] Keep duplicate handling (return silently)

- [ ] **Modify `SupabaseStore.add_diary()`**
  - [ ] Remove `_ensure_movie_placeholder()` call
  - [ ] Change FK violation handling to raise `ValueError`
  - [ ] Update error message: "Cannot add {slug} to diary: movie metadata not found"
  - [ ] Keep duplicate handling (return silently)

- [ ] **Modify `SupabaseStore.add_exclusion()`**
  - [ ] Remove `_ensure_movie_placeholder()` call
  - [ ] Change FK violation handling to raise `ValueError`
  - [ ] Update error message: "Cannot add {slug} to exclusions: movie metadata not found"
  - [ ] Keep duplicate handling (return silently)

- [ ] **Remove `_ensure_movie_placeholder()` method**
  - [ ] Delete entire method
  - [ ] Search codebase for any remaining calls
  - [ ] Remove from `InMemoryStore` if present

- [ ] **Mark `get_placeholder_movie_slugs()` as deprecated**
  - [ ] Add deprecation warning to docstring
  - [ ] Add log warning when called
  - [ ] Keep method for now (used by backfill)

---

### 2. Update Batch Operations

- [ ] **Modify `batch_add_watchlist()`**
  - [ ] Add `missing_metadata` list to track FK violations
  - [ ] Catch `ValueError` exceptions (metadata missing)
  - [ ] Add to `missing_metadata` list instead of `errors`
  - [ ] Log warning if `missing_metadata` not empty
  - [ ] Return `missing_metadata` in result dict

- [ ] **Modify `batch_add_diary()`**
  - [ ] Add `missing_metadata` list to track FK violations
  - [ ] Catch `ValueError` exceptions (metadata missing)
  - [ ] Add to `missing_metadata` list instead of `errors`
  - [ ] Log warning if `missing_metadata` not empty
  - [ ] Return `missing_metadata` in result dict

- [ ] **Update return type**
  - [ ] Add `missing_metadata: list[str]` field to return dict
  - [ ] Update docstrings to document new field

---

### 3. Update Extension Batch Endpoints

- [ ] **Modify `/api/extension/batch/watchlist`**
  - [ ] Check `result.get("missing_metadata")`
  - [ ] Log warning if not empty
  - [ ] Include `missing_metadata` in response
  - [ ] Don't fail request (log only)

- [ ] **Modify `/api/extension/batch/diary`**
  - [ ] Check `result.get("missing_metadata")`
  - [ ] Log warning if not empty
  - [ ] Include `missing_metadata` in response
  - [ ] Don't fail request (log only)

- [ ] **Update response models**
  - [ ] Add `missing_metadata` field to response (optional)
  - [ ] Update API documentation

---

### 4. Testing

- [ ] **Unit tests**
  - [ ] Test `add_watchlist()` raises `ValueError` when movie missing
  - [ ] Test `add_diary()` raises `ValueError` when movie missing
  - [ ] Test `batch_add_watchlist()` returns `missing_metadata` list
  - [ ] Test `batch_add_diary()` returns `missing_metadata` list
  - [ ] Test duplicate handling still works

- [ ] **Integration tests**
  - [ ] Test sync with complete metadata (should succeed)
  - [ ] Test sync with missing metadata (should log warning)
  - [ ] Test batch operations with mixed data (some missing)
  - [ ] Test error messages are clear and actionable

- [ ] **Regression tests**
  - [ ] Test existing functionality still works
  - [ ] Test deck loading with complete data
  - [ ] Test swipe actions
  - [ ] Test list catalog

---

### 5. Documentation Updates

- [ ] **Update README.md**
  - [ ] Document that metadata is required before adding to watchlist/diary
  - [ ] Update error handling section
  - [ ] Add troubleshooting for FK violation errors

- [ ] **Update PRD.md**
  - [ ] Remove placeholder pattern from data models
  - [ ] Update error handling section
  - [ ] Document new FK violation behavior

- [ ] **Create CHANGELOG.md entry**
  ```markdown
  ## [0.8.0] - 2026-XX-XX
  
  ### BREAKING CHANGES
  - Removed automatic placeholder creation for missing movies
  - `add_watchlist()`, `add_diary()`, `add_exclusion()` now raise `ValueError` if movie metadata missing
  - Batch operations return `missing_metadata` list instead of auto-creating placeholders
  
  ### Removed
  - `_ensure_movie_placeholder()` method (no longer needed)
  - Automatic FK violation recovery (metadata must be fetched first)
  
  ### Improved
  - Cleaner codebase (less technical debt)
  - Better data integrity (no incomplete records)
  - Clearer error messages when metadata missing
  ```

---

### 6. Migration

- [ ] **Pre-migration**
  - [ ] Announce breaking changes to users (if any API consumers)
  - [ ] Run final backfill to clean all placeholders
  - [ ] Verify placeholder count is 0
  - [ ] Create database backup

- [ ] **Deploy**
  - [ ] Deploy server changes
  - [ ] Monitor logs for FK violation errors
  - [ ] Monitor error rates
  - [ ] Check user reports

- [ ] **Post-migration**
  - [ ] Verify no FK violations in logs
  - [ ] Verify sync still works
  - [ ] Verify batch operations work
  - [ ] Remove deprecated `get_placeholder_movie_slugs()` after 1 month

---

### 7. Rollback Plan

- [ ] **If FK violations occur**
  - [ ] Revert to Phase 1 code (keep placeholder creation)
  - [ ] Investigate why metadata wasn't fetched
  - [ ] Run backfill to clean up
  - [ ] Fix root cause before re-deploying Phase 2

- [ ] **If sync breaks**
  - [ ] Revert to Phase 1 code
  - [ ] Check logs for error patterns
  - [ ] Fix issues in staging
  - [ ] Re-deploy after testing

---

## Success Metrics

### Phase 1 Metrics

- [ ] **Placeholder creation rate**
  - Target: 0 new placeholders per day
  - Measure: `grep "created placeholder movie" logs | wc -l`

- [ ] **Metadata fetch success rate**
  - Target: > 95%
  - Measure: `metadata_count / (watchlist_count + diary_count)`

- [ ] **Sync completion rate**
  - Target: > 90%
  - Measure: Syncs that reach 100% progress

- [ ] **User satisfaction**
  - Target: No complaints about missing posters/ratings
  - Measure: User feedback, support tickets

### Phase 2 Metrics

- [ ] **FK violation rate**
  - Target: 0 per day
  - Measure: `grep "foreign key" logs | wc -l`

- [ ] **Code complexity**
  - Target: -100 lines of code
  - Measure: `git diff --stat`

- [ ] **Database integrity**
  - Target: 100% of movies have complete metadata
  - Measure: `SELECT COUNT(*) FROM movies WHERE poster_url IS NULL`

---

## Sign-off

### Phase 1 Complete

- [ ] All Phase 1 tasks completed
- [ ] All tests passing
- [ ] Deployed to production
- [ ] Monitoring shows success metrics met
- [ ] No critical issues reported

**Signed:** _________________ **Date:** _________

### Phase 2 Complete

- [ ] All Phase 2 tasks completed
- [ ] All tests passing
- [ ] Deployed to production
- [ ] Monitoring shows success metrics met
- [ ] No critical issues reported
- [ ] Placeholder pattern fully removed

**Signed:** _________________ **Date:** _________
