# Current work

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
