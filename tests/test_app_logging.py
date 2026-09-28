from __future__ import annotations

import logging
import sys

import app_logging


def test_setup_logging_creates_rotating_file(tmp_path):
    path = app_logging.setup_logging(tmp_path, debug=True)
    assert path == tmp_path / "logs" / "app.log"
    logging.getLogger("t").info("hola-log")
    for h in logging.getLogger().handlers:
        h.flush()
    assert "hola-log" in path.read_text(encoding="utf-8")
    # idempotente: no duplica handlers
    n = len([h for h in logging.getLogger().handlers if getattr(h, "_dockerlabs", False)])
    app_logging.setup_logging(tmp_path, debug=True)
    assert n == len([h for h in logging.getLogger().handlers if getattr(h, "_dockerlabs", False)])


def test_excepthook_logs_without_dialog(tmp_path, monkeypatch):
    path = app_logging.setup_logging(tmp_path, debug=True)
    old = sys.excepthook
    try:
        app_logging.install_excepthook(show_dialog=False)
        try:
            raise ValueError("boom-excepthook")
        except ValueError:
            sys.excepthook(*sys.exc_info())
    finally:
        sys.excepthook = old
    for h in logging.getLogger().handlers:
        h.flush()
    txt = path.read_text(encoding="utf-8")
    assert "boom-excepthook" in txt and "Traceback" in txt
