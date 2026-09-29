"""Read-only fixture server for isolated Compose smoke tests. Never use as inventory."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

fixtures = Path(__file__).resolve().parents[1] / "fixtures"
device = json.loads((fixtures / "device.json").read_text())
ip = json.loads((fixtures / "ip.json").read_text())


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("Authorization") != "Token fixture-only":
            self.send_error(403)
            return
        resources = {
            "/api/dcim/devices/": {"results": [device], "next": None},
            "/api/dcim/devices/1/": device,
            "/api/ipam/ip-addresses/10/": ip,
        }
        result = resources.get(urlsplit(self.path).path)
        if result is None:
            self.send_error(404)
            return
        data = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
