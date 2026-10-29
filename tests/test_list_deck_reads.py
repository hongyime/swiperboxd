"""Real API/PostgREST client over synthetic HTTP; no providers or live records."""

import json
from collections import Counter

import httpx
import pytest
from fastapi.testclient import TestClient
from postgrest import SyncPostgrestClient

import src.api.app as api_module
from src.api.store import SupabaseStore


@pytest.fixture
def deck_client(monkeypatch):
    state = {
        "slugs": [f"film-{i}" for i in range(120)],
        "watchlist": [f"film-{i}" for i in range(40)],
        "diary": [f"film-{i}" for i in range(40, 70)],
        "exclusions": [f"film-{i}" for i in range(70, 100)],
        "missing": set(), "fail": None, "requests": [], "sample_requests": [],
    }

    def respond(request):
        table = request.url.path.rsplit("/", 1)[-1]
        state["requests"].append((request.method, table))
        assert request.method == "GET" or (
            request.method == "POST" and table in ("users", "deck_sample")
        ), "A deck read may only upsert the user id and call the sample RPC"
        if table == state["fail"]:
            return httpx.Response(503, json={"message": "Synthetic unavailable store",
                                            "code": "fixture", "details": None, "hint": None})
        if table == "list_summaries":
            rows = [{"list_id": "fixture-list", "title": "Fixture"}]
        elif table == "users":
            rows = [{"id": "fixture-user"}]
        elif table == "list_memberships":
            rows = [{"movie_slug": slug} for slug in state["slugs"]]
        elif table in ("watchlist", "diary", "exclusions"):
            rows = [{"movie_slug": slug} for slug in state[table]]
        elif table == "deck_sample":
            payload = json.loads(request.content)
            state["sample_requests"].append(payload)
            assert payload["p_list_id"] == "fixture-list"
            seen = set(payload["p_seen"])
            rows = [{"slug": slug, "title": slug, "genres": ["Drama"]}
                    for slug in state["slugs"]
                    if slug not in seen and slug not in state["missing"]]
            rows = rows[:payload["p_sample_size"]]
        else:
            raise AssertionError(f"Unexpected table: {table}")
        return httpx.Response(200, json=rows)

    def shuffle(user_id, movies, _user_id=None):
        state["shuffle_input"] = [movie["slug"] for movie in movies]
        return list(reversed(movies))

    def no_scrape(*args, **kwargs):
        raise AssertionError("Vercel deck reads must not start provider collection")

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr(api_module.scraper, "fetch_list_movie_slugs", no_scrape)
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        sdk = SyncPostgrestClient("https://database.test/rest/v1", http_client=http)
        monkeypatch.setattr("src.api.store.get_supabase_client", lambda: sdk)
        store = SupabaseStore()
        monkeypatch.setattr(store, "weighted_shuffle", shuffle)
        monkeypatch.setattr(api_module, "store", store)
        with TestClient(api_module.app) as client:
            yield client, state


def get_deck(client, **params):
    return client.get("/lists/fixture-list/deck", params={"user_id": "fixture", **params})


def test_seen_films_are_filtered_by_one_sample_query(deck_client):
    client, state = deck_client
    response = get_deck(client)
    assert response.status_code == 200
    eligible = state["slugs"][100:]
    assert state["shuffle_input"] == eligible
    assert [row["slug"] for row in response.json()["results"]] == eligible[::-1]
    assert len(state["sample_requests"]) == 1
    payload = state["sample_requests"][0]
    assert set(payload["p_seen"]) == set(state["watchlist"] + state["diary"] + state["exclusions"])
    assert payload["p_sample_size"] == 200
    counts = Counter(state["requests"])
    assert counts[("GET", "list_memberships")] == 1
    assert counts[("POST", "deck_sample")] == 1
    assert ("GET", "movies") not in state["requests"]


