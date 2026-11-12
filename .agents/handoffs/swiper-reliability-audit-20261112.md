# Reliability audit handoff — 2026-11-12

Completed task: applied the 13 requested production-reliability fixes without live writes, scraping, or migrations.

Confirmed already fixed: `background.js` returns `true` from its message listener, and user-history sync already uses the two batch store methods.

Implemented: list membership writers ensure movie placeholders; Supabase ingest progress/errors persist in `user_throttle` with migration 006; migration errors propagate; discovery uses randomized database samples via migration 007; membership upserts are 200-row chunks; users are read before inserting; backfill selection belongs to Store; cron work is capped with a 2-second per-tier provider timeout and leaves multi-page list scraping to the extension; pytest has the repo root on `pythonpath`; PyJWT is removed; timestamps are timezone-aware.

Regression coverage includes cross-instance progress state, membership FK preconditions and chunking, migration failure propagation, randomized bounded discovery, local backfill endpoint, extension error clearing, and cron caps/timeouts. Final checks: 146 Python passed (2 optional skips), 25 Node passed, compilation and diff checks passed. Existing hardening and production data remain untouched.
