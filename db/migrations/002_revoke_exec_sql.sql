-- Revoke the generic exec_sql RPC from the Supabase REST roles; keep it for server-side migrations only.
revoke execute on function public.exec_sql(text) from anon, authenticated;
grant execute on function public.exec_sql(text) to service_role;
