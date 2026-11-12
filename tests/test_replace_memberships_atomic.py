"""replace_list_memberships must go through one atomic RPC, not delete+insert."""

import pytest

from src.api.store import SupabaseStore


class _FakeResponse:
    def __init__(self, data=None):
        self.data = data or []

    def execute(self):
        return self


class _FakeRpc:
    def __init__(self, calls, name, params):
        self.calls = calls
        self.calls.append((name, params))

    def execute(self):
        return _FakeResponse()


class _FakeMovieQuery:
    def __init__(self, client):
        self.client = client
        self.rows = []

    def upsert(self, rows, **kwargs):
        self.rows = rows
        self.client.events.append(("movies", rows))
        return self

    def select(self, *_columns):
        return self

    def execute(self):
        return _FakeResponse([{"slug": row["slug"]} for row in self.rows])


class _FakeClient:
    def __init__(self):
        self.rpc_calls = []
        self.events = []

    def rpc(self, name, params):
        self.events.append(("rpc", name))
        return _FakeRpc(self.rpc_calls, name, params)

    def table(self, name):
        assert name == "movies"
        return _FakeMovieQuery(self)


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr("src.api.store.get_supabase_client", lambda: client)
    return client


def test_replace_list_memberships_uses_single_atomic_rpc(fake_client):
    store = SupabaseStore()

    store.replace_list_memberships("lst", ["a", "b", "a", "c"])

    assert len(fake_client.rpc_calls) == 1
    name, params = fake_client.rpc_calls[0]
    assert name == "replace_list_memberships_atomic"
    assert params["p_list_id"] == "lst"
    assert params["p_slugs"] == ["a", "b", "c"]
    assert fake_client.events == [
        ("movies", [{"slug": "a", "title": "A"}, {"slug": "b", "title": "B"}, {"slug": "c", "title": "C"}]),
        ("rpc", "replace_list_memberships_atomic"),
    ]


def test_replace_list_memberships_empty_input_still_one_rpc(fake_client):
    store = SupabaseStore()

    store.replace_list_memberships("lst", [])

    assert [name for name, _ in fake_client.rpc_calls] == ["replace_list_memberships_atomic"]
    assert fake_client.rpc_calls[0][1]["p_slugs"] == []
