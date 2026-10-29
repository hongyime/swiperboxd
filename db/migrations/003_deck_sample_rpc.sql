-- Return a random sample of UNSEEN movies for a list in one query.
create or replace function public.deck_sample(p_list_id text, p_seen text[], p_sample_size int)
returns setof public.movies
language sql
security definer
as $$
  select m.*
  from public.list_memberships lm
  join public.movies m on m.slug = lm.movie_slug
  where lm.list_id = p_list_id
    and not (m.slug = any(coalesce(p_seen, array[]::text[])))
  order by random()
  limit greatest(p_sample_size, 0);
$$;

revoke execute on function public.deck_sample(text, text[], int) from public, anon, authenticated;
grant execute on function public.deck_sample(text, text[], int) to service_role;
