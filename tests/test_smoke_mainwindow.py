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
    monkeypatch.setenv("HOME", str(tmp_path))
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
    w.labs.shutdown(); w.downloads.shutdown()
    w.close()
    _spin(100)
