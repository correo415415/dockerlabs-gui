"""Tests del cliente HTTP (requests.Session) contra un servidor local: keep-alive,
cookies, CSRF y login sin tocar la red."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dockerlabs_api import DockerLabsClient, DockerLabsError
from settings_store import UserSettings


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    hits: list[str] = []

    def log_message(self, *_a):  # silencio
        pass

    def _send(self, code: int, body: bytes, ctype="text/html", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        _Handler.hits.append(self.path)
        if self.path == "/login":
            self._send(200, b'<html><meta name="csrf-token" content="CSRF123"></html>',
                       extra={"Set-Cookie": "session=abc; Path=/; HttpOnly"})
        elif self.path == "/api":
            self._send(200, json.dumps({"info_maquinas": []}).encode(), "application/json")
        else:
            self._send(404, b"{}", "application/json")

    def do_POST(self):  # noqa: N802
        _Handler.hits.append(self.path)
        n = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(n) or b"{}")
        ok = (self.headers.get("X-CSRFToken") == "CSRF123"
              and "session=abc" in (self.headers.get("Cookie") or "")
              and payload.get("password") == "pw")
        body = json.dumps({"success": ok, "message": "ok" if ok else "bad"}).encode()
        self._send(200 if ok else 401, body, "application/json")


@pytest.fixture
def server():
    _Handler.hits = []
    # Threading + daemon: con keep-alive (HTTP/1.1) un servidor mono-hilo se
    # quedaría bloqueado en la conexión persistente y shutdown() no volvería.
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_login_flow_with_cookies_and_csrf(server):
    c = DockerLabsClient(base_url=server)
    res = c.login("neo", "pw")
    assert res.success and res.session_cookie == "abc" and res.csrf_token == "CSRF123"
    assert c.session_cookie_value() == "abc"
    assert _Handler.hits == ["/login", "/api/auth/login"]
    bad = c.login("neo", "nope")
    assert not bad.success and bad.message == "bad"


def test_fetch_api_and_errors(server):
    c = DockerLabsClient(base_url=server)
    assert c.fetch_api_data() == {"info_maquinas": []}
    c2 = DockerLabsClient(base_url="http://127.0.0.1:9", timeout=2)
    c2.CONNECT_TIMEOUT = 0.5
    with pytest.raises(DockerLabsError):
        c2.fetch_api_data()


def test_inject_cookie_and_close(server):
    c = DockerLabsClient(base_url=server)
    c.inject_session_cookie("zzz")
    assert c.session_cookie_value() == "zzz"
    c.close()


def test_settings_network_defaults_to_auto():
    assert UserSettings.from_dict({}).docker_network == "auto"
    assert UserSettings.from_dict({"docker_network": "host"}).docker_network == "host"
    assert UserSettings.from_dict({"docker_network": "banana"}).docker_network == "auto"
    assert UserSettings.from_dict({"docker_network": None}).docker_network == "auto"
