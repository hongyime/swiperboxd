# Bug Ledger

## Scope
This document tracks defects, security vulnerabilities, and technical debt items identified in the reconciliation audit and their remediation status.

---

## Security Vulnerabilities

| ID | Status | Category | Finding | Root Cause | Impact |
|---|---|---|---|---|---|
| SEC-001 | Fixed | Security | Hardcoded secrets in `.env` file | Credentials committed to repository including `LETTERBOXD_PASSWORD`, `SUPABASE_*_KEY`, `UPSTASH_*_TOKEN`, `QSTASH_*` | Replaced `.env` with template, new master key generated |
| SEC-002 | Fixed | Security | `.env` file committed to version control | File exists but should be in `.gitignore` | `.gitignore` already contains `.env`, file replaced with template |
| SEC-003 | Open | High | No password sanitization on upstream auth | Raw password passed to `HttpLetterboxdScraper.login()`, no validation or hashing | Credentials transmitted without sanitization |
| SEC-004 | Open | High | No CSRF token rotation or validation | CSRF token extracted but validity not verified | Vulnerable to CSRF attacks on login flow |
| SEC-005 | Open | Medium | No input validation on `movie_slug` | Only Pydantic `min_length=1` constraint, no character whitelist | Potential injection attacks via slug parameter |
| SEC-006 | Open | Medium | No HTTPS enforcement | Application accepts HTTP requests | Credentials transmitted insecurely |

---

## Logic & Edge Cases

| ID | Status | Category | Finding | Root Cause | Impact |
|---|---|---|---|---|---|
| LOG-001 | Open | High | Race condition in `weighted_shuffle()` | Lock released before list mutation (lines 85-100 in `store.py`) | Inconsistent shuffle results under concurrent access |
| LOG-002 | Open | High | Daemon thread swallows exceptions | `_simulate_ingest()` runs in `daemon=True` thread with no error handling | Failures silently lost, no observability |
| LOG-003 | Open | High | No cleanup for `ingest_progress` | Progress tracking grows unbounded, never reset between sessions | Memory leak over time |
| LOG-004 | Open | Medium | Unbounded `InMemoryStore.actions` | Append-only list with no size limit or archival policy | Memory growth, potential OOM in long-running instances |
| LOG-005 | Open | Medium | Type coercion missing in `set_ingest_progress()` | Accepts int only, no validation or coercion | Potential type errors from malformed input |
| LOG-006 | Open | Medium | Live scraper returns empty data | `HttpLetterboxdScraper.pull_*()` methods return empty stubs | Live mode produces no results, mock-only effectively |

---

## Architecture Gaps

| ID | Status | Category | Finding | Root Cause | Impact |
|---|---|---|---|---|---|
| ARCH-001 | Fixed | Data Layer | Supabase not imported or integrated | No `supabase` library in dependencies, no client code | Added supabase>=2.0.0, created `database.py`, `SupabaseStore` |
| ARCH-002 | Fixed | Data Layer | No database migrations | `db/migrations/` contains only README | Created `001_initial_schema.sql` and `002_rls_policies.sql` |
| ARCH-003 | Fixed | Data Layer | No Row Level Security (RLS) policies | No RLS defined in Supabase | Added RLS policies for user isolation |
| ARCH-004 | Open | Queue | Queue is in-memory stub with no async dispatch | `InMemoryQueue` only appends to list, no background processing | `QStashQueue` implemented but not yet wired into endpoints |
| ARCH-005 | Fixed | Cache | No Redis integration for rate limiting | Rate limiting uses in-memory dictionary | `RedisRateLimiter` implemented with sliding window |
| ARCH-006 | Open | Queue | No QStash integration for background jobs | `QSTASH_*` env vars configured but unused | `QStashQueue` implemented but not yet wired into endpoints |
| ARCH-007 | Open | Scraper | Scraping methods return empty stubs | `HttpLetterboxdScraper` has login but pull methods empty | Cannot fetch real data from Letterboxd |
| ARCH-008 | Open | Scraper | No rotating proxy fallback | `resilience.py` has trigger logic but no proxy integration | Requests blocked by 429/403, no fallback path |
| ARCH-009 | Open | Session | No session cookie storage/persistence | Only encryption in `security.py`, no storage mechanism | Sessions lost after encryption, cannot be retrieved |
| ARCH-010 | Open | Session | No TTL enforcement for sessions | `SESSION_TTL_SECONDS` defined but never checked | Sessions never expire, security risk |
| ARCH-011 | Open | Frontend | Next.js not used, only vanilla JS | Static HTML + plain JS served by FastAPI | No SSR/SSG, limited PWA capabilities |
| ARCH-012 | Open | Frontend | Zustand not used for state management | Plain JS module `state.js` with local state | No centralized store, state management fragmented |
| ARCH-013 | Open | Frontend | No PWA manifest or service worker | No manifest.json or SW registration | Cannot install as app, no offline support |
| ARCH-014 | Open | Frontend | No TypeScript | All JS is untyped | No compile-time type safety, runtime errors possible |
| ARCH-015 | Open | Frontend | No linting configured for JS | No ESLint setup | Code quality issues, inconsistent style |

---

## Maintainability Issues

| ID | Status | Category | Finding | Root Cause | Impact |
|---|---|---|---|---|---|
| MAINT-001 | Open | Type Safety | No Python type stubs beyond basic hints | `@lru_cache` without TTL, incomplete type coverage | IDE assistance limited, potential runtime type errors |
| MAINT-002 | Open | Build | Import path mismatch | `api/index.py` imports from `./providers/` but route is `/api/index.py` | Deployment may fail, import errors possible |
| MAINT-003 | Open | API | No API versioning | All endpoints at root level | Breaking changes require client updates, no backward compatibility |
| MAINT-004 | Open | API | No OpenAPI customization | No description/summary on endpoints | Poor API documentation, auto-generation limited |
| MAINT-005 | Open | Testing | Mock implementation fragile | `test_letterboxd_provider.py` mocks `httpx.Client` globally | Tests may break with library updates |

---

## Progress History

- 2026-04-15: Initial bug ledger created from comprehensive reconciliation audit. All items initially Open.
- 2026-04-15: T-001 completed. Credentials sanitized, new master key generated.
- 2026-04-15: T-002 through T-009 baseline Phase 1 (Data Layer) completed. Supabase integration implemented with migrations, RLS, Store protocol, and conditional store selection in API.
- 2026-04-15: T-011 and T-013 completed. RedisRateLimiter and QStashQueue implemented but not yet wired into endpoints.
- **Entries from earlier audit cycles preserved in `AUDIT_REPORT.md` for reference.**

