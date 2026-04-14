from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field


@dataclass
class InMemoryStore:
    exclusions: dict[str, set[str]] = field(default_factory=dict)
    watchlist: dict[str, set[str]] = field(default_factory=dict)
    diary: dict[str, set[str]] = field(default_factory=dict)
    movies: dict[str, dict] = field(default_factory=dict)
    actions: list[dict] = field(default_factory=list)
    ingest_progress: dict[str, int] = field(default_factory=dict)
    last_action_at: dict[str, float] = field(default_factory=dict)
    last_scrape_at: dict[str, float] = field(default_factory=dict)
    ingest_running: set[str] = field(default_factory=set)
    genre_weights: dict[str, dict[str, int]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add_exclusion(self, user_id: str, slug: str) -> None:
        with self.lock:
            self.exclusions.setdefault(user_id, set()).add(slug)

    def add_watchlist(self, user_id: str, slug: str) -> None:
        with self.lock:
            self.watchlist.setdefault(user_id, set()).add(slug)

    def add_diary(self, user_id: str, slug: str) -> None:
        with self.lock:
            self.diary.setdefault(user_id, set()).add(slug)

    def get_exclusions(self, user_id: str) -> set[str]:
        with self.lock:
            return set(self.exclusions.get(user_id, set()))

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
