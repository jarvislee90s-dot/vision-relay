"""Slim standalone conftest for the proxy capability (only what proxy tests need)."""

import json
import threading

import pytest


class RecordingUpstream:
    """Minimal local HTTP upstream stub shared by proxy server/integration tests."""

    def __init__(self):
        self.received: list[dict] = []
        self.received_headers: list[dict] = []
        self._server = None
        self.port = None
        self.content = "ok"

    def start(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        up = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                up.received.append(json.loads(self.rfile.read(length)))
                up.received_headers.append(dict(self.headers))
                payload = json.dumps({"choices": [{"message": {"content": up.content}}]}, ensure_ascii=False).encode(
                    "utf-8"
                )
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self._server.shutdown()


@pytest.fixture()
def upstream():
    u = RecordingUpstream().start()
    yield u
    u.stop()
