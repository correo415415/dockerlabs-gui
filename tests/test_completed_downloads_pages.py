"""CompletedPage (búsqueda/agrupación/desmarcar) y DownloadsPage (scroll + limpiar terminadas)."""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

import catalog as cat  # noqa: E402
from download_manager import DownloadState  # noqa: E402
from widgets.pages import CompletedPage, DownloadsPage  # noqa: E402

_app = QApplication.instance() or QApplication([])  # noqa: F841


def _catalog():
    return cat.parse_catalog({"info_maquinas": [
        {"id": 1, "nombre": "Alpha", "dificultad": "Fácil", "autor": "Z", "fecha": "10/08/2024"},
        {"id": 2, "nombre": "Beta", "dificultad": "Difícil", "autor": "Z", "fecha": "11/08/2024"},
        {"id": 3, "nombre": "Gamma", "dificultad": "Fácil", "autor": "Y", "fecha": "01/01/2025"},
    ], "writeups": {}})


def _machine_items(page):
    lw = page.list_widget
    return [lw.item(i).data(CompletedPage.ROLE_NAME) for i in range(lw.count()) if lw.item(i).data(CompletedPage.ROLE_NAME)]


def _texts(page):
    lw = page.list_widget
    return [lw.item(i).text() for i in range(lw.count())]


def test_completed_grouping_and_search():
    page = CompletedPage()
    page.set_items({"Beta", "Alpha", "Desconocida"})
    assert _machine_items(page) == ["Alpha", "Beta", "Desconocida"]     # sin catálogo → lista plana ordenada
    page.set_catalog(_catalog())
    texts = _texts(page)
    assert texts[0].startswith("Fácil") and "1" in texts[0]
    assert any(t.startswith("Difícil") for t in texts)
    assert any(t.startswith("Sin clasificar") for t in texts)
    assert _machine_items(page) == ["Alpha", "Beta", "Desconocida"]
    page.search.setText("bet")
    assert _machine_items(page) == ["Beta"]
    assert "1 coinciden" in page.lbl.text()
    page.search.setText("zzz")
    assert _machine_items(page) == [] and "zzz" in _texts(page)[0]
    page.chk_group.setChecked(False)
    page.search.setText("")
    assert _texts(page)[0].strip() == "Alpha"


def test_completed_signals():
    page = CompletedPage()
    page.set_items(["Alpha"])
    got = []
    page.request_open_machine.connect(got.append)
    page._on_double_click(page.list_widget.item(0))
    assert got == ["Alpha"]
    page.set_items([])
    assert "0 máquinas" in page.lbl.text()


def _S(machine, state):
    return DownloadState(machine=machine, url="http://x/" + machine, dest_dir=Path("/tmp"), state=state)


def test_downloads_clear_button():
    page = DownloadsPage()
    assert not page.btn_clear.isEnabled()
    page.render_states([_S("a", "running"), _S("b", "done"), _S("c", "error")])
    assert page.btn_clear.isEnabled()
    assert "(2)" in page.btn_clear.text()
    assert page.scroll.isVisibleTo(page)
    fired = []
    page.request_clear_finished.connect(lambda: fired.append(1))
    page.btn_clear.click()
    assert fired == [1]
    page.render_states([_S("a", "running")])
    assert not page.btn_clear.isEnabled()
    assert set(page._widgets) == {"a"}
    page.render_states([])
    assert page.empty.isVisibleTo(page) and not page.scroll.isVisibleTo(page)


def test_settings_max_concurrent_spinner():
    from widgets.pages import SettingsPage

    page = SettingsPage()
    got = []
    page.request_set_max_concurrent.connect(got.append)
    page.set_state("/tmp", True, True, False, "auto", max_concurrent=4)
    assert page.spin_concurrent.value() == 4 and got == []          # set_state no emite
    page.spin_concurrent.setValue(3)
    assert got == [3]
    page.set_state("/tmp", True, True, False, "auto", max_concurrent=99)
    assert page.spin_concurrent.value() == 6                        # clamp 1..6
