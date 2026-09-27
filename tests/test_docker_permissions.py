"""Detección de permisos sobre el socket de Docker y elevación (Linux)."""
from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

import pytest

import lab_manager as lm
from lab_manager import DockerClient, DockerInfo


class Runner:
    def __init__(self, rc=0, out="", err=""):
        self.rc, self.out, self.err = rc, out, err
        self.calls = []

    def __call__(self, cmd, timeout=30, input_data=None):
        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, self.rc, self.out.encode(), self.err.encode())


@pytest.fixture
def unix_socket(tmp_path):
    path = tmp_path / "docker.sock"
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    yield path
    srv.close()


def test_socket_access_states(tmp_path, unix_socket):
    assert lm.socket_access("") == "ok"
    assert lm.socket_access(str(tmp_path / "nope.sock")) == "missing"
    plain = tmp_path / "file"; plain.write_text("x")
    assert lm.socket_access(str(plain)) == "missing"
    assert lm.socket_access(str(unix_socket)) == "ok"


def test_socket_access_denied(monkeypatch, unix_socket):
    monkeypatch.setattr(os, "access", lambda p, m: False)
    assert lm.socket_access(str(unix_socket)) == "denied"


def test_docker_socket_path_from_env(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "unix:///tmp/x.sock")
    assert lm.docker_socket_path() == "/tmp/x.sock"
    monkeypatch.setenv("DOCKER_HOST", "tcp://1.2.3.4:2375")
    assert lm.docker_socket_path() == ""
    monkeypatch.delenv("DOCKER_HOST")
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert lm.docker_socket_path() == "/var/run/docker.sock"


