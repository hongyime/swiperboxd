from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
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


@lru_cache(maxsize=1)
def _load_mock_catalog() -> dict[str, LetterboxdMovie]:
    catalog_path = Path(__file__).with_name("mock_catalog.json")
    rows = json.loads(catalog_path.read_text())
    return {row["slug"]: LetterboxdMovie(**row) for row in rows}


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
        lookup = _load_mock_catalog()
        return [lookup[slug] for slug in slugs if slug in lookup]


class HttpLetterboxdScraper:
    def __init__(self, base_url: str | None = None, timeout_seconds: float | None = None):
        self.base_url = (base_url or os.getenv("TARGET_PLATFORM_BASE_URL") or "https://letterboxd.com").rstrip("/")
        timeout_from_env = os.getenv("TARGET_PLATFORM_TIMEOUT_SECONDS")
        resolved_timeout = timeout_seconds
        if resolved_timeout is None and timeout_from_env:
            try:
                resolved_timeout = float(timeout_from_env)
            except ValueError:
                resolved_timeout = None
        self.timeout_seconds = resolved_timeout if resolved_timeout and resolved_timeout > 0 else 20.0

    def login(self, username: str, password: str) -> str:
        with httpx.Client(follow_redirects=True, timeout=self.timeout_seconds) as client:
            page = client.get(f"{self.base_url}/sign-in/")
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
                "Referer": f"{self.base_url}/sign-in/",
                "Origin": self.base_url,
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            }
            response = client.post(f"{self.base_url}/user/login.do", data=payload, headers=headers)
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
