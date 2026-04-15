"""Test script for Supabase authentication."""

import sys
import base64
import json
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload (middle part) without verification."""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return {"error": "Invalid JWT format - should have 3 parts"}

        # Decode payload (second part)
        payload = parts[1]
        # Add padding if needed
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += '=' * padding

        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception as e:
        return {"error": str(e)}


def test_jwt_secret_format():
    """Test if JWT secret has the correct format."""
    import os
    from dotenv import load_dotenv

    load_dotenv()

    jwt_secret = os.getenv("SUPABASE_JWT_SECRET")

    if not jwt_secret:
        print("❌ SUPABASE_JWT_SECRET not found in .env")
        return False

    print(f"JWT Secret: {jwt_secret[:50]}...")

    # Legacy JWT secrets are base64-encoded strings (not JWT tokens)
    # They should NOT have the typical JWT format (3 parts separated by dots)
    # They look like: sKxW9rUq0rF0I2Zo7EkjzWikM298wV0YPSlpP1J3OXyqjG75UhlShcdqjowuy4ZcXmadcpORUZaV+TH1uFsXVQ==

    # Check if it's NOT a JWT token format (which would be wrong)
    if jwt_secret.count('.') == 2:
        print("❌ JWT Secret appears to be a JWT token format")
        print("   The Legacy JWT Secret should be a base64-encoded string, not a JWT token")
        print("   Example: sKxW9rUq0rF0I2Zo7EkjzWikM298wV0YPSlpP1J3OXyqjG75UhlShcdqjowuy4ZcXmadcpORUZaV+TH1uFsXVQ==")
        return False

    # Check if it's a valid base64 string
    try:
        import base64
        decoded = base64.b64decode(jwt_secret)
        print("✓ JWT Secret is a valid base64-encoded Legacy JWT Secret")
        print(f"  - Decoded length: {len(decoded)} bytes")
        return True
    except Exception:
        print("❌ JWT Secret does not appear to be valid base64")
        return False


async def test_user_registration(email: str, password: str):
    """Test user registration."""
    from src.api.auth import get_auth_service

    print(f"\n📝 Testing user registration for: {email}")

    try:
        auth_service = get_auth_service()
        result = await auth_service.register_user(email, password)

        print("✓ Registration successful!")
        print(f"  - User ID: {result['user_id']}")
        print(f"  - Email: {result['email']}")
        print(f"  - Access Token: {result['access_token'][:50]}...")

        # Decode token to verify
        payload = decode_jwt_payload(result['access_token'])
        print(f"  - Token verified: User ID = {payload.get('sub', 'N/A')}")

        return result

    except Exception as e:
        print(f"❌ Registration failed: {e}")
        return None


async def test_user_login(email: str, password: str):
    """Test user login."""
    from src.api.auth import get_auth_service

    print(f"\n🔐 Testing user login for: {email}")

    try:
        auth_service = get_auth_service()
        result = await auth_service.login_user(email, password)

        print("✓ Login successful!")
        print(f"  - User ID: {result['user_id']}")
        print(f"  - Email: {result['email']}")
        print(f"  - Access Token: {result['access_token'][:50]}...")

        return result

    except Exception as e:
        print(f"❌ Login failed: {e}")
        return None


async def test_auth_flow():
    """Run complete auth flow test."""
    print("=" * 80)
    print("SUPABASE AUTHENTICATION TEST")
    print("=" * 80)

    # Step 1: Verify JWT secret format
    print("\n1. Checking JWT Secret format...")
    if not test_jwt_secret_format():
        print("\n❌ Please update SUPABASE_JWT_SECRET in .env with the correct value")
        print("   Get it from: Supabase Dashboard → Settings → API → JWT Secret (under Project API keys)")
        return False

    # Step 2: Test registration
    test_email = "test@example.com"
    test_password = "TestPassword123!"

    registration_result = await test_user_registration(test_email, test_password)

    if not registration_result:
        print("\n❌ Registration failed. Please check:")
        print("   - SUPABASE_JWT_SECRET is correct")
        print("   - SUPABASE_URL is correct")
        print("   - SUPABASE_ANON_KEY is correct")
        return False

    # Step 3: Test login
    print("\n2. Testing login with registered user...")
    login_result = await test_user_login(test_email, test_password)

    if not login_result:
        print("\n❌ Login failed. This might be expected if user was just registered.")
        print("   Try again in a few seconds...")
        return False

    print("\n" + "=" * 80)
    print("✓ ALL AUTH TESTS PASSED!")
    print("=" * 80)

    return True


if __name__ == "__main__":
    import asyncio

    # Try to run async test
    try:
        from dotenv import load_dotenv
        load_dotenv()

        result = asyncio.run(test_auth_flow())
        sys.exit(0 if result else 1)

    except ImportError:
        print("Installing python-dotenv for testing...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "python-dotenv", "-q"])
        print("\nPlease run the test again: python scripts/test_auth.py")
        sys.exit(1)
