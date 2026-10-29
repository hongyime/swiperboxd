"""Non-replace list batches insert only new rows; never delete/reinsert the list."""

import json
import os

from fastapi.testclient import TestClient

import src.api.app as app_module
from src.api.app import app
from src.api.security import encrypt_session_cookie
from src.api.store import SupabaseStore


class _FakeResponse:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self


class _FakeUpsert:
    def __init__(self, client, rows, kwargs):
        self.client = client
        self.rows = rows
        self.kwargs = kwargs

    def execute(self):
        self.client.upserts.append((self.rows, self.kwargs))
        return _FakeResponse(self.rows)


class _FakeTable:
    def __init__(self, client):
        self.client = client

    def upsert(self, rows, **kwargs):
        return _FakeUpsert(self.client, rows, kwargs)

    def delete(self):
        self.client.delete_calls += 1
        raise AssertionError("add_list_memberships must never delete rows")


class _FakeClient:
    def __init__(self):
        self.upserts = []
        self.delete_calls = 0

    def table(self, name):
        assert name == "list_memberships"
        return _FakeTable(self)


def test_add_list_memberships_upserts_only_new_rows(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr("src.api.store.get_supabase_client", lambda: client)
    store = SupabaseStore()
    monkeypatch.setattr(store, "get_list_memberships", lambda list_id: ["a"])

    store.add_list_memberships("lst", ["b", "a", "c", "b"])

    assert len(client.upserts) == 1
    rows, kwargs = client.upserts[0]
    assert [(row["movie_slug"], row["position"]) for row in rows] == [("b", 1), ("c", 2)]
    assert all(row["list_id"] == "lst" for row in rows)
    assert kwargs["on_conflict"] == "list_id,movie_slug"
    assert kwargs["ignore_duplicates"] is True
    assert kwargs["returning"] == "minimal"
    assert client.delete_calls == 0


def test_add_list_memberships_skips_upsert_when_all_exist(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr("src.api.store.get_supabase_client", lambda: client)
    store = SupabaseStore()
    monkeypatch.setattr(store, "get_list_memberships", lambda list_id: ["a", "b"])

    store.add_list_memberships("lst", ["a", "b", "a"])

    assert client.upserts == []
    assert client.delete_calls == 0


client = TestClient(app)
_HEADERS = {
    "X-Session-Token": encrypt_session_cookie(
        json.dumps({"u": "testuser", "c": "fake-cookie"}),
        os.environ["MASTER_ENCRYPTION_KEY"],
    )
}


def test_two_non_replace_batches_persist_union_without_replace(monkeypatch):
    store = app_module.store
    list_id = "test-insert-only-batch"
    store.list_memberships.pop(list_id, None)

    replace_calls = []
    original_replace = store.replace_list_memberships

    def _spy_replace(lid, slugs):
        replace_calls.append(list(slugs))
        return original_replace(lid, slugs)

    monkeypatch.setattr(store, "replace_list_memberships", _spy_replace)

    first = client.post(
        "/api/extension/batch/list-movies",
        headers=_HEADERS,
        json={"list_id": list_id, "slugs": ["one", "two"], "page": 1, "total_pages": 2},
    )
    second = client.post(
        "/api/extension/batch/list-movies",
        headers=_HEADERS,
        json={"list_id": list_id, "slugs": ["three", "four"], "page": 2, "total_pages": 2},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert set(store.get_list_memberships(list_id)) == {"one", "two", "three", "four"}
    assert second.json()["total_scraped"] == 4
    assert replace_calls == []
