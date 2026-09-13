"""Exercise real PostgREST requests against synthetic SQLite conflict semantics.

No network or Supabase credentials are used. This covers client headers/payloads
and insert/update behavior; it is not a deployed PostgreSQL integration test.
"""

import json
import os
import sqlite3

import httpx
import pytest
from postgrest import SyncPostgrestClient

from src.api.store import SupabaseStore


@pytest.mark.parametrize("table", ["watchlist", "diary"])
@pytest.mark.parametrize("failed_method", ["GET", "POST"])
def test_user_gateway_timeout_keeps_batch_retryable_and_existing_rows_intact(monkeypatch, table, failed_method):
    """Use the real PostgREST error parser and HTTP endpoint with synthetic rows."""
    from fastapi.testclient import TestClient
    import src.api.app as app_module
    from src.api.security import encrypt_session_cookie

    rest = SyntheticRest()
    rest.db.execute("INSERT INTO movies(slug, title, rating) VALUES ('existing', 'Original title', 4.5)")
    rest.db.execute(f"INSERT INTO {table}(user_id, movie_slug) VALUES ('synthetic-user', 'existing')")
    before = (rest.rows("movies"), rest.rows(table))
    unavailable = True
    requests = []

    def respond(request):
        target = request.url.path.rsplit("/", 1)[-1]
        requests.append((target, request.method))
        if target == "users":
            if unavailable and request.method == failed_method:
                return httpx.Response(504, json={"message": "Gateway Timeout"})
            if request.method == "GET" and failed_method == "POST":
                return httpx.Response(200, json=[])
            return httpx.Response(200 if request.method == "GET" else 201, json=[{"id": "synthetic-user"}])
        return rest(request)

    token = encrypt_session_cookie(json.dumps({"u": "testuser", "c": "fixture-cookie"}), os.environ["MASTER_ENCRYPTION_KEY"])
    payload = {"user_id": "testuser", "slugs": ["existing", "new-film"], "page": 2, "total_pages": 3}
    try:
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            sdk = SyncPostgrestClient("https://database.test/rest/v1", http_client=http)
            monkeypatch.setattr("src.api.store.get_supabase_client", lambda: sdk)
            monkeypatch.setattr(app_module, "store", SupabaseStore())
            with TestClient(app_module.app, raise_server_exceptions=False) as api:
                endpoint = f"/api/extension/batch/{table}"
                failed = api.post(endpoint, headers={"X-Session-Token": token}, json=payload)
                assert failed.status_code == 503
                body = failed.json()
                assert (body["status"], body["page"], body["total_pages"]) == ("error", 2, 3)
                assert body["result"]["added"] == 0 and body["result"]["errors"]
                assert requests == ([("users", "GET")] if failed_method == "GET" else [("users", "GET"), ("users", "POST")])
                assert (rest.rows("movies"), rest.rows(table)) == before
                unavailable = False
                retried = api.post(endpoint, headers={"X-Session-Token": token}, json=payload)
                assert retried.status_code == 200 and retried.json()["result"]["added"] == 2
                after = (rest.rows("movies"), rest.rows(table))
                repeated = api.post(endpoint, headers={"X-Session-Token": token}, json=payload)
                assert repeated.status_code == 200
                assert (rest.rows("movies"), rest.rows(table)) == after
                assert before[0][0] in after[0] and before[1][0] in after[1]
                assert rest.rows("updates") == []
    finally:
        rest.db.close()


