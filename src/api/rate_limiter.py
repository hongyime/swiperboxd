# STATUS: IMPLEMENTED BUT NOT WIRED
# This module is not imported by app.py. Rate limiting is currently handled in-memory by
# InMemoryStore/SupabaseStore. Retained for future Upstash Redis integration.
# Do not assume this code is active or enforced at runtime.
"""Redis-based rate limiter for Upstash integration."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis

class RedisRateLimiter:
    """Redis-based rate limiter using sliding window algorithm."""

    def __init__(self):
        """Initialize Redis connection from environment variables."""
        self.redis: redis.Redis = self._get_redis_client()

    def _get_redis_client(self) -> redis.Redis:
        """Create Redis client from environment configuration."""
        # Parse UPSTASH_REDIS_REST_URL to extract host and port
        redis_url = os.getenv("UPSTASH_REDIS_REST_URL")
        token = os.getenv("UPSTASH_REDIS_REST_TOKEN")

        if not redis_url or not token:
            raise ValueError("UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN must be set")

        try:
            import redis
        except ImportError as exc:
            raise ImportError("redis package is required for RedisRateLimiter") from exc

        # Upstash uses HTTPS + REST protocol with token as password
        # Format: https://<hash>.upstash.io
        if redis_url.startswith("https://"):
            redis_url = redis_url.replace("https://", "")

        return redis.Redis(
            host=redis_url,
            port=6379,
            password=token,
            ssl=True,
            socket_timeout=5,
            socket_connect_timeout=5,
            retry_on_timeout=True,
        )

    def should_rate_limit(
        self,
        user_id: str,
        key: str,
        window_seconds: int,
        max_requests: int,
    ) -> tuple[bool, float]:
        """
        Check if user should be rate-limited using sliding window.

        Args:
            user_id: User identifier
            key: Rate limit key (e.g., 'ingest', 'swipe')
            window_seconds: Time window in seconds
            max_requests: Maximum allowed requests in window

        Returns:
            tuple[bool, float]: (is_limited, retry_after_seconds)
        """
        now = time.time()
        window_start = now - window_seconds

        try:
            pipe = self.redis.pipeline()

            # Remove old entries outside the window
            pipe.zremrangebyscore(f"rate:{key}:{user_id}", 0, window_start)

            # Add current request
            pipe.zadd(f"rate:{key}:{user_id}", {str(now): now})

            # Get current count
            pipe.zcard(f"rate:{key}:{user_id}")

            # Set expiry on the key
            pipe.expire(f"rate:{key}:{user_id}", window_seconds)

            results = pipe.execute()
            count = results[2]

            if count >= max_requests:
                # Rate limited - calculate retry after
                min_id = pipe.zrange(
                    f"rate:{key}:{user_id}", 0, 0, withscores=True
                ).execute()

                if min_id:
                    oldest_timestamp = float(min_id[0][1])
                    retry_after = window_start - oldest_timestamp
                    return True, max(0, retry_after)

                return True, window_seconds

            return False, 0.0

        except Exception as exc:
            # Fail open: allow request if Redis is down
            print(f"Redis rate limiter error, allowing request: {exc}")
            return False, 0.0
