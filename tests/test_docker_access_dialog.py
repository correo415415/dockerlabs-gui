"""Diálogo «Permitir acceso a Docker» y flujo sudo en LabController (sin red ni docker)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import lab_controller as lc  # noqa: E402
import lab_manager as lm  # noqa: E402
from widgets.docker_access_dialog import (  # noqa: E402
    CHOICE_GROUP,
    CHOICE_SUDO,
    CHOICE_TEMP,
    DockerAccessDialog,
)

_app = QApplication.instance() or QApplication([])  # noqa: F841


def test_dialog_defaults_to_temp_with_system_dialog():
    d = DockerAccessDialog(None, can_sudo=True, can_elevate=True)
    assert d.opt_temp.isChecked() and d.opt_temp.isEnabled() and d.opt_group.isEnabled()
    # con pkexec disponible no se pide contraseña en la app
    assert not d.opt_sudo.isVisibleTo(d) and not d.in_pwd.isEnabled()
    assert d.btn_ok.text() == "Abrir diálogo del sistema"
    d._accept()
    assert d.choice == CHOICE_TEMP and d.password == ""


def test_dialog_group_option():
    d = DockerAccessDialog(None, can_sudo=True, can_elevate=True)
    d.opt_group.setChecked(True)
    assert not d.in_pwd.isEnabled()
    assert "sistema" in d.btn_ok.text()
    d._accept()
    assert d.choice == CHOICE_GROUP and d.password == ""


def test_dialog_sudo_fallback_without_pkexec():
    d = DockerAccessDialog(None, can_sudo=True, can_elevate=False)
    assert not d.opt_temp.isEnabled() and not d.opt_group.isEnabled()
    assert d.opt_sudo.isVisibleTo(d) and d.opt_sudo.isChecked() and d.in_pwd.isEnabled()
    assert d.btn_ok.text() == "Usar sudo"
    d._accept()                      # sin contraseña → no cierra
    assert d.choice == "" and d.result() == 0
    d.in_pwd.setText("s3cret")
    d._accept()
    assert d.choice == CHOICE_SUDO and d.password == "s3cret"


def test_dialog_nothing_available():
    d = DockerAccessDialog(None, can_sudo=False, can_elevate=False)
    assert not d.btn_ok.isEnabled() and not d.opt_sudo.isVisibleTo(d)


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


def test_controller_grant_temp_access_runs_worker(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(lc, "grant_temp_docker_access", lambda sock="": (True, "Acceso temporal concedido."))
    ctrl = lc.LabController(tmp_path)
    ctrl.client = lm.DockerClient(binary="", runner=lambda c, timeout=30, input_data=None: None)
    ctrl.elevation_done.connect(lambda a, ok, d: calls.append((a, ok, d)))
    monkeypatch.setattr(ctrl, "refresh_docker_info", lambda: None)
    assert ctrl.grant_temp_access()
    assert not ctrl.grant_temp_access()          # ya hay una en curso
    _pump(ctrl)
    assert calls == [("temp", True, "Acceso temporal concedido.")]
    assert not ctrl.via_sudo                     # no guarda contraseña alguna
    ctrl.shutdown()
