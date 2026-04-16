# STATUS: IMPLEMENTED BUT NOT WIRED
# This module is not imported by app.py. Retained for future use with auth.py.
# Do not assume this code is active or enforced at runtime.
"""Authentication dependencies for FastAPI endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from .auth import get_auth_service


class AuthenticatedUser(BaseModel):
    """Authenticated user extracted from JWT token."""
    user_id: str
    email: str


async def get_authenticated_user(request: Request) -> AuthenticatedUser:
    """
    Dependency that extracts and validates user from JWT token.
    
    Raises HTTPException 401 if:
    - No Authorization header present
    - Token is invalid or expired
    - Token claims are invalid
    
    Returns AuthenticatedUser with validated user_id and email.
    """
    auth_service = get_auth_service()
    user = await auth_service.get_user_from_request(request)
    
    if not user:
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthorized", "reason": "Invalid or expired token"}
        )
    
    if not user.get("user_id"):
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthorized", "reason": "Token missing user_id"}
        )
    
    return AuthenticatedUser(
        user_id=user["user_id"],
        email=user.get("email", "")
    )


async def get_optional_auth_user(request: Request) -> Optional[AuthenticatedUser]:
    """
    Optional authentication dependency - returns None if no valid auth.
    
    Use this for endpoints that support both authenticated and unauthenticated access.
    """
    try:
        return await get_authenticated_user(request)
    except HTTPException:
        return None


def require_user_id(user: AuthenticatedUser) -> str:
    """
    Dependency that returns the authenticated user's ID.
    Use this when you need to ensure operations are scoped to the authenticated user.
    """
    return user.user_id


def validate_user_id_match(auth_user_id: str, request_user_id: str | None) -> str:
    """
    Validates that the user_id in the request matches the authenticated user.
    
    This prevents users from accessing/modifying other users' data by spoofing user_id.
    
    Args:
        auth_user_id: The user_id from the JWT token
        request_user_id: The user_id from the request (query param or body)
        
    Returns:
        The validated user_id (from JWT)
        
    Raises:
        HTTPException 403 if user_ids don't match
    """
    if request_user_id and request_user_id != auth_user_id:
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "reason": "Cannot access another user's data"}
        )
    return auth_user_id
