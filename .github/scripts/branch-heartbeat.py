#!/usr/bin/env python3
"""Write opt-in repository activity to a branch which cannot deploy to Vercel."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

BRANCH = "automation/heartbeat"
CONFIG = {"version": 1, "rootDirectory": ""}
VERCEL = '{"git":{"deploymentEnabled":false}}\n'


def validate_root(root: str) -> str:
    # Keep this rollout bounded to the repository root or one plain directory.
    # No relative traversal, hidden directories, platform paths or glob syntax.
    if not isinstance(root, str) or (root and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", root)):
        raise ValueError("rootDirectory must be empty or one plain directory name")
    return root


def validate_config(path: Path) -> str:
    config = json.loads(path.read_text())
    if (path.is_symlink() or not isinstance(config, dict)
            or set(config) != set(CONFIG) or type(config.get("version")) is not int
            or config["version"] != 1):
        raise ValueError("Heartbeat opt-in must specify version 1 and rootDirectory")
    return validate_root(config["rootDirectory"])


def blob_sha(content: str) -> str:
    data = content.encode("utf-8")
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data, usedforsecurity=False).hexdigest()


def validate_vercel(path: Path) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Heartbeat deployment config must not use symbolic links")
    enabled = json.loads(path.read_text()).get("git", {}).get("deploymentEnabled")
    # A matching true wildcard can override false. Initial pilots use only
    # explicit false rules, leaving unspecified application branches enabled.
    if not isinstance(enabled, dict) or enabled.get(BRANCH) is not False or any(value is not False for value in enabled.values()):
        raise ValueError("Main Vercel config must exclude the heartbeat branch without enabling wildcards")


class GitHub:
    def __init__(self, repository: str, token: str):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Invalid repository name")
        if not token:
            raise ValueError("GITHUB_TOKEN is required")
        self.base = "https://api.github.com/repos/" + repository
        self.token = token

    def request(self, path: str, data: dict | None = None, method: str = "GET"):
        request = urllib.request.Request(
            self.base + path,
            data=None if data is None else json.dumps(data).encode(),
            method=method,
            headers={"Authorization": "Bearer " + self.token,
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 404 and method == "GET" and path == "/git/ref/heads/" + BRANCH:
                return None
            raise RuntimeError(f"GitHub {method} {path} failed: HTTP {error.code}") from None


def heartbeat(api, repository: str, ref: str, event: str, timestamp: str, root: str = "") -> dict:
    root = validate_root(root)
    info = api.request("")
    default = info["default_branch"]
    if info["full_name"] != repository or default == BRANCH:
        raise ValueError("Repository identity or default branch is unsafe")
    if event not in ("schedule", "workflow_dispatch") or ref != "refs/heads/" + default:
        raise ValueError("Run the heartbeat only from the default branch")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp):
        raise ValueError("Invalid UTC timestamp")

    ownership = {"version": 1, "repository": repository}
    if root:
        ownership["rootDirectory"] = root
    marker = json.dumps(ownership, sort_keys=True) + "\n"
    files = {".prawn-heartbeat.json": marker, "vercel.json": VERCEL,
             "last_sync.txt": timestamp + "\n"}
    if root:
        files[root + "/vercel.json"] = VERCEL
    directories = {root} if root else set()
    existing = api.request("/git/ref/heads/" + BRANCH)
    previous = None
    if existing is not None:
        if existing["ref"] != "refs/heads/" + BRANCH or existing["object"]["type"] != "commit":
            raise ValueError("Unexpected heartbeat ref")
        previous = existing["object"]["sha"]
        commit = api.request("/git/commits/" + previous)
        tree = api.request("/git/trees/" + commit["tree"]["sha"] + "?recursive=1")
        entries = {entry["path"]: entry for entry in tree["tree"]}
        if (tree.get("truncated") or len(tree["tree"]) != len(files) + len(directories)
                or set(entries) != set(files) | directories):
            raise ValueError("Reserved heartbeat branch contains unexpected files")
        if (any(entries[path]["type"] != "blob" or entries[path]["mode"] != "100644" for path in files)
                or any(entries[path]["type"] != "tree" or entries[path]["mode"] != "040000" for path in directories)):
            raise ValueError("Reserved heartbeat branch contains unexpected file modes")
        for path in files.keys() - {"last_sync.txt"}:
            if entries[path]["sha"] != blob_sha(files[path]):
                raise ValueError("Reserved heartbeat branch is not owned by this configuration")
        if entries["last_sync.txt"]["sha"] == blob_sha(files["last_sync.txt"]):
            return {"branch": BRANCH, "sha": previous, "changed": False}

    tree = api.request("/git/trees", {"tree": [
        {"path": path, "mode": "100644", "type": "blob", "content": content}
        for path, content in files.items()
    ]}, "POST")
    commit = api.request("/git/commits", {
        "message": "chore: repository heartbeat [skip ci]",
        "tree": tree["sha"], "parents": [] if previous is None else [previous],
    }, "POST")
    # No force update: concurrent writes fail rather than replacing history.
    if previous is None:
        api.request("/git/refs", {"ref": "refs/heads/" + BRANCH, "sha": commit["sha"]}, "POST")
    else:
        api.request("/git/refs/heads/" + BRANCH, {"sha": commit["sha"], "force": False}, "PATCH")
    return {"branch": BRANCH, "sha": commit["sha"], "changed": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-config", type=Path)
    args = parser.parse_args()
    config_path = args.validate_config or Path(".github/branch-heartbeat.json")
    root = validate_config(config_path)
    if args.validate_config:
        return
    validate_vercel(Path(root) / "vercel.json")
    repository = os.environ["GITHUB_REPOSITORY"]
    result = heartbeat(GitHub(repository, os.environ["GITHUB_TOKEN"]), repository,
                       os.environ["GITHUB_REF"], os.environ["GITHUB_EVENT_NAME"],
                       datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), root)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
