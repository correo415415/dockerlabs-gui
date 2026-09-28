from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import catalog as cat  # noqa: E402
from widgets.machine_model import (  # noqa: E402
    COL_DATE,
    COL_DIFF,
    COL_NAME,
    ROLE_STATE,
    MachineFilterProxy,
    MachineTableModel,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


def machines():
    raw = {"info_maquinas": [
        {"id": 1, "nombre": "Psycho", "dificultad": "Fácil", "autor": "Luis", "fecha": "10/08/2024", "descripcion": "LFI"},
        {"id": 2, "nombre": "Hard", "dificultad": "Difícil", "autor": "Z", "fecha": "31/12/2024"},
        {"id": 3, "nombre": "Baby", "dificultad": "Muy Fácil", "autor": "Z", "fecha": "01/01/2023"},
    ]}
    return cat.parse_catalog(raw).machines


def test_model_and_proxy(app):
    model = MachineTableModel()
    model.set_machines(machines())
    proxy = MachineFilterProxy()
    proxy.setSourceModel(model)
    assert proxy.rowCount() == 3

    proxy.sort(COL_DIFF, Qt.SortOrder.AscendingOrder)
    names = [proxy.index(i, COL_NAME).data() for i in range(3)]
    assert names == ["Baby", "Psycho", "Hard"]

    proxy.sort(COL_DATE, Qt.SortOrder.DescendingOrder)
    assert proxy.index(0, COL_NAME).data() == "Hard"

    proxy.set_query("lfi")
    assert proxy.rowCount() == 1 and proxy.index(0, COL_NAME).data() == "Psycho"
    proxy.set_query("")
    proxy.set_difficulty("Difícil")
    assert proxy.rowCount() == 1
    proxy.set_difficulty("Todas")

    changes = []
    model.dataChanged.connect(lambda a, b, r=None: changes.append((a.row(), b.row())))
    model.set_completed({"Psycho"})
    assert changes and changes[-1] == (0, 0)
    proxy.set_state("Completadas")
    assert proxy.rowCount() == 1
    proxy.set_state("Pendientes")
    assert proxy.rowCount() == 2
    proxy.set_state("Todas")

    model.set_downloaded({"Hard"}); model.set_running({"Hard"})
    idx = proxy.index([proxy.index(i, COL_NAME).data() for i in range(3)].index("Hard"), 1)
    assert idx.data(ROLE_STATE) == "running"
    proxy.set_state("En ejecución")
    assert proxy.rowCount() == 1 and proxy.machine_at(proxy.index(0, 0)).name == "Hard"
