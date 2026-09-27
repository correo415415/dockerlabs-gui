"""Notificaciones del sistema operativo (Windows / Linux / macOS).

Detecta automáticamente el backend disponible. Si nada funciona, devuelve
False y el caller puede mostrar un toast in-app como fallback.

Backends en orden de preferencia:
- Windows: ``winotify`` (PyPI) → balloon/Win10 toast nativo.
- Linux:   binario ``notify-send`` (paquete libnotify-bin).
- macOS:   AppleScript via ``osascript``.
"""
from __future__ import annotations

import logging
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _try_winotify(title: str, body: str, app_id: str) -> bool:
    try:
        from winotify import Notification  # type: ignore
    except Exception:
        return False
    try:
        n = Notification(app_id=app_id, title=title, msg=body)
        n.show()
        return True
    except Exception:
        logger.exception("winotify failed")
        return False


def _try_plyer(title: str, body: str, app_id: str) -> bool:
    """Fallback multiplataforma vía plyer si está instalado."""
    try:
        from plyer import notification  # type: ignore
    except Exception:
        return False
    try:
        notification.notify(title=title, message=body, app_name=app_id, timeout=4)
        return True
    except Exception:
        logger.exception("plyer notify failed")
        return False


def _try_notify_send(title: str, body: str, app_id: str) -> bool:
    binary = shutil.which("notify-send")
    if not binary:
        return False
    try:
        subprocess.Popen(
            [binary, "--app-name", app_id, title, body],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        logger.exception("notify-send failed")
        return False


def _try_osascript(title: str, body: str, app_id: str) -> bool:
    binary = shutil.which("osascript")
    if not binary:
        return False
    safe_title = title.replace('"', '\\"')
    safe_body = body.replace('"', '\\"')
    safe_app = app_id.replace('"', '\\"')
    script = (
        f'display notification "{safe_body}" with title "{safe_title}" '
        f'subtitle "{safe_app}"'
    )
    try:
        subprocess.Popen(
            [binary, "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        logger.exception("osascript failed")
        return False


def notify_os(title: str, body: str = "",
              app_id: str = "DockerLabs GUI") -> bool:
    """Envía una notificación nativa. Devuelve True si llegó al SO.

    Si todos los backends fallan devuelve False; en ese caso el caller debe
    encargarse de mostrar un toast in-app como fallback.
    """
    system = platform.system()

    if system == "Windows":
        if _try_winotify(title, body, app_id):
            return True
        if _try_plyer(title, body, app_id):
            return True
        return False

    if system == "Linux":
        if _try_notify_send(title, body, app_id):
            return True
        if _try_plyer(title, body, app_id):
            return True
        return False

    if system == "Darwin":
        if _try_osascript(title, body, app_id):
            return True
        if _try_plyer(title, body, app_id):
            return True
        return False

    return _try_plyer(title, body, app_id)


def os_backend_available() -> bool:
    """Comprueba si hay backend nativo disponible (rápido, no envía nada)."""
    system = platform.system()
    if system == "Windows":
        try:
            import winotify  # noqa: F401
            return True
        except Exception:
            pass
    elif system == "Linux":
        if shutil.which("notify-send"):
            return True
    elif system == "Darwin":
        if shutil.which("osascript"):
            return True
    try:
        import plyer  # noqa: F401
        return True
    except Exception:
        return False
