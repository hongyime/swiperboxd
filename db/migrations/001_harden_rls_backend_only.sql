-- Swiperboxd: backend-only Supabase access hardening
-- Goal: block direct anon/authenticated table reads/writes.
-- App traffic should go through backend APIs using service_role on server.

begin;

-- Ensure table-level privilege model is restrictive for browser roles.
do $$
declare
  t text;
  p record;
begin
  foreach t in array array[
    'users',
    'movies',
    'watchlist',
    'diary',
    'exclusions',
    'genre_preferences',
    'list_summaries',
    'list_memberships'
  ]
  loop
    if to_regclass(format('public.%I', t)) is null then
      continue;
    end if;

    execute format('revoke all on table public.%I from anon, authenticated', t);
    execute format('alter table public.%I enable row level security', t);
    execute format('alter table public.%I force row level security', t);

    for p in
      select policyname
      from pg_policies
      where schemaname = 'public' and tablename = t
    loop
      execute format('drop policy if exists %I on public.%I', p.policyname, t);
    end loop;

    execute format(
      'create policy %I on public.%I as permissive for anon using (false) with check (false)',
      t || '_deny_anon',
      t
    );
    execute format(
      'create policy %I on public.%I as permissive for authenticated using (false) with check (false)',
      t || '_deny_authenticated',
      t
    );
  end loop;
end $$;

commit;
