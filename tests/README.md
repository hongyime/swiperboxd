# Tests

`npm test` runs the isolated Python/API, Node browser-state and extension-worker tests.
Python fixtures disable dotenv credentials and remote sockets; optional live-service
cases remain skipped when those services are absent.

Run `python -m playwright install chromium`, then `python tests/browser_refresh.py`
for real Chromium coverage of local catalogue search, keyboard input, overlapping
deck reads, fallback cancellation, account changes, timeouts, status failures and
first-sync recovery. API and extension responses are synthetic; writes and
unexpected provider requests are rejected. The required Build check runs this suite.

`SWIPER_BROWSER_URL` can select deployed assets, and `SWIPER_WIDTH=390` checks a
mobile viewport with the same intercepted API fixtures. These checks do not prove
live Letterboxd or Supabase synchronization. Status transport tests use the actual
PostgREST SDK with synthetic HTTP responses, including counts over 1,000 records.

The extension worker fixtures execute its actual alarm and message listeners with
synthetic Chrome storage and alarms. They check six-hour scheduling, retained-alarm
migration, disabled/stale events and manual sync without network or cookie access.

After `vercel build`, run `python tests/verify_vercel_output.py` to inspect its
generated static/function artifacts. It compares public file contents and security
headers against local FastAPI responses, follows HTML/module asset references and
checks that API routes still reach Python without a new shared cache.
