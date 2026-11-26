#!/usr/bin/env python3
"""Physical trading time series. One caller owns each series type."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8080
# type -> {"owner": str, "points": [point, ...]}
SERIES = {}


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
        prefix = "/time-series/"
        if path.startswith(prefix) and path != prefix:
            caller = self.caller()
            if not caller:
                self.send_json(401, {"error": "missing caller"})
                return
            kind = path[len(prefix):]
            item = SERIES.get(kind)
            if item is None or item["owner"] != caller:
                self.send_json(404, {"error": "not found"})
                return
            self.send_json(200, {"type": kind, "owner": caller, "points": item["points"]})
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/time-series":
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
        kind = body.get("type")
        if not isinstance(kind, str) or not kind or any(k not in body for k in ("instrument", "ts", "value")):
            self.send_json(400, {"error": "type, instrument, ts, and value are required"})
            return
        item = SERIES.get(kind)
        if item is not None and item["owner"] != caller:
            self.send_json(409, {"error": "series type is owned by another customer"})
            return
        if item is None:
            item = {"owner": caller, "points": []}
            SERIES[kind] = item
        item["points"].append(
            {"instrument": body["instrument"], "ts": body["ts"], "value": body["value"]}
        )
        self.send_json(200, {"type": kind, "owner": caller, "points": item["points"]})

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
