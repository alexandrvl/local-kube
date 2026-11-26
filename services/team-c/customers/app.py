#!/usr/bin/env python3
"""Customer records. The first caller to write a customer name owns that record."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8080
# name -> {"owner": str, "name": str}
CUSTOMERS = {}


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
        prefix = "/customers/"
        if path.startswith(prefix) and path != prefix:
            caller = self.caller()
            if not caller:
                self.send_json(401, {"error": "missing caller"})
                return
            name = path[len(prefix):]
            item = CUSTOMERS.get(name)
            if item is None or item["owner"] != caller:
                self.send_json(404, {"error": "not found"})
                return
            self.send_json(200, {"name": name, "owner": caller})
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/customers":
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
        name = body.get("name")
        if not isinstance(name, str) or not name:
            self.send_json(400, {"error": "name is required"})
            return
        item = CUSTOMERS.get(name)
        if item is not None and item["owner"] != caller:
            self.send_json(409, {"error": "customer record is owned by another customer"})
            return
        CUSTOMERS[name] = {"owner": caller}
        self.send_json(200, {"name": name, "owner": caller})

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