class SyntheticRest:
    def __init__(self):
        # TestClient serves requests on its event-loop thread; fixtures issue
        # sequential requests and inspect rows on the test thread afterwards.
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.requests = []
        self.fail_table = None
        self.before_movies = None
        self.db.executescript("""
            PRAGMA foreign_keys = ON;
            CREATE TABLE movies (slug TEXT PRIMARY KEY, title TEXT NOT NULL,
                rating REAL DEFAULT 0, created_at TEXT DEFAULT 'original-time');
            CREATE TABLE watchlist (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL,
                movie_slug TEXT NOT NULL REFERENCES movies(slug), created_at TEXT DEFAULT 'original-time',
                UNIQUE(user_id, movie_slug));
            CREATE TABLE diary (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL,
                movie_slug TEXT NOT NULL REFERENCES movies(slug), created_at TEXT DEFAULT 'original-time',
                UNIQUE(user_id, movie_slug));
            CREATE TABLE updates (table_name TEXT);
            CREATE TRIGGER movie_updates AFTER UPDATE ON movies BEGIN
                INSERT INTO updates VALUES ('movies'); END;
            CREATE TRIGGER watchlist_updates AFTER UPDATE ON watchlist BEGIN
                INSERT INTO updates VALUES ('watchlist'); END;
            CREATE TRIGGER diary_updates AFTER UPDATE ON diary BEGIN
                INSERT INTO updates VALUES ('diary'); END;
        """)

    def rows(self, table):
        assert table in {"movies", "watchlist", "diary", "updates"}
        return [dict(row) for row in self.db.execute(f'SELECT * FROM "{table}"')]

    def __call__(self, request):
        self.requests.append(request)
        table = request.url.path.rsplit("/", 1)[-1]
        assert table in {"movies", "watchlist", "diary"}
        if table == self.fail_table:
            return httpx.Response(503, json={"code": "test_unavailable", "message": "Unavailable",
                                            "details": None, "hint": None})
        if table == "movies" and request.method == "POST" and self.before_movies:
            callback, self.before_movies = self.before_movies, None
            callback(self.db)
        if request.method == "GET":
            # Baseline implementation performs this lookup before its upsert.
            requested = request.url.params["slug"][4:-1].replace('"', '').split(',')
            data = [{"slug": row["slug"]} for row in self.rows("movies") if row["slug"] in requested]
            return httpx.Response(200, json=data)
        assert request.method == "POST"
        records = json.loads(request.content)
        records = records if isinstance(records, list) else [records]
        prefer = request.headers.get("prefer", "")
        conflict = request.url.params["on_conflict"]
        assert conflict in {"slug", "user_id,movie_slug"}
        returned = []
        for record in records:
            columns = list(record)
            assert set(columns) <= {"slug", "title", "user_id", "movie_slug"}
            action = "NOTHING" if "resolution=ignore-duplicates" in prefer else (
                "UPDATE SET " + ", ".join(f'"{key}" = excluded."{key}"' for key in columns)
            )
            sql = (f'INSERT INTO "{table}" ({", ".join(columns)}) '
                   f'VALUES ({", ".join("?" for _ in columns)}) '
                   f'ON CONFLICT ({conflict}) DO {action} RETURNING *')
            returned.extend(dict(row) for row in self.db.execute(sql, list(record.values())))
        if "return=minimal" in prefer:
            return httpx.Response(201, content=b"")
        selected = request.url.params.get("select", "*")
        if selected != "*":
            returned = [{key: row[key] for key in selected.split(",")} for row in returned]
        return httpx.Response(201, json=returned)


@pytest.fixture
def persistence(monkeypatch):
    rest = SyntheticRest()
    with httpx.Client(transport=httpx.MockTransport(rest)) as http:
        client = SyncPostgrestClient("https://database.test/rest/v1", http_client=http)
        monkeypatch.setattr("src.api.store.get_supabase_client", lambda: client)
        store = SupabaseStore()
        monkeypatch.setattr(store, "_get_or_create_user_id", lambda username: "synthetic-user")
        yield store, rest
    rest.db.close()


