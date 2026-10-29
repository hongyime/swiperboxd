-- Cross-instance rate limiting + ingest lease.
create table if not exists public.user_throttle (
  user_id text primary key,
  last_action_at timestamptz,
  last_scrape_at timestamptz,
  ingest_running_at timestamptz
);
alter table public.user_throttle enable row level security;
alter table public.user_throttle force row level security;
do $$
begin
  if not exists (select 1 from pg_policies where schemaname='public' and tablename='user_throttle' and policyname='user_throttle_deny_anon') then
    create policy user_throttle_deny_anon on public.user_throttle for all to anon using (false) with check (false);
  end if;
  if not exists (select 1 from pg_policies where schemaname='public' and tablename='user_throttle' and policyname='user_throttle_deny_authenticated') then
    create policy user_throttle_deny_authenticated on public.user_throttle for all to authenticated using (false) with check (false);
  end if;
end $$;
grant all on public.user_throttle to service_role;

create or replace function public.throttle_claim(p_user_id text, p_column text, p_window_seconds double precision)
returns boolean language plpgsql security definer as $$
declare claimed boolean;
begin
  if p_column not in ('last_action_at','last_scrape_at','ingest_running_at') then
    raise exception 'invalid throttle column %', p_column;
  end if;
  insert into public.user_throttle(user_id) values (p_user_id) on conflict (user_id) do nothing;
  execute format(
    'update public.user_throttle set %1$I = now() where user_id = $1 and (%1$I is null or %1$I < now() - make_interval(secs => $2)) returning true',
    p_column
  ) into claimed using p_user_id, p_window_seconds;
  return coalesce(claimed, false);
end; $$;

create or replace function public.throttle_release(p_user_id text)
returns void language plpgsql security definer as $$
begin
  update public.user_throttle set ingest_running_at = null where user_id = p_user_id;
end; $$;

revoke execute on function public.throttle_claim(text, text, double precision) from anon, authenticated;
grant execute on function public.throttle_claim(text, text, double precision) to service_role;
revoke execute on function public.throttle_release(text) from anon, authenticated;
grant execute on function public.throttle_release(text) to service_role;
