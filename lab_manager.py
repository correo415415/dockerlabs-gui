"""Lanzador de laboratorios (CTF) de DockerLabs sobre Docker — multiplataforma.

Formato de una máquina (analizado a partir de los zips oficiales):

    <slug>.zip
    ├── <slug>.tar        ← imagen Docker exportada con `docker save`
    └── auto_deploy.sh    ← script bash oficial (docker load + run + inspect IP)

Este módulo reproduce lo que hace `auto_deploy.sh` pero:

* sin depender de bash (funciona en Windows / macOS / Linux),
* sin `sudo` (si el socket no es accesible, se informa y se sugiere la solución),
* con soporte de publicación de puertos: en Docker Desktop (Windows/macOS) la IP
  del bridge NO es alcanzable desde el host, así que publicamos los
  `ExposedPorts` de la imagen en `127.0.0.1`.

No depende de Qt para poder testearse con pytest. Toda interacción con Docker
se hace a través del CLI `docker` (JSON), que es lo único garantizado en todas
las instalaciones (Docker Engine, Docker Desktop, Podman con alias, WSL2…).
"""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

CONTAINER_PREFIX = "dockerlabs_"
LABEL_KEY = "es.dockerlabs.gui"
LABEL_MACHINE = "es.dockerlabs.machine"
DOCKER_TIMEOUT = 30

# En Windows, ocultar la ventana de consola que aparece al lanzar subprocesos
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# =====================================================================
# Errores
# =====================================================================

class LabError(Exception):
    """Base."""


class DockerNotInstalled(LabError):
    """No se encontró el binario `docker` en el PATH."""


class DockerNotRunning(LabError):
    """El daemon no responde (Docker Desktop cerrado, servicio parado…)."""


class DockerPermissionDenied(LabError):
    """El usuario no puede hablar con el socket (falta grupo `docker`)."""


class InvalidLabArchive(LabError):
    """El zip no contiene un `.tar` de imagen Docker válido."""


class LabCancelled(LabError):
    """Operación cancelada por el usuario."""


# =====================================================================
# Modelos
# =====================================================================

@dataclass
class DockerInfo:
    available: bool
    running: bool
    version: str = ""
    server_os: str = ""
    server_arch: str = ""
    is_desktop: bool = False          # Docker Desktop (Win/mac/Linux)
    is_wsl: bool = False              # cliente corriendo dentro de WSL
    error: str = ""

    @property
    def bridge_ip_reachable(self) -> bool:
        """En Linux nativo (Engine) el host llega a la IP del bridge.
        En Docker Desktop (mac/Win, y también Desktop en Linux) no."""
        return self.running and platform.system() == "Linux" and not self.is_desktop


@dataclass
class ImageInfo:
    repo_tag: str                     # p.ej. 'breakmyssh:latest'
    image_name: str                   # 'breakmyssh'
    exposed_ports: List[Tuple[int, str]]  # [(22,'tcp'), (80,'tcp')]
    architecture: str = ""
    os: str = ""
    cmd: List[str] = field(default_factory=list)
    entrypoint: List[str] = field(default_factory=list)


@dataclass
class LabFiles:
    slug: str
    root: Path
    tar_path: Path
    deploy_script: Optional[Path]


@dataclass
class PortMapping:
    container_port: int
    proto: str
    host_port: int

    @property
    def docker_arg(self) -> str:
        return f"127.0.0.1:{self.host_port}:{self.container_port}/{self.proto}"


@dataclass
class ContainerStatus:
    name: str
    machine: str
    image: str
    status: str            # running | exited | created | paused | ...
    ip: str = ""
    ports: List[PortMapping] = field(default_factory=list)
    container_id: str = ""

    @property
    def is_running(self) -> bool:
        return self.status == "running"


# =====================================================================
# Utilidades
# =====================================================================

def slug_from_name(name: str) -> str:
    """'Pequeñas-Mentirosas' -> 'pequenas-mentirosas' (como los zips oficiales)."""
    import unicodedata
    dec = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in dec if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9._-]+", "-", s).strip("-")
    return s or "machine"


