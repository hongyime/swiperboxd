"""Script to execute SQL migrations on Supabase database using direct SQL."""

import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def run_migrations_manually():
    """Print the SQL migrations so you can run them manually in Supabase Dashboard."""
    migrations_dir = Path(__file__).parent.parent / "db" / "migrations"
    new_migrations = sorted(f for f in migrations_dir.glob("*.sql") if not f.name.startswith("LEGACY_"))

    if not new_migrations:
        print("No new migrations to run")
        return

    print("=" * 80)
    print("MIGRATION SCRIPTS FOR SUPABASE DASHBOARD")
    print("=" * 80)
    print("\nPlease run these SQL scripts in Supabase Dashboard > SQL Editor:\n")

    for migration_file in new_migrations:
        print(f"\n--- {migration_file.name} ---\n")
        print(migration_file.read_text())
        print("\n" + "-" * 80)

    print("\n" + "=" * 80)
    print("INSTRUCTIONS:")
    print("=" * 80)
    supabase_url = os.getenv("SUPABASE_URL", "")
    project_id = supabase_url.split("//")[-1].split(".")[0] if supabase_url else "<YOUR_PROJECT_ID>"
    print(f"1. Go to https://app.supabase.com/project/{project_id}/sql/new")
    print("2. Copy and paste each migration block above")
    print("3. Click 'Run' for each migration")
    print("4. Check that all tables are created successfully")


if __name__ == "__main__":
    run_migrations_manually()
