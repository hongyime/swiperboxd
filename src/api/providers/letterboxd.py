from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx
from bs4 import BeautifulSoup


@dataclass
class LetterboxdMovie:
    slug: str
    title: str
    poster_url: str
    rating: float
    popularity: int
    genres: list[str]
    synopsis: str
    cast: list[str]


class Scraper(Protocol):
    def login(self, username: str, password: str) -> str: ...

    def pull_watchlist_slugs(self, session_cookie: str) -> set[str]: ...

    def pull_diary_slugs(self, session_cookie: str) -> set[str]: ...

    def pull_source_slugs(self, source: str, depth_pages: int = 2) -> list[str]: ...

    def metadata_for_slugs(self, slugs: list[str]) -> list[LetterboxdMovie]: ...


class MockLetterboxdScraper:
    """Scraper abstraction for local/dev; replace with real parser updates when HTML changes."""

    def login(self, username: str, password: str) -> str:
        return f"session::{username}"

    def pull_watchlist_slugs(self, session_cookie: str) -> set[str]:
        return {"film-a"}

    def pull_diary_slugs(self, session_cookie: str) -> set[str]:
        return {"film-b"}

    def pull_source_slugs(self, source: str, depth_pages: int = 2) -> list[str]:
        return ["film-a", "film-b", "film-c", "film-d", "film-e"]

    def metadata_for_slugs(self, slugs: list[str]) -> list[LetterboxdMovie]:
        lookup = {
            "film-a": LetterboxdMovie("film-a", "Film A", "https://picsum.photos/seed/film-a/300/450", 4.8, 120, ["Horror"], "A horror story.", ["Actor One"]),
            "film-b": LetterboxdMovie("film-b", "Film B", "https://picsum.photos/seed/film-b/300/450", 4.6, 80, ["Drama"], "A drama story.", ["Actor Two"]),
            "film-c": LetterboxdMovie("film-c", "Film C", "https://picsum.photos/seed/film-c/300/450", 4.2, 30, ["Horror", "Thriller"], "A thriller story.", ["Actor Three"]),
            "film-d": LetterboxdMovie("film-d", "Film D", "https://picsum.photos/seed/film-d/300/450", 3.9, 15, ["Comedy"], "A comedy story.", ["Actor Four"]),
            "film-e": LetterboxdMovie("film-e", "Film E", "https://picsum.photos/seed/film-e/300/450", 4.9, 8, ["Horror"], "An arthouse horror.", ["Actor Five"]),
        }
        return [lookup[s] for s in slugs if s in lookup]


class HttpLetterboxdScraper:
    BASE_URL = "https://letterboxd.com"

    def login(self, username: str, password: str) -> str:
        with httpx.Client(follow_redirects=True, timeout=20) as client:
            page = client.get(f"{self.BASE_URL}/sign-in/")
            soup = BeautifulSoup(page.text, "html.parser")
            csrf_input = soup.select_one('input[name="__csrf"]')
            auth_code_input = soup.select_one('input[name="authenticationCode"]')
            if not csrf_input or not csrf_input.get("value"):
                raise RuntimeError("csrf_token_missing")

            payload = {
                "username": username,
                "password": password,
                "__csrf": csrf_input.get("value"),
                "authenticationCode": auth_code_input.get("value", "") if auth_code_input else "",
                "remember": "true",
            }
            headers = {
                "Referer": f"{self.BASE_URL}/sign-in/",
                "Origin": self.BASE_URL,
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            }
            response = client.post(f"{self.BASE_URL}/user/login.do", data=payload, headers=headers)
            cookie = response.cookies.get("letterboxd.session")
            if not cookie:
                if response.status_code in {401, 403, 429}:
                    raise RuntimeError("auth_rejected_or_challenge")
                raise RuntimeError("session_cookie_missing")
            return cookie

    def pull_watchlist_slugs(self, session_cookie: str) -> set[str]:
        return set()

    def pull_diary_slugs(self, session_cookie: str) -> set[str]:
        return set()

    def pull_source_slugs(self, source: str, depth_pages: int = 2) -> list[str]:
        return []

    def metadata_for_slugs(self, slugs: list[str]) -> list[LetterboxdMovie]:
        return []
