"""Database client module for Supabase integration."""

from __future__ import annotations

import os
from functools import lru_cache
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
