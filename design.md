# design.md

## Objective
Define minimal architecture needed to move the system from documentation-only state to a verifiable implementation baseline.

## Proposed Architecture Changes

1. **Frontend Skeleton**
   - Initialize a web client with route structure and state store.
   - Add placeholder interfaces for authentication, deck fetch, and swipe actions.

2. **Backend Skeleton**
   - Add serverless API layer with explicit contracts:
     - `POST /auth/session`
     - `POST /ingest/start`
     - `GET /discovery/deck`
     - `POST /actions/swipe`

3. **Data Model Baseline**
   - Add migration scripts for:
     - `user_exclusions`
     - `movies`
     - optional action-log table for idempotency and auditing.

4. **Queue/Cache Contracts**
   - Define interfaces for rate-limiting and async ingestion dispatch.
   - Add abstraction boundaries so queue/cache providers can be swapped.

5. **Security Contract**
   - Add environment-variable contract document.
   - Define encrypted session storage interface and key-source policy.

6. **Operations Contract**
   - Add CI workflow for lint/type-check/test.
   - Add keep-alive workflow and DB RPC script.

## Interface Contracts (Initial)

- Request/response schemas must be declared before endpoint implementation.
- All external calls must return structured error envelopes.
- Retry/backoff policy should be centralized in one utility module.

## Dependency Compatibility Verification Plan

- Introduce manifests only after choosing a single runtime stack for frontend and backend.
- Lock dependency versions.
- Validate compatibility via:
  - install/build,
  - lint/type-check,
  - smoke tests for endpoint startup.

## State Summary (Phase 2 Checkpoint)

- Phase 1 artifacts created: `bugfix.md`, `design.md`, `tasks.md`.
- No runtime code changes performed yet.
- Execution is paused before Phase 3 implementation by design.
