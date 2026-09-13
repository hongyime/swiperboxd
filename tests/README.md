# Tests

`npm test` runs the isolated Python/API suite and Node browser-state tests.
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
