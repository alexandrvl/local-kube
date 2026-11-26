#!/usr/bin/env python3
"""Energy prices. The first caller to write a market and delivery owns that price."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8080
# (market, delivery) -> {"owner": str, "value": number}
PRICES = {}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def caller(self):
        return (self.headers.get("X-Customer") or "").strip()

    def send_json(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self.send_json(200, {"ok": True})
            return
        if path == "/.well-known/jwks.json":
            self.serve_jwks()
            return
        prefix = "/prices/"
        if path.startswith(prefix):
            caller = self.caller()
            if not caller:
                self.send_json(401, {"error": "missing caller"})
                return
            parts = path[len(prefix):].split("/", 1)
            if len(parts) != 2 or not parts[0] or not parts[1]:
                self.send_json(404, {"error": "not found"})
                return
            item = PRICES.get((parts[0], parts[1]))
            if item is None or item["owner"] != caller:
                self.send_json(404, {"error": "not found"})
                return
            self.send_json(
                200,
                {
                    "market": parts[0],
                    "delivery": parts[1],
                    "owner": caller,
                    "value": item["value"],
                },
            )
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/prices":
            self.send_json(404, {"error": "not found"})
            return
        caller = self.caller()
        if not caller:
            self.send_json(401, {"error": "missing caller"})
            return
        try:
            body = json.loads(self.read_body() or b"{}")
        except json.JSONDecodeError:
            self.send_json(400, {"error": "invalid json"})
            return
        market = body.get("market")
        delivery = body.get("delivery")
        if not isinstance(market, str) or not market or not isinstance(delivery, str) or not delivery or "value" not in body:
            self.send_json(400, {"error": "market, delivery, and value are required"})
            return
        key = (market, delivery)
        item = PRICES.get(key)
        if item is not None and item["owner"] != caller:
            self.send_json(409, {"error": "price is owned by another customer"})
            return
        PRICES[key] = {"owner": caller, "value": body["value"]}
        self.send_json(200, {"market": market, "delivery": delivery, "owner": caller, "value": body["value"]})

    def serve_jwks(self):
        try:
            raw = open("/etc/jwks/jwks.json", "rb").read()
        except OSError:
            raw = b'{"keys":[]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_body(self):
        encoding = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in encoding:
            chunks = []
            while True:
                line = self.rfile.readline().strip()
                size = int(line.split(b";", 1)[0] or b"0", 16)
                if size == 0:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                        pass
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.read(2)
            return b"".join(chunks)
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length) if length else b""

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
