"""User-history sync writes in bulk: one call per list, capped/skipped metadata."""

import src.api.app as app_module


class _RecordingStore:
    def __init__(self):
        self.calls: list[tuple] = []
        self.progress: list[int] = []

    def set_ingest_progress(self, user_id, value):
        self.progress.append(value)

    def release_ingest(self, user_id):
        pass

    def batch_add_watchlist(self, user_id, slugs):
        self.calls.append(("watchlist", user_id, list(slugs)))
        return {"added": len(slugs), "errors": [], "missing_metadata": [], "total": len(slugs)}

    def batch_add_diary(self, user_id, slugs):
        self.calls.append(("diary", user_id, list(slugs)))
        return {"added": len(slugs), "errors": [], "missing_metadata": [], "total": len(slugs)}

    def get_movies_by_slugs(self, slugs):
        self.calls.append(("movies_by_slugs", len(list(slugs))))
        return {}

    def upsert_movie(self, movie):
        self.calls.append(("upsert", movie))


class _RecordingScraper:
    def __init__(self, watchlist, diary, metadata_raises=False):
        self.watchlist = watchlist
        self.diary = diary
        self.metadata_raises = metadata_raises
        self.metadata_calls: list[list[str]] = []

    def pull_watchlist_slugs(self, session_cookie, username=None, max_pages=5):
        return set(self.watchlist)

    def pull_diary_slugs(self, session_cookie, username=None, max_pages=5):
        return set(self.diary)

    def metadata_for_slugs(self, slugs):
        if self.metadata_raises:
            raise AssertionError("metadata_for_slugs must not run on Vercel")
        self.metadata_calls.append(list(slugs))
        return []


def _run(monkeypatch, vercel: bool, watchlist, diary, metadata_raises=False):
    store = _RecordingStore()
    scraper = _RecordingScraper(watchlist, diary, metadata_raises)
    monkeypatch.setattr(app_module, "store", store)
    monkeypatch.setattr(app_module, "scraper", scraper)
    if vercel:
        monkeypatch.setenv("VERCEL", "1")
    else:
        monkeypatch.delenv("VERCEL", raising=False)
    stats = app_module._run_user_history_sync("u", "cookie", "u")
    return store, scraper, stats


def _count(store, name):
    return sum(1 for call in store.calls if call[0] == name)


def test_user_history_sync_uses_one_batch_per_list(monkeypatch):
    watchlist = {f"wl-{i}" for i in range(240)}
    diary = {f"diary-{i}" for i in range(240)}
    store, _, stats = _run(monkeypatch, vercel=False, watchlist=watchlist, diary=diary)

    assert _count(store, "watchlist") == 1
    assert _count(store, "diary") == 1
    wl_call = next(call for call in store.calls if call[0] == "watchlist")
    diary_call = next(call for call in store.calls if call[0] == "diary")
    assert set(wl_call[2]) == watchlist
    assert set(diary_call[2]) == diary
    assert stats["watchlist_count"] == 240
    assert stats["diary_count"] == 240


def test_user_history_sync_skips_metadata_on_vercel(monkeypatch):
    store, scraper, stats = _run(
        monkeypatch,
        vercel=True,
        watchlist={"wl-0"},
        diary={"diary-0"},
        metadata_raises=True,
    )

    assert scraper.metadata_calls == []
    assert _count(store, "movies_by_slugs") == 0
    assert stats["metadata_count"] == 0


def test_user_history_sync_caps_metadata_lookups(monkeypatch):
    watchlist = {f"wl-{i}" for i in range(240)}
    store, scraper, _ = _run(monkeypatch, vercel=False, watchlist=watchlist, diary=set())

    assert _count(store, "movies_by_slugs") == 1
    assert len(scraper.metadata_calls) == 1
    assert len(scraper.metadata_calls[0]) == 50
