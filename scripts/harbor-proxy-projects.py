#!/usr/bin/env python3
"""Create Harbor pull-through projects. Idempotent. Reads the admin password from credentials/."""
import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRIES = [
    ("dockerhub", "docker-hub", "https://hub.docker.com"),
    ("ghcr", "github-ghcr", "https://ghcr.io"),
    ("quay", "docker-registry", "https://quay.io"),
    ("k8s", "docker-registry", "https://registry.k8s.io"),
    ("ecr-public", "docker-registry", "https://ecr-public.aws.com"),
    ("kyverno", "docker-registry", "https://reg.kyverno.io"),
]


def load_env():
    env = {}
    for line in (ROOT / "credentials" / "lab-secrets.env").read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key] = value
    return env


def call(auth, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request("https://harbor.lab.test/api/v2.0" + path, data=data, method=method)
    req.add_header("Authorization", "Basic " + auth)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:240]
        if exc.code in (401, 403):
            raise SystemExit(f"harbor auth failed: {exc.code}") from exc
        return exc.code, detail


def main():
    env = load_env()
    auth = base64.b64encode(f"{env['HARBOR_ADMIN_USER']}:{env['HARBOR_ADMIN_PASSWORD']}".encode()).decode()
    _, existing = call(auth, "GET", "/registries?page_size=100")
    by_name = {item["name"]: item for item in existing}
    for name, kind, url in REGISTRIES:
        if name in by_name:
            print(f"registry {name} exists")
            continue
        code, body = call(auth, "POST", "/registries", {"name": name, "type": kind, "url": url, "insecure": False})
        if code >= 300:
            raise SystemExit(f"registry {name} failed: {code} {body}")
        print(f"registry {name} created")
    _, existing = call(auth, "GET", "/registries?page_size=100")
    by_name = {item["name"]: item for item in existing}
    _, projects = call(auth, "GET", "/projects?page_size=100")
    have = {item["name"] for item in projects}
    if "lab" not in have:
        code, body = call(auth, "POST", "/projects", {"project_name": "lab", "metadata": {"public": "true"}})
        if code >= 300:
            raise SystemExit(f"lab project failed: {code} {body}")
        print("project lab created")
    else:
        print("project lab exists")
    for name, _, _ in REGISTRIES:
        if name in have:
            print(f"project {name} exists")
            continue
        code, body = call(auth, "POST", "/projects", {
            "project_name": name,
            "registry_id": by_name[name]["id"],
            "metadata": {"public": "true"},
        })
        if code >= 300:
            raise SystemExit(f"project {name} failed: {code} {body}")
        print(f"project {name} created")


if __name__ == "__main__":
    main()
