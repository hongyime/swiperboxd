from types import SimpleNamespace

from src.api.providers.letterboxd import HttpLetterboxdScraper


def _page_html(*slugs: str) -> str:
    divs = "".join(
        f'<div class="react-component" data-item-slug="{slug}"></div>' for slug in slugs
    )
    return f"<html><body>{divs}</body></html>"


def _install_fake_fetch(monkeypatch, scraper, pages):
    """pages: list of slug tuples, one per requested URL, in request order."""
    requested: list[str] = []
    queue = list(pages)

    def fake_fetch(url, params=None, session_cookie=None):
        requested.append(url)
        slugs = queue.pop(0) if queue else ()
        return SimpleNamespace(status_code=200, text=_page_html(*slugs))

    monkeypatch.setattr(scraper, "_fetch", fake_fetch)
    return requested


def test_list_pagination_uses_path_and_stops_on_repeated_slug(monkeypatch):
    scraper = HttpLetterboxdScraper(base_url="https://letterboxd.com")
    requested = _install_fake_fetch(monkeypatch, scraper, [("a",), ("b",), ("a",)])

    slugs = scraper.fetch_list_movie_slugs("lst", list_url="https://letterboxd.com/u/list/x/")

    assert slugs == ["a", "b"]
    assert requested == [
        "https://letterboxd.com/u/list/x/",
        "https://letterboxd.com/u/list/x/page/2/",
        "https://letterboxd.com/u/list/x/page/3/",
    ]
    assert not any("?page=" in url for url in requested)


def test_list_pagination_stops_on_empty_page(monkeypatch):
    scraper = HttpLetterboxdScraper(base_url="https://letterboxd.com")
    requested = _install_fake_fetch(monkeypatch, scraper, [("a",), ()])

    slugs = scraper.fetch_list_movie_slugs("lst", list_url="https://letterboxd.com/u/list/x/")

    assert slugs == ["a"]
    assert requested == [
        "https://letterboxd.com/u/list/x/",
        "https://letterboxd.com/u/list/x/page/2/",
    ]


def test_watchlist_pagination_uses_path(monkeypatch):
    scraper = HttpLetterboxdScraper(base_url="https://letterboxd.com")
    requested = _install_fake_fetch(monkeypatch, scraper, [("a",), ("b",), ()])

    slugs = scraper.pull_watchlist_slugs("cookie", username="u")

    assert slugs == {"a", "b"}
    assert requested == [
        "https://letterboxd.com/u/watchlist/",
        "https://letterboxd.com/u/watchlist/page/2/",
        "https://letterboxd.com/u/watchlist/page/3/",
    ]
    assert not any("?page=" in url for url in requested)
