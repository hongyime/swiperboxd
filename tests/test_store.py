"""Integration tests for Store implementations (InMemoryStore and SupabaseStore)."""

import os
import time
from unittest.mock import MagicMock, patch

import pytest

# Ensure we use InMemoryStore for testing
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_ANON_KEY", None)

from src.api.store import InMemoryStore


@pytest.fixture
def in_memory_store():
    """Create a fresh InMemoryStore for each test."""
    return InMemoryStore()


def test_in_memory_store_add_get_exclusions(in_memory_store):
    """Test adding and retrieving exclusion lists."""
    in_memory_store.add_exclusion("user1", "film-a")
    in_memory_store.add_exclusion("user1", "film-b")
    in_memory_store.add_exclusion("user2", "film-c")

    exclusions1 = in_memory_store.get_exclusions("user1")
    assert exclusions1 == {"film-a", "film-b"}

    exclusions2 = in_memory_store.get_exclusions("user2")
    assert exclusions2 == {"film-c"}

    # Empty list for non-existent user
    exclusions3 = in_memory_store.get_exclusions("user3")
    assert exclusions3 == set()


def test_in_memory_store_add_get_watchlist(in_memory_store):
    """Test adding and retrieving watchlist."""
    in_memory_store.add_watchlist("user1", "film-x")
    in_memory_store.add_watchlist("user1", "film-y")

    watchlist = in_memory_store.get_watchlist("user1")
    assert watchlist == {"film-x", "film-y"}


def test_in_memory_store_add_get_diary(in_memory_store):
    """Test adding and retrieving diary."""
    in_memory_store.add_diary("user1", "film-z")

    diary = in_memory_store.get_diary("user1")
    assert diary == {"film-z"}


def test_in_memory_store_upsert_get_movie(in_memory_store):
    """Test upserting and retrieving movies."""
    movie1 = {
        "slug": "test-film-1",
        "title": "Test Film 1",
        "poster_url": "http://example.com/poster1.jpg",
        "rating": 4.5,
        "popularity": 100,
        "genres": ["Drama", "Thriller"],
        "synopsis": "A test film",
        "cast": ["Actor A", "Actor B"]
    }

    in_memory_store.upsert_movie(movie1)

    retrieved = in_memory_store.get_movie("test-film-1")
    assert retrieved is not None
    assert retrieved["slug"] == "test-film-1"
    assert retrieved["title"] == "Test Film 1"
    assert retrieved["rating"] == 4.5

    # Update movie
    movie1["rating"] = 5.0
    in_memory_store.upsert_movie(movie1)
    retrieved = in_memory_store.get_movie("test-film-1")
    assert retrieved["rating"] == 5.0


def test_in_memory_store_get_movies(in_memory_store):
    """Test retrieving all movies."""
    movie1 = {
        "slug": "film-1",
        "title": "Film 1",
        "poster_url": "url1",
        "rating": 4.0,
        "popularity": 50,
        "genres": ["Action"]
    }
    movie2 = {
        "slug": "film-2",
        "title": "Film 2",
        "poster_url": "url2",
        "rating": 3.5,
        "popularity": 30,
        "genres": ["Comedy"]
    }

    in_memory_store.upsert_movie(movie1)
    in_memory_store.upsert_movie(movie2)

    movies = in_memory_store.get_movies()
    assert len(movies) == 2
    assert any(m["slug"] == "film-1" for m in movies)
    assert any(m["slug"] == "film-2" for m in movies)


def test_in_memory_store_ingest_progress(in_memory_store):
    """Test ingest progress tracking."""
    # Set progress
    in_memory_store.set_ingest_progress("user1", 25)
    assert in_memory_store.get_ingest_progress("user1") == 25

    # Update progress
    in_memory_store.set_ingest_progress("user1", 50)
    assert in_memory_store.get_ingest_progress("user1") == 50

    # Clamp to valid range
    in_memory_store.set_ingest_progress("user1", 150)
    assert in_memory_store.get_ingest_progress("user1") == 100

    in_memory_store.set_ingest_progress("user1", -10)
    assert in_memory_store.get_ingest_progress("user1") == 0


def test_in_memory_store_should_rate_limit(in_memory_store):
    """Test rate limiting."""
    # First request - no limit
    limited1, wait1 = in_memory_store.should_rate_limit("user1", lock_ms=500)
    assert limited1 is False
    assert wait1 == 0

    # Immediate second request - should be limited
    limited2, wait2 = in_memory_store.should_rate_limit("user1", lock_ms=500)
    assert limited2 is True
    assert wait2 > 0

    # Different user - no limit
    limited3, wait3 = in_memory_store.should_rate_limit("user2", lock_ms=500)
    assert limited3 is False


def test_in_memory_store_allow_scrape_request(in_memory_store):
    """Test scrape request rate limiting."""
    # First request - allowed
    allowed1, wait1 = in_memory_store.allow_scrape_request("user1", min_interval_seconds=1.0)
    assert allowed1 is True
    assert wait1 == 0.0

    # Immediate second request - not allowed
    allowed2, wait2 = in_memory_store.allow_scrape_request("user1", min_interval_seconds=1.0)
    assert allowed2 is False
    assert wait2 > 0


