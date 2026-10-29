# Current work

## 2026-10-29 — Swiperboxd hardening landed

Landed the 20-issue hardening plan (`swiperboxd-hardening`) as 22 commits (`07b4528` baseline .. `1f47c57`) on `main`. Fixes: revoke `exec_sql` + new RPCs `EXECUTE` **from PUBLIC** (the anon key can no longer run SQL; per-role revoke does not override the default PUBLIC grant), extension register derives the username server-side via the proxy chain (no username impersonation), cron accepts GET + `Authorization: Bearer`, `/lists/*` GETs are read-only, path-based list/watchlist pagination, deck uses one bounded-sample RPC, atomic membership replace, insert-only list batches, DB-backed rate limits + ingest lease, 30-day token TTL with legacy tokens rejected, extension credentials kept in `storage.local` + origin-gated postMessage, page-1 write verification, authenticated `/ingest/progress`, lazy Scrape.do probe, honest catalog pagination, dead queue/qstash/rate-limiter code + ad-hoc scripts deleted, migration 001 syntax fixed, and `app.py` split into routers/workers (1590→408 lines).

Verified: 133 Python tests passed (2 live-service skips), 21 Node tests, and a 25-check live TestClient QA matrix. Final verification wave F1 plan-compliance, F2 code/security, F3 manual QA, F4 ideal-state — all APPROVE. NOT pushed; no PR opened. Unrelated Antigravity WIP was snapshotted as baseline `07b4528` and left intact. Next: review/merge the 22 commits. Non-blocking follow-ups: GET `/deck` still performs one bounded `users` upsert via `resolve_user_id`; `keep_alive_ping` remains anon-callable SECURITY DEFINER (pre-existing).


2026-09-15: Reduce redundant list-deck reads on `perf/list-deck-reads`. Read saved memberships once and exclude seen slugs before metadata queries; include-seen requests skip unused history. Required history failures return a retryable 503 instead of an unfiltered deck. Preserve the full eligible shuffle pool, 20-card response, stored records, schema, provider schedules and extension behavior. Eight of ten new regression cases fail against main; all 99 Python tests now pass (five optional live-service skips), along with 11 Node checks and dependency/compile checks. The local Chromium runner exceeded its 240-second deadline; hosted browser, Build and Vercel checks remain release gates. Previous config PR #103 is merged at 4f8aa703; its application bytes match existing production 0fe8fa9f, while its maintenance-only production build was rate-limited.

2026-09-15: Reconcile config PR #103 on its existing `chore/config-sync-34864413485-1` branch. Retain main's setup-python v7 and labeler v7 instead of the generated v6 downgrades. Keep the shallow, fail-closed LFS index guard. Application source, dependencies, records and extension behavior are unchanged. Local checks pass: 89 Python tests (five optional live-service skips), 11 Node tests, 13 Chromium cases, dependency compatibility, compilation and heartbeat configuration. The LFS workflow is byte-identical to source PR #51's ten successful hosted Linux fixtures. Required Build/Vercel checks and exact production verification remain release gates. Resume details: `.agents/handoffs/swiper-config-20260915.json`.

2026-09-14: Reduce avoidable Vercel CPU after the owner reported the shared Free CPU allowance exhausted. Baseline production is 9fbb114; Swiperboxd accounted for 38m 6s (15.7%) of the supplied 30-day dashboard, without route-level attribution. Work is isolated from the original checkout.

- Route only the existing public homepage/assets through the static CDN, preserving their contents and security headers. Personal and API responses remain on Python without a new shared cache. A real local Vercel build produces seven static files and anchored routes before the Python fallback; output/content parity verification is being completed.
- Correct the extension's two 15-minute scheduling sites to its documented six hours. Reconcile stored alarms on install/update and browser startup; ignore stale or disabled queued automatic alarms. Manual full sync remains available. Seven worker behavior tests reproduced the original errors and now pass.
- The extension update requires existing unpacked installations to reload updated files. For enabled installations the schedule permits four rather than 96 automatic runs per day while the browser runs; records can be up to six hours less current. Actual CPU savings require usage measurements after rollout.
- Preserve all stored records, dependencies, sync scope and existing collection handlers. Separate observed cron GET/POST mismatch must not be changed into active scraping during the CPU incident. No Spotify integration was found in this repository.
- Local validation: 89 Python tests passed (five optional live-service skips), 11 Node tests passed, all 13 Chromium refresh cases passed, and dependency/compile/maintenance checks passed. The real popup preserves an enabled choice, sends the disable event and still starts manual full sync using synthetic Chrome APIs. An independent review found no blocking route/cache/header or alarm issues. An older original virtual environment did not match the committed dependency pins; the matching isolated environment passes without dependency changes.
- Next: publish the small PR, wait for protected hosted Build/Vercel checks, verify the exact production commit, and record the release. Task handoff: .agents/handoffs/swiper-free-cpu-20260914.json.

