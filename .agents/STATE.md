# Current work

2026-09-11: Maintenance of extension watchlist/diary batch persistence.

- Confirmed: merge-style upserts rewrite existing memberships; placeholder movie upserts can replace real titles after a failed lookup or concurrent metadata insert.
- Implemented: conflict-ignore placeholder/membership inserts, deduplicated payloads, 200-row placeholder chunks with slug-only returns, minimal membership responses, and HTTP 503 for failed batches. `added` retains its accepted-input meaning.
- Verified locally: 54 Python tests passed, five optional live-service tests skipped, four web state tests passed, and compile checks passed. Twelve persistence tests use the real PostgREST client against synthetic SQLite conflict semantics; two API tests cover failure responses. Ten of the persistence tests failed against the original implementation. Remote sockets are blocked during the automated suite.
- Live read-only metadata confirms the required uniqueness constraints and no custom triggers on movies, diary or watchlist. This is not a live database write test or a measured savings claim.
- Next: publish the reviewed source, verify release workflows and exact production deployment, then record public route checks in the portfolio report.
- Keep all existing application records and real metadata updates. Do not invoke live sync, scraping, cookies or migrations for testing.
- Source baseline and rollback reference: `9a6bed4`.
