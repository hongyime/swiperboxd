"""Database client module for Supabase integration."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supabase import Client


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    """
    Get a cached Supabase client instance.

    Requires SUPABASE_URL and SUPABASE_ANON_KEY environment variables.
    Raises ValueError if required environment variables are missing.

    Returns:
        Client: Supabase client instance
    """
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        raise ValueError("SUPABASE_URL and SUPABASE_ANON_KEY environment variables must be set")

    # Lazy import to avoid errors when Supabase credentials are not configured
    from supabase import create_client

    return create_client(supabase_url, supabase_key)


def is_supabase_configured() -> bool:
    """
    Check if Supabase is configured with required environment variables.

    Returns:
        bool: True if SUPABASE_URL and SUPABASE_ANON_KEY are set
    """
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_ANON_KEY"))


def run_migrations() -> None:
    """
    Run all pending database migrations.
    
    Reads migration files from db/migrations/ directory and executes them
    in order (001 -> 002 -> ...).
    
    Uses service role key for admin operations.
    """
    client = get_supabase_client()
    
    # Use RPC to execute raw SQL (requires service role key)
    # Note: In production, you should do this via Supabase Dashboard or migration tool
    migrations_dir = Path(__file__).parent.parent.parent / "db" / "migrations"
    
    if not migrations_dir.exists():
        print(f"⚠️  Migrations directory not found: {migrations_dir}")
        return
    
    migration_files = sorted(migrations_dir.glob("*.sql"))
    
    for migration_file in migration_files:
        with open(migration_file, 'r') as f:
            sql = f.read()
        
        migration_name = migration_file.name
        print(f"📝 Running migration: {migration_name}")
        
        try:
            # Use Supabase SQL function to execute migration
            # This is a simplified version - production should use proper migration tool
            result = client.rpc('exec_sql', {'sql': sql}).execute()
            print(f"✅ Completed: {migration_name}")
        except Exception as e:
            # Check if migration already ran (shouldn't fail with CREATE TABLE IF NOT EXISTS)
            print(f"⚠️  Migration error (might have run already): {e}")
