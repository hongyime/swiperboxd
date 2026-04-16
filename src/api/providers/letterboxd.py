from __future__ import annotations

import json
import os
import requests
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import httpx
from bs4 import BeautifulSoup

from .. import resilience


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


@dataclass
class LetterboxdListSummary:
    list_id: str
    slug: str
    url: str
    title: str
    owner_name: str
    owner_slug: str
    description: str
    film_count: int
    like_count: int
    comment_count: int
    is_official: bool
    tags: list[str]


class Scraper(Protocol):
    def login(self, username: str, password: str) -> str: ...

    def pull_watchlist_slugs(self, session_cookie: str) -> set[str]: ...

    def pull_diary_slugs(self, session_cookie: str) -> set[str]: ...

    def pull_source_slugs(self, source: str, depth_pages: int = 2) -> list[str]: ...

    def metadata_for_slugs(self, slugs: list[str]) -> list[LetterboxdMovie]: ...

    def discover_site_lists(self, query: str | None = None, page: int = 1) -> list[LetterboxdListSummary]: ...

    def fetch_list_movie_slugs(self, list_id: str) -> list[str]: ...


@lru_cache(maxsize=1)
def _load_mock_catalog() -> dict[str, LetterboxdMovie]:
    catalog_path = Path(__file__).with_name("mock_catalog.json")
    rows = json.loads(catalog_path.read_text())
    return {row["slug"]: LetterboxdMovie(**row) for row in rows}


def _load_mock_lists() -> list[LetterboxdListSummary]:
    return [
        LetterboxdListSummary(
            list_id="official-best-picture",
            slug="official-best-picture",
            url="https://letterboxd.com/official/list/best-picture/",
            title="Oscar-winning films: Best Picture",
            owner_name="Oscars",
            owner_slug="oscars",
            description="Official awards list for Best Picture winners.",
            film_count=5,
            like_count=9800,
            comment_count=420,
            is_official=True,
            tags=["awards", "official"],
        ),
        LetterboxdListSummary(
            list_id="community-hidden-gems",
            slug="community-hidden-gems",
            url="https://letterboxd.com/community/list/hidden-gems/",
            title="Hidden Gems You Need to Watch",
            owner_name="Letterboxd Community",
            owner_slug="community",
            description="Community-curated favorites beyond the canon.",
            film_count=5,
            like_count=6400,
            comment_count=128,
            is_official=False,
            tags=["community", "discover"],
        ),
        LetterboxdListSummary(
            list_id="official-top-500",
            slug="official-top-500",
            url="https://letterboxd.com/official/list/top-500/",
            title="Letterboxd's Top 500 Films",
            owner_name="Official Lists",
            owner_slug="official",
            description="Top-rated narrative features on Letterboxd.",
            film_count=5,
            like_count=12000,
            comment_count=510,
            is_official=True,
            tags=["official", "top-rated"],
        ),
    ]


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

    def discover_site_lists(self, query: str | None = None, page: int = 1) -> list[LetterboxdListSummary]:
        lists = _load_mock_lists()
        if query:
            query_lower = query.lower()
            lists = [item for item in lists if query_lower in item.title.lower() or query_lower in item.description.lower()]
        page_size = 12
        start = max(page - 1, 0) * page_size
        end = start + page_size
        return lists[start:end]

    def fetch_list_movie_slugs(self, list_id: str) -> list[str]:
        mapping = {
            "official-best-picture": ["film-c", "film-d", "film-e", "film-a"],
            "community-hidden-gems": ["film-d", "film-e", "film-c"],
            "official-top-500": ["film-c", "film-a", "film-d", "film-e"],
        }
        return mapping.get(list_id, ["film-c", "film-d"])


