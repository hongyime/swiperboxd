import os
import time

import pytest
from fastapi.testclient import TestClient

# Force mock scraper for all tests
os.environ["SCRAPER_BACKEND"] = "mock"
os.environ.setdefault("MASTER_ENCRYPTION_KEY", "test-master-key-32-bytes-padding!")

from src.api.app import app
from src.api.resilience import exponential_backoff_seconds, should_trigger_proxy_fallback
from src.api.security import decrypt_session_cookie, encrypt_session_cookie


client = TestClient(app)

# A valid encrypted session token for use in tests that hit guarded endpoints.
_TEST_SESSION = encrypt_session_cookie("session::testuser", os.environ["MASTER_ENCRYPTION_KEY"])
_AUTH_HEADERS = {"X-Session-Token": _TEST_SESSION}


def test_encrypt_roundtrip():
    token = encrypt_session_cookie("session::abc", "test-master-key")
    plain = decrypt_session_cookie(token, "test-master-key")
    assert plain == "session::abc"


def test_auth_session_endpoint():
    response = client.post("/auth/session", json={"username": "u", "password": "p"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["encrypted_session_cookie"]


def test_profiles_and_discovery_deck():
    profiles = client.get("/discovery/profiles")
    assert profiles.status_code == 200
    assert "gold-standard" in profiles.json()["profiles"]

    client.post("/ingest/start", headers=_AUTH_HEADERS, json={"user_id": "u1", "source": "trending", "depth_pages": 2})
    time.sleep(0.35)
    deck = client.get("/discovery/deck", params={"user_id": "u1", "profile": "gold-standard"})
    assert deck.status_code == 200
    assert isinstance(deck.json()["results"], list)


def test_discovery_details():
    client.post("/ingest/start", headers=_AUTH_HEADERS, json={"user_id": "u2", "source": "trending", "depth_pages": 2})
    time.sleep(0.35)
    details = client.get("/discovery/details", params={"slug": "film-c"})
    assert details.status_code == 200
    assert "genres" in details.json()


def test_ingest_rate_limit():
    first = client.post("/ingest/start", headers=_AUTH_HEADERS, json={"user_id": "u-rate", "source": "trending", "depth_pages": 1})
    assert first.status_code == 200
    second = client.post("/ingest/start", headers=_AUTH_HEADERS, json={"user_id": "u-rate", "source": "trending", "depth_pages": 1})
    assert second.status_code == 429


def test_swipe_has_sync_lock():
    client.post("/ingest/start", headers=_AUTH_HEADERS, json={"user_id": "u-lock", "source": "trending", "depth_pages": 2})
    time.sleep(0.35)

    payload = {"user_id": "u-lock", "movie_slug": "film-a", "action": "dismiss"}
    first = client.post("/actions/swipe", headers=_AUTH_HEADERS, json=payload)
    assert first.status_code == 200

    second = client.post("/actions/swipe", headers=_AUTH_HEADERS, json=payload)
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "sync_lock"


def test_guarded_endpoints_reject_missing_token():
    """Mutating endpoints must return 422 when X-Session-Token header is absent."""
    r = client.post("/ingest/start", json={"user_id": "x", "source": "trending", "depth_pages": 1})
    assert r.status_code == 422

    r = client.post("/actions/swipe", json={"user_id": "x", "movie_slug": "film-a", "action": "dismiss"})
    assert r.status_code == 422


def test_guarded_endpoints_reject_invalid_token():
    """Mutating endpoints must return 401 when X-Session-Token is not decryptable."""
    bad_headers = {"X-Session-Token": "not-a-valid-token"}
    r = client.post("/ingest/start", headers=bad_headers, json={"user_id": "x", "source": "trending", "depth_pages": 1})
    assert r.status_code == 401


def test_resilience_helpers():
    assert should_trigger_proxy_fallback(429)
    assert should_trigger_proxy_fallback(403)
    assert not should_trigger_proxy_fallback(500)
    assert exponential_backoff_seconds(2, jitter=False) == 2.0
