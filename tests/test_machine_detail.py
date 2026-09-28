from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")
from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import catalog as cat  # noqa: E402
from widgets.machine_detail import MachineDetailPanel, stars  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


def _spin(ms=50):
    loop = QEventLoop(); QTimer.singleShot(ms, loop.quit); loop.exec()


def _png_bytes() -> bytes:
    from PyQt6.QtCore import QBuffer, QIODevice
    from PyQt6.QtGui import QColor, QImage
    img = QImage(8, 8, QImage.Format.Format_ARGB32)
    img.fill(QColor("#22d3ee"))
    buf = QBuffer(); buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


class FakeClient:
    def fetch_bytes(self, url):
        return _png_bytes()

    def machine_rating(self, name):
        return {"average": 4.7, "count": 52,
                "details": {"dificultad": 4.4, "aprendizaje": 4.8}, "user_rating": None}


def test_stars():
    assert stars(4.7) == "★★★★★" and stars(0) == "☆☆☆☆☆" and stars(2.4) == "★★☆☆☆"


def test_detail_panel_shows_machine(app):
    c = cat.parse_catalog({"info_maquinas": [
        {"id": 1, "nombre": "Psycho", "dificultad": "Fácil", "autor": "Luis", "enlace_autor": "https://x",
         "fecha": "10/08/2024", "descripcion": "LFI", "link_descarga": "https://d/psycho.zip"}],
        "writeups": {"textos": [{"id": 1, "maquina": "Psycho", "autor": "w", "url": "https://gh", "tipo": "texto"}],
                     "videos": []}})
    panel = MachineDetailPanel(client=FakeClient())
    m = c.machines[0]
    panel.show_machine(m, c.writeups_for(m.name))
    assert panel.lbl_title.text() == "Psycho"
    assert "Writeups (1)" in panel.lbl_wu_title.text()
    for _ in range(40):
        _spin(50)
        if "4.7" in panel.lbl_rating.text() and panel.img.pixmap() and not panel.img.pixmap().isNull():
            break
    assert "4.7" in panel.lbl_rating.text() and "52" in panel.lbl_rating.text()
    assert panel.img.pixmap() is not None and not panel.img.pixmap().isNull()

    got = []
    panel.request_download.connect(lambda n, u: got.append(("dl", n, u)))
    panel.request_launch.connect(lambda n: got.append(("launch", n)))
    panel.request_toggle_completed.connect(lambda n: got.append(("toggle", n)))
    panel.set_status(done=False, downloading=False, downloaded=False, running=False)
    assert panel.btn_download.isVisibleTo(panel) and not panel.btn_launch.isVisibleTo(panel)
    panel.btn_download.click()
    panel.set_status(done=True, downloading=False, downloaded=True, running=False)
    assert panel.btn_launch.isVisibleTo(panel) and "Desmarcar" in panel.btn_done.text()
    panel.btn_launch.click(); panel.btn_done.click()
    assert got == [("dl", "Psycho", "https://d/psycho.zip"), ("launch", "Psycho"), ("toggle", "Psycho")]
    panel.shutdown()
