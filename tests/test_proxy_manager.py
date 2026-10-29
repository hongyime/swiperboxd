"""ProxyManager startup behavior: no network probe unless explicitly enabled."""

import httpx

from src.api.proxy_manager import ProxyManager


class _FakeResponse:
    status_code = 200


class _RecordingClient:
    """Stand-in for httpx.Client that records construction and GET calls."""

    instances: list["_RecordingClient"] = []
    gets: list[str] = []

    def __init__(self, *args, **kwargs):
        _RecordingClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def get(self, url):
        _RecordingClient.gets.append(url)
        return _FakeResponse()


def _reset() -> None:
    _RecordingClient.instances.clear()
    _RecordingClient.gets.clear()


def _scrape_do_env(monkeypatch) -> None:
    monkeypatch.setenv("SCRAPEDO_TOKENS", "tok")
    monkeypatch.delenv("SCRAPEDO_TOKEN", raising=False)
    monkeypatch.delenv("WEBSHARE_PROXIES", raising=False)


def test_no_probe_by_default_when_env_unset(monkeypatch):
    _reset()
    _scrape_do_env(monkeypatch)
    monkeypatch.delenv("SCRAPEDO_PROBE_ON_STARTUP", raising=False)
    monkeypatch.setattr(httpx, "Client", _RecordingClient)

    manager = ProxyManager()

    assert manager._scrape_do is not None
    assert _RecordingClient.instances == []
    assert _RecordingClient.gets == []


def test_probe_runs_when_env_true(monkeypatch):
    _reset()
    _scrape_do_env(monkeypatch)
    monkeypatch.setenv("SCRAPEDO_PROBE_ON_STARTUP", "true")
    monkeypatch.setattr(httpx, "Client", _RecordingClient)

    ProxyManager()

    assert _RecordingClient.gets
    assert all("token=tok" in url for url in _RecordingClient.gets)
