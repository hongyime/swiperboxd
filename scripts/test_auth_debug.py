"""Debug test script for Supabase authentication."""

import sys
import os
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


async def test_registration_with_details():
    """Test registration and show all details."""
    import httpx
    from dotenv import load_dotenv

    load_dotenv()

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_anon_key = os.getenv("SUPABASE_ANON_KEY")

    print("=" * 80)
    print("SUPABASE AUTH REGISTRATION TEST")
    print("=" * 80)

    print(f"\n📡 Supabase URL: {supabase_url}")
    print(f"🔑 Using ANON key: {supabase_anon_key[:50]}...")

    # Test registration
    auth_url = f"{supabase_url}/auth/v1/signup"
    email = "newuser123@example.com"
    password = "TestPassword123!"

    print(f"\n📝 Attempting to register: {email}")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                auth_url,
                json={"email": email, "password": password},
                headers={
                    "apikey": supabase_anon_key,
                    "Content-Type": "application/json"
                }
            )

            print(f"\n📊 Response Status: {response.status_code}")
            print(f"📊 Response Headers: {dict(response.headers)}")
            print(f"\n📊 Response Body: {response.json()}")

            if response.status_code in {200, 201}:
                data = response.json()
                print("\n✓ Registration successful!")
                print(f"  - User ID: {data.get('id')}")
                print(f"  - Email: {data.get('email')}")
                print(f"  - Access Token: {data.get('access_token', 'None')[:50] if data.get('access_token') else 'None'}...")
                print(f"  - Confirmation sent: {data.get('confirmation_sent_at', 'N/A')}")

                if not data.get('access_token'):
                    print("\n⚠️  NOTE: Registration successful but access token not returned.")
                    print("   This usually means email confirmation is required.")
                    print("   Check your email to confirm your account.")
            else:
                print(f"\n❌ Registration failed with status {response.status_code}")

    except Exception as e:
        print(f"\n❌ Error: {e}")


async def test_login_with_details():
    """Test login and show all details."""
    import httpx
    from dotenv import load_dotenv

    load_dotenv()

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_anon_key = os.getenv("SUPABASE_ANON_KEY")

    print("\n" + "=" * 80)
    print("SUPABASE AUTH LOGIN TEST")
    print("=" * 80)

    # Try to login (this user might not exist, but let's see the error)
    auth_url = f"{supabase_url}/auth/v1/token?grant_type=password"
    email = "test@example.com"
    password = "TestPassword123!"

    print(f"\n🔐 Attempting to login: {email}")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                auth_url,
                json={"email": email, "password": password},
                headers={
                    "apikey": supabase_anon_key,
                    "Content-Type": "application/json"
                }
            )

            print(f"\n📊 Response Status: {response.status_code}")
            print(f"\n📊 Response Body: {response.json()}")

            if response.status_code == 200:
                data = response.json()
                print("\n✓ Login successful!")
                print(f"  - User ID: {data.get('user', {}).get('id')}")
                print(f"  - Email: {data.get('user', {}).get('email')}")
                print(f"  - Access Token: {data.get('access_token', '')[:50] if data.get('access_token') else 'None'}...")
            else:
                print(f"\n❌ Login failed with status {response.status_code}")
                print("   This is expected if user doesn't exist or password is wrong")

    except Exception as e:
        print(f"\n❌ Error: {e}")


async def check_supabase_config():
    """Check Supabase configuration."""
    from dotenv import load_dotenv

    load_dotenv()

    print("\n" + "=" * 80)
    print("SUPABASE CONFIGURATION CHECK")
    print("=" * 80)

    # Check all required env vars
    required_vars = [
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_JWT_SECRET"
    ]

    print("\n📋 Environment Variables:")
    for var in required_vars:
        value = os.getenv(var)
        if value:
            print(f"  ✓ {var}: {value[:30]}...")
        else:
            print(f"  ✗ {var}: NOT SET")

    # Validate URL format
    url = os.getenv("SUPABASE_URL")
    if url and url.startswith("https://"):
        print("\n✓ SUPABASE_URL format is correct (https://)")
    else:
        print(f"\n✗ SUPABASE_URL format might be incorrect: {url}")

    # Validate ANON key format (should be JWT)
    anon_key = os.getenv("SUPABASE_ANON_KEY")
    if anon_key and anon_key.count('.') == 2:
        print("✓ SUPABASE_ANON_KEY is a valid JWT token")
    else:
        print("✗ SUPABASE_ANON_KEY format might be incorrect")


if __name__ == "__main__":
    import asyncio

    # Run all tests
    asyncio.run(check_supabase_config())
    asyncio.run(test_registration_with_details())
    asyncio.run(test_login_with_details())
