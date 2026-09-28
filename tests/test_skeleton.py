"""Tests del estado de carga (skeleton/spinner) de la página Máquinas."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from widgets.pages import MachinesPage  # noqa: E402
from widgets.skeleton import LoadingPanel, SkeletonRows, Spinner  # noqa: E402

_app = QApplication.instance() or QApplication([])  # noqa: F841


class _Cat:
    def __init__(self, machines):
        self.machines = machines

    def by_name(self):
        return {}

    def writeups_for(self, _n):
        return []


def test_skeleton_paints_and_animates():
    sk = SkeletonRows(rows=3)
    sk.resize(400, 200); sk.show()
    _app.processEvents()
    before = sk._phase  # noqa: SLF001
    sk._tick(); sk._tick()  # noqa: SLF001
    assert sk._phase != before  # noqa: SLF001
    pm = sk.grab()
    assert not pm.isNull() and pm.width() == 400
    sk.hide()
    assert not sk._timer.isActive()  # noqa: SLF001


def test_spinner_rotates():
    sp = Spinner(20); sp.show(); _app.processEvents()
    a = sp._angle; sp._tick()  # noqa: SLF001
    assert sp._angle == (a + 6) % 360  # noqa: SLF001
    assert not sp.grab().isNull()


def test_loading_panel_text():
    lp = LoadingPanel(rows=2)
    lp.set_text("Descargando…")
    assert lp.label.text() == "Descargando…"


def test_machines_page_loading_states():
    page = MachinesPage(client=None)
    page.resize(900, 600); page.show(); _app.processEvents()
    page.set_loading(True, "Descargando catálogo…")
    assert page.is_loading and not page.table.isVisible() and not page.empty.isVisible()
    assert page.loading.label.text() == "Descargando catálogo…"
    assert not page.btn_refresh.isEnabled()
    # termina sin catálogo -> mensaje de vacío
    page.set_loading(False)
    assert not page.is_loading and page.empty.isVisible()
    assert page.btn_refresh.isEnabled()
    # llega catálogo -> tabla visible, skeleton oculto
    page.set_loading(True)
    page.set_catalog(_Cat([]))
    assert not page.is_loading
    page.hide()