def container_name_for(slug: str) -> str:
    return f"{CONTAINER_PREFIX}{slug}"


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def plan_port_mappings(exposed: Iterable[Tuple[int, str]],
                       is_free: Callable[[int], bool] = is_port_free,
                       fallback_base: int = 20000) -> List[PortMapping]:
    """Asigna puertos del host para cada puerto expuesto.

    Preferimos el mapeo 1:1 (22→22) para que los writeups funcionen tal cual;
    si está ocupado o es privilegiado y no somos root, usamos
    ``fallback_base + puerto`` (22 → 20022) y, si también está ocupado,
    el siguiente libre.
    """
    used: set[int] = set()
    out: List[PortMapping] = []
    privileged_ok = _can_bind_privileged()
    for cport, proto in exposed:
        candidates: List[int] = []
        if cport >= 1024 or privileged_ok:
            candidates.append(cport)
        candidates.append(fallback_base + cport)
        chosen: Optional[int] = None
        for c in candidates:
            if c not in used and 1 <= c <= 65535 and is_free(c):
                chosen = c
                break
        if chosen is None:
            c = fallback_base + cport + 1
            while c <= 65535 and (c in used or not is_free(c)):
                c += 1
            chosen = c if c <= 65535 else cport
        used.add(chosen)
        out.append(PortMapping(cport, proto, chosen))
    return out


def _can_bind_privileged() -> bool:
    if os.name == "nt":
        return True  # Docker Desktop publica en Windows sin restricción
    if platform.system() == "Darwin":
        return True  # Docker Desktop mac publica <1024 sin problema
    try:
        return os.geteuid() == 0  # type: ignore[attr-defined]
    except AttributeError:
        return False


# =====================================================================
# Extracción e inspección del zip / tar
# =====================================================================

def extract_lab(zip_path: Path, labs_dir: Path, slug: Optional[str] = None,
                on_progress: Optional[Callable[[int, int], None]] = None,
                cancel: Optional[Callable[[], bool]] = None) -> LabFiles:
    """Extrae el zip en ``labs_dir/<slug>/`` (idempotente) y localiza el tar."""
    zip_path = Path(zip_path)
    if not zipfile.is_zipfile(zip_path):
        raise InvalidLabArchive(f"{zip_path.name} no es un zip válido")
    slug = slug or slug_from_name(zip_path.stem)
    root = Path(labs_dir).expanduser().resolve() / slug
    root.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        # Seguridad: sin rutas absolutas ni '..'
        for m in members:
            p = Path(m.filename)
            if p.is_absolute() or ".." in p.parts:
                raise InvalidLabArchive(f"Ruta sospechosa dentro del zip: {m.filename}")
        total = sum(m.file_size for m in members) or 1
        done = 0
        for m in members:
            if cancel and cancel():
                raise LabCancelled()
            target = root / Path(m.filename).name  # aplanamos: todo a la raíz
            if target.exists() and target.stat().st_size == m.file_size:
                done += m.file_size
                if on_progress:
                    on_progress(done, total)
                continue
            with zf.open(m) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, 4 * 1024 * 1024)
            done += m.file_size
            if on_progress:
                on_progress(done, total)

    tars = sorted(root.glob("*.tar"), key=lambda p: p.stat().st_size, reverse=True)
    if not tars:
        raise InvalidLabArchive(
            "El zip no contiene ningún .tar de imagen Docker. "
            "¿Es realmente una máquina de DockerLabs?"
        )
    script = next(iter(root.glob("*.sh")), None)
    return LabFiles(slug=slug, root=root, tar_path=tars[0], deploy_script=script)


