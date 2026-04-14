from __future__ import annotations

import os
import time

from fastapi.testclient import TestClient

from api.app import app


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing_env:{name}")
    return value


def main() -> int:
    user = os.getenv("TEST_TARGET_USERNAME") or os.getenv("LETTERBOXD_USERNAME")
    password = os.getenv("TEST_TARGET_PASSWORD") or os.getenv("LETTERBOXD_PASSWORD")
    if not user or not password:
        print("ERROR missing TEST_TARGET_USERNAME/TEST_TARGET_PASSWORD (or LETTERBOXD_USERNAME/LETTERBOXD_PASSWORD)")
        return 1

    _require_env("MASTER_ENCRYPTION_KEY")

    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200, health.text

    profiles = client.get("/discovery/profiles")
    assert profiles.status_code == 200, profiles.text

    user_id = "smoke-user"
    ingest = client.post("/ingest/start", json={"user_id": user_id, "source": "trending", "depth_pages": 2})
    assert ingest.status_code == 200, ingest.text
    deadline = time.time() + 3
    while time.time() < deadline:
        progress = client.get("/ingest/progress", params={"user_id": user_id})
        assert progress.status_code == 200, progress.text
        payload = progress.json()
        if payload.get("progress") == 100 and not payload.get("running"):
            break
        time.sleep(0.1)

    deck = client.get("/discovery/deck", params={"user_id": user_id, "profile": "gold-standard"})
    assert deck.status_code == 200, deck.text
    results = deck.json().get("results")
    assert isinstance(results, list)
    if results:
        details = client.get("/discovery/details", params={"slug": results[0]["slug"]})
        assert details.status_code == 200, details.text
    else:
        print("WARN discovery/deck returned no results for current scraper backend")

    auth = client.post("/auth/session", json={"username": user, "password": password})
    if auth.status_code == 200:
        print("OK auth/session login succeeded")
        return 0

    print(f"WARN auth/session failed status={auth.status_code} body={auth.text}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
