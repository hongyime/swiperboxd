-- Randomized, bounded discovery candidates without loading the catalog in Python.
create or replace function public.discovery_sample(
  p_min_rating double precision,
  p_max_popularity integer,
  p_sample_size integer
)
returns setof public.movies
language sql
security definer
as $$
  select m.*
  from public.movies m
  where m.rating >= p_min_rating
    and (p_max_popularity is null or m.popularity <= p_max_popularity)
  order by random()
  limit greatest(p_sample_size, 0);
$$;

revoke execute on function public.discovery_sample(double precision, integer, integer) from public, anon, authenticated;
grant execute on function public.discovery_sample(double precision, integer, integer) to service_role;
