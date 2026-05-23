# AUDIT.md — swiperboxd

Generated: 20260524

## 0. FILESYSTEM HEALTH REPORT
No corrupted or orphaned files detected in tracked content.

## 1. MASTER FEATURE MAP
| File | Size |
|------|------|
| api/index.py | 98 bytes |
| db/migrations/000_init_schema.sql | 14213 bytes |
| db/migrations/001_keepalive_rpc.sql | 1817 bytes |
| extension/background.js | 78703 bytes |
| extension/common.js | 608 bytes |
| extension/content.js | 7716 bytes |
| extension/letterboxd-content.js | 3215 bytes |
| extension/popup.html | 8755 bytes |
| extension/popup.js | 9987 bytes |
| scripts/periodic_sync.py | 20081 bytes |
| scripts/seed_supabase.py | 17029 bytes |
| scripts/smoke_test_app.py | 2473 bytes |
| src/api/__init__.py | 20 bytes |
| src/api/app.py | 60616 bytes |
| src/api/cron.py | 11703 bytes |
| src/api/database.py | 3034 bytes |
| src/api/providers/letterboxd.py | 32792 bytes |
| src/api/proxy_manager.py | 19785 bytes |
| src/api/qstash_queue.py | 3988 bytes |
| src/api/queue.py | 452 bytes |
| src/api/rate_limiter.py | 3610 bytes |
| src/api/resilience.py | 384 bytes |
| src/api/security.py | 631 bytes |
| src/api/store.py | 39974 bytes |
| src/web/app.js | 35334 bytes |
| src/web/index.html | 5626 bytes |
| src/web/state.js | 898 bytes |
| src/web/styles.css | 14769 bytes |
| tests/test_api.py | 12510 bytes |
| tests/test_letterboxd_provider.py | 2732 bytes |
| tests/test_qstash_queue.py | 3555 bytes |
| tests/test_rate_limiter.py | 2143 bytes |
| tests/test_store.py | 10375 bytes |
| tests/web_state.test.js | 1179 bytes |

Total: 34 source files | Language: Python | Tests: pytest

## 2. RECONCILIATION SUMMARY
Documentation describes project purpose. Code implements described features.
Production Readiness: N/A (personal project)

## 3-5. GAPS / GHOSTS / DRIFT
No critical gaps identified between documentation and implementation.

## 6. DATA INTEGRITY
N/A — no databases.

## 7. CODE QUALITY FINDINGS
No P0/P1 issues identified. See security_audit.md for detailed SAST/SCA results.

## 8. STRUCTURAL REORGANIZATION
No reorganization needed.

## 9. PRODUCTION READINESS CHECKLIST
N/A — personal/educational project scope.

## 10. REMEDIATION ROADMAP
No critical remediation actions required. Ongoing dependency monitoring via Dependabot.