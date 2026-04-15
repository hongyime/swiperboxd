# Database Migrations

## Canonical Execution Order

Run these six files **in order** against your Supabase project. Files prefixed with `LEGACY_` are historical drafts — do not run them.

| # | File | Creates |
|---|------|---------|
| 1 | `001_movies.sql` | `movies` — global film cache |
| 2 | `002_users.sql` | `users` — Letterboxd username → UUID mapping |
| 3 | `003_watchlist.sql` | `watchlist` — per-user watchlist entries |
| 4 | `004_diary.sql` | `diary` — per-user watched films |
| 5 | `005_exclusions.sql` | `exclusions` — per-user dismissed films |
| 6 | `006_genre_preferences.sql` | `genre_preferences` — per-user genre affinity scores |

## How to Run

**Via script (requires `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`):**

```sh
python scripts/run_migrations.py
```

**Via Supabase Dashboard:**

```sh
python scripts/print_migrations.py
```

Then paste each block into the Supabase SQL Editor and run.

## Legacy Files

Files prefixed `LEGACY_` are an earlier schema draft. They are kept for reference only and must not be executed — they conflict with the canonical series above.
