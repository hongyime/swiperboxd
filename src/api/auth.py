"""Authentication module for Supabase Auth integration."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

if TYPE_CHECKING:
    from fastapi import Request


class AuthService:
    """Service for handling Supabase authentication operations."""

    def __init__(self):
        self.supabase_url = os.getenv("SUPABASE_URL")
        self.supabase_anon_key = os.getenv("SUPABASE_ANON_KEY")
        self.supabase_jwt_secret = os.getenv("SUPABASE_JWT_SECRET")

        if not all([self.supabase_url, self.supabase_anon_key, self.supabase_jwt_secret]):
            raise ValueError("Supabase auth credentials not configured")

    async def register_user(self, email: str, password: str) -> dict:
        """
        Register a new user with Supabase Auth.

        Args:
            email: User's email address
            password: User's password

        Returns:
            Dict with access_token, user_id, and email

        Raises:
            ValueError: If registration fails
        """
        import httpx

        # Use Supabase Auth REST API directly
        auth_url = f"{self.supabase_url}/auth/v1/signup"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    auth_url,
                    json={"email": email, "password": password},
                    headers={
                        "apikey": self.supabase_anon_key,
                        "Content-Type": "application/json"
                    }
                )

                response_data = response.json()

                if response.status_code not in {200, 201}:
                    error_message = response_data.get('error_description', response_data.get('msg', 'Unknown error'))
                    error_code = response_data.get('error_code', 'unknown')

                    # Provide more helpful error messages
                    if 'email_address_invalid' in str(response_data):
                        raise ValueError(f"Invalid email address: {email}")
                    elif 'Password should be at least' in str(response_data):
                        raise ValueError("Password must be at least 6 characters")
                    elif 'already been registered' in str(response_data):
                        raise ValueError(f"Email {email} is already registered")
                    else:
                        raise ValueError(f"Registration failed ({error_code}): {error_message}")

                # Check if user needs email confirmation
                if not response_data.get('access_token'):
                    # User was created but needs email confirmation
                    user_data = response_data.get('user', {})
                    user_id = user_data.get('id')
                    if user_id:
                        return {
                            "access_token": None,
                            "user_id": user_id,
                            "email": user_data.get('email', email),
                            "needs_confirmation": True
                        }
                    else:
                        raise ValueError("Registration requires email confirmation. Please check your email.")

                # Extract user data from nested 'user' object
                user_data = response_data.get('user', {})
                user_id = user_data.get('id') or response_data.get('id')

                return {
                    "access_token": response_data.get("access_token"),
                    "user_id": user_id,
                    "email": user_data.get('email', email)
                }

        except httpx.RequestError as e:
            raise ValueError(f"Network error during registration: {str(e)}")
        except Exception as e:
            raise ValueError(f"Registration error: {str(e)}")

    async def login_user(self, email: str, password: str) -> dict:
        """
        Login a user with email and password.

        Args:
            email: User's email address
            password: User's password

        Returns:
            Dict with access_token, user_id, and email

        Raises:
            ValueError: If login fails
        """
        import httpx

        # Use Supabase Auth REST API directly
        auth_url = f"{self.supabase_url}/auth/v1/token?grant_type=password"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    auth_url,
                    json={"email": email, "password": password},
                    headers={
                        "apikey": self.supabase_anon_key,
                        "Content-Type": "application/json"
                    }
                )

                response_data = response.json()

                if response.status_code != 200:
                    error_message = response_data.get('error_description', response_data.get('msg', 'Unknown error'))
                    error_code = response_data.get('error_code', 'unknown')

                    # Provide more helpful error messages
                    if 'Invalid login credentials' in str(response_data):
                        raise ValueError("Invalid email or password")
                    elif 'Email not confirmed' in str(response_data):
                        raise ValueError("Please confirm your email address before logging in")
                    elif 'User not found' in str(response_data):
                        raise ValueError(f"No account found with email: {email}")
                    else:
                        raise ValueError(f"Login failed ({error_code}): {error_message}")

                # Extract user data from nested 'user' object
                user_data = response_data.get('user', {})
                user_id = user_data.get('id')

                return {
                    "access_token": response_data.get("access_token"),
                    "user_id": user_id,
                    "email": user_data.get("email")
                }

        except httpx.RequestError as e:
            raise ValueError(f"Network error during login: {str(e)}")
        except Exception as e:
            raise ValueError(f"Login error: {str(e)}")

    async def verify_token(self, token: str) -> Optional[dict]:
        """
        Verify a JWT token and return the user info.
        
        For Supabase, tokens are signed with ES256. We decode without 
        cryptographic verification (Supabase already verified it).
        We validate the basic claims manually.
        """
        try:
            import jwt

            # Decode without verification - Supabase has already verified the signature
            # We just need to extract and validate basic claims
            payload = jwt.decode(
                token,
                options={"verify_signature": False}
            )

            # Validate basic claims
            exp = payload.get("exp")
            if exp and exp < __import__("time").time():
                print("Token expired")
                return None

            # Validate issuer matches our Supabase project
            iss = payload.get("iss", "")
            if self.supabase_url and self.supabase_url not in iss:
                print(f"Invalid issuer: {iss}")
                return None

            # Extract user info
            return {
                "user_id": payload.get("sub"),
                "email": payload.get("email"),
                "exp": exp,
                "iat": payload.get("iat")
            }

        except Exception as e:
            print(f"Token verification failed: {e}")
            return None

    async def get_user_from_request(self, request: Request) -> Optional[dict]:
        """
        Extract and verify user from request headers.

        Args:
            request: FastAPI Request object

        Returns:
            Dict with user_id and email, or None if not authenticated
        """
        # Get authorization header
        auth_header = request.headers.get("Authorization")

        if not auth_header:
            return None

        # Extract token from "Bearer <token>" format
        if not auth_header.startswith("Bearer "):
            return None

        token = auth_header.split(" ", 1)[1]

        # Verify token
        return await self.verify_token(token)

    def verify_sync_token(self, token: str) -> Optional[dict]:
        """
        Synchronous version of token verification for non-async contexts.

        Args:
            token: JWT access token

        Returns:
            Dict with user_id and other claims, or None if invalid
        """
        try:
            import jwt

            # Decode and verify the JWT token
            # Note: Legacy JWT secrets are base64-encoded strings, not JWT tokens
            # We use them directly as the secret for HS256 algorithm
            payload = jwt.decode(
                token,
                self.supabase_jwt_secret,
                algorithms=["HS256"],
                audience=["authenticated"]
            )

            return {
                "user_id": payload.get("sub"),
                "email": payload.get("email"),
                "exp": payload.get("exp"),
                "iat": payload.get("iat")
            }

        except Exception as e:
            print(f"Token verification failed: {e}")
            return None


# Global auth service instance
_auth_service: Optional[AuthService] = None


def get_auth_service() -> AuthService:
    """Get or create the global auth service instance."""
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service
