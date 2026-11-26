#!/usr/bin/env python3
"""Reconcile CustomerToken objects for one team namespace.

Signs a JWT with the team gateway certificate, stores the token string in
Vault, and records only identifiers and expiry on the object status.

The issuance decision in reconcile_token is unchanged. main watches
CustomerToken, queues the object, and runs that decision at expiresAt minus
overlap. Kubernetes reads of the signer Secret, signer-observed,
signer-previous, the JWKS ConfigMap, the revoked ConfigMap, and the
CustomerToken list come from ReadCache. The Vault client token is reused
until shortly before its lease ends. A full resync (RESYNC_SECONDS, default
10 minutes, clamped to 5–15 minutes) relists and drops that cache.
"""

import base64
import calendar
import json
import os
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

TEAM = os.environ["TEAM"]
VAULT_ADDR = os.environ["VAULT_ADDR"].rstrip("/")
VAULT_ROLE = os.environ["VAULT_ROLE"]
SIGNER_SECRET = os.environ["SIGNER_SECRET"]
PREVIOUS_SECRET = os.environ.get("PREVIOUS_SECRET", "signer-previous")
OBSERVED_SECRET = os.environ.get("OBSERVED_SECRET", "signer-observed")
JWKS_CONFIGMAP = os.environ["JWKS_CONFIGMAP"]
REVOKED_CONFIGMAP = os.environ.get("REVOKED_CONFIGMAP", "gateway-revoked")
ISSUER = os.environ["ISSUER"]

API = "https://kubernetes.default.svc"
CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
NS_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
API_TOKEN = "/var/run/secrets/kubernetes.io/serviceaccount/token"
VAULT_TOKEN_FILE = "/var/run/secrets/vault/token"
CTX = None

RESYNC_KEY = "*resync"
NAMESPACE_KEY = "*namespace"
RETRY_SECONDS = 10
WATCH_SECONDS = 300
RESYNC_MIN = 300
RESYNC_MAX = 900
RESYNC_DEFAULT = 600
STOP = threading.Event()


def log(message):
    print(message, flush=True)


def read(path):
    with open(path) as handle:
        return handle.read().strip()


def api_context():
    global CTX
    if CTX is None:
        CTX = ssl.create_default_context(cafile=CA)
    return CTX


def configured_resync_seconds():
    """Full relist interval. Default 10 minutes, clamped to 5–15 minutes."""
    raw = int(os.environ.get("RESYNC_SECONDS", str(RESYNC_DEFAULT)))
    bounded = min(RESYNC_MAX, max(RESYNC_MIN, raw))
    if bounded != raw:
        log(f"RESYNC_SECONDS {raw} is outside {RESYNC_MIN}-{RESYNC_MAX}, using {bounded}")
    return bounded


def parse_duration(value):
    units = {"s": 1, "m": 60, "h": 3600}
    if not value or value[-1] not in units:
        raise ValueError(f"bad duration {value!r}")
    return int(value[:-1]) * units[value[-1]]


def b64u_int(number):
    length = max(1, (number.bit_length() + 7) // 8)
    return base64.urlsafe_b64encode(number.to_bytes(length, "big")).decode().rstrip("=")


def load_cert(pem):
    return x509.load_pem_x509_certificate(pem.encode())


def kid_of(cert):
    public = cert.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    digest = hashes.Hash(hashes.SHA256())
    digest.update(public)
    return base64.urlsafe_b64encode(digest.finalize()).decode().rstrip("=")


def jwk_of(cert):
    numbers = cert.public_key().public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid_of(cert),
        "n": b64u_int(numbers.n),
        "e": b64u_int(numbers.e),
    }


