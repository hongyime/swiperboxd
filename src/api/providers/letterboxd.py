from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import httpx
from bs4 import BeautifulSoup

from ..proxy_manager import ProxyManager


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

    def pull_watchlist_slugs(self, session_cookie: str, username: str | None = None) -> set[str]: ...

    def pull_diary_slugs(self, session_cookie: str, username: str | None = None) -> set[str]: ...

    def pull_source_slugs(self, source: str, depth_pages: int = 2) -> list[str]: ...

    def metadata_for_slugs(self, slugs: list[str]) -> list[LetterboxdMovie]: ...

    def discover_site_lists(self, query: str | None = None, page: int = 1) -> list[LetterboxdListSummary]: ...

    def fetch_list_movie_slugs(self, list_id: str, list_url: str | None = None) -> list[str]: ...


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

    def pull_watchlist_slugs(self, session_cookie: str, username: str | None = None) -> set[str]:
        return {"film-a"}

    def pull_diary_slugs(self, session_cookie: str, username: str | None = None) -> set[str]:
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

    def fetch_list_movie_slugs(self, list_id: str, list_url: str | None = None) -> list[str]:
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


def _extract_film_slugs(soup) -> list[str]:
    """Extract film slugs from a Letterboxd page using current selectors.

    Tries multiple selector strategies to stay robust against HTML changes:
      1. data-item-slug on react-component divs (2024+ redesign)
      2. data-film-slug on film-poster divs (older)
      3. href pattern on any <a> tag pointing to /film/slug/
    """
    slugs = []
    seen: set[str] = set()

    def _add(slug: str) -> None:
        if slug and slug not in seen:
            seen.add(slug)
            slugs.append(slug)

    # Strategy 1: react-component data-item-slug
    for el in soup.select("div.react-component[data-item-slug]"):
        _add(el["data-item-slug"])

    # Strategy 2: film-poster data-film-slug
    for el in soup.select("[data-film-slug]"):
        _add(el["data-film-slug"])

    # Strategy 3: anchor hrefs  /film/<slug>/
    if not slugs:
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            parts = href.strip("/").split("/")
            if len(parts) >= 2 and parts[0] == "film":
                _add(parts[1])

    return slugs


