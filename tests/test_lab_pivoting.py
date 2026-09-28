"""Labs multi-tar (pivoting, p.ej. Grandma): plan, redes y despliegue con un Docker falso."""
import subprocess
from pathlib import Path

import pytest

import lab_manager as lm


def _img(tag, arch="amd64", ports=()):
    return lm.ImageInfo(repo_tag=tag, image_name=tag.split(":")[0], exposed_ports=list(ports),
                        architecture=arch)


def test_pivoting_networks_match_official_script():
    nets = lm.pivoting_networks(3, prefix="dockerlabs_grandma_")
    assert [n.name for n in nets] == ["dockerlabs_grandma_pivoting1", "dockerlabs_grandma_pivoting2",
                                      "dockerlabs_grandma_pivoting3"]
    assert [n.subnet for n in nets] == ["10.10.10.0/24", "20.20.20.0/24", "30.30.30.0/24"]
    assert nets[0].gateway == "10.10.10.1" and nets[2].gateway == "30.30.30.1"
    assert nets[0].driver == "bridge" and not nets[0].internal
    assert nets[1].driver == "macvlan" and nets[1].internal
    a = nets[1].create_args()
    assert a[:2] == ["network", "create"] and "-d" in a and "macvlan" in a and "--internal" in a
    assert "--attachable" in nets[0].create_args() and f"{lm.LABEL_KEY}=1" in a


def test_build_plan_single_and_multi(tmp_path):
    f1 = lm.LabFiles(slug="trust", root=tmp_path, tar_path=tmp_path / "trust.tar", deploy_script=None)
    assert f1.tar_paths == [tmp_path / "trust.tar"] and not f1.is_multi
    p1 = lm.build_deploy_plan(f1, "Trust", "bridge", images=[_img("trust:latest")])
    assert p1.container_names == ["dockerlabs_trust"] and not p1.networks and not p1.is_multi

    tars = [tmp_path / f"grandma{i}.tar" for i in (1, 2, 3)]
    f3 = lm.LabFiles(slug="grandma", root=tmp_path, tar_path=tars[0], deploy_script=None, tar_paths=tars)
    p3 = lm.build_deploy_plan(f3, "Grandma", "bridge",
                              images=[_img(f"grandma{i}:latest") for i in (1, 2, 3)])
    assert p3.is_multi
    assert p3.container_names == ["dockerlabs_grandma_1", "dockerlabs_grandma_2", "dockerlabs_grandma_3"]
    assert [n.name for n in p3.networks] == [f"dockerlabs_grandma_pivoting{i}" for i in (1, 2, 3)]


def test_natural_tar_order(tmp_path):
    import zipfile
    z = tmp_path / "x.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for n in ("grandma10.tar", "grandma2.tar", "grandma1.tar", "auto_deploy.sh"):
            zf.writestr(n, b"x" * 10)
    files = lm.extract_lab(z, tmp_path / "labs", slug="grandma")
    assert [t.name for t in files.tar_paths] == ["grandma1.tar", "grandma2.tar", "grandma10.tar"]
    assert files.is_multi and files.deploy_script is not None


