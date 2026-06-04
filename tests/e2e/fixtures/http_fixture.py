"""A tiny stdlib HTTP service fixture (no third-party deps).

Serves /healthz -> 200 and /api/items -> 200 JSON; everything else 404.
Usage: python http_fixture.py --port <port>
"""

from __future__ import annotations

import argparse
import http.server
import json


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/healthz":
            self._send(200, b"ok")
        elif self.path == "/api/items":
            self._send(200, json.dumps({"items": [1, 2, 3]}).encode())
        else:
            self._send(404, b"not found")

    def _send(self, code: int, body: bytes):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # silence
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    args = ap.parse_args()
    server = http.server.HTTPServer(("127.0.0.1", args.port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
