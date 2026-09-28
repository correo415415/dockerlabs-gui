"""Logging a fichero rotativo + captura de excepciones no controladas.

- `setup_logging(app_dir)` → `~/.dockerlabs-gui/logs/app.log` (5 × 1 MiB) y consola
  (solo WARNING+ salvo `DOCKERLABS_DEBUG=1`).
- `install_excepthook(parent_getter)` → cualquier excepción que escape al bucle de
  eventos de Qt se registra y se muestra en un diálogo con el traceback y un botón
  para abrir la carpeta de logs, en lugar de matar la app en silencio.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Callable, Optional

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s"
_log_path: Optional[Path] = None


def setup_logging(app_dir: Path, debug: Optional[bool] = None) -> Path:
    """Configura el logger raíz. Idempotente. Devuelve la ruta del fichero."""
    global _log_path
    if debug is None:
        debug = os.environ.get("DOCKERLABS_DEBUG", "") not in ("", "0", "false")
    logs_dir = Path(app_dir) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "app.log"
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    if _log_path == path:
        return path
    for h in list(root.handlers):
        if getattr(h, "_dockerlabs", False):
            root.removeHandler(h)
    fh = logging.handlers.RotatingFileHandler(
        path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter(LOG_FORMAT))
    fh.setLevel(logging.DEBUG)
    fh._dockerlabs = True  # type: ignore[attr-defined]
    root.addHandler(fh)
    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    ch.setLevel(logging.DEBUG if debug else logging.WARNING)
    ch._dockerlabs = True  # type: ignore[attr-defined]
    root.addHandler(ch)
    # Ruido de librerías
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    _log_path = path
    logging.getLogger(__name__).info("---- inicio de sesión de log (%s) ----", path)
    return path


def log_path() -> Optional[Path]:
    return _log_path


def format_exception(exc_type, exc, tb) -> str:
    return "".join(traceback.format_exception(exc_type, exc, tb))


def install_excepthook(parent_getter: Optional[Callable[[], object]] = None,
                       show_dialog: bool = True) -> None:
    """Registra y muestra excepciones no capturadas (hilo principal y QThreads)."""
    logger = logging.getLogger("uncaught")

    def _show(text: str) -> None:
        if not show_dialog:
            return
        try:
            from PyQt6.QtWidgets import QApplication
            if QApplication.instance() is None:
                return
            from widgets.crash_dialog import show_crash_dialog
            parent = parent_getter() if parent_getter else None
            show_crash_dialog(text, _log_path, parent)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - nunca fallar dentro del hook
            logger.exception("no se pudo mostrar el diálogo de error")

    def hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        text = format_exception(exc_type, exc, tb)
        logger.error("Excepción no controlada:\n%s", text)
        _show(text)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        text = format_exception(args.exc_type, args.exc_value, args.exc_traceback)
        logger.error("Excepción no controlada en hilo %s:\n%s",
                     getattr(args.thread, "name", "?"), text)

    sys.excepthook = hook
    threading.excepthook = thread_hook
