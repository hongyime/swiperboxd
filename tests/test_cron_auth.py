"""Vercel Cron invokes GET with `Authorization: Bearer <CRON_SECRET>`."""

from fastapi.testclient import TestClient

import src.api.cron as cron
from src.api.app import app

client = TestClient(app)


class _FakeScraper:
    def discover_site_lists(self, page=1):
        return []


class _FakeStore:
    def cleanup_expired_progress(self):
        pass


def _configure(monkeypatch, secret="s3cret"):
    monkeypatch.setenv("CRON_SECRET", secret)
    monkeypatch.delenv("VERCEL_CRON_SECRET", raising=False)
    monkeypatch.setattr(cron, "HttpLetterboxdScraper", _FakeScraper)
    monkeypatch.setattr(cron, "_get_store", lambda: _FakeStore())


def test_refresh_lists_get_with_bearer_secret_succeeds(monkeypatch):
    _configure(monkeypatch)

    response = client.get(
        "/api/cron/refresh-lists",
        headers={"Authorization": "Bearer s3cret"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_refresh_lists_get_without_header_is_forbidden(monkeypatch):
    _configure(monkeypatch)

    response = client.get("/api/cron/refresh-lists")

    assert response.status_code == 403


def test_refresh_lists_get_with_wrong_bearer_is_forbidden(monkeypatch):
    _configure(monkeypatch)

    response = client.get(
        "/api/cron/refresh-lists",
        headers={"Authorization": "Bearer wrong"},
    )

    assert response.status_code == 403


def test_refresh_lists_get_with_legacy_header_is_forbidden(monkeypatch):
    _configure(monkeypatch)

    response = client.get(
        "/api/cron/refresh-lists",
        headers={"x-cron-secret": "s3cret"},
    )

    assert response.status_code == 403


class _BoundedSyncScraper:
    calls: list[tuple[str, int]] = []
    timeouts: list[float] = []

    def __init__(self, timeout_seconds=None):
        self.timeouts.append(timeout_seconds)

    def pull_watchlist_slugs(self, _cookie, username=None, max_pages=0):
        self.calls.append((f"watchlist:{username}", max_pages))
        return {"watchlisted"}

    def pull_diary_slugs(self, _cookie, username=None, max_pages=0):
        self.calls.append((f"diary:{username}", max_pages))
        return {"watched"}


class _BoundedSyncStore:
    def __init__(self):
        self.sessions = [
            {"user_id": "a", "username": "a", "encrypted_session": "a"},
            {"user_id": "b", "username": "b", "encrypted_session": "b"},
        ]

    def get_all_user_sessions(self):
        return self.sessions

    def batch_add_watchlist(self, _user_id, _slugs):
        return {"added": 1, "errors": []}

    def batch_add_diary(self, _user_id, _slugs):
        return {"added": 1, "errors": []}


def test_sync_users_cron_caps_work_to_one_user_and_page(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    _BoundedSyncScraper.calls = []
    _BoundedSyncScraper.timeouts = []
    monkeypatch.setattr(cron, "HttpLetterboxdScraper", _BoundedSyncScraper)
    monkeypatch.setattr(cron, "_get_store", _BoundedSyncStore)
    monkeypatch.setattr(cron, "_decrypt_user_session", lambda _value: "cookie")

    response = client.get(
        "/api/cron/sync-users?max_users=25&max_pages=10",
        headers={"Authorization": "Bearer s3cret"},
    )

    assert response.status_code == 200
    assert response.json()["users_processed"] == 1
    assert _BoundedSyncScraper.calls and all(max_pages == 1 for _, max_pages in _BoundedSyncScraper.calls)
    assert len(_BoundedSyncScraper.calls) == 2
    assert _BoundedSyncScraper.timeouts == [2.0]


class _BoundedBackfillScraper:
    metadata_calls: list[list[str]] = []
    list_calls: list[str] = []
    timeouts: list[float] = []

    def __init__(self, timeout_seconds=None):
        self.timeouts.append(timeout_seconds)

    def metadata_for_slugs(self, slugs):
        self.metadata_calls.append(list(slugs))
        return []

    def fetch_list_movie_slugs(self, list_id, list_url=None):
        self.list_calls.append(list_id)
        return ["film"]


class _BoundedBackfillStore:
    def get_placeholder_movie_slugs(self, limit):
        assert limit == 1
        return ["first", "second"][:limit]

    def get_underscraped_lists(self, limit):
        assert limit == 1
        return [{"list_id": "one"}]


def test_backfill_cron_skips_multi_page_list_scrapes(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    _BoundedBackfillScraper.metadata_calls = []
    _BoundedBackfillScraper.list_calls = []
    _BoundedBackfillScraper.timeouts = []
    monkeypatch.setattr(cron, "HttpLetterboxdScraper", _BoundedBackfillScraper)
    monkeypatch.setattr(cron, "_get_store", _BoundedBackfillStore)

    response = client.get(
        "/api/cron/backfill-scrapes?max_movies=60&max_lists=10",
        headers={"Authorization": "Bearer s3cret"},
    )

    assert response.status_code == 200
    assert _BoundedBackfillScraper.metadata_calls == [["first"]]
    assert _BoundedBackfillScraper.list_calls == []
    assert _BoundedBackfillScraper.timeouts == [2.0]
