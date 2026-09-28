from __future__ import annotations

import io
import json
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from lab_manager import (
    DockerClient,
    DockerInfo,
    DockerNotInstalled,
    DockerPermissionDenied,
    InvalidLabArchive,
    PortMapping,
    choose_network_strategy,
    container_name_for,
    describe_access,
    extract_lab,
    inspect_image_tar,
    plan_port_mappings,
    slug_from_name,
)

# ---------- fixtures: tar `docker save` sintético ----------

def make_image_tar(path: Path, repo_tag="breakmyssh:latest",
                   exposed=("22/tcp", "80/tcp")) -> Path:
    cfg = {
        "architecture": "amd64", "os": "linux",
        "config": {"ExposedPorts": {p: {} for p in exposed},
                   "Cmd": ["/usr/sbin/sshd", "-D"], "Entrypoint": None},
    }
    cfg_name = "abc123.json"
    manifest = [{"Config": cfg_name, "RepoTags": [repo_tag], "Layers": ["l1/layer.tar"]}]
    with tarfile.open(path, "w") as tf:
        for name, obj in (("manifest.json", manifest), (cfg_name, cfg)):
            data = json.dumps(obj).encode()
            ti = tarfile.TarInfo(name); ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        layer = b"\0" * 1024
        ti = tarfile.TarInfo("l1/layer.tar"); ti.size = len(layer)
        tf.addfile(ti, io.BytesIO(layer))
    return path


def make_lab_zip(path: Path, slug="breakmyssh") -> Path:
    tar = make_image_tar(path.parent / f"{slug}.tar")
    with zipfile.ZipFile(path, "w") as zf:
        zf.write(tar, f"{slug}.tar")
        zf.writestr("auto_deploy.sh", "#!/bin/bash\n")
    tar.unlink()
    return path


# ---------- helpers ----------

def test_slug_from_name():
    assert slug_from_name("Pequeñas-Mentirosas") == "pequenas-mentirosas"
    assert slug_from_name("Walking Dead") == "walking-dead"
    assert slug_from_name("404-not-found") == "404-not-found"
    assert container_name_for("psycho") == "dockerlabs_psycho"


def test_plan_ports_prefers_1to1(monkeypatch):
    import lab_manager
    monkeypatch.setattr(lab_manager, "_can_bind_privileged", lambda: True)
    plan = plan_port_mappings([(22, "tcp"), (80, "tcp")], is_free=lambda p: True)
    assert [(p.container_port, p.host_port) for p in plan] == [(22, 22), (80, 80)]
    assert plan[0].docker_arg == "127.0.0.1:22:22/tcp"


def test_plan_ports_fallback_when_busy(monkeypatch):
    import lab_manager
    monkeypatch.setattr(lab_manager, "_can_bind_privileged", lambda: True)
    busy = {22, 20022}
    plan = plan_port_mappings([(22, "tcp")], is_free=lambda p: p not in busy)
    assert plan[0].host_port == 20023


def test_plan_ports_unprivileged(monkeypatch):
    import lab_manager
    monkeypatch.setattr(lab_manager, "_can_bind_privileged", lambda: False)
    plan = plan_port_mappings([(22, "tcp"), (8080, "tcp")], is_free=lambda p: True)
    assert [(p.container_port, p.host_port) for p in plan] == [(22, 20022), (8080, 8080)]


def test_plan_ports_no_duplicates(monkeypatch):
    import lab_manager
    monkeypatch.setattr(lab_manager, "_can_bind_privileged", lambda: True)
    plan = plan_port_mappings([(80, "tcp"), (80, "udp")], is_free=lambda p: True)
    assert len({p.host_port for p in plan}) == 2


# ---------- extracción / inspección ----------

def test_extract_and_inspect(tmp_path):
    z = make_lab_zip(tmp_path / "breakmyssh.zip")
    files = extract_lab(z, tmp_path / "labs")
    assert files.slug == "breakmyssh"
    assert files.tar_path.name == "breakmyssh.tar"
    assert files.deploy_script and files.deploy_script.name == "auto_deploy.sh"
    # idempotente
    files2 = extract_lab(z, tmp_path / "labs")
    assert files2.tar_path == files.tar_path

    info = inspect_image_tar(files.tar_path)
    assert info.repo_tag == "breakmyssh:latest"
    assert info.image_name == "breakmyssh"
    assert info.exposed_ports == [(22, "tcp"), (80, "tcp")]
    assert info.architecture == "amd64"


def test_extract_rejects_non_zip(tmp_path):
    p = tmp_path / "x.zip"; p.write_bytes(b"nope")
    with pytest.raises(InvalidLabArchive):
        extract_lab(p, tmp_path / "labs")


def test_extract_rejects_zip_without_tar(tmp_path):
    p = tmp_path / "x.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("readme.txt", "hi")
    with pytest.raises(InvalidLabArchive):
        extract_lab(p, tmp_path / "labs")


def test_extract_rejects_traversal(tmp_path):
    p = tmp_path / "x.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("../evil.tar", "hi")
    with pytest.raises(InvalidLabArchive):
        extract_lab(p, tmp_path / "labs")


def test_inspect_tar_without_manifest(tmp_path):
    t = tmp_path / "bad.tar"
    with tarfile.open(t, "w") as tf:
        data = b"x"; ti = tarfile.TarInfo("foo"); ti.size = 1
        tf.addfile(ti, io.BytesIO(data))
    with pytest.raises(InvalidLabArchive):
        inspect_image_tar(t)


