"""Tests del descargador HTTP con un servidor local que imita a
gestion-maquinas.dockerlabs.es (GET only, Content-Disposition, 500 intermitentes)."""
from __future__ import annotations

import io
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event

import pytest

from http_downloader import (
    DownloadCancelled,
    DownloadIntegrityError,
    DownloadNotFound,
    DownloadServerError,
    HttpDownloader,
    UnsupportedLinkError,
    filename_from_headers,
    is_direct_http_link,
    sanitize_filename,
)


def make_zip(payload: bytes = b"x" * 5000) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("auto_deploy.sh", "#!/bin/bash\necho hi\n")
        zf.writestr("machine.tar", payload)
    return buf.getvalue()


ZIP_BYTES = make_zip()


class _State:
    fail_first = 0        # nº de 500 antes de servir OK
    truncate = False      # cortar cuerpo a la mitad
    html = False          # devolver html con 200
    hits = 0


class Handler(BaseHTTPRequestHandler):
    state = _State()

    def log_message(self, *a):  # silencio
        pass

    def do_HEAD(self):
        self.send_response(405)
        self.send_header("Allow", "GET")
        self.end_headers()

    def do_GET(self):
        st = self.state
        st.hits += 1
        if self.path.endswith("/noexiste.zip"):
            body = b'{"detail":"No encontrado"}'
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if st.fail_first > 0:
            st.fail_first -= 1
            body = b"Internal Server Error"
            self.send_response(500)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if st.html:
            body = b"<html><body>captcha</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        name = self.path.rsplit("/", 1)[-1]
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(ZIP_BYTES)))
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.end_headers()
        data = ZIP_BYTES[: len(ZIP_BYTES) // 2] if st.truncate else ZIP_BYTES
        try:
            self.wfile.write(data)
        except BrokenPipeError:
            pass


@pytest.fixture
def server():
    Handler.state = _State()
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def dl(**kw) -> HttpDownloader:
    return HttpDownloader(max_retries=kw.pop("max_retries", 4), sleep=lambda s: None, **kw)


# ---------- helpers ----------

def test_is_direct_http_link():
    assert is_direct_http_link("https://gestion-maquinas.dockerlabs.es/dl/psycho.zip")
    assert not is_direct_http_link("https://mega.nz/file/abc#key")
    assert not is_direct_http_link("")
    assert not is_direct_http_link("ftp://x/y.zip")


def test_filename_from_headers():
    assert filename_from_headers({"Content-Disposition": 'attachment; filename="psycho.zip"'},
                                 "http://x/dl/other.zip") == "psycho.zip"
    assert filename_from_headers({"Content-Disposition": "attachment; filename*=UTF-8''a%20b.zip"},
                                 "http://x/dl/other.zip") == "a b.zip"
    assert filename_from_headers({}, "http://x/dl/trust.zip") == "trust.zip"


def test_sanitize_filename():
    assert sanitize_filename('a<b>:"/\\|?*.zip') == "a_b________.zip"
    assert sanitize_filename("") == "dockerlabs_machine.zip"


# ---------- descargas ----------

def test_download_ok(server, tmp_path):
    infos, progress = [], []
    out = dl().download(f"{server}/dl/psycho.zip", tmp_path,
                        on_info=infos.append, on_progress=progress.append)
    assert out == tmp_path / "psycho.zip"
    assert out.read_bytes() == ZIP_BYTES
    assert infos[0].name == "psycho.zip" and infos[0].size == len(ZIP_BYTES)
    assert progress[-1].state == "done"
    assert not list(tmp_path.glob("*.part"))


def test_retries_on_500(server, tmp_path):
    Handler.state.fail_first = 2
    out = dl().download(f"{server}/dl/trust.zip", tmp_path)
    assert out.exists()
    assert Handler.state.hits == 3


def test_persistent_500_raises(server, tmp_path):
    Handler.state.fail_first = 99
    with pytest.raises(DownloadServerError):
        dl(max_retries=3).download(f"{server}/dl/trust.zip", tmp_path)
    assert Handler.state.hits == 3
    assert not list(tmp_path.iterdir())


def test_404_no_retry(server, tmp_path):
    with pytest.raises(DownloadNotFound):
        dl().download(f"{server}/dl/noexiste.zip", tmp_path)
    assert Handler.state.hits == 1


def test_truncated_then_ok(server, tmp_path):
    """Un cuerpo truncado dispara integridad → reintento."""
    Handler.state.truncate = True

    class Once(HttpDownloader):
        def _attempt(self, *a, **k):
            try:
                return super()._attempt(*a, **k)
            finally:
                Handler.state.truncate = False

    d = Once(max_retries=3, sleep=lambda s: None)
    out = d.download(f"{server}/dl/x.zip", tmp_path)
    assert out.read_bytes() == ZIP_BYTES
    assert Handler.state.hits == 2


def test_html_response_is_server_error(server, tmp_path):
    Handler.state.html = True
    with pytest.raises(DownloadServerError):
        dl(max_retries=2).download(f"{server}/dl/x.zip", tmp_path)


def test_unsupported_link(tmp_path):
    with pytest.raises(UnsupportedLinkError):
        dl().download("https://mega.nz/file/abc#def", tmp_path)


def test_cancel_before_start(server, tmp_path):
    ev = Event()
    ev.set()
    with pytest.raises(DownloadCancelled):
        dl().download(f"{server}/dl/x.zip", tmp_path, cancel_event=ev)


def test_unique_path_when_exists(server, tmp_path):
    (tmp_path / "psycho.zip").write_bytes(b"old")
    out = dl().download(f"{server}/dl/psycho.zip", tmp_path)
    assert out.name == "psycho (1).zip"


def test_verify_zip_rejects_garbage(tmp_path):
    from http_downloader import verify_zip
    p = tmp_path / "bad.zip"
    p.write_bytes(b"Internal Server Error")
    with pytest.raises(DownloadIntegrityError):
        verify_zip(p)
