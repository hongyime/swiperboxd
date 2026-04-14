# bugfix.md

## Scope
This document tracks defects and risk items identified in the audit and their remediation status.

## Bug Ledger

| ID | Status | Category | Finding | Root Cause | Impact |
|---|---|---|---|---|---|
| B-001 | Fixed | Architecture | No runnable application code exists. | Added runnable API service module, endpoint contracts, and executable tests. | Baseline runtime now exists for local validation. |
| B-002 | Fixed | Security | Session credential handling is undocumented in code and unimplemented. | Added `/auth/session` contract with input validation and encrypted cookie response path. | Authentication flow contract is now testable. |
| B-003 | Fixed | Security | Encryption of session material is unimplemented. | Added encryption/decryption utility using symmetric token encryption. | Session payload protection can now be validated by tests. |
| B-004 | Fixed | Data | Historical ingestion pipeline is unimplemented. | Added ingest endpoint and queue abstraction with enqueue behavior. | Ingestion kickoff is available for integration wiring. |
| B-005 | Fixed | UX/State | Progress indicator and local suppression state are unimplemented. | Added web state module for progress clamping and 24-hour suppression TTL. | Client-side behavior now has deterministic logic and tests. |
| B-006 | Fixed | Discovery | Discovery filtering/scraping/cache-upsert flow is unimplemented. | Added discovery endpoint with exclusion filtering and in-memory upsert store interface. | Deck retrieval contract exists for further provider integration. |
| B-007 | Fixed | Resilience | Retry/backoff and anti-rate-limit controls are unimplemented. | Added centralized exponential backoff and fallback trigger logic. | Resilience policy is now codified and test-covered. |
| B-008 | Fixed | Operations | Keep-alive workflow and DB RPC are unimplemented. | Added keep-alive workflow and SQL RPC function artifact. | Operational uptime guardrails can be configured in CI/secrets. |
| B-009 | Fixed | Maintainability | No tests, linters, type checks, or CI quality gates. | Added API tests, web state tests, and CI workflow with Python + Node runs. | Regression detection baseline now exists. |
| B-010 | Fixed | Dependency Hygiene | No dependency manifests exist. | Runtime manifests added for JS and Python baselines. | Build/install bootstrap is now possible. |

## Progress History

- 2026-04-14: Initial bug ledger created from audit report. All items currently Open.
- 2026-04-14: T-001 completed. B-010 moved to Fixed after manifest bootstrap.
- 2026-04-14: T-002 through T-008 baseline implementation completed; B-001 through B-009 moved to Fixed.
- 2026-04-14: Phase 3 expansion delivered runnable UI + API integration baseline with real route wiring.
