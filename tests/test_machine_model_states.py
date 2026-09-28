"""El proxy de la tabla re-evalúa filtro/orden cuando cambian los estados (descargada, etc.)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from catalog import Machine  # noqa: E402
from widgets.machine_model import COL_STATE, MachineFilterProxy, MachineTableModel  # noqa: E402

_app = QApplication.instance() or QApplication([])  # noqa: F841


def _m(i, name):
    return Machine(id=i, name=name, difficulty="Fácil", difficulty_raw="facil", css_class="",
                   color="", author="a", author_url="", date="2024", date_iso=f"2024-01-0{i}",
                   description="", download_url="", image_url="", authors=[])


def test_filter_downloaded_updates_dynamically():
    model = MachineTableModel()
    proxy = MachineFilterProxy()
    proxy.setSourceModel(model)
    model.set_machines([_m(1, "Trust"), _m(2, "Pinguinazo"), _m(3, "Grandma")])
    proxy.set_state("Descargadas")
    assert proxy.rowCount() == 0
    model.set_downloaded({"Trust"})
    assert proxy.rowCount() == 1
    assert proxy.data(proxy.index(0, 2)) == "Trust"
    model.set_downloaded(set())
    assert proxy.rowCount() == 0


def test_sort_by_state_updates_dynamically():
    model = MachineTableModel()
    proxy = MachineFilterProxy()
    proxy.setSourceModel(model)
    model.set_machines([_m(1, "Trust"), _m(2, "Pinguinazo")])
    proxy.sort(COL_STATE, Qt.SortOrder.AscendingOrder)
    names = lambda: [proxy.data(proxy.index(r, 2)) for r in range(proxy.rowCount())]  # noqa: E731
    assert names()[0] == "Trust"
    model.set_downloaded({"Pinguinazo"})     # descargada → primero
    assert names()[0] == "Pinguinazo"