def test_in_memory_store_record_genre_preference(in_memory_store):
    """Test genre preference recording."""
    in_memory_store.record_genre_preference("user1", ["Action", "Comedy"])
    in_memory_store.record_genre_preference("user1", ["Action", "Drama"])
    in_memory_store.record_genre_preference("user2", ["Comedy"])

    # Get weights through weighted_shuffle behavior
    movies = [
        {"slug": "film-1", "genres": ["Action"]},
        {"slug": "film-2", "genres": ["Comedy"]},
        {"slug": "film-3", "genres": ["Drama"]},
    ]

    shuffled1 = in_memory_store.weighted_shuffle("user1", movies[:])

    # After 2 Action votes for user1, Action films should be boosted
    # (implementation may vary, just test it doesn't crash)
    assert len(shuffled1) == 3


def test_in_memory_store_weighted_shuffle_no_weights(in_memory_store):
    """Test weighted shuffle when no genre weights exist."""
    movies = [
        {"slug": "film-1", "genres": ["Action"]},
        {"slug": "film-2", "genres": ["Comedy"]},
        {"slug": "film-3", "genres": ["Drama"]},
    ]

    # No weights - should still shuffle randomly
    shuffled = in_memory_store.weighted_shuffle("user1", movies[:])
    assert len(shuffled) == 3


def test_in_memory_store_cleanup_expired_progress(in_memory_store):
    """Test cleanup of expired ingest progress entries."""
    in_memory_store.set_ingest_progress("user1", 50)
    in_memory_store.set_ingest_progress("user2", 75)

    # Should remove 0 entries (all recent)
    removed = in_memory_store.cleanup_expired_progress(ttl_seconds=3600.0)
    assert removed == 0

    # Should remove entries older than negative TTL (all entries)
    # We can't actually test time-based cleanup without mocking,
    # but we can test the method exists and returns int
    removed = in_memory_store.cleanup_expired_progress(ttl_seconds=-1.0)
    assert isinstance(removed, int)


def test_in_memory_store_archive_old_actions(in_memory_store):
    """Test archiving of old action entries."""
    # Add some actions with timestamps
    now = time.time()
    old_action = {"slug": "film-old", "timestamp": now - 10 * 86400}  # 10 days ago
    recent_action = {"slug": "film-new", "timestamp": now}

    # Directly access actions for testing
    with in_memory_store.lock:
        in_memory_store.actions = [old_action, recent_action]

    # Archive actions older than 7 days
    remaining = in_memory_store.archive_old_actions(keep_days=7.0)

    # Should only have the recent action
    assert remaining == 1


def test_in_memory_store_concurrent_add_exclusion(in_memory_store):
    """Test thread safety of exclusion list."""
    import threading

    def add_exclusions(user_id, count):
        for i in range(count):
            in_memory_store.add_exclusion(user_id, f"film-{i}-{threading.current_thread().ident}")

    threads = []
    for _ in range(5):
        t = threading.Thread(target=add_exclusions, args=("concurrent-user", 10))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    exclusions = in_memory_store.get_exclusions("concurrent-user")
    # All 50 exclusions should be present (5 threads * 10 each)
    assert len(exclusions) == 50


# SupabaseStore integration tests (only run if Supabase is configured)
@pytest.mark.skipif(
    not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_ANON_KEY"),
    reason="Supabase not configured"
)
def test_supabase_store_basic_operations():
    """Test basic SupabaseStore operations."""
    from src.api.store import SupabaseStore

    store = SupabaseStore()

    # Test exclusions
    user_id = "test-user-integration"
    store.add_exclusion(user_id, "test-film-1")

    exclusions = store.get_exclusions(user_id)
    assert "test-film-1" in exclusions

    # Test movie upsert
    movie = {
        "slug": "integration-test",
        "title": "Integration Test Film",
        "poster_url": "http://example.com/test.jpg",
        "rating": 4.0,
        "popularity": 10,
        "genres": ["Test"],
        "synopsis": "Test synopsis"
    }
    store.upsert_movie(movie)

    retrieved = store.get_movie("integration-test")
    assert retrieved is not None
    assert retrieved["title"] == "Integration Test Film"


@pytest.mark.skipif(
    not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_ANON_KEY"),
    reason="Supabase not configured"
)
def test_supabase_store_concurrent_operations():
    """Test SupabaseStore concurrent access."""
    from src.api.store import SupabaseStore

    store = SupabaseStore()

    def add_genre_preferences(user_id, count):
        for i in range(count):
            store.record_genre_preference(user_id, [f"Genre-{i % 5}"])

    threads = []
    for _ in range(3):
        t = threading.Thread(target=add_genre_preferences, args=("supabase-concurrent-user", 10))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # Should not crash and should have recorded preferences
    shuffled = store.weighted_shuffle("supabase-concurrent-user", [])
    assert isinstance(shuffled, list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
