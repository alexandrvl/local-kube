"""Queue, resync, cache, and Vault-lease tests for the token controller.

The process has no other test module. These cover the scheduling change:
a live CustomerToken is not read from Vault on a 10 second cadence, the
Vault client token is reused across reconciles, and the resync timer is the
5–15 minute safety net.
"""

import base64
import datetime
import os
import tempfile
import unittest

os.environ["TEAM"] = "team-a"
os.environ["VAULT_ADDR"] = "http://vault.local:8200"
os.environ["VAULT_ROLE"] = "team-a-issuer"
os.environ["SIGNER_SECRET"] = "physical-trading-signer"
os.environ["JWKS_CONFIGMAP"] = "gateway-jwks"
os.environ["ISSUER"] = "https://physical-trading.trading.lab.test"
os.environ["RESYNC_SECONDS"] = "600"

import controller

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _pem_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "physical-trading-signer")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return cert_pem, key_pem


def _token(name, customer, now, ttl=180, overlap="90s", jti=None, revoked=False, status_revoked=False, rv="3"):
    expires = controller.stamp(now + ttl) if jti else None
    issued = controller.stamp(now) if jti else None
    status = {}
    if jti:
        status = {
            "jti": jti,
            "kid": "kid",
            "issuedAt": issued,
            "expiresAt": expires,
            "revoked": status_revoked,
        }
    elif status_revoked:
        status = {"revoked": True}
    return {
        "apiVersion": "lab.test/v1",
        "kind": "CustomerToken",
        "metadata": {"name": name, "namespace": "team-a", "resourceVersion": rv},
        "spec": {
            "domain": "trading",
            "api": "physical-trading/time-series",
            "customer": customer,
            "ttl": "3m",
            "overlap": overlap,
            "revoked": revoked,
        },
        "status": status,
    }


class ControllerLoopTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cert_pem, cls.key_pem = _pem_pair()

    def setUp(self):
        self.clock = [1_700_000_000]
        self._time = controller.time.time
        controller.time.time = lambda: float(self.clock[0])
        self.kube_calls = []
        self.vault_calls = []
        self.server_tokens = []
        self.lease = 3600
        self.vault_current = "existing-jwt"
        self._kube = controller.kube_call
        self._vault = controller.vault_request
        self._ns = controller.NS_FILE
        self._vault_file = controller.VAULT_TOKEN_FILE
        self._resync = os.environ.get("RESYNC_SECONDS")
        os.environ["RESYNC_SECONDS"] = "600"
        controller.kube_call = self.fake_kube
        controller.vault_request = self.fake_vault
        controller.CACHE = controller.ReadCache()
        controller.CACHE.listed = True
        controller.VAULT = controller.VaultSession()
        controller.QUEUE = controller.WorkQueue()
        self.tmp = tempfile.TemporaryDirectory()
        ns_path = os.path.join(self.tmp.name, "namespace")
        token_path = os.path.join(self.tmp.name, "vault-token")
        with open(ns_path, "w", encoding="utf-8") as handle:
            handle.write("team-a\n")
        with open(token_path, "w", encoding="utf-8") as handle:
            handle.write("projected-sa-token\n")
        controller.NS_FILE = ns_path
        controller.VAULT_TOKEN_FILE = token_path

    def tearDown(self):
        controller.time.time = self._time
        controller.kube_call = self._kube
        controller.vault_request = self._vault
        controller.NS_FILE = self._ns
        controller.VAULT_TOKEN_FILE = self._vault_file
        if self._resync is None:
            os.environ.pop("RESYNC_SECONDS", None)
        else:
            os.environ["RESYNC_SECONDS"] = self._resync
        self.tmp.cleanup()

    def at(self, now):
        self.clock[0] = now

    def fake_kube(self, method, path, body=None, content_type="application/json"):
        self.kube_calls.append((method, path))
        if method == "GET" and path.endswith("/secrets/" + controller.SIGNER_SECRET):
            return 200, {
                "metadata": {"name": controller.SIGNER_SECRET, "resourceVersion": "7"},
                "data": {
                    "tls.crt": base64.b64encode(self.cert_pem.encode()).decode(),
                    "tls.key": base64.b64encode(self.key_pem.encode()).decode(),
                },
            }
        if method == "GET" and path.endswith("/customertokens"):
            return 200, {"metadata": {"resourceVersion": "50"}, "items": self.server_tokens}
        if method == "GET" and ("/secrets/" in path or "/configmaps/" in path):
            return 404, None
        if method in ("POST", "PUT"):
            stored = dict(body or {})
            meta = dict(stored.get("metadata") or {})
            meta.setdefault("resourceVersion", "9")
            stored["metadata"] = meta
            return 201 if method == "POST" else 200, stored
        if method == "PATCH":
            return 200, body
        if method == "DELETE":
            return 200, {}
        return 500, f"unexpected {method} {path}"

    def fake_vault(self, token, method, path, body=None):
        self.vault_calls.append((method, path, token))
        if path == "/v1/auth/kubernetes/login":
            return 200, {"auth": {"client_token": "hvs.test", "lease_duration": self.lease}}
        if method == "GET":
            return 200, {"data": {"data": {"current": self.vault_current, "previous": ""}}}
        if method == "POST":
            return 204, None
        return 500, "unexpected"

    def vault_gets(self):
        return [call for call in self.vault_calls if call[0] == "GET"]

    def vault_logins(self):
        return [call for call in self.vault_calls if call[1] == "/v1/auth/kubernetes/login"]

    def signer_gets(self):
        suffix = "/secrets/" + controller.SIGNER_SECRET
        return [call for call in self.kube_calls if call[0] == "GET" and call[1].endswith(suffix)]

    def drain(self, now):
        for key in controller.QUEUE.pop_due(now):
            controller.handle(key, now)

    def test_resync_bounds(self):
        os.environ["RESYNC_SECONDS"] = "600"
        self.assertEqual(controller.configured_resync_seconds(), 600)
        os.environ["RESYNC_SECONDS"] = "300"
        self.assertEqual(controller.configured_resync_seconds(), 300)
        os.environ["RESYNC_SECONDS"] = "900"
        self.assertEqual(controller.configured_resync_seconds(), 900)
        os.environ["RESYNC_SECONDS"] = "299"
        self.assertEqual(controller.configured_resync_seconds(), 300)
        os.environ["RESYNC_SECONDS"] = "901"
        self.assertEqual(controller.configured_resync_seconds(), 900)
        os.environ.pop("RESYNC_SECONDS")
        self.assertEqual(controller.configured_resync_seconds(), 600)

    def test_queue_keeps_the_earlier_deadline(self):
        queue = controller.WorkQueue()
        self.assertTrue(queue.enqueue("acme", 100))
        self.assertFalse(queue.enqueue("acme", 200))
        self.assertEqual(queue.at("acme"), 100)
        self.assertTrue(queue.enqueue("acme", 50))
        self.assertEqual(queue.at("acme"), 50)
        queue.enqueue(controller.RESYNC_KEY, 50)
        queue.enqueue(controller.NAMESPACE_KEY, 50)
        queue.enqueue("globex", 50)
        self.assertEqual(
            queue.pop_due(50),
            [controller.RESYNC_KEY, controller.NAMESPACE_KEY, "acme", "globex"],
        )
        self.assertEqual(queue.pop_due(50), [])

    def test_watch_enqueues_add_and_delete(self):
        now = self.clock[0]
        added = _token("acme", "acme", now, jti="jti-1", rv="4")
        controller.apply_watch_event({"type": "ADDED", "object": added}, now)
        self.assertIs(controller.CACHE.get_token("acme"), added)
        self.assertEqual(controller.QUEUE.at("acme"), now)
        self.assertEqual(controller.QUEUE.at(controller.NAMESPACE_KEY), now)
        controller.QUEUE = controller.WorkQueue()
        controller.apply_watch_event(
            {"type": "DELETED", "object": _token("acme", "acme", now, jti="jti-1", rv="6")},
            now + 1,
        )
        self.assertIsNone(controller.CACHE.get_token("acme"))
        self.assertIsNone(controller.QUEUE.at("acme"))
        self.assertEqual(controller.QUEUE.at(controller.NAMESPACE_KEY), now + 1)

    def test_list_keeps_a_newer_watch_event(self):
        now = self.clock[0]
        current = _token("acme", "acme", now, jti="new", rv="12")
        controller.CACHE.put_token(current)
        controller.CACHE.delete_token(_token("globex", "globex", now, jti="gone", rv="15"))
        controller.CACHE.merge_list({
            "metadata": {"resourceVersion": "14"},
            "items": [
                _token("acme", "acme", now, jti="old", rv="8"),
                _token("globex", "globex", now, jti="gone", rv="9"),
            ],
        })
        self.assertEqual(controller.CACHE.get_token("acme")["status"]["jti"], "new")
        self.assertIsNone(controller.CACHE.get_token("globex"))
        added = _token("north", "north", now, jti="later", rv="20")
        controller.CACHE.put_token(added)
        controller.CACHE.merge_list({"metadata": {"resourceVersion": "16"}, "items": []})
        self.assertEqual(controller.CACHE.get_token("north")["status"]["jti"], "later")

    def test_unchanged_token_is_not_read_every_10s(self):
        now = self.clock[0]
        obj = _token("acme", "acme", now, ttl=180, jti="jti-1", rv="4")
        controller.apply_watch_event({"type": "ADDED", "object": obj}, now)
        self.drain(now)
        self.assertEqual(self.vault_gets(), [])
        self.assertEqual(self.vault_logins(), [])
        overlap = controller.parse_duration("90s")
        deadline = controller.parse_stamp(obj["status"]["expiresAt"]) - overlap
        self.assertEqual(deadline, now + 90)
        self.assertEqual(controller.QUEUE.at("acme"), deadline)
        self.assertEqual(controller.QUEUE.pop_due(now + 10), [])
        controller.handle_token("acme", now + 10)
        self.assertEqual(self.vault_gets(), [])
        self.assertEqual(controller.QUEUE.at("acme"), deadline)

        self.at(deadline)
        self.drain(deadline)
        self.assertEqual(len(self.vault_gets()), 1)
        self.assertEqual(len(self.vault_logins()), 1)
        posted = [call for call in self.vault_calls if call[0] == "POST" and call[1] != "/v1/auth/kubernetes/login"]
        self.assertEqual(len(posted), 1)
        self.assertGreater(controller.QUEUE.at("acme"), deadline + 10)

    def test_vault_token_reused_across_reconciles(self):
        now = self.clock[0]
        acme = _token("acme", "acme", now, rv="1")
        globex = _token("globex", "globex", now, rv="2")
        controller.CACHE.put_token(acme)
        controller.CACHE.put_token(globex)
        controller.handle_token("acme", now)
        controller.handle_token("globex", now)
        self.assertEqual(len(self.vault_logins()), 1)
        self.assertEqual(len(self.vault_gets()), 2)
        self.assertEqual(self.vault_calls[0][1], "/v1/auth/kubernetes/login")
        self.assertEqual(self.vault_gets()[0][2], "hvs.test")
        self.assertEqual(self.vault_gets()[1][2], "hvs.test")
        self.assertEqual(len(self.signer_gets()), 1)

        self.at(now + 10)
        controller.handle_token("acme", now + 10)
        controller.handle_token("globex", now + 10)
        self.assertEqual(len(self.vault_logins()), 1)
        self.assertEqual(len(self.vault_gets()), 2)

        later = now + 3600
        self.at(later)
        other = _token("other", "other", later, rv="3")
        controller.CACHE.put_token(other)
        controller.handle_token("other", later)
        self.assertEqual(len(self.vault_logins()), 2)
        self.assertEqual(controller.VAULT.refresh_at, later + 3600 - 60)

    def test_resync_schedules_expiry_not_a_10s_poll(self):
        now = self.clock[0]
        live = _token("acme", "acme", now, ttl=180, jti="jti-1", rv="4")
        self.server_tokens = [live]
        controller.resync(now)
        self.assertEqual(self.vault_gets(), [])
        self.assertEqual(self.vault_logins(), [])
        self.assertEqual(controller.QUEUE.at(controller.RESYNC_KEY), now + 600)
        self.assertEqual(controller.QUEUE.at("acme"), now + 90)
        self.assertEqual(controller.QUEUE.pop_due(now + 10), [])
        self.assertEqual(len(self.signer_gets()), 1)
        controller.sync_public_state()
        self.assertEqual(len(self.signer_gets()), 1)
        self.assertEqual(self.vault_gets(), [])

    def test_revoked_token_reads_vault_once(self):
        now = self.clock[0]
        obj = _token("acme", "acme", now, ttl=180, jti="jti-1", revoked=True, rv="4")
        controller.CACHE.put_token(obj)
        controller.handle_token("acme", now)
        self.assertEqual(len(self.vault_gets()), 1)
        self.assertEqual(len(self.vault_logins()), 1)
        self.assertTrue(controller.CACHE.get_token("acme")["status"]["revoked"])
        self.assertIsNone(controller.QUEUE.at("acme"))
        controller.handle_token("acme", now + 10)
        self.assertEqual(len(self.vault_gets()), 1)
        self.assertEqual(len(self.vault_logins()), 1)

    def test_vault_401_forces_a_new_login(self):
        now = self.clock[0]
        controller.VAULT.client_token(now)
        self.assertEqual(len(self.vault_logins()), 1)

        def denied(token, method, path, body=None):
            self.vault_calls.append((method, path, token))
            if path == "/v1/auth/kubernetes/login":
                return 200, {"auth": {"client_token": "hvs.next", "lease_duration": self.lease}}
            return 403, "denied"

        controller.vault_request = denied
        with self.assertRaises(RuntimeError):
            controller.read_vault("hvs.test", "physical-trading/time-series", "acme")
        self.assertIsNone(controller.VAULT.token)
        controller.vault_request = self.fake_vault
        self.assertEqual(controller.VAULT.client_token(now + 10), "hvs.test")
        self.assertEqual(len(self.vault_logins()), 2)

    def test_short_lease_refreshes_before_expiry(self):
        now = self.clock[0]
        self.lease = 30
        controller.VAULT.client_token(now)
        skew = max(1, 30 // 10)
        self.assertEqual(controller.VAULT.refresh_at, now + 30 - skew)
        self.assertEqual(controller.VAULT.client_token(now + 30 - skew - 1), "hvs.test")
        self.assertEqual(len(self.vault_logins()), 1)
        controller.VAULT.client_token(now + 30 - skew)
        self.assertEqual(len(self.vault_logins()), 2)


if __name__ == "__main__":
    unittest.main()
