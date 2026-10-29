"""SupabaseStore throttling and ingest lease are shared across instances via the DB."""

import time

import pytest

import src.api.store as store_module
from src.api.store import SupabaseStore


class _FakeResponse:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self


class _FakeRpcClient:
    """Simulates the throttle_claim/throttle_release SQL functions over shared state."""

    def __init__(self):
        self.state: dict[str, float | None] = {}
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, name, params):
        self.calls.append((name, dict(params)))
        now = time.monotonic()

        if name == "throttle_claim":
            column = params["p_column"]
            window = params["p_window_seconds"]
            previous = self.state.get(column)
            if previous is None or now - previous >= window:
                self.state[column] = now
                return _FakeResponse([{"ok": True}])
            return _FakeResponse([])

        if name == "throttle_release":
            self.state["ingest_running_at"] = None
            return _FakeResponse(None)

        raise AssertionError(f"unexpected rpc: {name}")


@pytest.fixture
def shared_client(monkeypatch):
    client = _FakeRpcClient()
    monkeypatch.setattr(store_module, "get_supabase_client", lambda: client)
    return client


def test_throttles_and_ingest_lease_shared_across_instances(shared_client):
    store_a = SupabaseStore()
    store_b = SupabaseStore()
    user_id = "u-cross-instance"

    # Scrape slot
    assert store_a.allow_scrape_request(user_id) == (True, 0.0)
    allowed_b, retry_b = store_b.allow_scrape_request(user_id)
    assert allowed_b is False
    assert retry_b == 1.0

    # Swipe slot
    assert store_a.should_rate_limit(user_id) == (False, 0)
    limited_b, wait_b = store_b.should_rate_limit(user_id)
    assert limited_b is True
    assert wait_b == 500

    # Ingest lease
    assert store_a.claim_ingest(user_id) is True
    assert store_b.claim_ingest(user_id) is False
    store_a.release_ingest(user_id)
    assert store_b.claim_ingest(user_id) is True

    # The DB functions were called with the expected names/params.
    claims = [(name, params) for name, params in shared_client.calls if name == "throttle_claim"]
    assert ("throttle_claim", {
        "p_user_id": user_id,
        "p_column": "last_scrape_at",
        "p_window_seconds": 1.0,
    }) in claims
    assert ("throttle_claim", {
        "p_user_id": user_id,
        "p_column": "last_action_at",
        "p_window_seconds": 0.5,
    }) in claims
    assert ("throttle_claim", {
        "p_user_id": user_id,
        "p_column": "ingest_running_at",
        "p_window_seconds": 120.0,
    }) in claims
    assert ("throttle_release", {"p_user_id": user_id}) in shared_client.calls
