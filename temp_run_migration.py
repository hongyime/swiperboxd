"""Create list_summaries and list_memberships tables using Supabase client operations."""

import os
import sys
from pathlib import Path

# Load .env file
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value

from supabase import create_client

# Get Supabase credentials
supabase_url = os.getenv("SUPABASE_URL")
service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not supabase_url or not service_key:
    print("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
    sys.exit(1)

print(f"Connecting to Supabase: {supabase_url}")

# Create client
client = create_client(supabase_url, service_key)

# First, check if we can access existing tables
print("\nChecking existing tables...")
try:
    result = client.table('movies').select('*').limit(1).execute()
    print(f"✓ Can access movies table ({len(result.data)} rows found)")
except Exception as e:
    print(f"✗ Cannot access movies table: {e}")
    sys.exit(1)

# Read the migration SQL to see what we need to create
migration_path = Path(__file__).parent / "db" / "migrations" / "007_lists.sql"
sql_content = migration_path.read_text()

print("\n" + "=" * 60)
print("Manual Migration Required")
print("=" * 60)
print("\nThe Supabase Python client doesn't support table creation via SQL.")
print("Please follow these steps:")
print("\n1. Open your Supabase Dashboard:")
dashboard_url = supabase_url.replace('auth/v1', '').replace('/auth', '').rstrip('/')
print(f"   {dashboard_url}")
print("\n2. Navigate to: SQL Editor (in left sidebar)")
print("\n3. Click 'New Query'")
print("\n4. Paste and run this SQL:")
print("\n" + "=" * 60)
print(sql_content)
print("=" * 60)
print("\n5. After running the SQL, run this script again to verify")
print("\n" + "=" * 60)
