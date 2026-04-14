# Scripts

## `smoke_test_app.py`

Runs a lightweight end-to-end smoke flow against the FastAPI app using current environment variables:

- `/health`
- `/discovery/profiles`
- `/ingest/start`
- `/discovery/deck`
- `/discovery/details`
- `/auth/session` (with `TEST_TARGET_USERNAME`/`TEST_TARGET_PASSWORD`, fallback to `LETTERBOXD_*`)

### Required env vars

- `MASTER_ENCRYPTION_KEY`
- `TEST_TARGET_USERNAME` + `TEST_TARGET_PASSWORD` (or `LETTERBOXD_USERNAME` + `LETTERBOXD_PASSWORD`)

### Run

```bash
python scripts/smoke_test_app.py
```
