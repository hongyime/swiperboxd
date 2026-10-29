"""replace_list_memberships must go through one atomic RPC, not delete+insert."""

import pytest

from src.api.store import SupabaseStore


class _FakeResponse:
    def execute(self):
        return self


class _FakeRpc:
    def __init__(self, calls, name, params):
        self.calls = calls
        self.calls.append((name, params))

    def execute(self):
        return _FakeResponse()


class _FakeClient:
    def __init__(self):
        self.rpc_calls = []

    def rpc(self, name, params):
        return _FakeRpc(self.rpc_calls, name, params)

    def table(self, name):
        raise AssertionError(f"table({name!r}) must not be used by replace_list_memberships")


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


def test_replace_list_memberships_empty_input_still_one_rpc(fake_client):
    store = SupabaseStore()

    store.replace_list_memberships("lst", [])

    assert [name for name, _ in fake_client.rpc_calls] == ["replace_list_memberships_atomic"]
    assert fake_client.rpc_calls[0][1]["p_slugs"] == []
