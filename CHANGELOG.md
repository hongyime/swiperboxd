# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.8.0] - 2026-04-19

### BREAKING CHANGES

- **Removed automatic placeholder creation for missing movies**
- `add_watchlist()`, `add_diary()`, `add_exclusion()` now raise `ValueError` if movie metadata missing
- Batch operations return `missing_metadata` list instead of auto-creating placeholders
- Metadata must be fetched before adding movies to watchlist/diary

### Removed

- `_ensure_movie_placeholder()` method (no longer needed)
- Automatic FK violation recovery (metadata must be fetched first)

### Improved

- Cleaner codebase (less technical debt)
- Better data integrity (no incomplete records)
- Clearer error messages when metadata missing
- Batch operations now track `missing_metadata` separately from other errors

### Technical

- `add_watchlist()`, `add_diary()`, `add_exclusion()` raise `ValueError` on FK violations
- Batch operations return `missing_metadata: list[str]` field
- Extension batch endpoints log warnings when metadata missing
- Error messages include instructions to fetch metadata first

### Migration Notes

- Ensure Phase 1 (v0.7.0) has been deployed for 2+ weeks before upgrading
- Run backfill to clean all placeholder records before deploying
- Monitor logs for FK violation errors after deployment
- If errors occur, roll back and run more backfill

## [0.7.0] - 2026-04-19

### Changed

- **MAJOR:** Sync now fetches complete movie metadata immediately during initial sync
- Users see complete movie data (posters, ratings, genres) right after sync completes
- Backfill is now only for cleanup of old placeholder records, not primary metadata source
- Progress bar updated to show 3 phases: watchlist (33%), diary (33%), metadata (33%)

### Improved

- Sync reliability: No longer depends on unreliable backfill for metadata
- User experience: Complete data shown immediately after sync
- Data integrity: Database contains complete records by design
- Extension now collects all slugs during sync and fetches metadata in batch

### Technical

- Extension: Added `onSlugsCollected` callback to `scrapeListType()` function
- Extension: `scrapeUserHistory()` now calls `scrapeMoviesMetadata()` after watchlist/diary scraping
- Server: `_run_user_history_sync()` now fetches metadata for all collected slugs
- Server: Progress tracking updated (10% → 40% → 70% → 100%)
- Backfill: Added warning logs when placeholder movies are found

### Notes

- Old placeholder records will be cleaned up by backfill over time
- This change eliminates the poor UX of seeing incomplete movie cards after sync
- Metadata fetch is non-fatal: if it fails, slugs are still stored and can be retried

## [0.6.0] - Previous Release

- Initial release with placeholder pattern
- Sync stored slugs only, backfill fetched metadata later