def _parse_member_count(text: str) -> int:
    """Parse Letterboxd member count strings like '1.2M', '45K', '1,234' → int."""
    text = text.strip().replace(",", "").replace("\xa0", "")
    if not text:
        return 0
    try:
        if text.upper().endswith("M"):
            return int(float(text[:-1]) * 1_000_000)
        if text.upper().endswith("K"):
            return int(float(text[:-1]) * 1_000)
        return int(float(text))
    except (ValueError, TypeError):
        return 0


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
        self._http_client = httpx.Client(
            timeout=self.timeout_seconds,
            follow_redirects=True
        )

    # Browser headers sent on every request to avoid bot detection.
    _BROWSER_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
        "Upgrade-Insecure-Requests": "1",
    }

    def login(self, username: str, password: str) -> str:
        with httpx.Client(
            follow_redirects=True,
            timeout=self.timeout_seconds,
            headers=self._BROWSER_HEADERS,
        ) as client:
            page = client.get(f"{self.base_url}/sign-in/")
            print(f"[auth] sign-in page status={page.status_code} cookies={list(client.cookies.keys())}", flush=True)

            soup = BeautifulSoup(page.text, "html.parser")
            csrf_input = soup.select_one('input[name="__csrf"]')
            auth_code_input = soup.select_one('input[name="authenticationCode"]')
            if not csrf_input or not csrf_input.get("value"):
                print("[auth] csrf token not found in sign-in page", flush=True)
                raise RuntimeError("csrf_token_missing")

            print(f"[auth] csrf found, authCode present={auth_code_input is not None}", flush=True)

            payload = {
                "username": username,
                "password": password,
                "__csrf": csrf_input.get("value"),
                "authenticationCode": auth_code_input.get("value", "") if auth_code_input else "",
                "remember": "true",
            }
            # Plain browser form POST — NOT XHR.
            # Success: Letterboxd 302-redirects to the user profile, setting the
            # session cookie during the redirect chain.
            # Failure (wrong creds / challenge): stays on /user/login.do with 200.
            login_headers = {
                "Referer": f"{self.base_url}/sign-in/",
                "Origin": self.base_url,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            response = client.post(
                f"{self.base_url}/user/login.do",
                data=payload,
                headers=login_headers,
            )
            print(
                f"[auth] login.do status={response.status_code} "
                f"final_url={response.url} "
                f"jar_keys={list(client.cookies.keys())}",
                flush=True,
            )

            # Successful login: follow_redirects=True chases the 302 → profile page,
            # and the session cookie lands in client.cookies during that chain.
            cookie = client.cookies.get("letterboxd.session")
            if not cookie:
                status = response.status_code
                if status in {401, 403, 429}:
                    raise RuntimeError(f"upstream_rejected (status={status})")

                # status=200 + still on login page = wrong credentials or CAPTCHA
                if "login.do" in str(response.url):
                    error_soup = BeautifulSoup(response.text, "html.parser")
                    msg_tag = (
                        error_soup.select_one(".form-error")
                        or error_soup.select_one('[class*="error"]')
                        or error_soup.select_one(".alert")
                    )
                    hint = msg_tag.get_text(" ", strip=True)[:120] if msg_tag else "no error element found in page"
                    print(f"[auth] login page returned — hint: {hint}", flush=True)
                    raise RuntimeError(f"wrong_credentials_or_captcha — {hint}")

                raise RuntimeError(f"session_cookie_missing (status={status}, final_url={response.url})")
            return cookie

    def pull_watchlist_slugs(self, session_cookie: str) -> set[str]:
        """Pull all film slugs from user's watchlist with pagination."""
        slugs = set()
        page = 1

        try:
            with httpx.Client(
                cookies={"letterboxd.user.CURRENT": session_cookie},
                timeout=self.timeout_seconds,
                follow_redirects=True
            ) as client:
                while True:
                    response = client.get(f"{self.base_url}/watchlist/", params={"page": page})

                    if response.status_code in {403, 429}:
                        raise RuntimeError("rate_limit_require_proxy")

                    soup = BeautifulSoup(response.text, "html.parser")
                    film_links = soup.select("li.poster-container a")

                    if not film_links:
                        break  # No more pages

                    for link in film_links:
                        href = link.get("href", "")
                        if href.startswith("/film/"):
                            slug = href.split("/")[2]
                            slugs.add(slug)

                    page += 1

        except httpx.TimeoutException:
            # Apply exponential backoff and retry logic would go here
            pass

        return slugs

    def pull_diary_slugs(self, session_cookie: str) -> set[str]:
        """Pull all film slugs from user's diary (watched films) with pagination."""
        slugs = set()
        page = 1

        try:
            with httpx.Client(
                cookies={"letterboxd.user.CURRENT": session_cookie},
                timeout=self.timeout_seconds,
                follow_redirects=True
            ) as client:
                while True:
                    response = client.get(f"{self.base_url}/diary/", params={"page": page})

                    if response.status_code in {403, 429}:
                        raise RuntimeError("rate_limit_require_proxy")

                    soup = BeautifulSoup(response.text, "html.parser")
                    film_links = soup.select("td.poster-container a")

                    if not film_links:
                        break  # No more pages

                    for link in film_links:
                        href = link.get("href", "")
                        if href.startswith("/film/"):
                            slug = href.split("/")[2]
                            slugs.add(slug)

                    page += 1

        except httpx.TimeoutException:
            pass

        return slugs

    def pull_source_slugs(self, source: str, depth_pages: int = 2) -> list[str]:
        """Pull film slugs from source pages (trending, popular, recommended) with pagination."""
        url_map = {
            "trending": "/films/trending/",
            "popular": "/films/popular/",
            "recommended": "/films/reception/recommended/",
        }

        if source not in url_map:
            raise ValueError(f"Unknown source: {source}")

        slugs = []
        base_path = url_map[source]

        try:
            for page in range(1, depth_pages + 1):
                response = self._http_client.get(
                    f"{self.base_url}{base_path}",
                    params={"page": page}
                )

                # Check for rate limiting
                if response.status_code in {403, 429}:
                    raise RuntimeError("rate_limit_require_proxy")

                soup = BeautifulSoup(response.text, "html.parser")
                film_links = soup.select("li.poster-container a")

                for link in film_links:
                    href = link.get("href", "")
                    if href.startswith("/film/"):
                        slug = href.split("/")[2]
                        if slug not in slugs:
                            slugs.append(slug)

        except httpx.TimeoutException:
            pass

        return slugs

    def metadata_for_slugs(self, slugs: list[str]) -> list[LetterboxdMovie]:
        """Fetch metadata for given film slugs by scraping individual film pages."""
        movies = []
        from .resilience import should_trigger_proxy_fallback, exponential_backoff_seconds

        for i, slug in enumerate(slugs):
            try:
                use_proxy = False
                retry_count = 0

                while retry_count < 3:
                    client_config = {
                        "timeout": self.timeout_seconds,
                        "follow_redirects": True,
                    }

                    if use_proxy:
                        proxy_url = self._get_proxy_url()
                        if proxy_url:
                            client_config["proxies"] = {"http://": proxy_url, "https://": proxy_url}

                    with httpx.Client(**client_config) as client:
                        response = client.get(f"{self.base_url}/film/{slug}/")

                        # Check for rate limiting
                        if response.status_code in {403, 429}:
                            if should_trigger_proxy_fallback(response.status_code):
                                use_proxy = True
                                retry_count = 0
                                continue
                            else:
                                raise RuntimeError("rate_limit_no_proxy_available")

                        soup = BeautifulSoup(response.text, "html.parser")
                        
                        # Extract title
                        title_tag = soup.select_one("h1.title-h1")
                        title = title_tag.get_text().strip() if title_tag else ""
                        
                        # Extract poster URL
                        poster_tag = soup.select_one("img.poster-image")
                        poster_url = poster_tag.get("src", "") if poster_tag else ""
                        
                        # Extract rating and popularity
                        meta_rating = soup.select_one("meta[name='twitter:data1']")
                        rating = 0.0
                        if meta_rating:
                            try:
                                rating = float(meta_rating.get("content", "0"))
                            except ValueError:
                                rating = 0.0

                        # Extract genres
                        genre_tags = soup.select("a[href*='/genre/']")
                        genres = [tag.get_text().strip() for tag in genre_tags]
                        
                        # Extract synopsis
                        synopsis_tag = soup.select_one("div.truncate-credits div")
                        synopsis = synopsis_tag.get_text().strip() if synopsis_tag else ""
                        
                        # Extract cast list
                        cast_tags = soup.select("span.cast a")
                        cast = [tag.get_text().strip() for tag in cast_tags[:5]] if cast_tags else []

                        # Extract member/watch count as popularity proxy.
                        # Letterboxd renders this as a stat link containing a numeric text
                        # (e.g. "1.2M", "45K"). Try known selectors in priority order.
                        popularity = 0
                        for sel in [
                            "a.has-icon.icon-watched span",
                            "li.filmstat-watches a",
                            "a[href$='/members/']",
                        ]:
                            tag = soup.select_one(sel)
                            if tag:
                                popularity = _parse_member_count(tag.get_text())
                                if popularity:
                                    break

                        movies.append(
                            LetterboxdMovie(
                                slug=slug,
                                title=title,
                                poster_url=poster_url,
                                rating=rating,
                                popularity=popularity,
                                genres=genres,
                                synopsis=synopsis,
                                cast=cast,
                            )
                        )
                        break

                    retry_count += 1
                    if retry_count > 0:
                        time.sleep(exponential_backoff_seconds(retry_count))

            except Exception as exc:
                pass

        return movies

    def discover_site_lists(self, query: str | None = None, page: int = 1) -> list[LetterboxdListSummary]:
        raise NotImplementedError("discover_site_lists is not yet implemented for HTTP scraper")

    def fetch_list_movie_slugs(self, list_id: str) -> list[str]:
        raise NotImplementedError("fetch_list_movie_slugs is not yet implemented for HTTP scraper")

    def _get_proxy_url(self) -> str | None:
        """Get rotating proxy URL if configured."""
        proxy_endpoint = os.getenv("ROTATING_PROXY_ENDPOINT")
        proxy_key = os.getenv("ROTATING_PROXY_API_KEY")

        if not proxy_endpoint or not proxy_key:
            return None

        try:
            response = requests.get(
                proxy_endpoint,
                headers={"X-API-Key": proxy_key},
                timeout=5
            )
            return response.json().get("proxy_url")
        except Exception:
            return None
