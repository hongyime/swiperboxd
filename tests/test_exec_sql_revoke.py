"""Guardrail: exec_sql must stay reachable only by service_role."""

from pathlib import Path

from src.api import database

MIGRATION = Path(__file__).parent.parent / "db" / "migrations" / "002_revoke_exec_sql.sql"


class _FakeResponse:
    def execute(self):
        return None


class _FakeClient:
    def __init__(self):
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _FakeResponse()


def test_migration_002_revokes_exec_sql_from_rest_roles(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(database, "get_supabase_client", lambda: fake)

    database.run_migrations()

    assert MIGRATION.exists()

    revoke_sql = [
        params["sql"]
        for name, params in fake.calls
        if name == "exec_sql"
        and "revoke execute on function public.exec_sql(text)" in params["sql"].lower()
    ]
    assert revoke_sql, "002_revoke_exec_sql.sql was not executed"
    assert all(
        "revoke execute on function public.exec_sql(text) from anon, authenticated"
        in sql.lower()
        for sql in revoke_sql
    )
    assert all(
        "grant execute on function public.exec_sql(text) to service_role" in sql.lower()
        for sql in revoke_sql
    )
    assert all("from public" not in sql.lower() for sql in revoke_sql)
