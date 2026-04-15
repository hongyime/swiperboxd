"""Authentication module for Supabase Auth integration."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

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

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                auth_url,
                json={"email": email, "password": password},
                headers={
                    "apikey": self.supabase_anon_key,
                    "Content-Type": "application/json"
                }
            )

            if response.status_code not in {200, 201}:
                error_data = response.json()
                raise ValueError(f"Registration failed: {error_data.get('error_description', 'Unknown error')}")

            data = response.json()

            return {
                "access_token": data.get("access_token"),
                "user_id": data.get("user", {}).get("id"),
                "email": data.get("user", {}).get("email")
            }

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

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                auth_url,
                json={"email": email, "password": password},
                headers={
                    "apikey": self.supabase_anon_key,
                    "Content-Type": "application/json"
                }
            )

            if response.status_code != 200:
                error_data = response.json()
                raise ValueError(f"Login failed: {error_data.get('error_description', 'Unknown error')}")

            data = response.json()

            return {
                "access_token": data.get("access_token"),
                "user_id": data.get("user", {}).get("id"),
                "email": data.get("user", {}).get("email")
            }

    async def verify_token(self, token: str) -> Optional[dict]:
        """
        Verify a JWT token and return the user info.

        Args:
            token: JWT access token

        Returns:
            Dict with user_id and other claims, or None if invalid
        """
        try:
            import jwt

            # Decode and verify the JWT token
            payload = jwt.decode(
                token,
                self.supabase_jwt_secret,
                algorithms=["HS256"],
                audience=["authenticated"]
            )

            # Extract user info
            return {
                "user_id": payload.get("sub"),
                "email": payload.get("email"),
                "exp": payload.get("exp"),
                "iat": payload.get("iat")
            }

        except Exception as e:
            # Token invalid or expired
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
