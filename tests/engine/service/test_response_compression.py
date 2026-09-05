import gzip
import io
import tempfile
from pathlib import Path

import engine_service


class _Handler:
    def __init__(self, accept_encoding=""):
        self.headers = {"Accept-Encoding": accept_encoding}
        self.wfile = io.BytesIO()
        self.sent_headers = {}
        self.config = {"allowInsecureAuth": True}
        self.agent_public_url = ""
        self.request_version = "HTTP/1.1"
        self.command = "POST"

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.sent_headers[name] = value

    def end_headers(self):
        pass


def test_large_json_response_uses_deterministic_gzip_when_accepted():
    handler = _Handler("br, gzip; q=1.0")
    engine_service.response_json(handler, 200, {"value": "x" * (70 * 1024)})
    assert handler.sent_headers["Content-Encoding"] == "gzip"
    assert handler.sent_headers["Vary"] == "Accept-Encoding"
    assert gzip.decompress(handler.wfile.getvalue()).startswith(b'{"value":"')


def test_json_file_respects_gzip_quality_zero():
    handler = _Handler("gzip;q=0, br")
    with tempfile.TemporaryDirectory() as root:
        path = Path(root) / "payload.json"
        payload = b'{"value":"' + (b"x" * (70 * 1024)) + b'"}'
        path.write_bytes(payload)
        engine_service.response_json_file(handler, 200, path)
    assert "Content-Encoding" not in handler.sent_headers
    assert handler.wfile.getvalue() == payload
