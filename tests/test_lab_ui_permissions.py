"""Smoke UI (offscreen): la página Laboratorio reacciona al estado de permisos."""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402
from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402

import lab_controller  # noqa: E402
from lab_manager import DockerInfo  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


def _spin(ms=50):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_lab_page_permission_states(app):
    from widgets.lab_page import LabPage
    page = LabPage()
    denied = DockerInfo(available=True, running=False, permission_denied=True,
                        can_elevate=True, socket_path="/var/run/docker.sock",
                        error="Sin permiso")
    page.set_docker_info(denied, "instala", "pulsa Conceder acceso")
    assert page.btn_grant.isVisibleTo(page) and not page.btn_start_service.isVisibleTo(page)
    assert "permiso" in page.lbl_docker_title.text().lower()

    denied_no_pk = DockerInfo(available=True, running=False, permission_denied=True,
                              can_elevate=False)
    page.set_docker_info(denied_no_pk, "", "usa usermod")
    assert not page.btn_grant.isVisibleTo(page)
    assert "usermod" in page.lbl_docker_sub.text()

    root = DockerInfo(available=True, running=True, version="27", permission_denied=True,
                      is_root=True, server_os="linux", server_arch="amd64")
    page.set_docker_info(root, "", "")
    assert "root" in page.lbl_docker_title.text() and not page.btn_grant.isVisibleTo(page)

    stopped = DockerInfo(available=True, running=False, service_state="inactive",
                         can_elevate=True, error="daemon parado")
    page.set_docker_info(stopped, "", "")
    assert page.btn_start_service.isVisibleTo(page) and not page.btn_grant.isVisibleTo(page)

    page.set_elevating(True)
    assert not page.btn_grant.isEnabled() and "autorizaci" in page.lbl_docker_title.text().lower()
    page.set_elevating(False)
    assert page.btn_refresh.isEnabled()


def test_controller_grant_access_flow(app, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(lab_controller, "grant_docker_access",
                        lambda sock="": (calls.append(sock) or (True, "Acceso concedido. ok")))
    infos = iter([DockerInfo(available=True, running=True, version="27")])
    monkeypatch.setattr(lab_controller.DockerClient, "info",
                        lambda self: next(infos, DockerInfo(True, True, version="27")))
    monkeypatch.setattr(lab_controller.DockerClient, "list_lab_containers", lambda self: [])

    ctl = lab_controller.LabController(tmp_path)
    ctl.client.binary = "docker"
    ctl.docker_info = DockerInfo(available=True, running=False, permission_denied=True,
                                 can_elevate=True, socket_path="/run/docker.sock")
    results = []
    ctl.elevation_started.connect(lambda a: results.append(("started", a)))
    ctl.elevation_done.connect(lambda a, ok, d: results.append(("done", a, ok)))
    refreshed = []
    ctl.docker_info_changed.connect(lambda i: refreshed.append(i))

    assert ctl.grant_access() is True
    for _ in range(60):
        _spin(50)
        if refreshed:
            break
    ctl.shutdown()
    assert calls == ["/run/docker.sock"]
    assert ("started", "grant") in results
    assert ("done", "grant", True) in results
    assert refreshed and refreshed[0].running