def inspect_image_tar(tar_path: Path) -> ImageInfo:
    """Lee ``manifest.json`` y el config JSON del `docker save` sin cargarlo."""
    tar_path = Path(tar_path)
    try:
        with tarfile.open(tar_path) as tf:
            names = set(tf.getnames())
            if "manifest.json" not in names:
                raise InvalidLabArchive("El .tar no tiene manifest.json (no es `docker save`)")
            manifest = json.loads(tf.extractfile("manifest.json").read().decode("utf-8"))
            if not manifest:
                raise InvalidLabArchive("manifest.json vacío")
            entry = manifest[0]
            tags = entry.get("RepoTags") or []
            repo_tag = tags[0] if tags else ""
            config_name = entry.get("Config", "")
            cfg: dict = {}
            if config_name and config_name in names:
                cfg = json.loads(tf.extractfile(config_name).read().decode("utf-8"))
    except tarfile.TarError as exc:
        raise InvalidLabArchive(f"El .tar está corrupto: {exc}") from exc

    if not repo_tag:
        # Fallback: nombre del fichero (como hace auto_deploy.sh)
        repo_tag = f"{tar_path.stem}:latest"
    image_name = repo_tag.split(":", 1)[0].rsplit("/", 1)[-1]
    inner = cfg.get("config") or {}
    exposed: List[Tuple[int, str]] = []
    for key in (inner.get("ExposedPorts") or {}):
        port_s, _, proto = key.partition("/")
        try:
            exposed.append((int(port_s), proto or "tcp"))
        except ValueError:
            continue
    exposed.sort()
    return ImageInfo(
        repo_tag=repo_tag,
        image_name=image_name,
        exposed_ports=exposed,
        architecture=cfg.get("architecture", ""),
        os=cfg.get("os", ""),
        cmd=list(inner.get("Cmd") or []),
        entrypoint=list(inner.get("Entrypoint") or []),
    )


# =====================================================================
# Cliente Docker (CLI)
# =====================================================================

