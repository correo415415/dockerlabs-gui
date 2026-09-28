"""Tests de CatalogController (caché + refresco en hilo) sin red."""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication  # noqa: E402

from catalog_controller import CatalogController  # noqa: E402

_app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841

API = {"info_maquinas": [
    {"id": 1, "nombre": "Trust", "dificultad": "Muy Fácil", "autor": "a", "fecha": "01/01/2024"},
    {"id": 2, "nombre": "Grandma", "dificultad": "Difícil", "autor": "b", "fecha": "02/01/2024"},
]}


def _pump(ctrl, ms=3000):
    deadline = time.time() + ms / 1000
    while time.time() < deadline and ctrl.refreshing:
        QCoreApplication.processEvents(); time.sleep(0.01)
    for _ in range(20):
        QCoreApplication.processEvents()
    ctrl.shutdown()   # nunca dejar hilos vivos al destruir el controlador


def test_no_cache_then_refresh_saves(tmp_path):
    path = tmp_path / "catalog.json"
    ctrl = CatalogController(path, lambda: API)
    loads, cats = [], []
    ctrl.loading.connect(lambda a, m: loads.append((a, m)))
    ctrl.catalog_changed.connect(cats.append)
    assert ctrl.load_cached() is False
    assert loads[-1][0] is True and "catálogo" in loads[-1][1].lower()
    assert ctrl.refresh() is True
    assert ctrl.refresh() is False          # una sola descarga a la vez
    _pump(ctrl)
    assert len(cats) == 1 and len(cats[0].machines) == 2
    assert ctrl.catalog is cats[0]
    assert loads[-1] == (False, "")
    assert path.exists()


def test_cache_used_when_refresh_fails(tmp_path):
    path = tmp_path / "catalog.json"
    warm = CatalogController(path, lambda: API)
    warm.refresh(); _pump(warm)

    def boom():
        raise OSError("sin red")
    ctrl = CatalogController(path, boom)
    fails = []
    ctrl.refresh_failed.connect(lambda e, c: fails.append((e, c)))
    assert ctrl.load_cached() is True
    assert ctrl.catalog is not None and len(ctrl.catalog.machines) == 2
    ctrl.refresh(); _pump(ctrl, 8000)
    assert len(fails) == 1
    assert fails[0][1] is True              # seguimos con caché
    assert "sin red" in fails[0][0]


def test_failure_without_cache(tmp_path):
    def boom():
        raise OSError("offline")
    ctrl = CatalogController(tmp_path / "c.json", boom)
    fails = []
    ctrl.refresh_failed.connect(lambda e, c: fails.append((e, c)))
    ctrl.refresh(); _pump(ctrl, 8000)
    assert fails and fails[0][1] is False
