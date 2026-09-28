"""Diálogo «Permitir acceso a Docker» y flujo sudo en LabController (sin red ni docker)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import lab_controller as lc  # noqa: E402
import lab_manager as lm  # noqa: E402
from widgets.docker_access_dialog import CHOICE_GROUP, CHOICE_SUDO, DockerAccessDialog  # noqa: E402

_app = QApplication.instance() or QApplication([])  # noqa: F841


def test_dialog_defaults_to_sudo_and_requires_password():
    d = DockerAccessDialog(None, can_sudo=True, can_elevate=True)
    assert d.opt_sudo.isChecked() and d.in_pwd.isEnabled()
    assert d.btn_ok.text() == "Usar sudo"
    d._accept()                      # sin contraseña → no cierra
    assert d.choice == "" and d.result() == 0
    d.in_pwd.setText("s3cret")
    d._accept()
    assert d.choice == CHOICE_SUDO and d.password == "s3cret"


def test_dialog_group_option():
    d = DockerAccessDialog(None, can_sudo=True, can_elevate=True)
    d.opt_group.setChecked(True)
    assert not d.in_pwd.isEnabled()
    assert "sistema" in d.btn_ok.text()
    d._accept()
    assert d.choice == CHOICE_GROUP and d.password == ""


def test_dialog_without_sudo_preselects_group():
    d = DockerAccessDialog(None, can_sudo=False, can_elevate=True)
    assert not d.opt_sudo.isEnabled() and d.opt_group.isChecked()
    d2 = DockerAccessDialog(None, can_sudo=False, can_elevate=False)
    assert not d2.btn_ok.isEnabled()


def _pump(ctrl, ms=3000):
    import time
    deadline = time.time() + ms / 1000
    while time.time() < deadline:
        QCoreApplication.processEvents()
        if not ctrl._workers.any_running():  # noqa: SLF001
            break
        time.sleep(0.01)
    QCoreApplication.processEvents()


def test_controller_use_sudo_stores_password_only_on_success(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(lc, "verify_sudo_password", lambda pw: (pw == "ok", "msg"))
    ctrl = lc.LabController(tmp_path)
    ctrl.client = lm.DockerClient(binary="", runner=lambda c, timeout=30, input_data=None: None)
    ctrl.elevation_done.connect(lambda a, ok, d: calls.append((a, ok)))
    monkeypatch.setattr(ctrl, "refresh_docker_info", lambda: None)

    assert ctrl.use_sudo("bad")
    _pump(ctrl)
    assert calls == [("sudo", False)] and not ctrl.via_sudo

    assert ctrl.use_sudo("ok")
    _pump(ctrl)
    assert calls[-1] == ("sudo", True) and ctrl.via_sudo
    ctrl.forget_sudo()
    assert not ctrl.via_sudo
    ctrl.shutdown()
