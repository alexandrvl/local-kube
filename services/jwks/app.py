#!/usr/bin/env python3
"""Public keys for one team. The token controller writes the file."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8080
JWKS_PATH = "/etc/jwks/jwks.json"
EMPTY = b'{"keys":[]}'


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def send_bytes(self, code, raw, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self.send_bytes(200, b'{"ok":true}', "application/json")
            return
        if path == "/.well-known/jwks.json":
            try:
                raw = open(JWKS_PATH, "rb").read()
            except OSError:
                raw = EMPTY
            self.send_bytes(200, raw, "application/json")
            return
        self.send_bytes(404, b'{"error":"not found"}', "application/json")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
