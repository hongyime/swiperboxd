"""Guardrails for migration 001: PostgreSQL has no CREATE POLICY IF NOT EXISTS."""

from pathlib import Path

MIGRATION = Path(__file__).parent.parent / "db" / "migrations" / "001_keepalive_rpc.sql"


def test_migration_001_uses_idempotent_create_policy():
    sql = MIGRATION.read_text()
    lowered = sql.lower()

    assert "create policy if not exists" not in lowered
    assert "do $$" in lowered
    assert "pg_policies" in lowered
    assert 'create policy "allow keepalive for all"' in lowered
    assert "grant select, insert on public.keepalive_logs to anon" in lowered
    assert "keep_alive_ping" in lowered
