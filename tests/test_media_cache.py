"""Caché en disco de imágenes/valoraciones y su uso en el panel de detalle."""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import media_cache as mc  # noqa: E402
from catalog import Machine  # noqa: E402
from widgets.machine_detail import MachineDetailPanel  # noqa: E402

_app = QApplication.instance() or QApplication([])  # noqa: F841



def _png_bytes() -> bytes:
    from PyQt6.QtCore import QBuffer, QIODevice
    from PyQt6.QtGui import QColor, QImage
    img = QImage(4, 4, QImage.Format.Format_ARGB32)
    img.fill(QColor("#43959b"))
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


PNG = _png_bytes()


def test_image_roundtrip(tmp_path):
    c = mc.MediaCache(tmp_path / "cache")
    assert c.get_image("https://x/img/1") is None
    c.put_image("https://x/img/1", PNG)
    assert c.get_image("https://x/img/1") == PNG
    files = list((tmp_path / "cache" / "images").iterdir())
    assert len(files) == 1 and files[0].suffix == ".png"
    c.put_image("https://x/img/2", b"")           # vacío: se ignora
    assert c.get_image("https://x/img/2") is None
    assert c.size_bytes() == len(PNG)
    c.clear()
    assert c.get_image("https://x/img/1") is None


def test_rating_ttl(tmp_path, monkeypatch):
    c = mc.MediaCache(tmp_path / "cache")
    assert c.get_rating("Trust") == (None, False)
    c.put_rating("Trust", {"average": 4.5, "count": 3})
    data, fresh = c.get_rating("Trust")
    assert data["count"] == 3 and fresh
    # otra instancia lee del disco
    c2 = mc.MediaCache(tmp_path / "cache")
    assert c2.get_rating("Trust")[0]["average"] == 4.5
    # caducada → data sigue disponible pero fresh=False
    future = time.time() + mc.RATING_TTL + 1
    monkeypatch.setattr(mc.time, "time", lambda: future)
    data, fresh = c2.get_rating("Trust")
    assert data is not None and not fresh


def test_corrupt_ratings_file(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    (root / "ratings.json").write_text("{not json", encoding="utf-8")
    c = mc.MediaCache(root)
    assert c.get_rating("x") == (None, False)
    c.put_rating("x", {"average": 1})
    assert c.get_rating("x")[0] == {"average": 1}


class _Client:
    base_url = "https://dockerlabs.es"

    def __init__(self):
        self.image_calls = 0
        self.rating_calls = 0

    def fetch_bytes(self, url):
        self.image_calls += 1
        return PNG

    def machine_rating(self, name):
        self.rating_calls += 1
        return {"average": 4.0, "count": 2, "details": {}}


def _machine(name="Trust"):
    return Machine(id=1, name=name, difficulty="Fácil", difficulty_raw="facil", css_class="",
                   color="", author="a", author_url="", date="2024", date_iso="2024-01-01",
                   description="d", download_url="", image_url=f"https://dockerlabs.es/img/{name}",
                   authors=[])


def _pump(panel, ms=2000):
    deadline = time.time() + ms / 1000
    while time.time() < deadline:
        QCoreApplication.processEvents()
        if not panel._workers.any_running():  # noqa: SLF001
            break
        time.sleep(0.01)
    QCoreApplication.processEvents()


def test_detail_panel_uses_disk_cache(tmp_path):
    cache = mc.MediaCache(tmp_path / "cache")
    cli = _Client()
    p = MachineDetailPanel(client=cli, media_cache=cache)
    p.show_machine(_machine(), [])
    _pump(p)
    assert cli.image_calls == 1 and cli.rating_calls == 1
    assert "4.0" in p.lbl_rating.text()
    assert cache.get_image("https://dockerlabs.es/img/Trust") == PNG
    assert cache.get_rating("Trust")[0]["count"] == 2
    p.shutdown()

    # Nueva sesión (otro panel, misma caché): la valoración sale al instante, sin
    # pasar por "Valoración: …", y la imagen no vuelve a pedirse a la red.
    cli2 = _Client()
    p2 = MachineDetailPanel(client=cli2, media_cache=cache)
    p2.show_machine(_machine(), [])
    assert "4.0" in p2.lbl_rating.text()          # antes de que corra ningún hilo
    _pump(p2)
    assert cli2.image_calls == 0                   # imagen desde disco
    assert cli2.rating_calls == 0                  # valoración fresca (TTL): sin red
    assert p2.img.pixmap() is not None and not p2.img.pixmap().isNull()
    p2.shutdown()