class DockerClient:
    """Envoltorio fino sobre el CLI `docker`."""

    def __init__(self, binary: Optional[str] = None,
                 runner: Optional[Callable[..., subprocess.CompletedProcess]] = None) -> None:
        self.binary = binary or shutil.which("docker") or ""
        self._run = runner or self._default_runner

    # ---------- bajo nivel ----------

    @staticmethod
    def _default_runner(cmd: Sequence[str], timeout: float = DOCKER_TIMEOUT,
                        input_data: Optional[bytes] = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            list(cmd), capture_output=True, timeout=timeout, input=input_data,
            creationflags=_CREATE_NO_WINDOW,
        )

    def _docker(self, *args: str, timeout: float = DOCKER_TIMEOUT,
                check: bool = True) -> str:
        if not self.binary:
            raise DockerNotInstalled("Docker no está instalado o no está en el PATH.")
        try:
            cp = self._run([self.binary, *args], timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise DockerNotRunning(f"`docker {args[0]}` no respondió en {timeout}s") from exc
        except FileNotFoundError as exc:
            raise DockerNotInstalled("Docker no está instalado o no está en el PATH.") from exc
        out = (cp.stdout or b"").decode("utf-8", "replace")
        err = (cp.stderr or b"").decode("utf-8", "replace")
        if cp.returncode != 0 and check:
            self._raise_for_stderr(err or out, args)
        return out

    @staticmethod
    def _raise_for_stderr(err: str, args: Sequence[str]) -> None:
        low = err.lower()
        if "permission denied" in low and ("docker.sock" in low or "socket" in low):
            raise DockerPermissionDenied(
                "Sin permiso para hablar con el daemon de Docker.\n"
                "Añade tu usuario al grupo docker y vuelve a iniciar sesión:\n"
                "    sudo usermod -aG docker $USER\n"
                "(o ejecuta la app con permisos de administrador)."
            )
        if ("cannot connect to the docker daemon" in low
                or "is the docker daemon running" in low
                or "error during connect" in low
                or "pipe/docker_engine" in low
                or "dockerdesktoplinuxengine" in low):
            raise DockerNotRunning(
                "El daemon de Docker no está en marcha.\n"
                "Arranca Docker Desktop (Windows/macOS) o el servicio "
                "(`sudo systemctl start docker` en Linux)."
            )
        raise LabError(f"docker {' '.join(args[:2])} falló:\n{err.strip()[:800]}")

    # ---------- detección ----------

    def info(self) -> DockerInfo:
        if not self.binary:
            return DockerInfo(available=False, running=False,
                              error="Docker no está instalado o no está en el PATH.")
        try:
            raw = self._docker("version", "--format", "{{json .}}", timeout=15)
            data = json.loads(raw or "{}")
        except DockerNotRunning as exc:
            return DockerInfo(available=True, running=False, error=str(exc))
        except DockerPermissionDenied as exc:
            return DockerInfo(available=True, running=False, error=str(exc))
        except (LabError, json.JSONDecodeError) as exc:
            # `docker version` devuelve rc!=0 si el server no responde pero
            # sigue imprimiendo el JSON del cliente.
            return DockerInfo(available=True, running=False, error=str(exc))
        server = data.get("Server") or {}
        client = data.get("Client") or {}
        platform_name = ((server.get("Platform") or {}).get("Name")
                         or (client.get("Platform") or {}).get("Name") or "")
        is_desktop = "desktop" in platform_name.lower()
        if not is_desktop:
            # Docker Desktop en Linux/WSL expone el contexto 'desktop-linux'
            try:
                ctx = self._docker("context", "show", timeout=10, check=False).strip()
                is_desktop = "desktop" in ctx.lower()
            except LabError:
                pass
        is_wsl = "microsoft" in platform.uname().release.lower()
        return DockerInfo(
            available=True,
            running=bool(server),
            version=server.get("Version") or client.get("Version") or "",
            server_os=server.get("Os", ""),
            server_arch=server.get("Arch", ""),
            is_desktop=is_desktop,
            is_wsl=is_wsl,
            error="" if server else "El daemon no respondió.",
        )

    # ---------- imágenes ----------

    def image_exists(self, repo_tag: str) -> bool:
        out = self._docker("images", "-q", repo_tag, check=False)
        return bool(out.strip())

    def load_image(self, tar_path: Path,
                   on_line: Optional[Callable[[str], None]] = None,
                   cancel: Optional[Callable[[], bool]] = None) -> str:
        """`docker load -i tar` en streaming. Devuelve el repo:tag cargado."""
        if not self.binary:
            raise DockerNotInstalled("Docker no está instalado o no está en el PATH.")
        proc = subprocess.Popen(
            [self.binary, "load", "-i", str(tar_path)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", bufsize=1,
            creationflags=_CREATE_NO_WINDOW,
        )
        loaded = ""
        lines: List[str] = []
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                line = line.rstrip()
                lines.append(line)
                if on_line:
                    on_line(line)
                m = re.search(r"Loaded image(?: ID)?:\s*(\S+)", line)
                if m:
                    loaded = m.group(1)
                if cancel and cancel():
                    proc.kill()
                    raise LabCancelled()
        finally:
            proc.wait()
        if proc.returncode != 0:
            self._raise_for_stderr("\n".join(lines), ("load",))
        return loaded

    def remove_image(self, repo_tag: str) -> None:
        self._docker("rmi", "-f", repo_tag, check=False, timeout=120)

    # ---------- contenedores ----------

    def run_container(self, image: ImageInfo, machine: str, slug: str,
                      ports: Sequence[PortMapping] = (),
                      network_mode: str = "bridge",
                      extra_args: Sequence[str] = ()) -> str:
        name = container_name_for(slug)
        self.remove_container(name)  # limpieza previa (como auto_deploy.sh)
        cmd = ["run", "-d", "--name", name,
               "--label", f"{LABEL_KEY}=1",
               "--label", f"{LABEL_MACHINE}={machine}"]
        if network_mode == "host":
            cmd += ["--network", "host"]
        else:
            for pm in ports:
                cmd += ["-p", pm.docker_arg]
        cmd += list(extra_args)
        cmd.append(image.repo_tag)
        out = self._docker(*cmd, timeout=120)
        return out.strip()

    def stop_container(self, name: str, timeout: int = 10) -> None:
        self._docker("stop", "-t", str(timeout), name, check=False, timeout=timeout + 20)

    def start_container(self, name: str) -> None:
        self._docker("start", name, timeout=60)

    def restart_container(self, name: str) -> None:
        self._docker("restart", name, timeout=90)

    def remove_container(self, name: str) -> None:
        self._docker("rm", "-f", name, check=False, timeout=60)

    def container_status(self, name: str) -> Optional[ContainerStatus]:
        out = self._docker("inspect", name, check=False, timeout=20)
        try:
            data = json.loads(out or "[]")
        except json.JSONDecodeError:
            return None
        if not data:
            return None
        return self._parse_inspect(data[0])

    def list_lab_containers(self) -> List[ContainerStatus]:
        out = self._docker("ps", "-a", "-q", "--filter", f"label={LABEL_KEY}=1",
                           check=False)
        ids = [i for i in out.split() if i]
        if not ids:
            return []
        raw = self._docker("inspect", *ids, check=False, timeout=30)
        try:
            data = json.loads(raw or "[]")
        except json.JSONDecodeError:
            return []
        return [self._parse_inspect(d) for d in data]

    def exec_shell_command(self, name: str) -> List[str]:
        """Devuelve el comando para abrir una shell interactiva en el contenedor."""
        return [self.binary or "docker", "exec", "-it", name, "sh", "-c",
                "command -v bash >/dev/null 2>&1 && exec bash || exec sh"]

    @staticmethod
    def _parse_inspect(d: dict) -> ContainerStatus:
        name = (d.get("Name") or "").lstrip("/")
        cfg = d.get("Config") or {}
        labels = cfg.get("Labels") or {}
        state = d.get("State") or {}
        net = d.get("NetworkSettings") or {}
        ip = net.get("IPAddress") or ""
        if not ip:
            for n in (net.get("Networks") or {}).values():
                if n.get("IPAddress"):
                    ip = n["IPAddress"]
                    break
        ports: List[PortMapping] = []
        for key, binds in (net.get("Ports") or {}).items():
            if not binds:
                continue
            port_s, _, proto = key.partition("/")
            for b in binds:
                try:
                    ports.append(PortMapping(int(port_s), proto or "tcp",
                                             int(b.get("HostPort") or 0)))
                except ValueError:
                    continue
        ports.sort(key=lambda p: p.container_port)
        return ContainerStatus(
            name=name,
            machine=labels.get(LABEL_MACHINE) or name.replace(CONTAINER_PREFIX, "", 1),
            image=cfg.get("Image") or "",
            status=state.get("Status") or "unknown",
            ip=ip,
            ports=ports,
            container_id=(d.get("Id") or "")[:12],
        )


# =====================================================================
# Orquestación de alto nivel
# =====================================================================

def install_hint() -> str:
    """Texto de ayuda para instalar Docker según el SO."""
    sysname = platform.system()
    if sysname == "Windows":
        return ("Instala Docker Desktop para Windows (requiere WSL2):\n"
                "https://docs.docker.com/desktop/setup/install/windows-install/")
    if sysname == "Darwin":
        return ("Instala Docker Desktop para macOS:\n"
                "https://docs.docker.com/desktop/setup/install/mac-install/")
    return ("Instala Docker Engine, p.ej. en Debian/Ubuntu/Kali:\n"
            "    sudo apt update && sudo apt install -y docker.io\n"
            "    sudo systemctl enable --now docker\n"
            "    sudo usermod -aG docker $USER   # y vuelve a iniciar sesión")


def choose_network_strategy(info: DockerInfo, preferred: str = "auto") -> str:
    """Devuelve 'bridge' | 'bridge+ports' | 'host'.

    * auto → 'bridge' en Linux nativo (comportamiento oficial: la IP del
      contenedor es accesible), 'bridge+ports' en Docker Desktop.
    """
    if preferred in ("bridge", "bridge+ports", "host"):
        if preferred == "host" and platform.system() != "Linux":
            return "bridge+ports"
        return preferred
    return "bridge" if info.bridge_ip_reachable else "bridge+ports"


def describe_access(status: ContainerStatus, strategy: str) -> str:
    """Frase para la UI con la IP/puertos por los que atacar la máquina."""
    if strategy == "host":
        return "Modo host: la máquina escucha directamente en 127.0.0.1 / tu IP local."
    if strategy == "bridge":
        return f"IP de la máquina: {status.ip or '—'} (accesible desde este equipo)"
    if status.ports:
        parts = ", ".join(f"{p.container_port}/{p.proto} → 127.0.0.1:{p.host_port}"
                          for p in status.ports)
        return f"Puertos publicados en localhost: {parts}"
    return (f"IP interna {status.ip or '—'} (no accesible desde Docker Desktop; "
            "la imagen no declara puertos EXPOSE)")