class HttpLetterboxdScraper:
    def __init__(self, base_url: str | None = None, timeout_seconds: float | None = None, session_cookie: str | None = None):
        self.base_url = (base_url or os.getenv("TARGET_PLATFORM_BASE_URL") or "https://letterboxd.com").rstrip("/")
        timeout_from_env = os.getenv("TARGET_PLATFORM_TIMEOUT_SECONDS")
        resolved_timeout = timeout_seconds
        if resolved_timeout is None and timeout_from_env:
            try:
                resolved_timeout = float(timeout_from_env)
            except ValueError:
                resolved_timeout = None
        self.timeout_seconds = resolved_timeout if resolved_timeout and resolved_timeout > 0 else 20.0
        # Default session cookie used as tier-1 on every request when set.
        # Pass session_cookie= at construction or set scraper.session_cookie later.
        self.session_cookie: str | None = session_cookie
        self._proxy_manager = ProxyManager()
        print(f"[proxy] Initialized proxy manager for {self.base_url}", flush=True)

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

    _RATE_LIMIT_STATUSES: frozenset[int] = frozenset({403, 429, 502, 503, 504})

    def _fetch(
        self,
        url: str,
        params: dict | None = None,
        session_cookie: str | None = None,
    ) -> httpx.Response:
        # Fall back to the scraper-level default cookie when no per-call cookie given
        if session_cookie is None:
            session_cookie = self.session_cookie
        """Fetch *url* through the 4-tier fallback chain.

        Tier 1: session-cookie + direct  (only when session_cookie is provided)
        Tier 2: WebShare rotating proxy  (+ cookie if available)
        Tier 3: Scrape.do URL-wrapping service
        Tier 4: raw direct request

        Returns the first response with a non-rate-limit status code.
        Raises RuntimeError("all_tiers_failed") when every tier is exhausted.
        """
        base_kwargs = {
            "timeout": self.timeout_seconds,
            "follow_redirects": True,
            "headers": self._BROWSER_HEADERS,
        }
        for tier_name, extra in self._proxy_manager.iter_tiers(url, session_cookie):
            extra = dict(extra)  # copy — don't mutate the list entry
            scrape_do_url = extra.pop("scrape_do_url", None)
            if scrape_do_url is not None:
                # ScrapeDo wraps the target URL — query params must be baked
                # into the target before URL-encoding, not appended to the API URL.
                if params:
                    from urllib.parse import urlencode, quote as _quote
                    target_with_params = f"{url}?{urlencode(params)}"
                    token = os.getenv("SCRAPEDO_TOKEN", "")
                    request_url = f"http://api.scrape.do/?token={token}&url={_quote(target_with_params, safe='')}"
                else:
                    request_url = scrape_do_url
                request_params = None  # already baked into the URL
            else:
                request_url = url
                request_params = params
            client_kwargs = {**base_kwargs, **extra}
            try:
                with httpx.Client(**client_kwargs) as client:
                    resp = client.get(request_url, params=request_params)
                if resp.status_code in self._RATE_LIMIT_STATUSES:
                    print(
                        f"[scraper] tier={tier_name} status={resp.status_code} url={url} → next tier",
                        flush=True,
                    )
                    self._proxy_manager.record_failure_for(tier_name)
                    continue
                self._proxy_manager.record_success_for(tier_name)
                if tier_name not in ("cookie", "direct"):
                    print(f"[scraper] tier={tier_name} OK url={url}", flush=True)
                return resp
            except (httpx.TimeoutException, httpx.ProxyError) as exc:
                print(f"[scraper] tier={tier_name} error={exc!r} url={url} → next tier", flush=True)
                self._proxy_manager.record_failure_for(tier_name)
        raise RuntimeError("all_tiers_failed")

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

    def pull_watchlist_slugs(self, session_cookie: str, username: str | None = None) -> set[str]:
        """Pull all film slugs from the authenticated user's watchlist."""
        slugs: set[str] = set()
        path = f"/{username}/watchlist/" if username else "/watchlist/"
        url = f"{self.base_url}{path}"
        for page in range(1, 51):  # cap at 50 pages (~600 films)
            try:
                resp = self._fetch(url, params={"page": page}, session_cookie=session_cookie)
            except RuntimeError:
                break
            soup = BeautifulSoup(resp.text, "html.parser")
            page_slugs = _extract_film_slugs(soup)
            if not page_slugs:
                break
            slugs.update(page_slugs)
        print(f"[scraper] watchlist pull: {len(slugs)} slugs for {username or '(no username)'}", flush=True)
        return slugs

    def pull_diary_slugs(self, session_cookie: str, username: str | None = None) -> set[str]:
        """Pull all film slugs from the authenticated user's diary."""
        slugs: set[str] = set()
        path = f"/{username}/films/diary/" if username else "/diary/"
        url = f"{self.base_url}{path}"
        for page in range(1, 201):  # cap at 200 pages (~2 400 entries)
            try:
                resp = self._fetch(url, params={"page": page}, session_cookie=session_cookie)
            except RuntimeError:
                break
            soup = BeautifulSoup(resp.text, "html.parser")
            page_slugs = _extract_film_slugs(soup)
            if not page_slugs:
                break
            slugs.update(page_slugs)
        print(f"[scraper] diary pull: {len(slugs)} slugs for {username or '(no username)'}", flush=True)
        return slugs

    def pull_source_slugs(self, source: str, depth_pages: int = 2) -> list[str]:
        """Pull film slugs from public source pages (trending, popular, recommended)."""
        url_map = {
            "trending": "/films/trending/",
            "popular": "/films/popular/",
            "recommended": "/films/reception/recommended/",
        }
        if source not in url_map:
            raise ValueError(f"Unknown source: {source}")

        slugs: list[str] = []
        seen: set[str] = set()
        url = f"{self.base_url}{url_map[source]}"
        for page in range(1, depth_pages + 1):
            try:
                resp = self._fetch(url, params={"page": page})
            except RuntimeError:
                break
            soup = BeautifulSoup(resp.text, "html.parser")
            for link in soup.select("li.poster-container a"):
                href = link.get("href", "")
                if href.startswith("/film/"):
                    slug = href.split("/")[2]
                    if slug not in seen:
                        seen.add(slug)
                        slugs.append(slug)
        return slugs

    def metadata_for_slugs(self, slugs: list[str]) -> list[LetterboxdMovie]:
        """Fetch metadata for given film slugs by scraping individual film pages.

        Primary source: JSON-LD (<script type="application/ld+json">) which
        Letterboxd embeds on every film page and is stable across HTML redesigns.
        HTML selectors are used only as fallbacks for fields not in JSON-LD.
        """
        import json as _json
        import re as _re

        movies = []
        for slug in slugs:
            url = f"{self.base_url}/film/{slug}/"
            try:
                resp = self._fetch(url)
            except RuntimeError:
                print(f"[scraper] all tiers failed for slug={slug}, skipping", flush=True)
                continue

            try:
                soup = BeautifulSoup(resp.text, "html.parser")

                # ── JSON-LD (primary) ──────────────────────────────────────
                ld: dict = {}
                for s in soup.find_all("script", type="application/ld+json"):
                    raw = _re.sub(r"/\*.*?\*/", "", s.string or "", flags=_re.DOTALL).strip()
                    try:
                        ld = _json.loads(raw)
                        break
                    except (_json.JSONDecodeError, TypeError):
                        continue

                # Title
                title = ld.get("name", "")
                if not title:
                    el = soup.select_one("h1.primaryname") or soup.select_one("h1.headline-1")
                    title = el.get_text(strip=True) if el else ""

                # Poster — JSON-LD image URL (high-res); og:image as fallback
                poster_url = ld.get("image", "")
                if not poster_url:
                    og = soup.select_one('meta[property="og:image"]')
                    poster_url = og.get("content", "") if og else ""

                # Rating
                rating = 0.0
                agg = ld.get("aggregateRating", {})
                if agg:
                    try:
                        rating = float(agg.get("ratingValue", 0) or 0)
                    except (TypeError, ValueError):
                        pass

                # Genres
                genres: list[str] = []
                if isinstance(ld.get("genre"), list):
                    genres = [str(g) for g in ld["genre"]]
                elif isinstance(ld.get("genre"), str):
                    genres = [ld["genre"]]
                if not genres:
                    genres = [tag.get_text(strip=True) for tag in soup.select("a[href*='/genre/']")]

                # Synopsis — JSON-LD description; fallback to .review block
                synopsis = ld.get("description", "") or ""
                if not synopsis:
                    review_tag = soup.select_one("div.review")
                    if review_tag:
                        # Strip the "Synopsis" label if present
                        text = review_tag.get_text(strip=True)
                        synopsis = text[len("Synopsis"):].strip() if text.startswith("Synopsis") else text

                # Cast — JSON-LD "actors" list
                cast: list[str] = []
                actors_raw = ld.get("actors") or ld.get("actor") or []
                if isinstance(actors_raw, list):
                    cast = [a["name"] for a in actors_raw[:5] if isinstance(a, dict) and "name" in a]

                # Popularity — use ratingCount from aggregateRating as proxy
                popularity = 0
                if agg:
                    try:
                        popularity = int(agg.get("ratingCount", 0) or 0)
                    except (TypeError, ValueError):
                        pass

                movies.append(LetterboxdMovie(
                    slug=slug,
                    title=title,
                    poster_url=poster_url,
                    rating=rating,
                    popularity=popularity,
                    genres=genres,
                    synopsis=synopsis,
                    cast=cast,
                ))
            except Exception as exc:
                print(f"[scraper] parse error slug={slug}: {exc}", flush=True)

        return movies

    def discover_site_lists(self, query: str | None = None, page: int = 1) -> list[LetterboxdListSummary]:
        """Scrape Letterboxd popular lists page for list summaries.

        CSS selectors are verified against Letterboxd's public list browse page
        (https://letterboxd.com/lists/popular/). If Letterboxd changes its HTML
        structure, update the selectors here — all other logic stays the same.
        """
        url = f"{self.base_url}/lists/popular/"
        results: list[LetterboxdListSummary] = []

        try:
            response = self._fetch(url, params={"page": page})
            soup = BeautifulSoup(response.text, "html.parser")

            # Each list entry: div.listitem > article.list-summary
            entries = soup.select("div.listitem article.list-summary")

            for entry in entries:
                try:
                    # Title + href (/owner/list/slug/)
                    title_tag = entry.select_one("h2.name a") or entry.select_one("h2 a")
                    if not title_tag:
                        continue

                    href = title_tag.get("href", "")
                    # href pattern: /owner_slug/list/list_slug/
                    parts = [p for p in href.strip("/").split("/") if p]
                    if len(parts) < 3 or parts[1] != "list":
                        continue

                    owner_slug = parts[0]
                    list_slug = parts[2]
                    list_id = f"{owner_slug}-{list_slug}"
                    list_url = f"{self.base_url}/{owner_slug}/list/{list_slug}/"
                    title = title_tag.get_text(strip=True)

                    # Owner display name
                    owner_tag = entry.select_one("strong.displayname") or entry.select_one("a.owner")
                    owner_name = owner_tag.get_text(strip=True) if owner_tag else owner_slug

                    # Description — first paragraph of the notes block
                    desc_tag = entry.select_one("div.notes p") or entry.select_one("div.body-text p")
                    description = desc_tag.get_text(strip=True) if desc_tag else ""

                    # Film count — "800 films" in span.value
                    film_count = 0
                    count_tag = entry.select_one("span.value")
                    if count_tag:
                        count_text = count_tag.get_text(strip=True).replace(",", "")
                        for token in count_text.split():
                            try:
                                film_count = int(token)
                                break
                            except ValueError:
                                continue

                    # Like count — link to /likes/ page contains "382K"
                    like_count = 0
                    like_tag = entry.select_one("a[href$='/likes/'] span.label")
                    if like_tag:
                        like_count = _parse_member_count(like_tag.get_text(strip=True))

                    # Comment count — link to /#comments
                    comment_count = 0
                    comment_tag = entry.select_one("a[href$='/#comments'] span.label") or entry.select_one("a[href*='#comments'] span.label")
                    if comment_tag:
                        comment_count = _parse_member_count(comment_tag.get_text(strip=True))

                    results.append(
                        LetterboxdListSummary(
                            list_id=list_id,
                            slug=list_slug,
                            url=list_url,
                            title=title,
                            owner_name=owner_name,
                            owner_slug=owner_slug,
                            description=description,
                            film_count=film_count,
                            like_count=like_count,
                            comment_count=comment_count,
                            is_official=(owner_slug in {"letterboxd", "official"}),
                            tags=[],
                        )
                    )
                except Exception as exc:
                    print(f"[lists] skipping entry due to parse error: {exc}", flush=True)
                    continue

        except Exception as exc:
            print(f"[lists] discover_site_lists failed page={page}: {exc}", flush=True)

        if query:
            q = query.lower()
            results = [
                r for r in results
                if q in r.title.lower() or q in r.description.lower() or q in r.owner_name.lower()
            ]

        return results

    def fetch_list_movie_slugs(self, list_id: str, list_url: str | None = None) -> list[str]:
        """Scrape all film slugs from a Letterboxd list page, with pagination.

        Requires `list_url` — the canonical URL of the list
        (e.g. https://letterboxd.com/someuser/list/my-list/).
        Capped at 20 pages (~240 films) to prevent runaway scraping.
        """
        if not list_url:
            raise ValueError(
                f"fetch_list_movie_slugs requires list_url for HTTP scraper "
                f"(list_id={list_id!r}). Pass list_url=summary['url'] from the store."
            )

        slugs: list[str] = []
        seen: set[str] = set()
        max_pages = 20

        for page_num in range(1, max_pages + 1):
            try:
                response = self._fetch(list_url, params={"page": page_num})
                if response.status_code == 404:
                    break

                soup = BeautifulSoup(response.text, "html.parser")

                # Primary selector: data-item-slug on react-component poster divs
                react_divs = soup.select("div.react-component[data-item-slug]")
                page_slugs: list[str] = [
                    div["data-item-slug"] for div in react_divs
                    if div.get("data-item-slug")
                ]

                # Fallback: extract slug from /film/<slug>/ href on any link
                if not page_slugs:
                    for link in soup.select("a[href^='/film/']"):
                        href = link.get("href", "")
                        parts = href.strip("/").split("/")
                        if len(parts) >= 2 and parts[0] == "film" and len(parts[1]) > 0:
                            page_slugs.append(parts[1])

                if not page_slugs:
                    break  # no more films on this page

                for slug in page_slugs:
                    if slug and slug not in seen:
                        seen.add(slug)
                        slugs.append(slug)

            except Exception as exc:
                print(f"[lists] error fetching list slugs list_id={list_id} page={page_num}: {exc}", flush=True)
                break

        return slugs



