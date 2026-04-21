-- ============================================================================
-- Swiperboxd: Supabase Schema Initialization
-- ============================================================================
-- Purpose: Bootstrap all tables, indexes, and RLS policies for new users.
-- 
-- This migration creates the complete database schema required for the
-- Swiperboxd movie discovery platform. It should be run FIRST when setting
-- up a new Supabase project.
--
-- After running this migration, your Supabase project will have:
--   - 8 tables with proper constraints and indexes
--   - Row Level Security (RLS) enabled on all app tables
--   - Backend-only access pattern (browser clients use API endpoints)
--   - Helper RPC function for running future migrations
--
-- IMPORTANT: Run this in the Supabase SQL Editor before deploying the app.
-- ============================================================================

begin;

-- ============================================================================
-- HELPER RPC FUNCTION FOR MIGRATIONS
-- ============================================================================
-- Required by run_migrations() in src/api/database.py
-- This allows the backend to execute arbitrary SQL via RPC.

create or replace function exec_sql(sql text)
returns void
language plpgsql
security definer
as $$
begin
  execute sql;
end;
$$;

comment on function exec_sql(text) is
  'Execute arbitrary SQL. Used by backend migrations. Requires service role key.';

-- ============================================================================
-- USERS TABLE
-- ============================================================================
-- Stores Letterboxd user credentials and encrypted session tokens.