def test_docker_socket_path_from_context(monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    r = Runner(0, "unix:///home/u/.docker/desktop/docker.sock\n")
    c = DockerClient(binary="docker", runner=r)
    assert lm.docker_socket_path(c) == "/home/u/.docker/desktop/docker.sock"
    assert r.calls[0][1:3] == ["context", "inspect"]


def test_info_permission_denied_precheck(monkeypatch, unix_socket):
    """Si el socket existe pero no es accesible → permission_denied sin llamar a docker."""
    monkeypatch.setattr(lm.platform, "system", lambda: "Linux")
    monkeypatch.setattr(lm, "docker_socket_path", lambda client=None: str(unix_socket))
    monkeypatch.setattr(lm, "socket_access", lambda s: "denied")
    monkeypatch.setattr(lm, "is_root", lambda: False)
    monkeypatch.setattr(lm, "elevation_available", lambda: True)
    monkeypatch.setattr(lm, "docker_service_state", lambda: "active")
    r = Runner(0, "{}")
    info = DockerClient(binary="docker", runner=r).info()
    assert info.available and not info.running
    assert info.permission_denied and info.needs_elevation and info.can_elevate
    assert info.socket_path == str(unix_socket)
    assert info.service_state == "active"
    assert not any(c[1] == "version" for c in r.calls)
    assert "Conceder acceso" in info.error


def test_info_permission_denied_from_stderr(monkeypatch):
    monkeypatch.setattr(lm.platform, "system", lambda: "Linux")
    monkeypatch.setattr(lm, "docker_socket_path", lambda client=None: "/var/run/docker.sock")
    monkeypatch.setattr(lm, "socket_access", lambda s: "ok")
    monkeypatch.setattr(lm, "is_root", lambda: False)
    monkeypatch.setattr(lm, "elevation_available", lambda: False)
    monkeypatch.setattr(lm, "docker_service_state", lambda: "active")
    r = Runner(1, "", "permission denied while trying to connect to the Docker daemon "
                      "socket at unix:///var/run/docker.sock: Get http://...: dial unix ...")
    info = DockerClient(binary="docker", runner=r).info()
    assert info.permission_denied and info.needs_elevation and not info.can_elevate


def test_info_root_does_not_need_elevation(monkeypatch):
    monkeypatch.setattr(lm.platform, "system", lambda: "Linux")
    monkeypatch.setattr(lm, "docker_socket_path", lambda client=None: "/var/run/docker.sock")
    monkeypatch.setattr(lm, "socket_access", lambda s: "denied")
    monkeypatch.setattr(lm, "is_root", lambda: True)
    monkeypatch.setattr(lm, "elevation_available", lambda: True)
    monkeypatch.setattr(lm, "docker_service_state", lambda: "active")
    info = DockerClient(binary="docker", runner=Runner(0, "{}")).info()
    assert info.permission_denied and info.is_root and not info.needs_elevation


def test_elevation_command_prefers_pkexec(monkeypatch):
    monkeypatch.setattr(lm.platform, "system", lambda: "Linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(lm.shutil, "which", lambda n: "/usr/bin/pkexec" if n == "pkexec" else None)
    assert lm.elevation_command() == ["/usr/bin/pkexec"]


def test_elevation_command_sudo_askpass(monkeypatch, tmp_path):
    monkeypatch.setattr(lm.platform, "system", lambda: "Linux")
    monkeypatch.setenv("DISPLAY", ":0")
    ask = tmp_path / "ssh-askpass"; ask.write_text("#!/bin/sh\n"); ask.chmod(0o755)
    monkeypatch.setenv("SUDO_ASKPASS", str(ask))
    monkeypatch.setattr(lm.shutil, "which", lambda n: "/usr/bin/sudo" if n == "sudo" else None)
    assert lm.elevation_command() == ["/usr/bin/sudo", "-A"]


def test_elevation_command_needs_display(monkeypatch):
    monkeypatch.setattr(lm.platform, "system", lambda: "Linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(lm.shutil, "which", lambda n: "/usr/bin/pkexec")
    assert lm.elevation_command() == []


def test_elevation_command_not_linux(monkeypatch):
    monkeypatch.setattr(lm.platform, "system", lambda: "Windows")
    assert lm.elevation_command() == []
    assert lm.grant_docker_access()[0] is False


def test_grant_docker_access_ok(monkeypatch):
    monkeypatch.setattr(lm.platform, "system", lambda: "Linux")
    monkeypatch.setattr(lm, "elevation_command", lambda: ["/usr/bin/pkexec"])
    r = Runner(0, "GRANT_OK\n")
    ok, msg = lm.grant_docker_access(sock="/var/run/docker.sock", user="alice", runner=r)
    assert ok, msg
    cmd = r.calls[0]
    assert cmd[0] == "/usr/bin/pkexec" and cmd[1:3] == ["/bin/sh", "-c"]
    assert "usermod -aG docker" in cmd[3] and "setfacl" in cmd[3]
    assert cmd[-2:] == ["alice", "/var/run/docker.sock"]


def test_grant_docker_access_cancelled(monkeypatch):
    monkeypatch.setattr(lm.platform, "system", lambda: "Linux")
    monkeypatch.setattr(lm, "elevation_command", lambda: ["/usr/bin/pkexec"])
    ok, msg = lm.grant_docker_access(user="alice", runner=Runner(126, "", "Request dismissed"))
    assert not ok and "cancelada" in msg.lower()


def test_grant_docker_access_no_elevation(monkeypatch):
    monkeypatch.setattr(lm.platform, "system", lambda: "Linux")
    monkeypatch.setattr(lm, "elevation_command", lambda: [])
    ok, msg = lm.grant_docker_access(user="alice", runner=Runner(0, "GRANT_OK"))
    assert not ok and "usermod" in msg


def test_start_docker_service(monkeypatch):
    monkeypatch.setattr(lm.platform, "system", lambda: "Linux")
    monkeypatch.setattr(lm, "is_root", lambda: False)
    monkeypatch.setattr(lm, "elevation_command", lambda: ["/usr/bin/pkexec"])
    monkeypatch.setattr(lm.shutil, "which", lambda n: "/bin/systemctl" if n == "systemctl" else None)
    r = Runner(0)
    ok, _ = lm.start_docker_service(runner=r)
    assert ok and r.calls[0] == ["/usr/bin/pkexec", "/bin/systemctl", "start", "docker"]


def test_permission_hint():
    assert "root" in lm.permission_hint(DockerInfo(True, False, is_root=True))
    assert "Conceder acceso" in lm.permission_hint(DockerInfo(True, False, can_elevate=True))
    assert "usermod" in lm.permission_hint(DockerInfo(True, False, can_elevate=False))
