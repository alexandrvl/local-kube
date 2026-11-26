#!/usr/bin/env python3
"""Reconcile CustomerToken objects for one team namespace.

Signs a JWT with the team gateway certificate, stores the token string in
Vault, and records only identifiers and expiry on the object status.
"""

import base64
import calendar
import json
import os
import ssl
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
ISSUER = os.environ["ISSUER"]
INTERVAL = int(os.environ.get("INTERVAL_SECONDS", "10"))

API = "https://kubernetes.default.svc"
CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
NS_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
API_TOKEN = "/var/run/secrets/kubernetes.io/serviceaccount/token"
VAULT_TOKEN_FILE = "/var/run/secrets/vault/token"
CTX = ssl.create_default_context(cafile=CA)


def log(message):
    print(message, flush=True)


def read(path):
    with open(path) as handle:
        return handle.read().strip()


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


def kube(method, path, body=None, content_type="application/json"):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(API + path, data=data, method=method)
    request.add_header("Authorization", "Bearer " + read(API_TOKEN))
    request.add_header("Accept", "application/json")
    if body is not None:
        request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, context=CTX, timeout=20) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:400]
        return exc.code, detail


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


def vault_login():
    code, body = vault_request(
        "",
        "POST",
        "/v1/auth/kubernetes/login",
        {"role": VAULT_ROLE, "jwt": read(VAULT_TOKEN_FILE)},
    )
    if code != 200:
        raise RuntimeError(f"vault login failed: {code}")
    return body["auth"]["client_token"]


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


def vault_path(api, customer):
    return f"/v1/secret/data/teams/{TEAM}/tokens/{api.strip('/')}/{customer}"


def read_vault(token, api, customer):
    code, body = vault_request(token, "GET", vault_path(api, customer))
    if code == 404:
        return {"current": "", "previous": ""}
    if code != 200:
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


def reconcile_token(namespace, token, obj, private_pem, cert):
    spec = obj["spec"]
    name = obj["metadata"]["name"]
    status = dict(obj.get("status") or {})
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
    if previous_meta:
        new_status["previous"] = previous_meta
    patch_status(namespace, name, new_status)
    log(f"issued {name} jti={payload['jti']} exp={new_status['expiresAt']} previous={bool(previous_meta)}")


def reconcile():
    namespace = read(NS_FILE)
    cert_pem = secret_pem(namespace, SIGNER_SECRET, "tls.crt")
    key_pem = secret_pem(namespace, SIGNER_SECRET, "tls.key")
    if not cert_pem or not key_pem:
        log("signer secret is not ready")
        return
    code, body = kube(
        "GET",
        f"/apis/lab.test/v1/namespaces/{namespace}/customertokens",
    )
    if code != 200:
        raise RuntimeError(f"list tokens failed: {code}")
    items = body.get("items", [])
    certs = retain_signer(namespace, cert_pem, items)
    publish_jwks(namespace, certs)
    token = vault_login()
    for item in items:
        reconcile_token(namespace, token, item, key_pem, certs[0])


def main():
    log(f"controller team={TEAM} issuer={ISSUER}")
    while True:
        try:
            reconcile()
        except Exception as exc:
            log(f"reconcile error: {exc}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
