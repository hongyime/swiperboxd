import os

os.environ["SCRAPER_BACKEND"] = "mock"
os.environ.setdefault("MASTER_ENCRYPTION_KEY", "test-master-key-32-bytes-padding!")
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = ""

from fastapi.testclient import TestClient

from src.api.app import app
from src.api.store import InMemoryStore


def _seed() -> InMemoryStore:
    store = InMemoryStore()
    for index, title in enumerate(
        ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"]
    ):
        store.upsert_list_summary(
            {
                "list_id": title.lower(),
                "slug": title.lower(),
                "title": title,
                "like_count": 100 - index,
            }
        )
    return store


def test_get_lists_pages_are_disjoint_and_ordered():
    store = _seed()

    first = store.get_lists(offset=0, limit=2, q=None)
    second = store.get_lists(offset=2, limit=2)

    assert [item["title"] for item in first] == ["Alpha", "Bravo"]
    assert [item["title"] for item in second] == ["Charlie", "Delta"]
    assert {item["list_id"] for item in first}.isdisjoint(
        {item["list_id"] for item in second}
    )


def test_get_lists_limit_is_honored():
    store = _seed()

    assert len(store.get_lists(offset=0, limit=3)) == 3


def test_get_lists_filters_before_paging():
    store = InMemoryStore()
    # Four matching lists occupy unfiltered positions 0..3; positions 2 and 3
    # are on the second page, so they are only reachable if filtering runs
    # before the offset slice.
    for index, title in enumerate(
        ["Star One", "Star Two", "Star Three", "Star Four", "Other"]
    ):
        store.upsert_list_summary(
            {
                "list_id": title.lower().replace(" ", "-"),
                "slug": title.lower().replace(" ", "-"),
                "title": title,
                "like_count": 100 - index,
            }
        )

    page_two = store.get_lists(offset=2, limit=2, q="star")

    assert [item["title"] for item in page_two] == ["Star Three", "Star Four"]


def test_catalog_endpoint_honors_page_size(monkeypatch):
    import src.api.app as app_module

    monkeypatch.setattr(app_module, "store", _seed())

    response = TestClient(app).get("/lists/catalog?page=1&page_size=2")

    assert response.status_code == 200
    payload = response.json()
    assert payload["page"] == 1
    assert len(payload["results"]) <= 2
    assert [item["title"] for item in payload["results"]] == ["Alpha", "Bravo"]
