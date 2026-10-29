-- Atomic replace of a list's memberships (delete + insert in one transaction).
create or replace function public.replace_list_memberships_atomic(p_list_id text, p_slugs jsonb)
returns void
language plpgsql
security definer
as $$
begin
  delete from public.list_memberships where list_id = p_list_id;
  insert into public.list_memberships (list_id, movie_slug, position)
  select p_list_id, t.slug, (t.ord - 1)::int
  from jsonb_array_elements_text(p_slugs) with ordinality as t(slug, ord)
  where t.slug is not null and t.slug <> '';
end;
$$;

revoke execute on function public.replace_list_memberships_atomic(text, jsonb) from public, anon, authenticated;
grant execute on function public.replace_list_memberships_atomic(text, jsonb) to service_role;
