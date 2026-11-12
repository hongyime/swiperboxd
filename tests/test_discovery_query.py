"""Discovery candidates must be filtered and bounded by the database query."""

from pathlib import Path

from src.api.store import SupabaseStore


class _Response:
    def __init__(self, data):
        self.data = data


class _Rpc:
    def __init__(self, client, name, params):
        self.client = client
        self.name = name
        self.params = params

    def execute(self):
        return _Response([{"slug": "eligible", "title": "Eligible", "rating": 4.5, "popularity": 50}])


class _Client:
    def __init__(self):
        self.ops = []

    def rpc(self, name, params):
        self.ops.append((name, params))
        return _Rpc(self, name, params)


def test_discovery_candidates_use_postgres_filters_and_limit(monkeypatch):
    client = _Client()
    monkeypatch.setattr("src.api.store.get_supabase_client", lambda: client)

    movies = SupabaseStore().get_movies(min_rating=4.5, max_popularity=50, limit=200)

    assert [movie["slug"] for movie in movies] == ["eligible"]
    assert client.ops == [(
        "discovery_sample",
        {"p_min_rating": 4.5, "p_max_popularity": 50, "p_sample_size": 200},
    )]


def test_discovery_sample_migration_filters_and_randomizes_in_postgres():
    migration = Path(__file__).parent.parent / "db" / "migrations" / "007_discovery_sample.sql"
    sql = migration.read_text().lower()

    assert "where m.rating >= p_min_rating" in sql
    assert "order by random()" in sql
    assert "limit greatest(p_sample_size, 0)" in sql
