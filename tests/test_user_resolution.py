"""User-id resolution: atomic upsert and one resolution per deck request."""

import pytest
from fastapi.testclient import TestClient

import src.api.app as api_module
from src.api.store import InMemoryStore, SupabaseStore


class _Response:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, client, table):
        self.client = client
        self.table = table
        self.ops = []

    def insert(self, payload):
        self.ops.append(("insert", payload))
        return self

    def select(self, *columns):
        self.ops.append(("select", columns))
        return self

    def eq(self, *args):
        return self

    def limit(self, *args):
        return self

    def execute(self):
        self.client.user_ops.extend((self.table, op) for op, *_ in self.ops)
        if self.table == "users":
            if self.ops[0][0] == "select":
                return _Response([{"id": "uuid-1"}] if self.client.user_exists else [])
            self.client.user_exists = True
            return _Response([{"id": "uuid-1"}])
        return _Response([])


class _FakeClient:
    def __init__(self):
        self.user_table_accesses = 0
        self.user_ops = []
        self.user_exists = True

    def table(self, name):
        if name == "users":
            self.user_table_accesses += 1
        return _FakeQuery(self, name)


@pytest.fixture
def fake_store(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr("src.api.store.get_supabase_client", lambda: client)
    return SupabaseStore(), client


def test_get_or_create_user_id_reads_existing_user_without_a_write(fake_store):
    store, client = fake_store

    result = store._get_or_create_user_id("u")

    assert result == "uuid-1"
    assert client.user_table_accesses == 1
    assert [op for _, op in client.user_ops] == ["select"]
    assert client.user_ops[0][0] == "users"


def test_get_or_create_user_id_inserts_only_after_a_miss(fake_store):
    store, client = fake_store
    client.user_exists = False

    assert store._get_or_create_user_id("u") == "uuid-1"
    assert [op for _, op in client.user_ops] == ["select", "insert", "select"]


def test_resolve_user_id_delegates_to_user_lookup(fake_store):
    store, client = fake_store

    assert store.resolve_user_id("u") == "uuid-1"
    assert client.user_table_accesses == 1


def test_membership_reads_reuse_resolved_id(fake_store):
    store, client = fake_store

    uid = store.resolve_user_id("u")
    store.get_watchlist("u", _user_id=uid)
    store.get_diary("u", _user_id=uid)
    store.get_exclusions("u", _user_id=uid)
    store.get_genre_weights("u", _user_id=uid)

    assert client.user_table_accesses == 1


def test_supabase_weighted_shuffle_forwards_resolved_id(fake_store):
    store, client = fake_store

    uid = store.resolve_user_id("u")
    store.weighted_shuffle("u", [], _user_id=uid)

    assert client.user_table_accesses == 1


def test_in_memory_resolve_user_id_is_identity():
    assert InMemoryStore().resolve_user_id("testuser") == "testuser"


def _seed_deck(store):
    store.upsert_list_summary({"list_id": "deck-list", "title": "Deck", "film_count": 1})
    store.replace_list_memberships("deck-list", ["film-a"])
    store.upsert_movie({"slug": "film-a", "title": "Film A"})


def test_list_deck_resolves_user_id_once(monkeypatch):
    store = InMemoryStore()
    _seed_deck(store)
    calls = []

    def counted(user_id):
        calls.append(user_id)
        return user_id

    monkeypatch.setattr(store, "resolve_user_id", counted)
    monkeypatch.setattr(api_module, "store", store)

    with TestClient(api_module.app) as client:
        response = client.get("/lists/deck-list/deck", params={"user_id": "testuser"})

    assert response.status_code == 200
    assert calls == ["testuser"]
