"""Verify a real `vercel build` output against local application responses.

Run after building; no credentials, provider calls or database reads are used.
"""
import json
import os
from pathlib import Path
import re
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(PYTHON_DOTENV_DISABLED="1", APP_ENV="test", SCRAPER_BACKEND="mock",
                  MASTER_ENCRYPTION_KEY="fixture-build-output-key")
for name in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "REDIS_URL", "QSTASH_URL",
             "QSTASH_TOKEN", "QSTASH_CURRENT_SIGNING_KEY", "QSTASH_NEXT_SIGNING_KEY"):
    os.environ[name] = ""

from fastapi.testclient import TestClient
from src.api.app import app


def main():
    output = ROOT / ".vercel" / "output"
    config = json.loads((output / "config.json").read_text())
    static = output / "static"
    files = sorted(path.relative_to(static).as_posix() for path in static.rglob("*") if path.is_file())
    expected = sorted(f"src/web/{name}" for name in (
        "index.html", "app.js", "read-requests.js", "state.js", "styles.css", "logo.svg", "manifest.json"))
    assert files == expected, f"Unexpected published files: {files}"

    def resolve(path, method="GET"):
        for route in config["routes"]:
            if "methods" in route and method not in route["methods"]:
                continue
            match = re.fullmatch(route["src"], path)
            if match:
                dest = re.sub(r"\$(\d+)", lambda group: match.group(int(group[1])), route["dest"])
                return dest.lstrip("/"), {key.lower(): value for key, value in route.get("headers", {}).items()}
        raise AssertionError(f"Unrouted {method} {path}")

    # Discover references in the actual built HTML/JS, including module imports.
    paths = {"/", "/favicon.ico", *(f"/web/{Path(name).name}" for name in files)}
    for filename in files:
        if Path(filename).suffix not in {".html", ".js"}:
            continue
        content = (static / filename).read_text(encoding="utf-8")
        paths.update(re.findall(r'''(?:src|href)=["'](/web/[^"']+)["']''', content))
        paths.update("/web/" + name for name in re.findall(r'''from\s+["']\./([^"']+)["']''', content))

    # FileResponse remains the local-development baseline; no real server is run.
    os.environ["APP_ENV"] = "production"
    client = TestClient(app)
    original_connect = socket.socket.connect
    def no_remote(sock, address):
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original_connect(sock, address)
        raise AssertionError("Build verification must not access external services")
    socket.socket.connect = no_remote
    try:
        for path in sorted(paths):
            destination, headers = resolve(path)
            artifact = static / destination
            assert artifact.is_file(), f"{path} invokes a function or missing artifact: {destination}"
            response = client.get(path)
            assert response.status_code == 200, (path, response.status_code)
            assert artifact.read_bytes() == response.content, f"Content changed at {path}"
            for name in ("x-content-type-options", "x-frame-options", "x-xss-protection",
                         "referrer-policy", "strict-transport-security"):
                assert headers[name] == response.headers[name], (path, name)
            assert "must-revalidate" in headers["cache-control"]
            assert resolve(path, "HEAD")[0] == destination
        # API and non-public paths still enter the actual Python function artifact.
        api_paths = ["/health", "/auth/session", "/users/fixture/sync-status",
                     "/api/extension/batch/watchlist", "/lists/catalog", "/lists/fixture/deck",
                     "/api/cron/sync-users", "/web/app.js/extra", "/faviconXico", "/web/README.md", "/.env"]
        for path in api_paths:
            for method in ("GET", "POST"):
                destination, headers = resolve(path, method)
                function = output / "functions" / (destination + ".func")
                runtime = json.loads((function / ".vc-config.json").read_text())["runtime"]
                assert runtime.startswith("python"), (path, method, runtime)
                assert "cache-control" not in headers, f"Unexpected shared API cache: {path}"
        response = client.post("/api/extension/batch/watchlist", json={},
                               headers={"X-Session-Token": "invalid-fixture-token"})
        assert response.status_code == 401
    finally:
        socket.socket.connect = original_connect
    print(json.dumps({"static_files": len(files), "public_routes_checked": len(paths),
                      "api_method_routes_checked": len(api_paths) * 2,
                      "content_and_security_headers_match": True,
                      "static_routes_invoke_python": False, "unauthenticated_batch_status": 401}))


if __name__ == "__main__":
    main()
