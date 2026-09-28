"""DashboardPage.set_catalog: progreso, desglose por dificultad, últimas y ranking."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

import catalog as cat  # noqa: E402
from widgets.pages import DashboardPage, _LatestRow, _RankRow  # noqa: E402

_app = QApplication.instance() or QApplication([])  # noqa: F841


def _catalog():
    return cat.parse_catalog({
        "info_maquinas": [
            {"id": 1, "nombre": "A", "dificultad": "Fácil", "autor": "Z", "fecha": "10/08/2024"},
            {"id": 2, "nombre": "B", "dificultad": "Fácil", "autor": "Z", "fecha": "11/08/2024"},
            {"id": 3, "nombre": "C", "dificultad": "Difícil", "autor": "Y", "fecha": "01/01/2025"},
            {"id": 4, "nombre": "D", "dificultad": "Muy Fácil", "autor": "Y", "fecha": "02/01/2025"},
        ],
        "ranking_creadores": [{"nombre": "Y", "maquinas": 2}, {"nombre": "Z", "maquinas": 2}, {"nombre": "W", "maquinas": 0}],
        "writeups": {},
    })


def test_set_catalog_progress_and_breakdown():
    page = DashboardPage()
    page.set_catalog(_catalog(), {"A", "C", "Inexistente"})
    assert page.card_total.value_label.text() == "4"
    assert page.progress.value() == 50
    assert "2</b> de 4" in page.lbl_progress.text()
    assert page.diff_rows["Fácil"].lbl_count.text() == "1/2"
    assert page.diff_rows["Difícil"].lbl_count.text() == "1/1"
    assert page.diff_rows["Medio"].lbl_count.text() == "0/0"
    assert page.diff_rows["Muy Fácil"].bar.value() == 0
    latest = [w for w in page.panel_latest.findChildren(_LatestRow)]
    assert len(latest) == 4
    assert latest[0].findChild(QLabel).parent().findChildren(QLabel)[1].text() == "D"   # más reciente primero
    ranks = page.panel_ranking.findChildren(_RankRow)
    assert len(ranks) == 3


def test_set_completed_refreshes():
    page = DashboardPage()
    page.set_catalog(_catalog(), set())
    assert page.progress.value() == 0
    page.set_completed({"A", "B", "C", "D"})
    assert page.progress.value() == 100
    assert page.card_done.value_label.text() == "4"


def test_empty_catalog():
    page = DashboardPage()
    page.set_catalog(cat.parse_catalog({"info_maquinas": []}), set())
    assert page.progress.value() == 0
    assert page.lbl_progress.text() == "Catálogo vacío"
