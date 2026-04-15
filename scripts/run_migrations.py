"""Script to execute SQL migrations on Supabase database."""

import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from supabase import Client, create_client


def execute_migration_sql(client: Client, sql: str, migration_name: str) -> bool:
    """Execute a single SQL migration."""
    try:
        # Split SQL by semicolons and execute each statement
        statements = [s.strip() for s in sql.split(';') if s.strip() and not s.strip().startswith('--')]

        for statement in statements:
            if statement:
                client.rpc('exec_sql', {'sql': statement}).execute()

        print(f"✓ {migration_name} executed successfully")
        return True
    except Exception as e:
        print(f"✗ {migration_name} failed: {e}")
        return False


def run_migrations():
    """Run all pending migrations."""
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")  # Use service role for migrations

    if not supabase_url or not supabase_key:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        return False

    print(f"Connecting to Supabase: {supabase_url}")

    try:
        client: Client = create_client(supabase_url, supabase_key)
    except Exception as e:
        print(f"Error connecting to Supabase: {e}")
        return False

    print("Running migrations...")

    migrations_dir = Path(__file__).parent.parent / "db" / "migrations"
    migration_files = sorted(f for f in migrations_dir.glob("*.sql") if not f.name.startswith("LEGACY_"))

    if not migration_files:
        print("No migrations to run")
        return True

    success = True
    for migration_file in migration_files:
        sql_content = migration_file.read_text()
        filename = migration_file.name

        print(f"\nExecuting {filename}...")

        if not execute_migration_sql(client, sql_content, filename):
            success = False
            break

    if success:
        print("\n✓ All migrations completed successfully!")
    else:
        print("\n✗ Migration failed - please check the errors above")

    return success


if __name__ == "__main__":
    success = run_migrations()
    sys.exit(0 if success else 1)