def stamp(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def parse_stamp(value):
    return int(calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ")))


def _rv_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _object_rv(obj):
    if not isinstance(obj, dict):
        return 0
    return _rv_int((obj.get("metadata") or {}).get("resourceVersion"))


def _named_core(path):
    return "/secrets/" in path or "/configmaps/" in path


def _write_target(method, path, request_body):
    if _named_core(path):
        return path
    if method != "POST" or not isinstance(request_body, dict):
        return None
    name = (request_body.get("metadata") or {}).get("name")
    base = path.rstrip("/")
    if name and (base.endswith("/secrets") or base.endswith("/configmaps")):
        return base + "/" + name
    return None


class ReadCache:
    """GET cache for the signer Secret, the two signer snapshots, and the two ConfigMaps.

    CustomerToken objects live here too. The watch updates them. A resync
    drops the Secret and ConfigMap entries and merges a fresh list.
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.gets = {}
        self.tokens = {}
        self.tombstones = {}
        self.token_rv = None
        self.listed = False

    def cached_get(self, path):
        if not _named_core(path):
            return None
        with self.lock:
            return self.gets.get(path)

    def store_get(self, path, status, payload):
        if not _named_core(path):
            return
        if status == 200 and isinstance(payload, dict):
            with self.lock:
                self.gets[path] = (status, payload)
        elif status == 404:
            with self.lock:
                self.gets[path] = (status, payload)

    def drop_get(self, path):
        with self.lock:
            self.gets.pop(path, None)

    def invalidate_reads(self):
        with self.lock:
            self.gets.clear()

    def note_write(self, method, path, request_body, status, response_body):
        target = _write_target(method, path, request_body)
        if target is None:
            return
        with self.lock:
            if method == "DELETE" and status in (200, 202, 404):
                self.gets[target] = (404, None)
                return
            if status not in (200, 201):
                self.gets.pop(target, None)
                return
            stored = response_body if isinstance(response_body, dict) and response_body.get("metadata") else None
            if stored is None and isinstance(request_body, dict):
                stored = request_body
            if isinstance(stored, dict):
                self.gets[target] = (200, stored)
            else:
                self.gets.pop(target, None)

    def is_listed(self):
        with self.lock:
            return self.listed

    def resource_version(self):
        with self.lock:
            return self.token_rv or "0"

    def token_items(self):
        with self.lock:
            return list(self.tokens.values())

    def get_token(self, name):
        with self.lock:
            return self.tokens.get(name)

    def update_status(self, name, status):
        with self.lock:
            obj = self.tokens.get(name)
            if obj is not None:
                obj["status"] = status

    def note_rv(self, obj):
        with self.lock:
            self._bump(obj)

    def put_token(self, obj):
        with self.lock:
            name = (obj.get("metadata") or {}).get("name")
            if not name:
                return
            rv = _object_rv(obj)
            current = self.tokens.get(name)
            if current is not None and _object_rv(current) > rv:
                return
            self.tokens[name] = obj
            if self.tombstones.get(name, 0) <= rv:
                self.tombstones.pop(name, None)
            self._bump(obj)

    def delete_token(self, obj):
        with self.lock:
            name = (obj.get("metadata") or {}).get("name")
            if not name:
                return
            rv = _object_rv(obj)
            current = self.tokens.get(name)
            if current is not None and _object_rv(current) > rv:
                return
            self.tokens.pop(name, None)
            self.tombstones[name] = rv
            self._bump(obj)

    def mark_stale(self):
        with self.lock:
            self.listed = False

    def merge_list(self, body):
        """Replace the token store with a list, keeping a newer watch event."""
        with self.lock:
            incoming = {}
            for item in body.get("items") or []:
                name = (item.get("metadata") or {}).get("name")
                if name:
                    incoming[name] = item
            list_rv = _rv_int((body.get("metadata") or {}).get("resourceVersion"))
            names = set(incoming) | set(self.tokens) | set(self.tombstones)
            merged = {}
            for name in names:
                inc = incoming.get(name)
                cur = self.tokens.get(name)
                tomb = self.tombstones.get(name, 0)
                if inc is None:
                    cur_rv = _object_rv(cur) if cur is not None else 0
                    if cur is not None and cur_rv > list_rv and tomb <= cur_rv:
                        merged[name] = cur
                    continue
                inc_rv = _object_rv(inc)
                if tomb > inc_rv:
                    continue
                if cur is not None and _object_rv(cur) > inc_rv:
                    merged[name] = cur
                else:
                    merged[name] = inc
            self.tokens = merged
            self.tombstones = {name: rv for name, rv in self.tombstones.items() if rv > list_rv}
            if list_rv >= _rv_int(self.token_rv):
                self.token_rv = (body.get("metadata") or {}).get("resourceVersion")
            self.listed = True

    def _bump(self, obj):
        rv = _object_rv(obj)
        if rv >= _rv_int(self.token_rv):
            self.token_rv = (obj.get("metadata") or {}).get("resourceVersion")


class WorkQueue:
    """Names to reconcile, each with the earliest time it should run."""

    def __init__(self):
        self._cv = threading.Condition()
        self.pending = {}

    def enqueue(self, key, when):
        with self._cv:
            current = self.pending.get(key)
            if current is not None and current <= when:
                return False
            self.pending[key] = when
            self._cv.notify()
            return True

    def cancel(self, key):
        with self._cv:
            self.pending.pop(key, None)
            self._cv.notify()

    def at(self, key):
        with self._cv:
            return self.pending.get(key)

    def pop_due(self, now):
        with self._cv:
            due = [key for key, when in self.pending.items() if when <= now]
            for key in due:
                del self.pending[key]
            due.sort(key=lambda key: (0 if key == RESYNC_KEY else 1 if key == NAMESPACE_KEY else 2, key))
            return due

    def wait_ready(self, stop):
        with self._cv:
            while not stop.is_set():
                now = time.time()
                if any(when <= now for when in self.pending.values()):
                    return
                if not self.pending:
                    self._cv.wait()
                    continue
                nxt = min(self.pending.values())
                self._cv.wait(timeout=max(0, nxt - time.time()))


class VaultSession:
    """One Kubernetes auth login, reused until shortly before lease_duration."""

    def __init__(self):
        self.token = None
        self.refresh_at = 0

    def clear(self):
        self.token = None
        self.refresh_at = 0

    def client_token(self, now):
        now = int(now)
        if self.token and now < self.refresh_at:
            return self.token
        code, body = vault_request(
            "",
            "POST",
            "/v1/auth/kubernetes/login",
            {"role": VAULT_ROLE, "jwt": read(VAULT_TOKEN_FILE)},
        )
        if code != 200 or not isinstance(body, dict):
            raise RuntimeError(f"vault login failed: {code}")
        auth = body["auth"]
        lease = int(auth["lease_duration"])
        if lease < 1:
            raise RuntimeError("vault login returned no lease")
        skew = min(60, max(1, lease // 10))
        self.token = auth["client_token"]
        self.refresh_at = now + lease - skew
        log(f"vault login lease={lease}s refresh_in={lease - skew}s")
        return self.token


def kube_call(method, path, body=None, content_type="application/json"):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(API + path, data=data, method=method)
    request.add_header("Authorization", "Bearer " + read(API_TOKEN))
    request.add_header("Accept", "application/json")
    if body is not None:
        request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, context=api_context(), timeout=20) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:400]
        return exc.code, detail


def kube(method, path, body=None, content_type="application/json"):
    if method == "GET":
        cached = CACHE.cached_get(path)
        if cached is not None:
            return cached
    status, payload = kube_call(method, path, body, content_type)
    if method == "GET":
        CACHE.store_get(path, status, payload)
    else:
        CACHE.note_write(method, path, body, status, payload)
    return status, payload


def vault_request(token, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(VAULT_ADDR + path, data=data, method=method)
    request.add_header("X-Vault-Token", token)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:400]
        return exc.code, detail


def _vault_denied(code):
    if code in (401, 403):
        VAULT.clear()
        return True
    return False


def secret_pem(namespace, name, key):
    code, body = kube("GET", f"/api/v1/namespaces/{namespace}/secrets/{name}")
    if code == 404:
        return None
    if code != 200:
        raise RuntimeError(f"secret {name} read failed: {code}")
    encoded = body.get("data", {}).get(key)
    if not encoded:
        return None
    return base64.b64decode(encoded).decode()


def write_public_cert(namespace, name, pem):
    encoded = base64.b64encode(pem.encode()).decode()
    code, current = kube("GET", f"/api/v1/namespaces/{namespace}/secrets/{name}")
    if code not in (200, 404):
        raise RuntimeError(f"secret {name} read failed: {code}")
    if code == 200 and (current.get("data") or {}).get("tls.crt") == encoded and list(current.get("data") or {}) == ["tls.crt"]:
        return
    body = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": namespace},
        "type": "Opaque",
        "data": {"tls.crt": encoded},
    }
    if code == 404:
        status, detail = kube("POST", f"/api/v1/namespaces/{namespace}/secrets", body)
    else:
        body["metadata"]["resourceVersion"] = current["metadata"]["resourceVersion"]
        status, detail = kube("PUT", f"/api/v1/namespaces/{namespace}/secrets/{name}", body)
    if status not in (200, 201):
        raise RuntimeError(f"secret {name} write failed: {status} {detail}")


def delete_secret(namespace, name):
    code, detail = kube("DELETE", f"/api/v1/namespaces/{namespace}/secrets/{name}")
    if code not in (200, 202, 404):
        raise RuntimeError(f"secret {name} delete failed: {code} {detail}")


def kid_is_live(meta, kid, now):
    if not meta or meta.get("kid") != kid:
        return False
    expires = meta.get("expiresAt")
    if not expires:
        return True
    return parse_stamp(expires) > now


def previous_still_needed(tokens, kid, now):
    for item in tokens:
        status = item.get("status") or {}
        if kid_is_live(status, kid, now) or kid_is_live(status.get("previous") or {}, kid, now):
            return True
    return False


def retain_signer(namespace, current_pem, tokens):
    """Keep the public certificate that was current before the signer Secret changed.

    The private key is not copied. signer-observed is the last certificate this
    controller saw. When the signer Secret carries a new kid, that saved
    certificate is written to signer-previous before the snapshot moves on.
    """
    current_kid = kid_of(load_cert(current_pem))
    observed = secret_pem(namespace, OBSERVED_SECRET, "tls.crt")
    previous = secret_pem(namespace, PREVIOUS_SECRET, "tls.crt")
    if observed and kid_of(load_cert(observed)) != current_kid:
        write_public_cert(namespace, PREVIOUS_SECRET, observed)
        previous = observed
        log(f"signer rotated from {kid_of(load_cert(observed))} to {current_kid}")
    write_public_cert(namespace, OBSERVED_SECRET, current_pem)
    now = int(time.time())
    if previous and kid_of(load_cert(previous)) == current_kid:
        delete_secret(namespace, PREVIOUS_SECRET)
        previous = None
    elif previous and not previous_still_needed(tokens, kid_of(load_cert(previous)), now):
        kept = kid_of(load_cert(previous))
        delete_secret(namespace, PREVIOUS_SECRET)
        log(f"signer dropped previous kid={kept}")
        previous = None
    certs = [load_cert(current_pem)]
    if previous:
        certs.append(load_cert(previous))
    return certs


def publish_jwks(namespace, certs):
    document = json.dumps({"keys": [jwk_of(cert) for cert in certs]}, separators=(",", ":"))
    code, current = kube("GET", f"/api/v1/namespaces/{namespace}/configmaps/{JWKS_CONFIGMAP}")
    if code == 200 and current.get("data", {}).get("jwks.json") == document:
        return
    payload = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": JWKS_CONFIGMAP, "namespace": namespace},
        "data": {"jwks.json": document},
    }
    if code == 404:
        status, detail = kube("POST", f"/api/v1/namespaces/{namespace}/configmaps", payload)
    elif code == 200:
        status, detail = kube(
            "PUT", f"/api/v1/namespaces/{namespace}/configmaps/{JWKS_CONFIGMAP}", payload
        )
    else:
        raise RuntimeError(f"configmap read failed: {code}")
    if status not in (200, 201):
        raise RuntimeError(f"configmap write failed: {status} {detail}")
    log(f"jwks updated keys={len(certs)}")


def read_revoked(namespace):
    code, body = kube("GET", f"/api/v1/namespaces/{namespace}/configmaps/{REVOKED_CONFIGMAP}")
    if code == 404:
        return {}, 404, None
    if code != 200:
        raise RuntimeError(f"revocation read failed: {code}")
    raw = (body.get("data") or {}).get("revoked.json") or '{"jti":{}}'
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    return data.get("jti") or {}, 200, body


def live_revocations(items, existing, now):
    kept = {}
    for jti, expires in (existing or {}).items():
        if isinstance(jti, str) and isinstance(expires, str) and parse_stamp(expires) > now:
            kept[jti] = expires
    for item in items:
        if not (item.get("spec") or {}).get("revoked"):
            continue
        status = item.get("status") or {}
        for meta in (status, status.get("previous") or {}):
            jti = meta.get("jti")
            expires = meta.get("expiresAt")
            if isinstance(jti, str) and isinstance(expires, str) and parse_stamp(expires) > now:
                kept[jti] = expires
    return kept


def publish_revoked(namespace, items):
    existing, code, current = read_revoked(namespace)
    kept = live_revocations(items, existing, int(time.time()))
    document = json.dumps({"jti": kept}, separators=(",", ":"), sort_keys=True)
    if code == 404 and not kept:
        return
    if code == 200 and (current.get("data") or {}).get("revoked.json") == document:
        return
    payload = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": REVOKED_CONFIGMAP, "namespace": namespace},
        "data": {"revoked.json": document},
    }
    if code == 404:
        status, detail = kube("POST", f"/api/v1/namespaces/{namespace}/configmaps", payload)
    else:
        payload["metadata"]["resourceVersion"] = current["metadata"]["resourceVersion"]
        status, detail = kube("PUT", f"/api/v1/namespaces/{namespace}/configmaps/{REVOKED_CONFIGMAP}", payload)
    if status not in (200, 201):
        raise RuntimeError(f"revocation write failed: {status} {detail}")
    log(f"revocations updated count={len(kept)}")


def vault_path(api, customer):
    return f"/v1/secret/data/teams/{TEAM}/tokens/{api.strip('/')}/{customer}"


def read_vault(token, api, customer):
    code, body = vault_request(token, "GET", vault_path(api, customer))
    if code == 404:
        return {"current": "", "previous": ""}
    if code != 200:
        _vault_denied(code)
        raise RuntimeError(f"vault read failed: {code}")
    data = body.get("data", {}).get("data", {})
    return {"current": data.get("current") or "", "previous": data.get("previous") or ""}


def write_vault(token, api, customer, current, previous):
    code, detail = vault_request(
        token,
        "POST",
        vault_path(api, customer),
        {"data": {"current": current, "previous": previous}},
    )
    if code not in (200, 204):
        _vault_denied(code)
        raise RuntimeError(f"vault write failed: {code} {detail}")


def sign_token(private_pem, cert, customer, api, ttl):
    now = int(time.time())
    payload = {
        "sub": customer,
        "aud": api,
        "iss": ISSUER,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(payload, private_pem, algorithm="RS256", headers={"kid": kid_of(cert)})
    return token, payload


def status_meta(payload, cert):
    return {
        "jti": payload["jti"],
        "kid": kid_of(cert),
        "issuedAt": stamp(payload["iat"]),
        "expiresAt": stamp(payload["exp"]),
    }


def patch_status(namespace, name, status):
    code, detail = kube(
        "PATCH",
        f"/apis/lab.test/v1/namespaces/{namespace}/customertokens/{name}/status",
        {"status": status},
        "application/merge-patch+json",
    )
    if code != 200:
        raise RuntimeError(f"status patch failed: {code} {detail}")
    CACHE.update_status(name, status)


def reconcile_token(namespace, token, obj, private_pem, cert):
    spec = obj["spec"]
    name = obj["metadata"]["name"]
    status = dict(obj.get("status") or {})
    if spec.get("revoked"):
        stored = read_vault(token, spec["api"], spec["customer"])
        if stored["current"] or stored["previous"]:
            write_vault(token, spec["api"], spec["customer"], "", "")
        if not status.get("revoked"):
            status["revoked"] = True
            patch_status(namespace, name, status)
            log(f"revoked {name} jti={status.get('jti', '')}")
        return
    ttl = parse_duration(spec["ttl"])
    overlap = parse_duration(spec["overlap"])
    if overlap >= ttl:
        raise RuntimeError(f"{name} overlap must be shorter than ttl")
    now = int(time.time())
    stored = read_vault(token, spec["api"], spec["customer"])
    expires = status.get("expiresAt")
    current_until = parse_stamp(expires) if expires else 0
    if status.get("jti") and stored["current"] and now < current_until - overlap:
        return
    previous_token = ""
    previous_meta = None
    if status.get("jti") and stored["current"] and now < current_until:
        previous_token = stored["current"]
        previous_meta = {
            "jti": status.get("jti", ""),
            "kid": status.get("kid", ""),
            "issuedAt": status.get("issuedAt", ""),
            "expiresAt": status.get("expiresAt", ""),
        }
    signed, payload = sign_token(private_pem, cert, spec["customer"], spec["api"], ttl)
    write_vault(token, spec["api"], spec["customer"], signed, previous_token)
    new_status = status_meta(payload, cert)
    new_status["revoked"] = False
    if previous_meta:
        new_status["previous"] = previous_meta
    patch_status(namespace, name, new_status)
    log(f"issued {name} jti={payload['jti']} exp={new_status['expiresAt']} previous={bool(previous_meta)}")


def needs_vault(obj, now):
    """True when reconcile_token would read Vault.

    The decision inside reconcile_token is unchanged. This only decides
    whether that function runs. A live jti before expiresAt minus overlap
    does not.
    """
    spec = obj.get("spec") or {}
    status = obj.get("status") or {}
    now = int(now)
    if spec.get("revoked"):
        return not bool(status.get("revoked"))
    if status.get("revoked"):
        return True
    if not status.get("jti") or not status.get("expiresAt"):
        return True
    overlap = parse_duration(spec["overlap"])
    return now >= parse_stamp(status["expiresAt"]) - overlap


def next_wake(obj, now):
    """When this object should be queued again. None means wait for a watch event."""
    now = int(now)
    if needs_vault(obj, now):
        return now
    if (obj.get("spec") or {}).get("revoked"):
        return None
    overlap = parse_duration(obj["spec"]["overlap"])
    return parse_stamp(obj["status"]["expiresAt"]) - overlap


def schedule_token(obj, now):
    name = obj["metadata"]["name"]
    wake = next_wake(obj, now)
    if wake is None:
        QUEUE.cancel(name)
        return
    QUEUE.enqueue(name, wake)


def list_tokens(namespace, fresh=False):
    if CACHE.is_listed() and not fresh:
        return 200, {"items": CACHE.token_items(), "metadata": {"resourceVersion": CACHE.resource_version()}}
    code, body = kube_call("GET", f"/apis/lab.test/v1/namespaces/{namespace}/customertokens")
    if code != 200 or not isinstance(body, dict):
        return 500 if code == 200 else code, body
    CACHE.merge_list(body)
    return code, body


def sync_public_state():
    """Refresh signer snapshots, JWKS, and the revocation ConfigMap from the cache."""
    namespace = read(NS_FILE)
    cert_pem = secret_pem(namespace, SIGNER_SECRET, "tls.crt")
    key_pem = secret_pem(namespace, SIGNER_SECRET, "tls.key")
    if not cert_pem or not key_pem:
        CACHE.drop_get(f"/api/v1/namespaces/{namespace}/secrets/{SIGNER_SECRET}")
        log("signer secret is not ready")
        return False
    if not CACHE.is_listed():
        code, _body = list_tokens(namespace)
        if code != 200:
            raise RuntimeError(f"list tokens failed: {code}")
    items = CACHE.token_items()
    certs = retain_signer(namespace, cert_pem, items)
    publish_jwks(namespace, certs)
    publish_revoked(namespace, items)
    return True


def handle_token(name, now):
    now = int(now)
    obj = CACHE.get_token(name)
    if obj is None:
        return
    if not needs_vault(obj, now):
        schedule_token(obj, now)
        return
    if not sync_public_state():
        QUEUE.enqueue(name, now + RETRY_SECONDS)
        return
    namespace = read(NS_FILE)
    cert_pem = secret_pem(namespace, SIGNER_SECRET, "tls.crt")
    key_pem = secret_pem(namespace, SIGNER_SECRET, "tls.key")
    if not cert_pem or not key_pem:
        QUEUE.enqueue(name, now + RETRY_SECONDS)
        return
    token = VAULT.client_token(now)
    reconcile_token(namespace, token, obj, key_pem, load_cert(cert_pem))
    updated = CACHE.get_token(name)
    if updated is not None:
        schedule_token(updated, now)


def resync(now):
    now = int(now)
    interval = configured_resync_seconds()
    CACHE.invalidate_reads()
    namespace = read(NS_FILE)
    code, _body = list_tokens(namespace, fresh=True)
    if code != 200:
        raise RuntimeError(f"list tokens failed: {code}")
    if sync_public_state():
        for item in CACHE.token_items():
            schedule_token(item, now)
    else:
        QUEUE.enqueue(NAMESPACE_KEY, now + RETRY_SECONDS)
    QUEUE.enqueue(RESYNC_KEY, now + interval)
    log(f"resync tokens={len(CACHE.token_items())} next={interval}s")


def handle(key, now):
    now = int(now)
    if key == RESYNC_KEY:
        resync(now)
        return
    if key == NAMESPACE_KEY:
        if not sync_public_state():
            QUEUE.enqueue(NAMESPACE_KEY, now + RETRY_SECONDS)
        return
    handle_token(key, now)


class WatchGone(Exception):
    pass


def apply_watch_event(event, now=None):
    now = time.time() if now is None else now
    kind = event.get("type")
    obj = event.get("object") or {}
    if kind == "BOOKMARK":
        CACHE.note_rv(obj)
        return
    if kind == "ERROR":
        if obj.get("code") == 410:
            raise WatchGone()
        raise RuntimeError(f"watch error: {obj}")
    name = (obj.get("metadata") or {}).get("name")
    if kind == "DELETED":
        CACHE.delete_token(obj)
        if name:
            QUEUE.cancel(name)
        QUEUE.enqueue(NAMESPACE_KEY, now)
        return
    if kind in ("ADDED", "MODIFIED"):
        CACHE.put_token(obj)
        if name:
            QUEUE.enqueue(name, now)
        QUEUE.enqueue(NAMESPACE_KEY, now)
        return
    log(f"watch ignored type={kind}")


def ensure_listed(namespace):
    if not CACHE.is_listed():
        code, _body = list_tokens(namespace)
        if code != 200:
            raise RuntimeError(f"list tokens failed: {code}")
    return CACHE.resource_version()


def stream_watch(namespace, resource_version, stop):
    path = (
        f"/apis/lab.test/v1/namespaces/{namespace}/customertokens"
        f"?watch=true&allowWatchBookmarks=true&timeoutSeconds={WATCH_SECONDS}"
        f"&resourceVersion={resource_version}"
    )
    request = urllib.request.Request(API + path, method="GET")
    request.add_header("Authorization", "Bearer " + read(API_TOKEN))
    request.add_header("Accept", "application/json")
    try:
        response = urllib.request.urlopen(request, context=api_context(), timeout=WATCH_SECONDS + 30)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:400]
        if exc.code == 410:
            raise WatchGone()
        raise RuntimeError(f"watch failed: {exc.code} {detail}") from exc
    with response:
        while not stop.is_set():
            line = response.readline()
            if not line:
                return
            if not line.strip():
                continue
            apply_watch_event(json.loads(line))


def watch_forever(stop=None):
    stop = STOP if stop is None else stop
    while not stop.is_set():
        try:
            namespace = read(NS_FILE)
            rv = ensure_listed(namespace)
            log(f"watch customertokens rv={rv}")
            stream_watch(namespace, rv, stop)
        except WatchGone:
            CACHE.mark_stale()
            log("watch resourceVersion expired")
        except (TimeoutError, socket.timeout):
            log("watch timeout, reconnecting")
        except Exception as exc:
            log(f"watch error: {exc}")
            stop.wait(1)


def run(stop=None):
    stop = STOP if stop is None else stop
    while not stop.is_set():
        QUEUE.wait_ready(stop)
        if stop.is_set():
            return
        now = time.time()
        for key in QUEUE.pop_due(now):
            try:
                handle(key, now)
            except Exception as exc:
                log(f"reconcile error: {exc}")
                QUEUE.enqueue(key, time.time() + RETRY_SECONDS)


def main():
    log(f"controller team={TEAM} issuer={ISSUER} resync={configured_resync_seconds()}s")
    QUEUE.enqueue(RESYNC_KEY, time.time())
    threading.Thread(target=watch_forever, name="customertoken-watch", daemon=True).start()
    run()


CACHE = ReadCache()
VAULT = VaultSession()
QUEUE = WorkQueue()


if __name__ == "__main__":
    main()
