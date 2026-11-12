-- Persist ingest state across serverless instances.
alter table public.user_throttle
  add column if not exists ingest_progress smallint not null default 0,
  add column if not exists ingest_error jsonb,
  add column if not exists ingest_progress_updated_at timestamptz;