class FakeDocker:
    """Runner que registra los comandos docker y simula respuestas mínimas."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on          # substring de comando que debe fallar
        self.containers = set()
        self.networks = set()

    def __call__(self, cmd, timeout=30, input_data=None):
        args = cmd[1:]
        self.calls.append(args)
        joined = " ".join(args)
        if self.fail_on and self.fail_on in joined:
            return subprocess.CompletedProcess(cmd, 1, b"", b"boom")
        out = b""
        if args[:2] == ["images", "-q"]:
            out = b"abc123\n"                                      # imagen ya cargada
        elif args[0] == "run":
            self.containers.add(args[args.index("--name") + 1]); out = b"cid\n"
        elif args[:2] == ["network", "create"]:
            self.networks.add(args[-1])
        elif args[:2] == ["network", "inspect"]:
            out = b"[]"
        elif args[:2] == ["network", "rm"]:
            self.networks.discard(args[2])
        elif args[0] == "rm":
            self.containers.discard(args[-1])
        elif args[0] == "inspect":
            names = args[1:]
            out = ("[" + ",".join(
                f'{{"Name":"/{n}","Config":{{"Image":"img","Labels":{{"{lm.LABEL_SLUG}":"grandma"}}}},'
                f'"State":{{"Status":"running"}},"NetworkSettings":{{"IPAddress":"","Networks":'
                f'{{"dockerlabs_grandma_pivoting{i + 1}":{{"IPAddress":"{(i + 1) * 10}.0.0.2"}}}}}}}}'
                for i, n in enumerate(names)) + "]").encode()
        elif args[:2] == ["ps", "-a"]:
            out = "\n".join(sorted(self.containers)).encode()
        elif args[:2] == ["network", "ls"]:
            out = "\n".join(sorted(self.networks)).encode()
        return subprocess.CompletedProcess(cmd, 0, out, b"")


def test_deploy_plan_multi_creates_chain():
    fake = FakeDocker()
    client = lm.DockerClient(binary="docker", runner=fake)
    imgs = [_img(f"grandma{i}:latest") for i in (1, 2)]
    files = lm.LabFiles(slug="grandma", root=Path("/x"), tar_path=Path("/x/grandma1.tar"),
                        deploy_script=None, tar_paths=[Path("/x/grandma1.tar"), Path("/x/grandma2.tar")])
    plan = lm.build_deploy_plan(files, "Grandma", "bridge", images=imgs)
    log = []
    statuses = lm.deploy_plan(client, plan, on_line=log.append, tar_paths=files.tar_paths)
    assert len(statuses) == 2 and statuses[0].network_ips == {"dockerlabs_grandma_pivoting1": "10.0.0.2"}
    joined = [" ".join(c) for c in fake.calls]
    assert any(c.startswith("network create --subnet 10.10.10.0/24") for c in joined)
    assert any(c.startswith("network create --subnet 20.20.20.0/24") and "macvlan" in c for c in joined)
    runs = [c for c in joined if c.startswith("run ")]
    assert "--network dockerlabs_grandma_pivoting1 --name dockerlabs_grandma_1" in runs[0].replace("  ", " ") \
        or ("--network" in runs[0] and "dockerlabs_grandma_1" in runs[0])
    assert any(c == "network connect dockerlabs_grandma_pivoting2 dockerlabs_grandma_1" for c in joined)
    assert not any(c.startswith("network connect") and c.endswith("dockerlabs_grandma_2") for c in joined)
    assert f"{lm.LABEL_SLUG}=grandma" in " ".join(runs[0].split())
    text = lm.describe_access_multi(statuses, "bridge")
    assert "Máquina 1: 10.0.0.2 (pivoting1)" in text and "Máquina 2" in text


def test_deploy_plan_rolls_back_only_own_resources():
    fake = FakeDocker(fail_on="--name dockerlabs_grandma_2")
    client = lm.DockerClient(binary="docker", runner=fake)
    imgs = [_img(f"grandma{i}:latest") for i in (1, 2)]
    files = lm.LabFiles(slug="grandma", root=Path("/x"), tar_path=Path("/x/g1.tar"), deploy_script=None,
                        tar_paths=[Path("/x/g1.tar"), Path("/x/g2.tar")])
    plan = lm.build_deploy_plan(files, "Grandma", "bridge", images=imgs)
    with pytest.raises(lm.LabError):
        lm.deploy_plan(client, plan, tar_paths=files.tar_paths)
    joined = [" ".join(c) for c in fake.calls]
    assert "rm -f dockerlabs_grandma_1" in joined
    assert "network rm dockerlabs_grandma_pivoting1" in joined and "network rm dockerlabs_grandma_pivoting2" in joined
    # Nunca el `docker rm $(docker ps -aq)` del script oficial:
    assert not any(c.startswith("container ") or c == "ps -aq" for c in joined)


def test_teardown_lab_scoped_to_slug():
    fake = FakeDocker()
    fake.containers = {"dockerlabs_grandma_1", "dockerlabs_grandma_2"}
    fake.networks = {"dockerlabs_grandma_pivoting1", "dockerlabs_grandma_pivoting2", "dockerlabs_otro_pivoting1"}
    client = lm.DockerClient(binary="docker", runner=fake)
    client.teardown_lab("grandma", image_tags=["grandma1:latest", ""])
    joined = [" ".join(c) for c in fake.calls]
    assert "rm -f dockerlabs_grandma_1" in joined and "rm -f dockerlabs_grandma_2" in joined
    assert "network rm dockerlabs_grandma_pivoting1" in joined
    assert "network rm dockerlabs_otro_pivoting1" not in joined
    assert "rmi -f grandma1:latest" in joined and "rmi -f " not in [c for c in joined if c.endswith("rmi -f ")]


def test_needs_amd64_emulation(monkeypatch):
    monkeypatch.setattr(lm.platform, "machine", lambda: "x86_64")
    assert not lm.needs_amd64_emulation(_img("a:1"))
    monkeypatch.setattr(lm.platform, "machine", lambda: "arm64")
    assert lm.needs_amd64_emulation(_img("a:1"))
    assert lm.needs_amd64_emulation(_img("a:1", arch=""))
    assert not lm.needs_amd64_emulation(_img("a:1", arch="arm64"))