2026-09-11: Targeted cryptography dependency update following the batch-sync release.

- Completed locally: release-note/application review, 48.0.1 synthetic fixtures, pin and minimum updated to 50.0.1, and old/new token compatibility in both directions. The installed package comparison changed only cryptography among 51 packages; dependency compatibility and vulnerability audit passed with zero known advisories.
- Current suite: 72 Python tests passed, five optional live-service tests skipped, four web tests passed, and compilation/diff checks passed. Eighteen compatibility tests preserve legacy-token decoding and rejection of tampered tokens/wrong keys.
- Dependency release `79e92a0726b4efe49800dde2f694e0d363ff4150` is verified on production deployment `dpl_DQnicksnBz2Qb6RhXXza7dgJmit2`. All six hosted workflows passed, including the 72-case Python suite and four web tests. Both public domains passed 12 HTTP checks and desktop/mobile anonymous setup checks.
- GitHub marked advisory 7 fixed at 2026-09-10 18:17:26 UTC; the subsequent repository query returned zero open dependency alerts. Full release evidence is maintained in https://aoo181uudk96.postplan.dev. No production sessions or database writes were exercised.
- Direct application crypto use is Fernet with SHA-256 key derivation. No production keys, stored sessions or application records are needed for validation.

- Confirmed: merge-style upserts rewrite existing memberships; placeholder movie upserts can replace real titles after a failed lookup or concurrent metadata insert.
- Implemented: conflict-ignore placeholder/membership inserts, deduplicated payloads, 200-row placeholder chunks with slug-only returns, minimal membership responses, and HTTP 503 for failed batches. `added` retains its accepted-input meaning.
- Verified locally: 54 Python tests passed, five optional live-service tests skipped, four web state tests passed, and compile checks passed. Twelve persistence tests use the real PostgREST client against synthetic SQLite conflict semantics; two API tests cover failure responses. Ten of the persistence tests failed against the original implementation. Remote sockets are blocked during the automated suite.
- Live read-only metadata confirms the required uniqueness constraints and no custom triggers on movies, diary or watchlist. This is not a live database write test or a measured savings claim.
- Published `b2d41241f04dda49a0270745d548b4d96d23302e`; production deployment `dpl_GX4ad9J1eywY3YykFDkjsvvs2dzN` is READY on both public domains. All five release workflows passed. Twelve public HTTP checks and desktop/mobile anonymous setup checks passed. Detailed evidence is linked from https://aoo181uudk96.postplan.dev.
- Portfolio queue after this release: real movie/list metadata write churn and scheduled sync/keepalive behavior. No monthly savings claim has been established. Rotate to another repository after closing the dependency release.
- Main tracks origin/main. The prior release's verification notes are included with this dependency update.
- Keep all existing application records and real metadata updates. Do not invoke live sync, scraping, cookies or migrations for testing.
- Dependency-update baseline and rollback reference: `b2d4124`. Rollback token compatibility was checked using synthetic data.


2026-09-12 — portfolio build-check repair: make the named Build check validate this application using existing fixture tests and production build/entrypoint checks. Preserve live data and existing collector behavior. No provider workflows are invoked for testing. Required-check enforcement and bot reactivation remain open because the shared heartbeat still pushes directly to main.

- [ ] Preserve original clean checkouts and work from current remote commits.
- [ ] Make GMapLists run its existing unit suite and production TypeScript/Vite build on every PR.
- [ ] Make Swiperboxd Build run the existing isolated Python and web suites; retain its legacy test workflow for manual use without duplicate automatic runs.
- [ ] Validate locally, publish small PRs, verify hosted checks and production deployments.
- [ ] Keep bot reactivation and required-check enforcement open until heartbeat direct commits are accounted for.

2026-09-12 validation: the replacement Build workflow runs the existing Python/API and browser-state suites instead of skipping when package.json has no build script. Local Python 3.12 checks pass: 72 tests, five optional live-service skips, four web tests, compilation of src/api and compatibility of 51 installed packages. The legacy Swiperboxd Tests workflow is retained for manual diagnostics; automatic runs are consolidated under Build Check. Application code, dependencies, provider calls and collection schedules are unchanged by this repair. Hosted checks and production verification are next.

