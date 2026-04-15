from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol

from .database import get_supabase_client


class Store(Protocol):
    """Store protocol defining the interface for state persistence."""

    def add_exclusion(self, user_id: str, slug: str) -> None: ...

    def get_exclusions(self, user_id: str) -> set[str]: ...

    def add_watchlist(self, user_id: str, slug: str) -> None: ...

    def get_watchlist(self, user_id: str) -> set[str]: ...

    def add_diary(self, user_id: str, slug: str) -> None: ...

    def get_diary(self, user_id: str) -> set[str]: ...

    def upsert_movie(self, movie: dict) -> None: ...

    def get_movie(self, slug: str) -> dict | None: ...

    def get_movies(self) -> list[dict]: ...

    def set_ingest_progress(self, user_id: str, value: int) -> None: ...

    def get_ingest_progress(self, user_id: str) -> int: ...

    def should_rate_limit(self, user_id: str, lock_ms: int = 500) -> tuple[bool, int]: ...

    def allow_scrape_request(self, user_id: str, min_interval_seconds: float = 1.0) -> tuple[bool, float]: ...

    def record_genre_preference(self, user_id: str, genres: list[str]) -> None: ...

    def get_genre_weights(self, user_id: str) -> dict[str, int]: ...

    def weighted_shuffle(self, user_id: str, movies: list[dict]) -> list[dict]: ...


