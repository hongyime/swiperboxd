from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from .database import get_supabase_client


def normalize_movie_record(movie: dict) -> dict:
    """Normalize movie payloads so deck building can tolerate partial/legacy records."""
    rating = movie.get("rating", 0.0)
    popularity = movie.get("popularity", 0)

    try:
        rating = float(rating if rating is not None else 0.0)
    except (TypeError, ValueError):
        rating = 0.0

    try:
        popularity = int(popularity if popularity is not None else 0)
    except (TypeError, ValueError):
        popularity = 0

    genres = movie.get("genres")
    if not isinstance(genres, list):
        genres = []

    cast = movie.get("cast")
    if not isinstance(cast, list):
        cast = []

    return {
        **movie,
        "slug": movie.get("slug", ""),
        "title": movie.get("title", ""),
        "poster_url": movie.get("poster_url", ""),
        "rating": rating,
        "popularity": popularity,
        "genres": genres,
        "synopsis": movie.get("synopsis", "") or "",
        "cast": cast,
    }


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

    def set_ingest_error(self, user_id: str, error: dict | None) -> None: ...

    def get_ingest_error(self, user_id: str) -> dict | None: ...

    def should_rate_limit(self, user_id: str, lock_ms: int = 500) -> tuple[bool, int]: ...

    def allow_scrape_request(self, user_id: str, min_interval_seconds: float = 1.0) -> tuple[bool, float]: ...

    def record_genre_preference(self, user_id: str, genres: list[str]) -> None: ...

    def get_genre_weights(self, user_id: str) -> dict[str, int]: ...

    def weighted_shuffle(self, user_id: str, movies: list[dict]) -> list[dict]: ...

    def upsert_list_summary(self, list_summary: dict) -> None: ...

    def get_list_summary(self, list_id: str) -> dict | None: ...

    def get_lists(self) -> list[dict]: ...

    def replace_list_memberships(self, list_id: str, movie_slugs: list[str]) -> None: ...

    def get_list_memberships(self, list_id: str) -> list[str]: ...


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
    ingest_errors: dict[str, dict] = field(default_factory=dict)
    last_action_at: dict[str, float] = field(default_factory=dict)
    last_scrape_at: dict[str, float] = field(default_factory=dict)
    ingest_running: set[str] = field(default_factory=set)
    genre_weights: dict[str, dict[str, int]] = field(default_factory=dict)
    list_summaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    list_memberships: dict[str, list[str]] = field(default_factory=dict)
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
        normalized = normalize_movie_record(movie)
        with self.lock:
            self.movies[normalized["slug"]] = normalized

    def get_movie(self, slug: str) -> dict | None:
        with self.lock:
            movie = self.movies.get(slug)
        return normalize_movie_record(movie) if movie else None

    def get_movies(self) -> list[dict]:
        with self.lock:
            movies = list(self.movies.values())
        return [normalize_movie_record(movie) for movie in movies]

    def set_ingest_progress(self, user_id: str, value: int) -> None:
        with self.lock:
            # -1 is the error sentinel; preserve it so the client can detect failure
            self.ingest_progress[user_id] = -1 if value == -1 else max(0, min(100, value))
            self.ingest_progress_updated_at[user_id] = time.time()

    def get_ingest_progress(self, user_id: str) -> int:
        with self.lock:
            return self.ingest_progress.get(user_id, 0)

    def set_ingest_error(self, user_id: str, error: dict | None) -> None:
        with self.lock:
            if error is None:
                self.ingest_errors.pop(user_id, None)
            else:
                self.ingest_errors[user_id] = dict(error)

    def get_ingest_error(self, user_id: str) -> dict | None:
        with self.lock:
            error = self.ingest_errors.get(user_id)
        return dict(error) if error else None

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

    def upsert_list_summary(self, list_summary: dict) -> None:
        normalized = {
            "list_id": list_summary.get("list_id", ""),
            "slug": list_summary.get("slug", ""),
            "url": list_summary.get("url", ""),
            "title": list_summary.get("title", ""),
            "owner_name": list_summary.get("owner_name", ""),
            "owner_slug": list_summary.get("owner_slug", ""),
            "description": list_summary.get("description", "") or "",
            "film_count": int(list_summary.get("film_count", 0) or 0),
            "like_count": int(list_summary.get("like_count", 0) or 0),
            "comment_count": int(list_summary.get("comment_count", 0) or 0),
            "is_official": bool(list_summary.get("is_official", False)),
            "tags": list_summary.get("tags", []) if isinstance(list_summary.get("tags", []), list) else [],
        }
        with self.lock:
            self.list_summaries[normalized["list_id"]] = normalized

    def get_list_summary(self, list_id: str) -> dict | None:
        with self.lock:
            summary = self.list_summaries.get(list_id)
        return dict(summary) if summary else None

    def get_lists(self) -> list[dict]:
        with self.lock:
            summaries = list(self.list_summaries.values())
        return [dict(summary) for summary in summaries]

    def replace_list_memberships(self, list_id: str, movie_slugs: list[str]) -> None:
        with self.lock:
            seen = set()
            deduped = []
            for slug in movie_slugs:
                if slug and slug not in seen:
                    seen.add(slug)
                    deduped.append(slug)
            self.list_memberships[list_id] = deduped

    def get_list_memberships(self, list_id: str) -> list[str]:
        with self.lock:
            memberships = self.list_memberships.get(list_id, [])
        return list(memberships)

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

    # NOTE: archive_old_actions is a future cleanup hook for the `actions` list.
    # The list is currently never populated by any active code path (queue.enqueue
    # was removed in Cycle 2). Wire this to a scheduler when the actions list gains
    # active writers.
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
    ingest_errors: dict = field(default_factory=dict)
    last_action_at: dict = field(default_factory=dict)
    last_scrape_at: dict = field(default_factory=dict)
    ingest_running: set = field(default_factory=set)
    genre_weights: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __init__(self):
        self.client = get_supabase_client()
        self.ingest_progress = {}
        self.ingest_errors = {}
        self.last_action_at = {}
        self.last_scrape_at = {}
        self.ingest_running = set()
        self.genre_weights = {}
        self.lock = threading.Lock()

    def _get_or_create_user_id(self, letterboxd_username: str) -> str:
        """Get user ID from Supabase or create new user."""
        # Try to find existing user by letterboxd_username
        response = self.client.table("users").select("id").eq("letterboxd_username", letterboxd_username).execute()

        if response.data:
            print(f"[store] found existing user id={response.data[0]['id']} for {letterboxd_username}", flush=True)
            return response.data[0]["id"]

        # Create new user
        print(f"[store] creating new user for {letterboxd_username}", flush=True)
        new_user = self.client.table("users").insert({
            "letterboxd_username": letterboxd_username
        }).execute()

        print(f"[store] created user id={new_user.data[0]['id']} for {letterboxd_username}", flush=True)
        return new_user.data[0]["id"]

    def add_exclusion(self, user_id: str, slug: str) -> None:
        """Add a movie to user's exclusion list in Supabase."""
        actual_user_id = self._get_or_create_user_id(user_id)
        try:
            self.client.table("exclusions").insert({"user_id": actual_user_id, "movie_slug": slug}).execute()
        except Exception as e:
            # Ignore duplicate key errors
            if "duplicate" not in str(e).lower() and "unique" not in str(e).lower():
                print(f"[store] ERROR: exclusion insert failed for {slug}: {e}", flush=True)
                raise

    def get_exclusions(self, user_id: str) -> set[str]:
        """Get user's exclusion list from Supabase."""
        actual_user_id = self._get_or_create_user_id(user_id)
        response = self.client.table("exclusions").select("movie_slug").eq("user_id", actual_user_id).execute()
        return {row["movie_slug"] for row in response.data}

    def add_watchlist(self, user_id: str, slug: str) -> None:
        """Add a movie to user's watchlist in Supabase."""
        actual_user_id = self._get_or_create_user_id(user_id)
        try:
            self.client.table("watchlist").insert({
                "user_id": actual_user_id,
                "movie_slug": slug
            }).execute()
        except Exception as e:
            # Ignore duplicate key errors
            if "duplicate" not in str(e).lower() and "unique" not in str(e).lower():
                print(f"[store] ERROR: watchlist insert failed for {slug}: {e}", flush=True)
                raise

    def get_watchlist(self, user_id: str) -> set[str]:
        """Get user's watchlist from Supabase."""
        actual_user_id = self._get_or_create_user_id(user_id)
        response = self.client.table("watchlist").select("movie_slug").eq("user_id", actual_user_id).execute()
        print(f"[store] get_watchlist: {len(response.data)} slugs for user_id={actual_user_id}", flush=True)
        return {row["movie_slug"] for row in response.data}

    def add_diary(self, user_id: str, slug: str) -> None:
        """Add a movie to user's diary in Supabase."""
        actual_user_id = self._get_or_create_user_id(user_id)
        try:
            self.client.table("diary").insert({
                "user_id": actual_user_id,
                "movie_slug": slug
            }).execute()
        except Exception as e:
            # Ignore duplicate key errors
            if "duplicate" not in str(e).lower() and "unique" not in str(e).lower():
                print(f"[store] ERROR: diary insert failed for {slug}: {e}", flush=True)
                raise

    def get_diary(self, user_id: str) -> set[str]:
        """Get user's diary from Supabase."""
        actual_user_id = self._get_or_create_user_id(user_id)
        response = self.client.table("diary").select("movie_slug").eq("user_id", actual_user_id).execute()
        print(f"[store] get_diary: {len(response.data)} slugs for user_id={actual_user_id}", flush=True)
        return {row["movie_slug"] for row in response.data}

    def get_diary(self, user_id: str) -> set[str]:
        """Get user's diary from Supabase."""
        actual_user_id = self._get_or_create_user_id(user_id)
        response = self.client.table("diary").select("movie_slug").eq("user_id", actual_user_id).execute()
        return {row["movie_slug"] for row in response.data}

    def upsert_movie(self, movie: dict) -> None:
        """Upsert movie metadata to Supabase cache."""
        normalized = normalize_movie_record(movie)
        record = {
            "slug": normalized["slug"],
            "title": normalized["title"],
            "poster_url": normalized.get("poster_url"),
            "rating": normalized.get("rating"),
            "popularity": normalized.get("popularity", 0),
            "genres": normalized.get("genres", []),
            "synopsis": normalized.get("synopsis", ""),
            "cast": normalized.get("cast", []),
        }
        
        # Add optional fields
        if "year" in movie:
            record["year"] = movie["year"]
        if "director" in movie:
            record["director"] = movie["director"]
            
        self.client.table("movies").upsert(record, on_conflict="slug").execute()

    def get_movie(self, slug: str) -> dict | None:
        """Get movie metadata from Supabase cache."""
        response = self.client.table("movies").select("*").eq("slug", slug).execute()
        if response.data:
            return normalize_movie_record(response.data[0])
        return None

    def get_movies(self) -> list[dict]:
        """Get all movies from Supabase cache."""
        response = self.client.table("movies").select("*").execute()
        return [normalize_movie_record(row) for row in response.data]

    def set_ingest_progress(self, user_id: str, value: int) -> None:
        """Set ingest progress (in-memory only - for performance)."""
        with self.lock:
            self.ingest_progress[user_id] = -1 if value == -1 else max(0, min(100, value))

    def get_ingest_progress(self, user_id: str) -> int:
        """Get ingest progress (in-memory only)."""
        with self.lock:
            return self.ingest_progress.get(user_id, 0)

    def set_ingest_error(self, user_id: str, error: dict | None) -> None:
        with self.lock:
            if error is None:
                self.ingest_errors.pop(user_id, None)
            else:
                self.ingest_errors[user_id] = dict(error)

    def get_ingest_error(self, user_id: str) -> dict | None:
        with self.lock:
            error = self.ingest_errors.get(user_id)
        return dict(error) if error else None

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
        import datetime
        actual_user_id = self._get_or_create_user_id(user_id)
        weights = self.get_genre_weights(user_id)
        now = datetime.datetime.utcnow().isoformat() + "Z"
        for genre in genres:
            weights[genre] = weights.get(genre, 0) + 1
            try:
                self.client.table("genre_preferences").upsert({
                    "user_id": actual_user_id,
                    "genre": genre,
                    "score": weights[genre],
                    "updated_at": now,
                }, on_conflict="user_id,genre").execute()
            except Exception as e:
                print(f"[store] genre_preference upsert failed for {genre}: {e}", flush=True)

    def get_genre_weights(self, user_id: str) -> dict[str, int]:
        """Get user's genre weights from Supabase."""
        actual_user_id = self._get_or_create_user_id(user_id)
        response = self.client.table("genre_preferences").select("genre", "score").eq("user_id", actual_user_id).execute()
        return {row["genre"]: int(row["score"]) for row in response.data}

    def weighted_shuffle(self, user_id: str, movies: list[dict]) -> list[dict]:
        """Shuffle movies with genre bias using persisted genre weights."""
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

    def upsert_list_summary(self, list_summary: dict) -> None:
        normalized = {
            "list_id": list_summary.get("list_id", ""),
            "slug": list_summary.get("slug", ""),
            "url": list_summary.get("url", ""),
            "title": list_summary.get("title", ""),
            "owner_name": list_summary.get("owner_name", ""),
            "owner_slug": list_summary.get("owner_slug", ""),
            "description": list_summary.get("description", "") or "",
            "film_count": int(list_summary.get("film_count", 0) or 0),
            "like_count": int(list_summary.get("like_count", 0) or 0),
            "comment_count": int(list_summary.get("comment_count", 0) or 0),
            "is_official": bool(list_summary.get("is_official", False)),
            "tags": list_summary.get("tags", []) if isinstance(list_summary.get("tags", []), list) else [],
            "updated_at": "now()",
        }
        if normalized["list_id"]:
            self.client.table("list_summaries").upsert(normalized, on_conflict="list_id").execute()

    def get_list_summary(self, list_id: str) -> dict | None:
        response = self.client.table("list_summaries").select("*").eq("list_id", list_id).execute()
        return response.data[0] if response.data else None

    def get_lists(self) -> list[dict]:
        response = self.client.table("list_summaries").select("*").execute()
        return list(response.data)

    def replace_list_memberships(self, list_id: str, movie_slugs: list[str]) -> None:
        seen: set[str] = set()
        deduped: list[str] = []
        for slug in movie_slugs:
            if slug and slug not in seen:
                seen.add(slug)
                deduped.append(slug)

        self.client.table("list_memberships").delete().eq("list_id", list_id).execute()
        if deduped:
            rows = [
                {"list_id": list_id, "movie_slug": slug, "position": i}
                for i, slug in enumerate(deduped)
            ]
            self.client.table("list_memberships").insert(rows).execute()

    def get_list_memberships(self, list_id: str) -> list[str]:
        response = (
            self.client.table("list_memberships")
            .select("movie_slug")
            .eq("list_id", list_id)
            .order("position")
            .execute()
        )
        return [row["movie_slug"] for row in response.data]
