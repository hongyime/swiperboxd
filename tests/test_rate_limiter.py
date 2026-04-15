"""Rate limiter tests."""

import time

import pytest

from src.api.rate_limiter import RedisRateLimiter


def test_redis_rate_limiter_basic():
    """Test basic rate limiting functionality."""
    if not pytest.config.getoption("--run-redis"):
        pytest.skip("Redis integration tests disabled")

    limiter = RedisRateLimiter()

    # First request should pass
    limited, retry_after = limiter.should_rate_limit("user1", "test", 10, 5)
    assert not limited
    assert retry_after == 0.0

    # Make 5 requests (within limit)
    for _ in range(4):
        limited, retry_after = limiter.should_rate_limit("user1", "test", 10, 5)
        assert not limited

    # 6th request should be rate limited
    limited, retry_after = limiter.should_rate_limit("user1", "test", 10, 5)
    assert limited
    assert retry_after > 0


def test_redis_rate_limiter_sliding_window():
    """Test that sliding window resets correctly."""
    if not pytest.config.getoption("--run-redis"):
        pytest.skip("Redis integration tests disabled")

    limiter = RedisRateLimiter()

    # Fill the rate limit
    for _ in range(5):
        limiter.should_rate_limit("user2", "window", 2, 5)

    # Should be limited
    limited, _ = limiter.should_rate_limit("user2", "window", 2, 5)
    assert limited

    # Wait for window to expire
    time.sleep(2.5)

    # Should be allowed again
    limited, retry_after = limiter.should_rate_limit("user2", "window", 2, 5)
    assert not limited


def test_redis_rate_limiter_isolated():
    """Test that different users are isolated."""
    if not pytest.config.getoption("--run-redis"):
        pytest.skip("Redis integration tests disabled")

    limiter = RedisRateLimiter()

    # User3 fills rate limit
    for _ in range(5):
        limiter.should_rate_limit("user3", "isolated", 10, 5)

    # User3 should be limited
    limited, _ = limiter.should_rate_limit("user3", "isolated", 10, 5)
    assert limited

    # User4 should not be limited
    limited, _ = limiter.should_rate_limit("user4", "isolated", 10, 5)
    assert not limited
