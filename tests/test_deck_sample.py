"""Deck sampling: one bounded store query instead of per-slug round-trips."""

from fastapi.testclient import TestClient

import src.api.app as api_module
from src.api.store import InMemoryStore


def _seed(store: InMemoryStore, list_id: str, slugs: list[str]) -> None:
    store.upsert_list_summary({"list_id": list_id, "title": "Fixture"})
    store.replace_list_memberships(list_id, slugs)
    for slug in slugs:
        store.upsert_movie({"slug": slug, "title": slug, "genres": ["Drama"]})


def test_in_memory_deck_sample_excludes_seen_and_is_bounded():
    store = InMemoryStore()
    slugs = [f"film-{i}" for i in range(6)]
    _seed(store, "list-x", slugs)

    sample = store.get_deck_movies("list-x", {"film-0", "film-1"}, sample_size=2)

    assert len(sample) == 2
    assert not {movie["slug"] for movie in sample} & {"film-0", "film-1"}


def test_deck_endpoint_returns_twenty_from_a_single_sample_call(monkeypatch):
    store = InMemoryStore()
    _seed(store, "list-y", ["film-a"])
    calls = []

    def spy(list_id, seen, sample_size=200):
        calls.append((list_id, set(seen), sample_size))
        return [{"slug": f"sample-{i}", "title": f"Sample {i}"} for i in range(200)]

    monkeypatch.setattr(store, "get_deck_movies", spy)
    monkeypatch.setattr(api_module, "store", store)

    response = TestClient(api_module.app).get(
        "/lists/list-y/deck", params={"user_id": "testuser"}
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["results"]) == 20
    assert calls == [("list-y", set(), 200)]


def test_deck_endpoint_tolerates_an_empty_sample(monkeypatch):
    store = InMemoryStore()
    _seed(store, "list-z", ["film-a"])
    monkeypatch.setattr(store, "get_deck_movies", lambda *args, **kwargs: [])
    monkeypatch.setattr(api_module, "store", store)

    response = TestClient(api_module.app).get(
        "/lists/list-z/deck", params={"user_id": "testuser"}
    )

    assert response.status_code == 200
    assert response.json()["results"] == []
