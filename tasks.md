# tasks.md

## Execution Policy
- Order is strict and sequential.
- Each completed item requires:
  - acceptance verification,
  - `bugfix.md` status update,
  - checkpoint entry.

## Project Skeleton Outline

```text
.
├── .github/workflows/
├── db/
│   └── migrations/
├── scripts/
├── src/
│   ├── api/
│   ├── shared/
│   └── web/
├── tests/
├── package.json
└── pyproject.toml
```

## Task List

### T-001 — Bootstrap runtime manifests and repo structure
- **Description:** Add minimal dependency manifests and base directories for client, serverless APIs, and migrations.
- **Acceptance Criteria:**
  - Manifest file(s) exist and install command resolves.
  - Directory skeleton exists for app, API, and database artifacts.
  - `B-010` moves to Fixed if install checks pass.
- **Status:** Complete
- **Validation:** `npm run bootstrap:check`

### T-002 — Implement auth/session endpoint contract
- **Description:** Add initial endpoint with validation, error envelope, and placeholder secure session flow.
- **Acceptance Criteria:**
  - Endpoint route exists and validates input.
  - Structured 2xx/4xx/5xx responses implemented.
  - `B-002` moves to Fixed when endpoint and tests pass.
- **Status:** Complete
- **Validation:** `pytest -q`

### T-003 — Implement encryption interface and env contract
- **Description:** Add encryption utility boundary and required environment variables documentation.
- **Acceptance Criteria:**
  - Encryption utility module exists with test coverage.
  - Environment-variable contract file exists.
  - `B-003` moves to Fixed when tests pass.
- **Status:** Complete
- **Validation:** `pytest -q`

### T-004 — Implement ingestion start endpoint + queue abstraction
- **Description:** Add endpoint and queue dispatcher interface for background ingestion jobs.
- **Acceptance Criteria:**
  - Ingestion endpoint exists with idempotent request handling.
  - Queue abstraction has mock-backed tests.
  - `B-004` moves to Fixed when endpoint/test checks pass.
- **Status:** Complete
- **Validation:** `pytest -q`

### T-005 — Implement discovery deck endpoint and cache/upsert abstraction
- **Description:** Add discovery endpoint with filtering contract and cache/persistence boundaries.
- **Acceptance Criteria:**
  - Endpoint contract implemented and tested.
  - Cache/persistence interfaces compile and are unit tested.
  - `B-006` moves to Fixed when tests pass.
- **Status:** Complete
- **Validation:** `pytest -q`

### T-006 — Implement client state placeholders for progress and suppression list
- **Description:** Add client state contract for ingestion progress and 24-hour suppression.
- **Acceptance Criteria:**
  - Progress state and suppression state logic implemented.
  - Unit tests validate TTL and reset logic.
  - `B-005` moves to Fixed when tests pass.
- **Status:** Complete
- **Validation:** `node --test tests/web_state.test.js`

### T-007 — Implement resilience policy module
- **Description:** Add centralized retry/backoff and rate-limit response handling utility.
- **Acceptance Criteria:**
  - Exponential backoff strategy implemented and tested.
  - Standardized handling for 429/403 responses exists.
  - `B-007` moves to Fixed when tests pass.
- **Status:** Complete
- **Validation:** `pytest -q`

### T-008 — Implement operations workflows
- **Description:** Add CI quality gates and keep-alive workflow plus DB RPC script artifact.
- **Acceptance Criteria:**
  - CI workflow runs lint/type-check/tests.
  - Keep-alive workflow file and DB RPC script exist.
  - `B-008` and `B-009` move to Fixed when workflow linting passes.
- **Status:** Complete
- **Validation:** `pytest -q`

## Checkpoint Log

- 2026-04-14 / CP-001:
  - Phase 1 completed.
  - Phase 2 persistence scaffolding established via this task file and logs.
  - Ready to start Phase 3 in the next execution step.

- 2026-04-14 / CP-002:
  - T-001 executed.
  - Skeleton manifests and directory layout created.
  - Validation command recorded; ready for T-002.

- 2026-04-14 / CP-003:
  - T-002 through T-008 executed at baseline contract level.
  - Added API routes, security utilities, resilience policy, state module, tests, and workflows.
  - Ready for integration with real external providers and persistent services.

- 2026-04-14 / CP-004:
  - Phase 3 expansion executed to deliver runnable full-stack baseline.
  - Added browser UI, profile loading, ingest progress polling, and swipe action sync.
  - Extended API with health endpoint, profile endpoint, progress endpoint, and sync-lock enforcement.

- 2026-04-14 / CP-005:
  - Vercel hardening pass completed: added `vercel.json`, `api/index.py`, and `requirements.txt`.
  - Added Letterboxd scraper abstraction module with CSRF-aware login path scaffold.
  - Implemented filter-first ingestion pipeline, genre weighting, and just-in-time metadata endpoint.
