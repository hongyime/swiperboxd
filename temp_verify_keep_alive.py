"""Verify keep_alive_ping function exists in Supabase."""

import os
import sys
from pathlib import Path

# Load .env
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value

from dotenv import load_dotenv
load_dotenv()
from supabase import create_client

supabase_url = os.getenv("SUPABASE_URL")
service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

client = create_client(supabase_url, service_key)

# Try to call the function
try:
    result = client.rpc('keep_alive_ping').execute()
    print("✅ keep_alive_ping function exists and works!")
    sys.exit(0)
except Exception as e:
    print(f"❌ keep_alive_ping function missing or failed: {e}")
    print("\nYou need to create it manually in Supabase SQL Editor:")
    print("CREATE OR REPLACE FUNCTION keep_alive_ping()")
    print("RETURNS void AS $$")
    print("BEGIN")
    print("  PERFORM 1;")
    print("END;")
    print("$$ LANGUAGE plpgsql SECURITY DEFINER;")
    sys.exit(1)