@dataclass
class InMemoryStore:
    """In-memory implementation of Store for testing and development."""

    exclusions: dict[str, set[str]] = field(default_factory=dict)
    watchlist: dict[str, set[str]] = field(default_factory=dict)
    diary: dict[str, set[str]] = field(default_factory=dict)
    movies: dict[str, dict] = field(default_factory=dict)
    actions: list[dict] = field(default_factory=list)
    ingest_progress: dict[str, int] = field(default_factory=dict)
    ingest_progress_updated_at: dict[str, float] = field(default_factory=dict)
    last_action_at: dict[str, float] = field(default_factory=dict)
    last_scrape_at: dict[str, float] = field(default_factory=dict)
    ingest_running: set[str] = field(default_factory=set)
    genre_weights: dict[str, dict[str, int]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add_exclusion(self, user_id: str, slug: str) -> None:
        """Add a movie to user's exclusion list."""
        with self.lock:
            self.exclusions.setdefault(user_id, set()).add(slug)

    def add_watchlist(self, user_id: str, slug: str) -> None:
        """Add a movie to user's watchlist."""
        with self.lock:
            self.watchlist.setdefault(user_id, set()).add(slug)

    def add_diary(self, user_id: str, slug: str) -> None:
        """Add a movie to user's diary (watched films)."""
        with self.lock:
            self.diary.setdefault(user_id, set()).add(slug)

    def get_exclusions(self, user_id: str) -> set[str]:
        """Get user's exclusion list (movies to skip)."""
        with self.lock:
            return set(self.exclusions.get(user_id, set()))

    def get_watchlist(self, user_id: str) -> set[str]:
        """Get user's watchlist (saved to watch)."""
        with self.lock:
            return set(self.watchlist.get(user_id, set()))

    def get_diary(self, user_id: str) -> set[str]:
        """Get user's diary (already watched films)."""
        with self.lock:
            return set(self.diary.get(user_id, set()))

    def upsert_movie(self, movie: dict) -> None:
        with self.lock:
            self.movies[movie["slug"]] = movie

    def get_movie(self, slug: str) -> dict | None:
        with self.lock:
            return self.movies.get(slug)

    def get_movies(self) -> list[dict]:
        with self.lock:
            return list(self.movies.values())

    def set_ingest_progress(self, user_id: str, value: int) -> None:
        with self.lock:
            self.ingest_progress[user_id] = max(0, min(100, value))
            self.ingest_progress_updated_at[user_id] = time.time()

    def get_ingest_progress(self, user_id: str) -> int:
        with self.lock:
            return self.ingest_progress.get(user_id, 0)

    def should_rate_limit(self, user_id: str, lock_ms: int = 500) -> tuple[bool, int]:
        now = time.time() * 1000
        with self.lock:
            previous = self.last_action_at.get(user_id, 0)
            delta = now - previous
            if delta < lock_ms:
                return True, int(lock_ms - delta)
            self.last_action_at[user_id] = now
            return False, 0

    def allow_scrape_request(self, user_id: str, min_interval_seconds: float = 1.0) -> tuple[bool, float]:
        now = time.time()
        with self.lock:
            previous = self.last_scrape_at.get(user_id, 0.0)
            delta = now - previous
            if delta < min_interval_seconds:
                return False, min_interval_seconds - delta
            self.last_scrape_at[user_id] = now
            return True, 0.0

    def record_genre_preference(self, user_id: str, genres: list[str]) -> None:
        with self.lock:
            bucket = self.genre_weights.setdefault(user_id, {})
            for genre in genres:
                bucket[genre] = bucket.get(genre, 0) + 1

    def get_genre_weights(self, user_id: str) -> dict[str, int]:
        """Get user's genre weights from in-memory store."""
        with self.lock:
            return dict(self.genre_weights.get(user_id, {}))

    def weighted_shuffle(self, user_id: str, movies: list[dict]) -> list[dict]:
        with self.lock:
            weights = self.genre_weights.get(user_id, {})

        if not weights:
            random.shuffle(movies)
            return movies

        def score(movie: dict) -> int:
            return sum(weights.get(g, 0) for g in movie.get("genres", []))

        boosted = sorted(movies, key=score, reverse=True)
        head = boosted[:8]
        tail = boosted[8:]
        random.shuffle(tail)
        return head + tail

    def cleanup_expired_progress(self, ttl_seconds: float = 3600.0) -> int:
        """Remove ingest progress entries older than TTL. Returns count removed."""
        cutoff = time.time() - ttl_seconds
        removed = 0

        with self.lock:
            to_remove = [
                user_id for user_id, last_updated
                in self.ingest_progress_updated_at.items()
                if last_updated < cutoff
            ]

            for user_id in to_remove:
                del self.ingest_progress[user_id]
                del self.ingest_progress_updated_at[user_id]
                removed += 1

        return removed

    def archive_old_actions(self, keep_days: float = 7.0) -> int:
        """Remove action entries older than keep_days. Returns remaining count."""
        cutoff = time.time() - (keep_days * 86400)

        with self.lock:
            self.actions = [
                action for action in self.actions
                if action.get("timestamp", 0) >= cutoff
                or "timestamp" not in action
            ]

        return len(self.actions)


@dataclass
class SupabaseStore:
    """Supabase-based implementation of Store for production persistence."""

    ingest_progress: dict = field(default_factory=dict)
    last_action_at: dict = field(default_factory=dict)
    last_scrape_at: dict = field(default_factory=dict)
    ingest_running: set = field(default_factory=set)
    genre_weights: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __init__(self):
        self.client = get_supabase_client()
        # Initialize fields that require runtime initialization
        self.ingest_progress = {}
        self.last_action_at = {}
        self.last_scrape_at = {}
        self.ingest_running = set()
        self.genre_weights = {}
        self.lock = threading.Lock()

    def add_exclusion(self, user_id: str, slug: str) -> None:
        """Add a movie to user's exclusion list in Supabase."""
        self.client.table("user_exclusions").insert({"user_id": user_id, "movie_slug": slug}).execute()

    def get_exclusions(self, user_id: str) -> set[str]:
        """Get user's exclusion list from Supabase."""
        response = self.client.table("user_exclusions").select("movie_slug").eq("user_id", user_id).execute()
        return {row["movie_slug"] for row in response.data}

    def add_watchlist(self, user_id: str, slug: str) -> None:
        """Add a movie to user's watchlist in Supabase."""
        try:
            self.client.table("user_watchlist").insert({
                "user_id": user_id,
                "movie_slug": slug
            }).execute()
        except Exception as e:
            # Ignore duplicate key errors (movie already in watchlist)
            if "duplicate key" not in str(e).lower():
                raise e

    def get_watchlist(self, user_id: str) -> set[str]:
        """Get user's watchlist from Supabase."""
        response = self.client.table("user_watchlist").select("movie_slug").eq("user_id", user_id).execute()
        return {row["movie_slug"] for row in response.data}

    def add_diary(self, user_id: str, slug: str) -> None:
        """Add a movie to user's diary in Supabase."""
        try:
            self.client.table("user_diary").insert({
                "user_id": user_id,
                "movie_slug": slug
            }).execute()
        except Exception as e:
            # Ignore duplicate key errors (movie already in diary)
            if "duplicate key" not in str(e).lower():
                raise e

    def get_diary(self, user_id: str) -> set[str]:
        """Get user's diary from Supabase."""
        response = self.client.table("user_diary").select("movie_slug").eq("user_id", user_id).execute()
        return {row["movie_slug"] for row in response.data}

    def upsert_movie(self, movie: dict) -> None:
        """Upsert movie metadata to Supabase cache."""
        self.client.table("movies").upsert({
            "slug": movie["slug"],
            "title": movie["title"],
            "poster_url": movie["poster_url"],
            "rating": movie["rating"],
            "popularity": movie["popularity"],
            "genres": movie["genres"],
            "synopsis": movie.get("synopsis", ""),
            "cast": movie.get("cast", []),
            "updated_at": time.time()
        }).execute()

    def get_movie(self, slug: str) -> dict | None:
        """Get movie metadata from Supabase cache."""
        response = self.client.table("movies").select("*").eq("slug", slug).execute()
        if response.data:
            return response.data[0]
        return None

    def get_movies(self) -> list[dict]:
        """Get all movies from Supabase cache."""
        response = self.client.table("movies").select("*").execute()
        return response.data

    def set_ingest_progress(self, user_id: str, value: int) -> None:
        """Set ingest progress (in-memory only)."""
        with self.lock:
            self.ingest_progress[user_id] = max(0, min(100, value))

    def get_ingest_progress(self, user_id: str) -> int:
        """Get ingest progress (in-memory only)."""
        with self.lock:
            return self.ingest_progress.get(user_id, 0)

    def should_rate_limit(self, user_id: str, lock_ms: int = 500) -> tuple[bool, int]:
        """Check if user should be rate-limited (in-memory only)."""
        now = time.time() * 1000
        with self.lock:
            previous = self.last_action_at.get(user_id, 0)
            delta = now - previous
            if delta < lock_ms:
                return True, int(lock_ms - delta)
            self.last_action_at[user_id] = now
            return False, 0

    def allow_scrape_request(self, user_id: str, min_interval_seconds: float = 1.0) -> tuple[bool, float]:
        """Check if user should be allowed to scrape (in-memory only)."""
        now = time.time()
        with self.lock:
            previous = self.last_scrape_at.get(user_id, 0.0)
            delta = now - previous
            if delta < min_interval_seconds:
                return False, min_interval_seconds - delta
            self.last_scrape_at[user_id] = now
            return True, 0.0

    def record_genre_preference(self, user_id: str, genres: list[str]) -> None:
        """Record user's genre preferences in Supabase."""
        for genre in genres:
            try:
                # Try to update existing preference
                existing = self.client.table("genre_preferences").select("id", "weight").eq("user_id", user_id).eq("genre", genre).execute()

                if existing.data:
                    # Update existing preference
                    new_weight = existing.data[0]["weight"] + 1
                    self.client.table("genre_preferences").update({
                        "weight": new_weight,
                        "updated_at": time.time()
                    }).eq("id", existing.data[0]["id"]).execute()
                else:
                    # Insert new preference
                    self.client.table("genre_preferences").insert({
                        "user_id": user_id,
                        "genre": genre,
                        "weight": 1
                    }).execute()

            except Exception as e:
                # Log error but don't fail
                print(f"Error recording genre preference: {e}")

    def get_genre_weights(self, user_id: str) -> dict[str, int]:
        """Get user's genre weights from Supabase."""
        response = self.client.table("genre_preferences").select("genre", "weight").eq("user_id", user_id).execute()
        return {row["genre"]: row["weight"] for row in response.data}

    def weighted_shuffle(self, user_id: str, movies: list[dict]) -> list[dict]:
        """Shuffle movies with genre bias using persisted genre weights."""
        # Get weights from Supabase
        weights = self.get_genre_weights(user_id)

        if not weights:
            random.shuffle(movies)
            return movies

        def score(movie: dict) -> int:
            return sum(weights.get(g, 0) for g in movie.get("genres", []))

        boosted = sorted(movies, key=score, reverse=True)
        head = boosted[:8]
        tail = boosted[8:]
        random.shuffle(tail)
        return head + tail