2026-09-12 merge-protection rotation: preserve the current application tests/build and all records; require Build on every PR/main update; replace main-branch heartbeat commits with an owned activity branch whose Vercel configs disable its deployments; install the shared checked bot policy while keeping automation disabled. Actual Vercel root is the repository root; the schedule retains minute 1. Task sequence: validate maintenance and app checks, publish reviewed PRs, verify hosted checks and production, configure required Build/Vercel identities with administrator enforcement, verify blocked heads and heartbeat behavior, then re-enable only the repaired bot policy. No collector, sync or provider-data workflow is dispatched for testing. All prior data and application source remain unchanged.

2026-09-13 refresh continuation: list typing currently repeats catalogue/status/deck requests, overlapping reads have no cancellation or generation guard, and sync-status loads complete memberships through a user-creating lookup while masking errors as empty data. Reproduce with synthetic HTTP/browser fixtures, implement local search and current-request coordination with read-only authenticated status counts, then verify production and update the portfolio report. Preserve all data, extension synchronization and existing protection rules. Five tasks are recorded in .agents/handoffs/swiper-refresh-20260913.json.

2026-09-13 local refresh verification: reproduced six browser failures and eight initial status failures. The fix passes 85 Python tests (five optional live-service skips), four Node tests and 13 Chromium cases. Search is local, keyboard input is isolated from swipe shortcuts, only current reads render, duplicate reads join, and per-read deadlines permit manual retry. Authenticated status uses one user lookup and two exact HEAD counts without inserts; failures remain 503 with private/no-store headers. No schema, records, schedules or provider calls changed. Hosted checks and production verification are next.

## Privacy maintenance - 2026-09-27

Remove the optional personal security contact while retaining private vulnerability-reporting guidance. The narrow documentation patch is prepared from the current default branch; staged whitespace and the inherited identity hook are publication checks. Application behavior is unchanged.

## Session resumption & onboarding - 2026-10-29

Synchronized repository with origin/main (dependency and workflow updates to 5a5f9a0). Verified test suites: 99 Python tests passed (5 live-service skips) via uv/pytest, 11 Node web tests passed. Clean working tree. Completed /grill-me alignment:
- Objective: Ground-up elimination of user experience and test suite flakiness.
- Public Username Onboarding: Simple single-input onboarding ("Enter Letterboxd username" + "Start Swiping") generating public session tokens, removing mandatory extension dependency.
- Resilient Sync: Instant deck presentation from selected lists; non-blocking background public RSS/watchlist ingestion; optional Letterboxd CSV/ZIP file dropzone on setup and in header.
- Extension Bridge Hardening: Resilient auto-reconnect, service worker wake-up retry with backoff, and local localStorage swipe write-back queue (`pending_lb_sync`).
- Test Suite Stabilization: Migrate web state/race logic to fast Node tests (<1s); optimize browser_refresh.py with a shared Chromium instance and event-driven waits (<15s).

Phase 1 completed:
- Added `POST /auth/public-session` (generating encrypted public user session tokens).
- Added `POST /ingest/public-sync` with Letterboxd RSS syndication parsing (`extract_slugs_from_rss`, `pull_rss_diary_slugs`).
- Verified swipe actions and sync status for public user sessions.
- All 100 Python tests pass (5 live-service skips).

Phase 2 completed:
- Hardened extension content script with `safeSendMessage` service worker wake-up retry and exponential backoff.
- Implemented `createSyncQueueStore` in `src/web/state.js` for optimistic buffering and deduplication of pending Letterboxd writes.
- Implemented `parseLetterboxdCsv` and `slugifyTitle` for Letterboxd CSV/ZIP file parsing.
- Verified with 14 Node web unit tests passing (253ms).

Phase 3 & Phase 4 completed:
- Redesigned `index.html` setup screen with direct Letterboxd username entry, extension auto-detection pill, and CSV import link.
- Added CSV drag-and-drop / file selector in setup and header dropdown menu.
- Updated `app.js` with public session management, non-blocking background RSS sync, optimistic card swiping, and automated write-back queue draining.
- Re-tested full browser refresh suite: all 13 Playwright Chromium cases passed in 17.96s (reduced from timeouts).
- Local verification complete: 100 Python tests passed (5 live-service skips), 14 Node web tests passed (129ms), 13 Playwright browser tests passed (17.96s), Python compilation check passed. Working tree clean and verified.