def test_include_seen_does_not_read_unused_history_and_samples_whole_list(deck_client):
    client, state = deck_client
    response = get_deck(client, include_seen="true")
    assert response.status_code == 200
    assert state["shuffle_input"] == state["slugs"]
    assert [row["slug"] for row in response.json()["results"]] == state["slugs"][::-1][:20]
    assert not any(table in ("watchlist", "diary", "exclusions", "users")
                   for _, table in state["requests"])
    assert state["sample_requests"][0]["p_seen"] == []
    assert Counter(state["requests"])[("GET", "list_memberships")] == 1


@pytest.mark.parametrize("empty_list", [False, True])
def test_empty_or_fully_seen_lists_return_no_movies(deck_client, empty_list):
    client, state = deck_client
    if empty_list:
        state["slugs"] = []
    else:
        state["watchlist"] = state["slugs"][:]
    response = get_deck(client)
    assert response.status_code == 200 and response.json()["results"] == []
    assert ("GET", "movies") not in state["requests"]
    assert len(state["sample_requests"]) == 1


@pytest.mark.parametrize("table", ["watchlist", "diary", "exclusions"])
def test_required_history_failure_is_retryable_without_sampling_an_unfiltered_deck(deck_client, table):
    client, state = deck_client
    state["fail"] = table
    response = get_deck(client)
    assert response.status_code == 503
    assert response.json()["code"] == "history_unavailable"
    assert response.headers["cache-control"] == "private, no-store"
    assert state["sample_requests"] == []
    state["fail"] = None
    retried = get_deck(client)
    assert retried.status_code == 200 and len(retried.json()["results"]) == 20


def test_missing_metadata_rows_are_absent_from_the_sample(deck_client):
    client, state = deck_client
    state["missing"] = {"film-105", "film-117"}
    response = get_deck(client)
    expected = [slug for slug in state["slugs"][100:] if slug not in state["missing"]]
    assert response.status_code == 200
    assert state["shuffle_input"] == expected
    assert [row["slug"] for row in response.json()["results"]] == expected[::-1]


def test_non_vercel_deck_reads_do_not_scrape_or_write(deck_client, monkeypatch):
    client, state = deck_client
    monkeypatch.delenv("VERCEL")
    monkeypatch.setattr(api_module.store, "replace_list_memberships",
                        lambda *a, **kw: pytest.fail("deck read must not write"))
    monkeypatch.setattr(api_module.store, "upsert_list_summary",
                        lambda *a, **kw: pytest.fail("deck read must not write"))
    response = get_deck(client)
    assert response.status_code == 200
    assert set(state["sample_requests"][0]["p_seen"]) == set(state["slugs"][:100])


def test_list_detail_and_deck_never_write_on_get(monkeypatch):
    store = api_module.store
    slugs = ["film-c", "film-d", "film-e", "film-a"]
    store.upsert_list_summary({"list_id": "readonly-list", "title": "Read only", "film_count": 4})
    store.replace_list_memberships("readonly-list", slugs)

    def boom(*args, **kwargs):
        raise AssertionError("GET must not write to the store")

    monkeypatch.setattr(store, "replace_list_memberships", boom)
    monkeypatch.setattr(store, "upsert_list_summary", boom)
    monkeypatch.setattr(store, "update_list_scrape_count", boom)
    with TestClient(api_module.app) as client:
        detail = client.get("/lists/readonly-list")
        deck = client.get("/lists/readonly-list/deck", params={"user_id": "testuser"})
        missing = client.get("/lists/missing-readonly-list")

    assert detail.status_code == 200
    assert detail.json()["movie_slugs"] == slugs
    assert deck.status_code == 200
    assert len(deck.json()["results"]) <= 20
    assert missing.status_code == 404
    assert missing.json()["code"] == "list_not_found"


def test_membership_failure_does_not_return_a_partial_deck(deck_client):
    client, state = deck_client
    state["fail"] = "list_memberships"
    assert get_deck(client).status_code == 500
    assert state["sample_requests"] == []
