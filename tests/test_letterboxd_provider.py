import httpx

from api.providers.letterboxd import HttpLetterboxdScraper


def test_http_scraper_uses_env_config(monkeypatch):
    monkeypatch.setenv("TARGET_PLATFORM_BASE_URL", "https://example.test")
    monkeypatch.setenv("TARGET_PLATFORM_TIMEOUT_SECONDS", "7")
    scraper = HttpLetterboxdScraper()
    assert scraper.base_url == "https://example.test"
    assert scraper.timeout_seconds == 7.0


def test_http_scraper_ignores_invalid_timeout(monkeypatch):
    monkeypatch.setenv("TARGET_PLATFORM_TIMEOUT_SECONDS", "not-a-number")
    scraper = HttpLetterboxdScraper(base_url="https://example.test/")
    assert scraper.base_url == "https://example.test"
    assert scraper.timeout_seconds == 20.0


def test_http_scraper_login_uses_configured_base_url_and_timeout(monkeypatch):
    called = {}

    class FakeClient:
        def __init__(self, follow_redirects: bool, timeout: float):
            called["follow_redirects"] = follow_redirects
            called["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url: str):
            called["get_url"] = url
            return type("Response", (), {"text": '<input name="__csrf" value="token"/>'})()

        def post(self, url: str, data: dict, headers: dict):
            called["post_url"] = url
            called["post_data"] = data
            called["post_headers"] = headers
            return type(
                "Response",
                (),
                {"status_code": 200, "cookies": type("Cookies", (), {"get": lambda self, key: "cookie-123"})()},
            )()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    scraper = HttpLetterboxdScraper(base_url="https://example.test", timeout_seconds=9)
    cookie = scraper.login("alice", "secret")

    assert cookie == "cookie-123"
    assert called["follow_redirects"] is True
    assert called["timeout"] == 9
    assert called["get_url"] == "https://example.test/sign-in/"
    assert called["post_url"] == "https://example.test/user/login.do"
    assert called["post_headers"]["Origin"] == "https://example.test"
    assert called["post_headers"]["Referer"] == "https://example.test/sign-in/"
    assert called["post_data"]["username"] == "alice"