@pytest.mark.parametrize("table", ["watchlist", "diary"])
def test_repeat_batch_preserves_rows_and_does_not_update(persistence, table):
    store, rest = persistence
    batch = getattr(store, f"batch_add_{table}")
    rest.db.execute("INSERT INTO movies(slug, title, rating) VALUES (?, ?, ?)",
                    ("existing", "Actual original title", 4.5))
    first = batch("test", ["existing", "new-film"])
    snapshot = (rest.rows("movies"), rest.rows(table))
    second = batch("test", ["existing", "new-film"])
    assert first == {"added": 2, "errors": [], "missing_metadata": ["new-film"], "total": 2}
    assert second == {"added": 2, "errors": [], "missing_metadata": [], "total": 2}
    assert (rest.rows("movies"), rest.rows(table)) == snapshot
    assert rest.rows("updates") == []
    membership_requests = [r for r in rest.requests if r.url.path.endswith(f"/{table}")]
    assert all("return=minimal" in r.headers["prefer"] for r in membership_requests)


@pytest.mark.parametrize("table", ["watchlist", "diary"])
def test_duplicate_input_is_accepted_without_duplicate_write_payloads(persistence, table):
    store, rest = persistence
    result = getattr(store, f"batch_add_{table}")("test", [" film ", "film", "", "   "])
    assert result == {"added": 2, "errors": [], "missing_metadata": ["film"], "total": 4}
    assert len(rest.rows(table)) == 1
    assert all(len(json.loads(r.content)) == 1 for r in rest.requests if r.method == "POST")


def test_movie_created_concurrently_is_not_replaced_or_reported_missing(persistence):
    store, rest = persistence
    rest.before_movies = lambda db: db.execute(
        "INSERT INTO movies(slug, title, rating) VALUES ('race-film', 'Original title', 4.75)"
    )
    assert store._bulk_ensure_movies(["race-film"]) == []
    assert rest.rows("movies")[0]["title"] == "Original title"
    assert rest.rows("movies")[0]["rating"] == 4.75
    assert rest.rows("updates") == []
    assert all(r.method == "POST" for r in rest.requests)


def test_large_batch_uses_bounded_posts_and_returns_only_inserted_slugs(persistence):
    store, rest = persistence
    slugs = [f"film-{i}" for i in range(500)]
    assert store._bulk_ensure_movies(slugs) == slugs
    assert store._bulk_ensure_movies(slugs) == []
    assert all(r.method == "POST" for r in rest.requests)
    assert all(len(json.loads(r.content)) <= 200 for r in rest.requests)
    assert all(r.url.params["select"] == "slug" for r in rest.requests)
    assert rest.rows("updates") == []


@pytest.mark.parametrize("table", ["watchlist", "diary"])
def test_movie_insert_failure_reports_error_without_writing_memberships(persistence, table):
    store, rest = persistence
    rest.fail_table = "movies"
    result = getattr(store, f"batch_add_{table}")("test", ["film"])
    assert result["added"] == 0
    assert result["errors"]
    assert rest.rows(table) == []
    assert not any(r.url.path.endswith(f"/{table}") for r in rest.requests)
    rest.fail_table = None
    assert getattr(store, f"batch_add_{table}")("test", ["film"])["added"] == 1


@pytest.mark.parametrize("table", ["watchlist", "diary"])
def test_membership_failure_is_retryable_without_updates(persistence, table):
    store, rest = persistence
    rest.fail_table = table
    result = getattr(store, f"batch_add_{table}")("test", ["film"])
    assert result["added"] == 0 and result["errors"]
    rest.fail_table = None
    assert getattr(store, f"batch_add_{table}")("test", ["film"])["added"] == 1
    assert len(rest.rows(table)) == 1
    assert rest.rows("updates") == []


@pytest.mark.parametrize("table", ["watchlist", "diary"])
def test_empty_batch_does_not_create_a_user_or_call_database(persistence, table, monkeypatch):
    store, rest = persistence
    def unexpected_user(_):
        pytest.fail("An empty batch should not create a user")
    monkeypatch.setattr(store, "_get_or_create_user_id", unexpected_user)
    assert getattr(store, f"batch_add_{table}")("test", ["", " "]) == {
        "added": 0, "errors": [], "missing_metadata": [], "total": 0
    }
    assert rest.requests == []
