# Decisions

- 2026-09-11: Use database conflict handling for immutable extension membership and placeholder inserts, avoiding read-before-write races. Preserve the existing `added` response meaning (accepted nonblank input entries), rather than representing it as a new-row count.
- 2026-09-11: Release b2d4124 passed all five hosted workflows and exact production deployment/public-route verification. Keep the post-release handoff notes for the next source commit to avoid an extra documentation-only Vercel build. The cryptography advisory remains a separate, uncompleted dependency follow-up.
- 2026-09-11: Update the cryptography pin and minimum to 50.0.1 for GHSA-g6cj-pr64-35w5. Keep key derivation and token serialization unchanged. Synthetic token decoding, tamper rejection and wrong-key rejection passed in both directions between 48.0.1 and 50.0.1, preserving a rollback path without inspecting real sessions.
- 2026-09-11: Dependency release 79e92a0 passed all six hosted workflows and production public-route checks; GitHub closed advisory 7 and reported no remaining open dependency alerts. Publish this verified handoff so the next repository rotation can start with a synchronized checkout.


2026-09-12 — portfolio build-check repair: make the named Build check validate this application using existing fixture tests and production build/entrypoint checks. Preserve live data and existing collector behavior. No provider workflows are invoked for testing. Required-check enforcement and bot reactivation remain open because the shared heartbeat still pushes directly to main.

- [ ] Preserve original clean checkouts and work from current remote commits.
- [ ] Make GMapLists run its existing unit suite and production TypeScript/Vite build on every PR.
- [ ] Make Swiperboxd Build run the existing isolated Python and web suites; retain its legacy test workflow for manual use without duplicate automatic runs.
- [ ] Validate locally, publish small PRs, verify hosted checks and production deployments.
- [ ] Keep bot reactivation and required-check enforcement open until heartbeat direct commits are accounted for.

2026-09-12 validation: the replacement Build workflow runs the existing Python/API and browser-state suites instead of skipping when package.json has no build script. Local Python 3.12 checks pass: 72 tests, five optional live-service skips, four web tests, compilation of src/api and compatibility of 51 installed packages. The legacy Swiperboxd Tests workflow is retained for manual diagnostics; automatic runs are consolidated under Build Check. Application code, dependencies, provider calls and collection schedules are unchanged by this repair. Hosted checks and production verification are next.
