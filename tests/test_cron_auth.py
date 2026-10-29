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
