create or replace function keep_alive_ping()
returns void as $$
begin
  perform 1;
end;
$$ language plpgsql security definer;
