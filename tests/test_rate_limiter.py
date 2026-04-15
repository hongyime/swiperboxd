"""Rate limiter tests."""

import time

import pytest

from src.api.rate_limiter import RedisRateLimiter

# These tests require a live Redis connection. Run with: pytest --run-redis
pytestmark = pytest.mark.skipif(
    not pytest.ini_options if hasattr(pytest, "ini_options") else True,
    reason="Redis integration tests skipped by default"
)


def _redis_enabled(request) -> bool:
    return request.config.getoption("--run-redis", default=False)


@pytest.fixture(autouse=True)
def skip_without_redis(request):
    if not request.config.getoption("--run-redis", default=False):
        pytest.skip("Redis integration tests disabled — pass --run-redis to enable")


def pytest_addoption(parser):
    parser.addoption("--run-redis", action="store_true", default=False, help="Run Redis integration tests")


def test_redis_rate_limiter_basic():
    """Test basic rate limiting functionality."""
    limiter = RedisRateLimiter()

    limited, retry_after = limiter.should_rate_limit("user1", "test", 10, 5)
    assert not limited
    assert retry_after == 0.0

    for _ in range(4):
        limited, retry_after = limiter.should_rate_limit("user1", "test", 10, 5)
        assert not limited

    limited, retry_after = limiter.should_rate_limit("user1", "test", 10, 5)
    assert limited
    assert retry_after > 0


def test_redis_rate_limiter_sliding_window():
    """Test that sliding window resets correctly."""
    limiter = RedisRateLimiter()

    for _ in range(5):
        limiter.should_rate_limit("user2", "window", 2, 5)

    limited, _ = limiter.should_rate_limit("user2", "window", 2, 5)
    assert limited

    time.sleep(2.5)

    limited, retry_after = limiter.should_rate_limit("user2", "window", 2, 5)
    assert not limited


def test_redis_rate_limiter_isolated():
    """Test that different users are isolated."""
    limiter = RedisRateLimiter()

    for _ in range(5):
        limiter.should_rate_limit("user3", "isolated", 10, 5)

    limited, _ = limiter.should_rate_limit("user3", "isolated", 10, 5)
    assert limited

    limited, _ = limiter.should_rate_limit("user4", "isolated", 10, 5)
    assert not limited
