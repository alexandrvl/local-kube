#!/usr/bin/env python3
"""Create private Harbor projects for the team images and one robot that can push them.

The robot password is written to credentials/lab-secrets.env. It is not printed.
"""

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ("team-a", "team-b", "team-c")
ROBOT = "team-builder"
ACCESS = [
    {"resource": "repository", "action": "push"},
    {"resource": "repository", "action": "pull"},
    {"resource": "artifact", "action": "read"},
    {"resource": "artifact", "action": "list"},
    {"resource": "tag", "action": "create"},
    {"resource": "tag", "action": "list"},
]


def load_env():
    env = {}
    path = ROOT / "credentials" / "lab-secrets.env"
    for line in path.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key] = value
    return env, path


def call(auth, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request("https://harbor.lab.test/api/v2.0" + path, data=data, method=method)
    request.add_header("Authorization", "Basic " + auth)
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:400]
        if exc.code in (401, 403):
            raise SystemExit(f"harbor auth failed: {exc.code}") from exc
        return exc.code, detail


def upsert_env(path, env, key, value):
    lines = path.read_text().splitlines()
    prefix = key + "="
    replaced = False
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = prefix + value
            replaced = True
            break
    if not replaced:
        lines.append(prefix + value)
    path.write_text("\n".join(lines) + "\n")
    env[key] = value


def main():
    env, path = load_env()
    auth = base64.b64encode(f"{env['HARBOR_ADMIN_USER']}:{env['HARBOR_ADMIN_PASSWORD']}".encode()).decode()
    _, projects = call(auth, "GET", "/projects?page_size=100")
    have = {item["name"] for item in projects}
    for name in PROJECTS:
        if name in have:
            print(f"project {name} exists")
            continue
        code, body = call(
            auth,
            "POST",
            "/projects",
            {"project_name": name, "metadata": {"public": "false"}},
        )
        if code >= 300:
            raise SystemExit(f"project {name} failed: {code} {body}")
        print(f"project {name} created")
    _, robots = call(auth, "GET", "/robots?page_size=100")
    existing = None
    for item in robots or []:
        if item.get("name", "").endswith(ROBOT):
            existing = item
            break
    permissions = [
        {"kind": "project", "namespace": name, "access": ACCESS}
        for name in PROJECTS
    ]
    permissions.append(
        {
            "kind": "project",
            "namespace": "lab",
            "access": ACCESS,
        }
    )
    if existing:
        code, body = call(
            auth,
            "PUT",
            f"/robots/{existing['id']}",
            {"name": ROBOT, "duration": -1, "level": "system", "permissions": permissions},
        )
        if code >= 300:
            raise SystemExit(f"robot update failed: {code} {body}")
        secret = env.get("HARBOR_ROBOT_PASSWORD", "")
        username = existing["name"]
        print(f"robot {username} updated")
    else:
        code, body = call(
            auth,
            "POST",
            "/robots",
            {
                "name": ROBOT,
                "duration": -1,
                "level": "system",
                "permissions": permissions,
            },
        )
        if code >= 300 or not isinstance(body, dict):
            raise SystemExit(f"robot create failed: {code} {body}")
        secret = body.get("secret") or ""
        username = body.get("name") or ""
        if not secret or not username:
            raise SystemExit("robot response did not include credentials")
        upsert_env(path, env, "HARBOR_ROBOT_USER", username)
        upsert_env(path, env, "HARBOR_ROBOT_PASSWORD", secret)
        print(f"robot {username} created")
    if not env.get("HARBOR_ROBOT_PASSWORD"):
        raise SystemExit("robot password is missing from credentials; recreate the robot")
    print("robot credentials stored")


if __name__ == "__main__":
    main()
