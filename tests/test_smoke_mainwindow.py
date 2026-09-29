"""Smoke offscreen de la ventana completa (sin red: el catálogo puede fallar, no debe romper)."""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


def _spin(ms):
    loop = QEventLoop(); QTimer.singleShot(ms, loop.quit); loop.exec()


def test_mainwindow_boots(tmp_path, monkeypatch):
    # Path.home() usa HOME en POSIX y USERPROFILE en Windows.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # Sin red: una petición real a /api con reintentos (4,5 s de sleeps + timeout 30 s)
    # dejaría el CatalogWorker vivo al cerrar y Qt abortaría el proceso al salir
    # (así falló la CI en Windows aunque todos los tests pasaran).
    import dockerlabs_api

    def _offline(self, *_a, **_k):
        raise dockerlabs_api.DockerLabsError("sin red (test)")
    monkeypatch.setattr(dockerlabs_api.DockerLabsClient, "_request", _offline)
    import catalog
    _orig_refresh = catalog.CatalogStore.refresh
    monkeypatch.setattr(catalog.CatalogStore, "refresh",
                        lambda self, fetch, **_k: _orig_refresh(self, fetch, retries=0, sleep=lambda _s: None))
    import importlib

    import main as m
    importlib.reload(m)
    _app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
    w = m.MainWindow()
    w.show()
    _spin(1500)
    for key in ("dashboard", "machines", "downloads", "lab", "completed", "settings", "about"):
        w._go(key)
        _spin(20)
    # señales nuevas del sidebar
    got = []
    w.sidebar.profile_clicked.connect(lambda: got.append(1))
    w.sidebar.profile_clicked.emit()
    assert got
    w.close()          # closeEvent cierra labs, descargas, sesión, catálogo y workers
    _spin(200)
    assert not w.catalogs.refreshing
    assert len(w._workers) == 0  # noqa: SLF001
