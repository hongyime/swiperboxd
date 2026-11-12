"""SupabaseStore throttling and ingest lease are shared across instances via the DB."""

import time
from pathlib import Path

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


class _ProgressQuery:
    def __init__(self, client):
        self.client = client
        self.payload = None
        self.user_id = None

    def upsert(self, payload, **_kwargs):
        self.payload = payload
        return self

    def select(self, *_columns):
        return self

    def eq(self, _column, value):
        self.user_id = value
        return self

    def limit(self, _value):
        return self

    def execute(self):
        if self.payload is not None:
            user_id = self.payload["user_id"]
            self.client.rows.setdefault(user_id, {}).update(self.payload)
            return _FakeResponse([dict(self.client.rows[user_id])])
        row = self.client.rows.get(self.user_id)
        return _FakeResponse([dict(row)] if row else [])


class _ProgressClient:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    def table(self, name):
        assert name == "user_throttle"
        return _ProgressQuery(self)


def test_ingest_progress_and_error_are_shared_across_instances(monkeypatch):
    client = _ProgressClient()
    monkeypatch.setattr(store_module, "get_supabase_client", lambda: client)
    store_a = SupabaseStore()
    store_b = SupabaseStore()

    store_a.set_ingest_progress("u-cross-instance", 55)
    store_a.set_ingest_error("u-cross-instance", {"code": "upstream_failed"})

    assert store_b.get_ingest_progress("u-cross-instance") == 55
    assert store_b.get_ingest_error("u-cross-instance") == {"code": "upstream_failed"}


def test_progress_migration_adds_persistent_state_columns():
    migration = Path(__file__).parent.parent / "db" / "migrations" / "006_ingest_progress.sql"
    sql = migration.read_text().lower()

    assert "ingest_progress" in sql
    assert "ingest_error" in sql