# ---------- DockerClient con runner falso ----------

class FakeRunner:
    def __init__(self, responses):
        self.responses = responses  # list of (predicate, rc, stdout, stderr)
        self.calls = []

    def __call__(self, cmd, timeout=30, input_data=None):
        self.calls.append(cmd)
        for pred, rc, out, err in self.responses:
            if pred(cmd):
                return subprocess.CompletedProcess(cmd, rc, out.encode(), err.encode())
        return subprocess.CompletedProcess(cmd, 0, b"", b"")


def test_docker_not_installed():
    c = DockerClient(binary="", runner=FakeRunner([]))
    info = c.info()
    assert not info.available and not info.running
    with pytest.raises(DockerNotInstalled):
        c.image_exists("x")


def test_docker_info_running():
    version = json.dumps({"Client": {"Version": "27.1", "Platform": {"Name": "Docker Engine - Community"}},
                          "Server": {"Version": "27.1", "Os": "linux", "Arch": "amd64",
                                     "Platform": {"Name": "Docker Engine - Community"}}})
    r = FakeRunner([(lambda c: c[1] == "version", 0, version, ""),
                    (lambda c: c[1] == "context", 0, "default\n", "")])
    info = DockerClient(binary="docker", runner=r).info()
    assert info.available and info.running and info.version == "27.1"
    assert not info.is_desktop


def test_docker_info_desktop():
    version = json.dumps({"Client": {"Version": "27.1"},
                          "Server": {"Version": "27.1", "Os": "linux", "Arch": "amd64",
                                     "Platform": {"Name": "Docker Desktop 4.30"}}})
    r = FakeRunner([(lambda c: c[1] == "version", 0, version, "")])
    info = DockerClient(binary="docker", runner=r).info()
    assert info.is_desktop
    assert choose_network_strategy(info) == "bridge+ports"


def test_docker_daemon_down():
    r = FakeRunner([(lambda c: c[1] == "version", 1, "",
                     "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?")])
    info = DockerClient(binary="docker", runner=r).info()
    assert info.available and not info.running
    assert "daemon" in info.error.lower()


def test_docker_permission_denied():
    r = FakeRunner([(lambda c: c[1] == "images", 1, "",
                     "permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock")])
    c = DockerClient(binary="docker", runner=r)
    with pytest.raises(DockerPermissionDenied):
        c._docker("images", "-q", "x")


def test_run_container_builds_command():
    from lab_manager import ImageInfo
    r = FakeRunner([(lambda c: c[1] == "run", 0, "abcdef123456\n", "")])
    c = DockerClient(binary="docker", runner=r)
    img = ImageInfo(repo_tag="psycho:latest", image_name="psycho", exposed_ports=[(80, "tcp")])
    cid = c.run_container(img, "Psycho", "psycho", ports=[PortMapping(80, "tcp", 8080)])
    assert cid == "abcdef123456"
    run_cmd = [cmd for cmd in r.calls if cmd[1] == "run"][0]
    assert "--name" in run_cmd and "dockerlabs_psycho" in run_cmd
    assert "-p" in run_cmd and "127.0.0.1:8080:80/tcp" in run_cmd
    assert run_cmd[-1] == "psycho:latest"
    # se hizo rm -f previo
    assert any(cmd[1] == "rm" for cmd in r.calls)


def test_run_container_host_mode():
    from lab_manager import ImageInfo
    r = FakeRunner([(lambda c: c[1] == "run", 0, "id\n", "")])
    c = DockerClient(binary="docker", runner=r)
    img = ImageInfo(repo_tag="x:latest", image_name="x", exposed_ports=[])
    c.run_container(img, "X", "x", network_mode="host")
    run_cmd = [cmd for cmd in r.calls if cmd[1] == "run"][0]
    assert "--network" in run_cmd and "host" in run_cmd and "-p" not in run_cmd


def test_parse_inspect_and_list():
    inspect = [{
        "Id": "0123456789abcdef", "Name": "/dockerlabs_psycho",
        "Config": {"Image": "psycho:latest", "Labels": {"es.dockerlabs.machine": "Psycho"}},
        "State": {"Status": "running"},
        "NetworkSettings": {"IPAddress": "", "Networks": {"bridge": {"IPAddress": "172.17.0.2"}},
                            "Ports": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}],
                                      "22/tcp": None}},
    }]
    r = FakeRunner([(lambda c: c[1] == "ps", 0, "0123456789ab\n", ""),
                    (lambda c: c[1] == "inspect", 0, json.dumps(inspect), "")])
    c = DockerClient(binary="docker", runner=r)
    lst = c.list_lab_containers()
    assert len(lst) == 1
    s = lst[0]
    assert s.name == "dockerlabs_psycho" and s.machine == "Psycho"
    assert s.ip == "172.17.0.2" and s.is_running
    assert s.ports == [PortMapping(80, "tcp", 8080)]
    assert "8080" in describe_access(s, "bridge+ports")
    assert "172.17.0.2" in describe_access(s, "bridge")


def test_choose_network_strategy_linux_native(monkeypatch):
    import lab_manager
    monkeypatch.setattr(lab_manager.platform, "system", lambda: "Linux")
    info = DockerInfo(available=True, running=True, is_desktop=False)
    assert choose_network_strategy(info) == "bridge"
    assert choose_network_strategy(info, "host") == "host"
    monkeypatch.setattr(lab_manager.platform, "system", lambda: "Windows")
    assert choose_network_strategy(info, "host") == "bridge+ports"
