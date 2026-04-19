import json
import os
import time

import pytest
from fastapi.testclient import TestClient

# Force mock scraper and in-memory store for all tests.
# Set Supabase vars to empty strings BEFORE importing app so that load_dotenv()
# inside app.py (which skips already-set vars) does not inject real credentials.
os.environ["SCRAPER_BACKEND"] = "mock"
os.environ.setdefault("MASTER_ENCRYPTION_KEY", "test-master-key-32-bytes-padding!")
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_ANON_KEY"] = ""

from src.api.app import app
from src.api.resilience import exponential_backoff_seconds, should_trigger_proxy_fallback
from src.api.security import decrypt_session_cookie, encrypt_session_cookie


client = TestClient(app)

# Old-format token (raw cookie string) — exercises the backward-compat path in
# verify_session where json.loads fails and verified_user returns "".
_TEST_SESSION = encrypt_session_cookie("session::testuser", os.environ["MASTER_ENCRYPTION_KEY"])
_AUTH_HEADERS = {"X-Session-Token": _TEST_SESSION}

# New-format token (JSON payload) — exercises the identity binding guard.
# verified_user will be "testuser" when this token is decrypted.
_NEW_FORMAT_SESSION = encrypt_session_cookie(
    json.dumps({"u": "testuser", "c": "fake-cookie"}),
    os.environ["MASTER_ENCRYPTION_KEY"],
)
_NEW_FORMAT_HEADERS = {"X-Session-Token": _NEW_FORMAT_SESSION}


def test_encrypt_roundtrip():
    token = encrypt_session_cookie("session::abc", "test-master-key")
    plain = decrypt_session_cookie(token, "test-master-key")
    assert plain == "session::abc"


def test_auth_session_endpoint(monkeypatch):
    import src.api.app as app_module
    monkeypatch.setattr(app_module, "_validate_letterboxd_session", lambda username, cookie: None)
    response = client.post("/auth/session", json={"username": "u", "session_cookie": "fake-session-value"})
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


def test_list_catalog_returns_mixed_lists():
    # Seed the store via the extension batch endpoint (mirrors real extension sync)
    seed = client.post(
        "/api/extension/batch/list-summaries",
        headers=_AUTH_HEADERS,
        json={
            "lists": [
                {
                    "list_id": "letterboxd-official-top250",
                    "slug": "official-top250",
                    "url": "https://letterboxd.com/letterboxd/list/official-top250/",
                    "title": "Official Top 250",
                    "owner_name": "Letterboxd",
                    "owner_slug": "letterboxd",
                    "description": "Top 250 narrative films",
                    "film_count": 250,
                    "like_count": 50000,
                    "comment_count": 100,
                    "is_official": True,
                    "tags": ["official"],
                },
                {
                    "list_id": "someuser-hidden-gems",
                    "slug": "hidden-gems",
                    "url": "https://letterboxd.com/someuser/list/hidden-gems/",
                    "title": "Hidden Gems",
                    "owner_name": "someuser",
                    "owner_slug": "someuser",
                    "description": "Underrated films",
                    "film_count": 42,
                    "like_count": 300,
                    "comment_count": 5,
                    "is_official": False,
                    "tags": [],
                },
            ],
            "source": "popular",
        },
    )
    assert seed.status_code == 200

    response = client.get("/lists/catalog")
    assert response.status_code == 200
    payload = response.json()
    assert payload["results"]
    assert any(item["is_official"] for item in payload["results"])
    assert any(not item["is_official"] for item in payload["results"])


def test_list_detail_returns_preview():
    response = client.get("/lists/official-best-picture")
    assert response.status_code == 200
    payload = response.json()
    assert payload["list"]["list_id"] == "official-best-picture"
    assert isinstance(payload["movie_slugs"], list)
    assert isinstance(payload["preview"], list)


def test_list_deck_returns_movies():
    response = client.get("/lists/official-best-picture/deck", params={"user_id": "list-user"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["list"]["list_id"] == "official-best-picture"
    assert isinstance(payload["results"], list)
    assert payload["results"]


def test_discovery_deck_tolerates_invalid_movie_records(monkeypatch):
    import src.api.app as app_module

    app_module.store.upsert_movie({
        "slug": "broken-film",
        "title": "Broken Film",
        "poster_url": "",
        "rating": None,
        "popularity": None,
        "genres": None,
    })
    app_module.store.upsert_movie({
        "slug": "good-film",
        "title": "Good Film",
        "poster_url": "",
        "rating": 4.7,
        "popularity": 12,
        "genres": ["Drama"],
    })

    deck = client.get("/discovery/deck", params={"user_id": "deck-safe", "profile": "gold-standard"})
    assert deck.status_code == 200
    body = deck.json()
    assert isinstance(body["results"], list)
    assert any(movie["slug"] == "good-film" for movie in body["results"])
    assert body["meta"]["matched_count"] >= 1


def test_ingest_progress_returns_error_details(monkeypatch):
    import src.api.app as app_module

    def boom(*args, **kwargs):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(app_module.scraper, "pull_source_slugs", boom)

    response = client.post(
        "/ingest/start",
        headers=_AUTH_HEADERS,
        json={"user_id": "u-ingest-error", "source": "trending", "depth_pages": 1},
    )
    assert response.status_code == 200

    time.sleep(0.15)
    progress = client.get("/ingest/progress", params={"user_id": "u-ingest-error"})
    assert progress.status_code == 200
    payload = progress.json()
    assert payload["progress"] == -1
    assert payload["error"]["code"] == "ingest_worker_failed"
    assert "upstream exploded" in payload["error"]["reason"]


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


def test_identity_binding_rejects_mismatched_user_id():
    """New-format token: user_id in body must match the username in the token."""
    r = client.post(
        "/ingest/start",
        headers=_NEW_FORMAT_HEADERS,
        json={"user_id": "other-user", "source": "trending", "depth_pages": 1},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "user_id_mismatch"

    r = client.post(
        "/actions/swipe",
        headers=_NEW_FORMAT_HEADERS,
        json={"user_id": "other-user", "movie_slug": "film-a", "action": "dismiss"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "user_id_mismatch"


def test_identity_binding_passes_matching_user_id():
    """New-format token: matching user_id must be accepted."""
    r = client.post(
        "/ingest/start",
        headers=_NEW_FORMAT_HEADERS,
        json={"user_id": "testuser", "source": "trending", "depth_pages": 1},
    )
    assert r.status_code == 200
    assert r.json()["status"] in {"queued", "already_running"}


def test_identity_binding_bypass_for_old_format_token():
    """Old-format token returns verified_user="" — any user_id must be accepted (backward compat)."""
    r = client.post(
        "/ingest/start",
        headers=_AUTH_HEADERS,
        json={"user_id": "any-random-user", "source": "trending", "depth_pages": 1},
    )
    assert r.status_code == 200


def test_resilience_helpers():
    assert should_trigger_proxy_fallback(429)
    assert should_trigger_proxy_fallback(403)
    assert not should_trigger_proxy_fallback(500)
    assert exponential_backoff_seconds(2, jitter=False) == 2.0
