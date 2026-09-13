"""Synthetic API and real SDK transport checks; no Supabase connection."""
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient
from postgrest import SyncPostgrestClient
from postgrest.exceptions import APIError

import src.api.app as api_module
from src.api.security import encrypt_session_cookie
from src.api.store import InMemoryStore, SupabaseStore


def headers(username="fixture"):
    token = encrypt_session_cookie(json.dumps({"u": username, "c": "synthetic"}),
                                   os.environ["MASTER_ENCRYPTION_KEY"])
    return {"X-Session-Token": token}


@pytest.fixture
def status_client(monkeypatch):
    store = InMemoryStore(watchlist={"fixture": {"a", "b"}}, diary={"fixture": {"c"}})
    monkeypatch.setattr(api_module, "store", store)
    with TestClient(api_module.app) as client:
        yield client, store


@pytest.mark.parametrize("request_headers,expected", [({}, 422), (headers("other"), 403),
                                                       ({"X-Session-Token": "invalid"}, 401)])
def test_status_requires_matching_identity(status_client, request_headers, expected):
    client, _ = status_client
    assert client.get("/users/fixture/sync-status", headers=request_headers).status_code == expected


def test_status_counts_and_private_response(status_client):
    client, _ = status_client
    response = client.get("/users/fixture/sync-status", headers=headers())
    assert response.status_code == 200
    assert response.json() == {"has_synced": True, "watchlist_count": 2, "diary_count": 1}
    assert response.headers["cache-control"] == "private, no-store"


def test_legacy_token_cannot_read_private_counts(status_client):
    client, _ = status_client
    token = encrypt_session_cookie("legacy-synthetic-cookie", os.environ["MASTER_ENCRYPTION_KEY"])
    response = client.get("/users/fixture/sync-status", headers={"X-Session-Token": token})
    assert response.status_code == 401


def test_missing_memory_user_does_not_create_state():
    store = InMemoryStore()
    assert store.get_sync_status("absent") == {"has_synced": False, "watchlist_count": 0, "diary_count": 0}
    assert store.watchlist == store.diary == {}


def test_status_failure_is_not_empty_data(status_client, monkeypatch):
    client, store = status_client
    def fail(*_):
        raise RuntimeError("private database details")
    # Replace either contract so the baseline also exercises its error handling.
    monkeypatch.setattr(store, "get_watchlist", fail)
    monkeypatch.setattr(store, "get_sync_status", fail, raising=False)
    response = client.get("/users/fixture/sync-status", headers=headers())
    assert response.status_code == 503
    assert "private database details" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("exists,watchlist,diary", [(False, 0, 0), (True, 2501, 1803), (True, 0, 0)])
def test_sdk_status_uses_lookup_and_head_counts_only(monkeypatch, exists, watchlist, diary):
    requests = []
    def transport(request):
        requests.append(request)
        table = request.url.path.rsplit("/", 1)[-1]
        if table == "users":
            assert request.method == "GET"
            assert request.url.params["letterboxd_username"] == "eq.fixture"
            assert request.url.params["select"] == "id"
            assert request.url.params["limit"] == "1"
            return httpx.Response(200, json=[{"id": "fixture-id"}] if exists else [])
        assert request.method == "HEAD"
        assert request.headers["prefer"] == "count=exact"
        assert request.url.params["user_id"] == "eq.fixture-id"
        count = {"watchlist": watchlist, "diary": diary}[table]
        return httpx.Response(200, headers={"content-range": f"*/{count}"})
    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        client = SyncPostgrestClient("https://database.test/rest/v1", http_client=http)
        monkeypatch.setattr("src.api.store.get_supabase_client", lambda: client)
        result = SupabaseStore().get_sync_status("fixture")
    assert result == {"has_synced": bool(watchlist or diary), "watchlist_count": watchlist,
                      "diary_count": diary}
    assert len(requests) == (3 if exists else 1)


@pytest.mark.parametrize("status,range_header", [(503, None), (200, None), (200, "*/*")])
def test_sdk_never_masks_failed_or_missing_counts(monkeypatch, status, range_header):
    def transport(request):
        if request.url.path.endswith("/users"):
            return httpx.Response(200, json=[{"id": "fixture-id"}])
        return httpx.Response(status, headers={"content-range": range_header} if range_header else {},
                              content=b"")
    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        client = SyncPostgrestClient("https://database.test/rest/v1", http_client=http)
        monkeypatch.setattr("src.api.store.get_supabase_client", lambda: client)
        with pytest.raises((ValueError, APIError)):
            SupabaseStore().get_sync_status("fixture")