create table if not exists public.users (
  id uuid primary key default gen_random_uuid(),
  letterboxd_username text unique not null,
  letterboxd_session text,  -- Encrypted session token (Fernet)
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_users_letterboxd_username on public.users(letterboxd_username);

comment on table public.users is 'Letterboxd user accounts with encrypted session tokens';
comment on column public.users.letterboxd_session is 'Fernet-encrypted Letterboxd session cookie';

-- ============================================================================
-- MOVIES TABLE
-- ============================================================================
-- Caches movie metadata scraped from Letterboxd to avoid repeated scraping.

create table if not exists public.movies (
  slug text primary key,  -- URL-friendly identifier, e.g., "the-shawshank-redemption"
  title text not null,
  poster_url text,
  rating float default 0.0,
  popularity int default 0,
  genres text[] default '{}',
  synopsis text default '',
  cast text[] default '{}',
  year int,
  director text,
  lb_film_id text,  -- Letterboxd internal film ID for deep links
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Indexes for common queries
create index if not exists idx_movies_popularity on public.movies(popularity desc);
create index if not exists idx_movies_rating on public.movies(rating desc);
create index if not exists idx_movies_year on public.movies(year desc);
create index if not exists idx_movies_genres on public.movies using gin(genres);

comment on table public.movies is 'Cached movie metadata from Letterboxd';
comment on column public.movies.slug is 'URL-friendly identifier matching Letterboxd URL';

-- ============================================================================
-- WATCHLIST TABLE
-- ============================================================================
-- Movies users want to watch (synced from Letterboxd watchlist).

create table if not exists public.watchlist (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  movie_slug text not null references public.movies(slug) on delete cascade,
  created_at timestamptz not null default now(),
  constraint uq_watchlist_user_movie unique (user_id, movie_slug)
);

create index if not exists idx_watchlist_user_id on public.watchlist(user_id);
create index if not exists idx_watchlist_movie_slug on public.watchlist(movie_slug);

comment on table public.watchlist is 'User watchlist - movies they want to watch';

-- ============================================================================
-- DIARY TABLE
-- ============================================================================
-- Movies users have watched (synced from Letterboxd diary).

create table if not exists public.diary (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  movie_slug text not null references public.movies(slug) on delete cascade,
  created_at timestamptz not null default now(),
  constraint uq_diary_user_movie unique (user_id, movie_slug)
);

create index if not exists idx_diary_user_id on public.diary(user_id);
create index if not exists idx_diary_movie_slug on public.diary(movie_slug);

comment on table public.diary is 'User diary - movies they have watched';

-- ============================================================================
-- EXCLUSIONS TABLE
-- ============================================================================
-- Movies users have dismissed (swiped left) - prevents re-suggestion.

create table if not exists public.exclusions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  movie_slug text not null references public.movies(slug) on delete cascade,
  created_at timestamptz not null default now(),
  constraint uq_exclusions_user_movie unique (user_id, movie_slug)
);

create index if not exists idx_exclusions_user_id on public.exclusions(user_id);
create index if not exists idx_exclusions_movie_slug on public.exclusions(movie_slug);

comment on table public.exclusions is 'User exclusions - movies dismissed via swipe left';

-- ============================================================================
-- GENRE PREFERENCES TABLE
-- ============================================================================
-- Tracks user genre preferences based on swipe behavior for weighted sorting.

create table if not exists public.genre_preferences (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  genre text not null,
  score int default 1,
  updated_at timestamptz not null default now(),
  constraint uq_genre_pref_user_genre unique (user_id, genre)
);

create index if not exists idx_genre_preferences_user_id on public.genre_preferences(user_id);

comment on table public.genre_preferences is 'User genre preference scores for weighted sorting';

-- ============================================================================
-- LIST SUMMARIES TABLE
-- ============================================================================
-- Caches metadata about Letterboxd lists (official and community).

create table if not exists public.list_summaries (
  list_id text primary key,  -- Letterboxd list identifier
  slug text,
  url text,
  title text,
  owner_name text,
  owner_slug text,
  description text default '',
  film_count int default 0,
  like_count int default 0,
  comment_count int default 0,
  is_official boolean default false,
  tags text[] default '{}',
  scraped_film_count int default 0,
  updated_at timestamptz not null default now()
);

create index if not exists idx_list_summaries_film_count on public.list_summaries(film_count desc);
create index if not exists idx_list_summaries_is_official on public.list_summaries(is_official);

comment on table public.list_summaries is 'Cached Letterboxd list metadata';

-- ============================================================================
-- LIST MEMBERSHIPS TABLE
-- ============================================================================
-- Junction table linking lists to movies with position ordering.

create table if not exists public.list_memberships (
  id uuid primary key default gen_random_uuid(),
  list_id text not null references public.list_summaries(list_id) on delete cascade,
  movie_slug text not null references public.movies(slug) on delete cascade,
  position int not null,
  constraint uq_list_membership unique (list_id, movie_slug)
);

create index if not exists idx_list_memberships_list_id on public.list_memberships(list_id);
create index if not exists idx_list_memberships_movie_slug on public.list_memberships(movie_slug);
create index if not exists idx_list_memberships_position on public.list_memberships(list_id, position);

comment on table public.list_memberships is 'Junction table: lists <-> movies with ordering';

-- ============================================================================
-- ROW LEVEL SECURITY (RLS)
-- ============================================================================
-- Enforce backend-only access pattern:
-- - Browser clients should NOT connect directly to Supabase
-- - All data access goes through backend API using service_role key
-- - RLS policies block anon/authenticated roles entirely

-- Enable RLS on all application tables
alter table public.users enable row level security;
alter table public.movies enable row level security;
alter table public.watchlist enable row level security;
alter table public.diary enable row level security;
alter table public.exclusions enable row level security;
alter table public.genre_preferences enable row level security;
alter table public.list_summaries enable row level security;
alter table public.list_memberships enable row level security;

-- Force RLS even for table owners (extra safety)
alter table public.users force row level security;
alter table public.movies force row level security;
alter table public.watchlist force row level security;
alter table public.diary force row level security;
alter table public.exclusions force row level security;
alter table public.genre_preferences force row level security;
alter table public.list_summaries force row level security;
alter table public.list_memberships force row level security;

-- Drop any existing policies (in case of re-run)
do $$
declare
  r record;
begin
  for r in (
    select schemaname, tablename, policyname 
    from pg_policies 
    where schemaname = 'public'
  ) loop
    execute format('drop policy if exists %I on public.%I', r.policyname, r.tablename);
  end loop;
end $$;

-- Create deny-all policies for anon and authenticated roles
-- These roles should NOT be used by the application

create policy users_deny_anon on public.users
  for all to anon
  using (false) with check (false);

create policy users_deny_authenticated on public.users
  for all to authenticated
  using (false) with check (false);

create policy movies_deny_anon on public.movies
  for all to anon
  using (false) with check (false);

create policy movies_deny_authenticated on public.movies
  for all to authenticated
  using (false) with check (false);

create policy watchlist_deny_anon on public.watchlist
  for all to anon
  using (false) with check (false);

create policy watchlist_deny_authenticated on public.watchlist
  for all to authenticated
  using (false) with check (false);

create policy diary_deny_anon on public.diary
  for all to anon
  using (false) with check (false);

create policy diary_deny_authenticated on public.diary
  for all to authenticated
  using (false) with check (false);

create policy exclusions_deny_anon on public.exclusions
  for all to anon
  using (false) with check (false);

create policy exclusions_deny_authenticated on public.exclusions
  for all to authenticated
  using (false) with check (false);

create policy genre_preferences_deny_anon on public.genre_preferences
  for all to anon
  using (false) with check (false);

create policy genre_preferences_deny_authenticated on public.genre_preferences
  for all to authenticated
  using (false) with check (false);

create policy list_summaries_deny_anon on public.list_summaries
  for all to anon
  using (false) with check (false);

create policy list_summaries_deny_authenticated on public.list_summaries
  for all to authenticated
  using (false) with check (false);

create policy list_memberships_deny_anon on public.list_memberships
  for all to anon
  using (false) with check (false);

create policy list_memberships_deny_authenticated on public.list_memberships
  for all to authenticated
  using (false) with check (false);

-- ============================================================================
-- GRANT PERMISSIONS FOR SERVICE ROLE
-- ============================================================================
-- The service_role key bypasses RLS, but we still need table permissions.

-- Note: In Supabase, service_role typically has full access by default.
-- These grants are included for completeness and self-hosted Postgres.
grant all on all tables in schema public to service_role;
grant all on all sequences in schema public to service_role;

commit;

-- ============================================================================
-- COMPLETION MESSAGE
-- ============================================================================
-- After running this migration, verify setup by checking:
--   1. Tables exist: SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';
--   2. RLS enabled: SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public';
--   3. exec_sql function exists: SELECT proname FROM pg_proc WHERE proname = 'exec_sql';

-- Success indicator
select 'Swiperboxd schema initialization complete!' as status;
