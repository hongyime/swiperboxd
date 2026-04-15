"""
One-shot database setup script.
Runs against the Supabase Management API — requires SUPABASE_MGMT_TOKEN env var.
Safe to re-run: all statements are idempotent (IF NOT EXISTS / OR REPLACE).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from textwrap import dedent

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MGMT_TOKEN = os.environ.get("SUPABASE_MGMT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
PROJECT_REF = SUPABASE_URL.split("//")[-1].split(".")[0] if SUPABASE_URL else ""

if not MGMT_TOKEN:
    sys.exit("ERROR: SUPABASE_MGMT_TOKEN env var is required.")
if not PROJECT_REF:
    sys.exit("ERROR: SUPABASE_URL env var is required to derive the project ref.")

API_URL = f"https://api.supabase.com/v1/projects/{PROJECT_REF}/database/query"
HEADERS = {
    "Authorization": f"Bearer {MGMT_TOKEN}",
    "Content-Type": "application/json",
}

MIGRATIONS_DIR = Path(__file__).parent.parent / "db" / "migrations"
KEEP_ALIVE_SQL = Path(__file__).parent.parent / "db" / "sql" / "keep_alive_ping.sql"

# ---------------------------------------------------------------------------
# SQL blocks
# ---------------------------------------------------------------------------

RLS_SETUP_SQL = dedent("""
    -- -----------------------------------------------------------------------
    -- Enable RLS on all tables
    -- -----------------------------------------------------------------------
    ALTER TABLE movies            ENABLE ROW LEVEL SECURITY;
    ALTER TABLE users             ENABLE ROW LEVEL SECURITY;
    ALTER TABLE watchlist         ENABLE ROW LEVEL SECURITY;
    ALTER TABLE diary             ENABLE ROW LEVEL SECURITY;
    ALTER TABLE exclusions        ENABLE ROW LEVEL SECURITY;
    ALTER TABLE genre_preferences ENABLE ROW LEVEL SECURITY;

    -- -----------------------------------------------------------------------
    -- Drop all legacy cosmetic policies (safe if they don't exist)
    -- -----------------------------------------------------------------------
    DO $$ DECLARE pol RECORD;
    BEGIN
        FOR pol IN
            SELECT policyname, tablename
            FROM pg_policies
            WHERE schemaname = 'public'
        LOOP
            EXECUTE format('DROP POLICY IF EXISTS %I ON %I', pol.policyname, pol.tablename);
        END LOOP;
    END $$;

    -- -----------------------------------------------------------------------
    -- movies: public read, write locked to service_role only
    -- (service_role bypasses RLS by default — this just blocks anon reads
    --  if you ever expose the anon key directly)
    -- -----------------------------------------------------------------------
    CREATE POLICY "movies_public_read"
        ON movies FOR SELECT
        USING (true);

    -- -----------------------------------------------------------------------
    -- User-data tables: NO policies = default deny for all roles except
    -- service_role (which bypasses RLS). The backend always uses service_role.
    -- -----------------------------------------------------------------------
    -- users, watchlist, diary, exclusions, genre_preferences:
    -- zero policies intentionally — anon/authenticated get nothing.
""")

UPDATED_AT_TRIGGER_SQL = dedent("""
    -- -----------------------------------------------------------------------
    -- Auto-update updated_at on movies and genre_preferences
    -- -----------------------------------------------------------------------
    CREATE OR REPLACE FUNCTION set_updated_at()
    RETURNS TRIGGER AS $$
    BEGIN
        NEW.updated_at = NOW();
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS trg_movies_updated_at ON movies;
    CREATE TRIGGER trg_movies_updated_at
        BEFORE UPDATE ON movies
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();

    DROP TRIGGER IF EXISTS trg_genre_prefs_updated_at ON genre_preferences;
    CREATE TRIGGER trg_genre_prefs_updated_at
        BEFORE UPDATE ON genre_preferences
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
""")

REVOKE_ANON_SQL = dedent("""
    -- -----------------------------------------------------------------------
    -- Revoke all direct table privileges from anon and authenticated roles.
    -- All DB access goes through the backend using service_role.
    -- -----------------------------------------------------------------------
    REVOKE ALL ON TABLE movies            FROM anon, authenticated;
    REVOKE ALL ON TABLE users             FROM anon, authenticated;
    REVOKE ALL ON TABLE watchlist         FROM anon, authenticated;
    REVOKE ALL ON TABLE diary             FROM anon, authenticated;
    REVOKE ALL ON TABLE exclusions        FROM anon, authenticated;
    REVOKE ALL ON TABLE genre_preferences FROM anon, authenticated;

    -- Re-grant only the movies SELECT (needed for the RLS read policy)
    GRANT SELECT ON TABLE movies TO anon, authenticated;
""")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_success(status_code: int) -> bool:
    """Supabase Management API returns 200 for SELECT results, 201 for DDL with no rows."""
    return status_code in {200, 201}


def run_sql(label: str, sql: str) -> bool:
    """Execute a SQL block via the Supabase Management API. Returns True on success."""
    try:
        r = httpx.post(API_URL, headers=HEADERS, json={"query": sql.strip()}, timeout=30)
        if _is_success(r.status_code):
            print(f"  [OK] {label}")
            return True
        print(f"  [FAIL] {label}")
        try:
            detail = r.json()
            print(f"        {detail.get('message') or detail}")
        except Exception:
            print(f"        HTTP {r.status_code}: {r.text[:300]}")
        return False
    except Exception as exc:
        print(f"  [ERROR] {label}: {exc}")
        return False


def query(sql: str) -> list[dict] | None:
    """Run a SELECT query and return rows, or None on error."""
    try:
        r = httpx.post(API_URL, headers=HEADERS, json={"query": sql.strip()}, timeout=30)
        if _is_success(r.status_code):
            return r.json() or []
        return None
    except Exception:
        return None


def validate_tables(expected: list[str]) -> bool:
    """Check that all expected tables exist in public schema."""
    rows = query(
        f"SELECT table_name FROM information_schema.tables "
        f"WHERE table_schema = 'public' AND table_name = ANY(ARRAY{expected});"
    )
    if rows is None:
        print("  [FAIL] Could not query information_schema")
        return False
    found = {row["table_name"] for row in rows}
    missing = set(expected) - found
    if missing:
        print(f"  [FAIL] Missing tables: {sorted(missing)}")
        return False
    print(f"  [OK]  All tables present: {sorted(found)}")
    return True


def validate_rls(expected: list[str]) -> bool:
    """Check that RLS is enabled on all expected tables."""
    rows = query(
        f"SELECT relname FROM pg_class "
        f"JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace "
        f"WHERE pg_namespace.nspname = 'public' AND relrowsecurity = true "
        f"AND relname = ANY(ARRAY{expected});"
    )
    if rows is None:
        print("  [FAIL] Could not query pg_class")
        return False
    found = {row["relname"] for row in rows}
    missing = set(expected) - found
    if missing:
        print(f"  [FAIL] RLS not enabled on: {sorted(missing)}")
        return False
    print(f"  [OK]  RLS enabled on: {sorted(found)}")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print(f"\nTarget project: {PROJECT_REF}")
    print("=" * 60)
    all_ok = True

    # 1. Canonical migrations (idempotent — all use IF NOT EXISTS)
    print("\n[1/5] Running canonical migrations...")
    canonical = sorted(
        f for f in MIGRATIONS_DIR.glob("*.sql")
        if not f.name.startswith("LEGACY_")
    )
    for mf in canonical:
        ok = run_sql(mf.name, mf.read_text())
        all_ok = all_ok and ok

    # 2. keep_alive_ping function
    print("\n[2/5] Creating keep_alive_ping function...")
    ok = run_sql("keep_alive_ping()", KEEP_ALIVE_SQL.read_text())
    all_ok = all_ok and ok

    # 3. RLS enable + policy reset
    print("\n[3/5] Applying RLS policies...")
    ok = run_sql("RLS enable + policy reset", RLS_SETUP_SQL)
    all_ok = all_ok and ok

    # 4. Revoke anon/authenticated direct access
    print("\n[4/5] Revoking direct anon/authenticated table access...")
    ok = run_sql("REVOKE anon/authenticated", REVOKE_ANON_SQL)
    all_ok = all_ok and ok

    # 5. updated_at triggers
    print("\n[5/5] Creating updated_at triggers...")
    ok = run_sql("set_updated_at trigger", UPDATED_AT_TRIGGER_SQL)
    all_ok = all_ok and ok

    # Validation
    print("\n" + "=" * 60)
    print("Validating schema...")
    TABLES = ["movies", "users", "watchlist", "diary", "exclusions", "genre_preferences"]
    v1 = validate_tables(TABLES)
    v2 = validate_rls(TABLES)
    all_ok = all_ok and v1 and v2

    print("\n" + "=" * 60)
    if all_ok:
        print("SETUP COMPLETE — database is ready.")
    else:
        print("SETUP FINISHED WITH ERRORS — review output above.")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
