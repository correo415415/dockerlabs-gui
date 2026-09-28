"""Lanzador de laboratorios (CTF) de DockerLabs sobre Docker — multiplataforma.

Formato de una máquina (analizado a partir de los zips oficiales):

    <slug>.zip
    ├── <slug>.tar        ← imagen Docker exportada con `docker save`
    └── auto_deploy.sh    ← script bash oficial (docker load + run + inspect IP)

Este módulo reproduce lo que hace `auto_deploy.sh` pero:

* sin depender de bash (funciona en Windows / macOS / Linux),
* sin `sudo` obligatorio: si el socket no es accesible y la app no corre como
  root, se detecta y se ofrece elevar privilegios con el diálogo nativo del
  escritorio (pkexec/polkit, o `sudo -A` con askpass gráfico) para conceder
  acceso al usuario (grupo `docker` + ACL inmediata sobre el socket),
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
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

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
    # --- permisos (Linux) ---
    permission_denied: bool = False   # el socket existe pero el usuario no puede usarlo
    is_root: bool = False             # la app se ejecuta como root (sudo)
    socket_path: str = ""             # ruta del socket unix (si aplica)
    can_elevate: bool = False         # hay un mecanismo gráfico para pedir privilegios
    service_state: str = ""           # systemd: active | inactive | failed | missing | ''

    @property
    def needs_elevation(self) -> bool:
        """True si Docker está instalado pero falta permiso y no somos root."""
        return self.available and self.permission_denied and not self.is_root

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
# Permisos y elevación de privilegios (Linux)
# =====================================================================

def is_root() -> bool:
    """True si el proceso corre como root/administrador."""
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None:
        return geteuid() == 0
    if os.name == "nt":  # pragma: no cover - solo Windows
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001
            return False
    return False


def docker_socket_path(client: Optional["DockerClient"] = None) -> str:
    """Ruta del socket unix que usa el cliente (DOCKER_HOST, contexto o por defecto)."""
    host = os.environ.get("DOCKER_HOST", "")
    if host.startswith("unix://"):
        return host[len("unix://"):]
    if host:
        return ""  # tcp://, ssh://, npipe… → no aplica el chequeo de socket
    # Docker Desktop / rootless suelen usar un contexto con otro socket
    if client is not None and client.binary:
        try:
            out = client._run([client.binary, "context", "inspect", "--format",
                               "{{.Endpoints.docker.Host}}"], timeout=5)
            ep = (out.stdout or b"").decode("utf-8", "replace").strip()
            if ep.startswith("unix://"):
                return ep[len("unix://"):]
            if ep:
                return ""
        except Exception:  # noqa: BLE001
            pass
    xdg = os.environ.get("XDG_RUNTIME_DIR", "")
    if xdg and Path(xdg, "docker.sock").exists():   # rootless docker
        return str(Path(xdg, "docker.sock"))
    return "/var/run/docker.sock"


def socket_access(sock: str) -> str:
    """'ok' | 'denied' | 'missing'."""
    if not sock:
        return "ok"
    try:
        st = os.stat(sock)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "denied"
    import stat as _stat
    if not _stat.S_ISSOCK(st.st_mode):
        return "missing"
    return "ok" if os.access(sock, os.R_OK | os.W_OK) else "denied"


def docker_service_state() -> str:
    """Estado del servicio systemd `docker` ('' si no hay systemd)."""
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return ""
    try:
        cp = subprocess.run([systemctl, "is-active", "docker"], capture_output=True,
                            timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    out = (cp.stdout or b"").decode("utf-8", "replace").strip()
    if out in ("active", "inactive", "failed", "activating", "deactivating"):
        return out
    return "missing" if "could not be found" in (cp.stderr or b"").decode("utf-8", "replace") \
        else (out or "")


def _askpass_helper() -> str:
    """Programa gráfico para `sudo -A`, si existe."""
    for cand in (os.environ.get("SUDO_ASKPASS", ""), "/usr/lib/ssh/ssh-askpass",
                 "/usr/libexec/openssh/ssh-askpass",
                 "/usr/lib/openssh/gnome-ssh-askpass",
                 "/usr/bin/ssh-askpass", "/usr/bin/ksshaskpass",
                 "/usr/bin/lxqt-openssh-askpass", "/usr/bin/x11-ssh-askpass"):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    which = shutil.which("ssh-askpass") or shutil.which("ksshaskpass")
    return which or ""


def elevation_command() -> List[str]:
    """Prefijo de comando para ejecutar algo como root con diálogo gráfico nativo.

    Orden de preferencia (Linux):
      1. pkexec (polkit → diálogo del escritorio: GNOME/KDE/XFCE…)
      2. sudo -A con un askpass gráfico (ssh-askpass, ksshaskpass…)
      3. lxqt-sudo / kdesu / gksudo (entornos concretos)
    Devuelve [] si no hay ninguno (o no estamos en Linux).
    """
    if platform.system() != "Linux":
        return []
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return []
    pk = shutil.which("pkexec")
    if pk:
        return [pk]
    sudo = shutil.which("sudo")
    if sudo and _askpass_helper():
        return [sudo, "-A"]
    for alt in ("lxqt-sudo", "kdesu", "gksudo", "gksu"):
        w = shutil.which(alt)
        if w:
            return [w] if alt != "kdesu" else [w, "-c"]
    return []


def elevation_available() -> bool:
    return bool(elevation_command())


GRANT_ACCESS_SCRIPT = r"""
set -e
USER_NAME="$1"
SOCK="$2"
# 1) grupo docker (persistente, requiere re-login para nuevos procesos)
if ! getent group docker >/dev/null 2>&1; then
  groupadd docker
fi
usermod -aG docker "$USER_NAME"
# 2) arrancar el servicio si está parado
if command -v systemctl >/dev/null 2>&1; then
  systemctl is-active --quiet docker || systemctl start docker || true
  systemctl enable docker >/dev/null 2>&1 || true
elif command -v service >/dev/null 2>&1; then
  service docker start >/dev/null 2>&1 || true
fi
# 3) acceso inmediato sin re-login: ACL sobre el socket (o chmod si no hay setfacl)
for i in 1 2 3 4 5 6 7 8 9 10; do
  [ -S "$SOCK" ] && break
  sleep 1
done
if [ -S "$SOCK" ]; then
  if command -v setfacl >/dev/null 2>&1; then
    setfacl -m "u:${USER_NAME}:rw" "$SOCK"
  else
    chgrp docker "$SOCK" 2>/dev/null || true
    chmod g+rw "$SOCK" 2>/dev/null || true
  fi
fi
echo GRANT_OK
"""


def grant_docker_access(sock: str = "", user: str = "",
                        runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
                        timeout: float = 180) -> Tuple[bool, str]:
    """Pide privilegios con el diálogo nativo y concede acceso al socket.

    Añade al usuario al grupo `docker`, arranca el servicio y aplica una ACL
    sobre el socket para que funcione *sin cerrar sesión*. Devuelve (ok, detalle).
    """
    if platform.system() != "Linux":
        return False, "La elevación de privilegios solo está soportada en Linux."
    prefix = elevation_command()
    if not prefix:
        return False, ("No se encontró pkexec ni sudo con askpass gráfico.\n"
                       "Ejecuta a mano: sudo usermod -aG docker $USER  (y reinicia sesión).")
    user = user or os.environ.get("SUDO_USER") or os.environ.get("USER") or ""
    if not user:
        try:
            import pwd
            user = pwd.getpwuid(os.getuid()).pw_name
        except Exception:  # noqa: BLE001
            return False, "No se pudo determinar el usuario actual."
    sock = sock or docker_socket_path() or "/var/run/docker.sock"
    cmd = [*prefix, "/bin/sh", "-c", GRANT_ACCESS_SCRIPT, "dockerlabs-grant", user, sock]
    env = dict(os.environ)
    if prefix and prefix[0].endswith("sudo") and not env.get("SUDO_ASKPASS"):
        env["SUDO_ASKPASS"] = _askpass_helper()
    run = runner or (lambda c, timeout=timeout, input_data=None: subprocess.run(
        list(c), capture_output=True, timeout=timeout, env=env))
    try:
        cp = run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "Se agotó el tiempo esperando la autorización."
    except OSError as exc:
        return False, f"No se pudo lanzar el diálogo de autorización: {exc}"
    out = (cp.stdout or b"").decode("utf-8", "replace")
    err = (cp.stderr or b"").decode("utf-8", "replace")
    if cp.returncode == 126 or "dismissed" in err.lower() or "not authorized" in err.lower():
        return False, "Autorización cancelada por el usuario."
    if cp.returncode != 0 or "GRANT_OK" not in out:
        return False, (err.strip() or out.strip() or f"Fallo al conceder acceso (rc={cp.returncode}).")[:800]
    return True, ("Acceso concedido. Tu usuario ya pertenece al grupo docker; "
                  "el acceso inmediato es válido hasta que se reinicie el servicio "
                  "(tras cerrar e iniciar sesión será permanente).")


def start_docker_service(runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
                         timeout: float = 120) -> Tuple[bool, str]:
    """Arranca el servicio docker (systemd) pidiendo privilegios con diálogo nativo."""
    if platform.system() != "Linux":
        return False, "Arranca Docker Desktop desde el sistema."
    prefix = [] if is_root() else elevation_command()
    if not prefix and not is_root():
        return False, "No hay forma gráfica de pedir privilegios (instala policykit-1 / pkexec)."
    systemctl = shutil.which("systemctl")
    if systemctl:
        cmd = [*prefix, systemctl, "start", "docker"]
    elif shutil.which("service"):
        cmd = [*prefix, shutil.which("service"), "docker", "start"]
    else:
        return False, "No se encontró systemctl ni service."
    run = runner or (lambda c, timeout=timeout, input_data=None: subprocess.run(
        list(c), capture_output=True, timeout=timeout))
    try:
        cp = run(cmd, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if cp.returncode != 0:
        err = (cp.stderr or b"").decode("utf-8", "replace").strip()
        if cp.returncode == 126 or "dismissed" in err.lower():
            return False, "Autorización cancelada por el usuario."
        return False, err[:800] or f"rc={cp.returncode}"
    return True, "Servicio docker iniciado."


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
        if "permission denied" in low and ("docker.sock" in low or "socket" in low
                                          or "dial unix" in low):
            raise DockerPermissionDenied(
                "Sin permiso para hablar con el daemon de Docker.\n"
                "Pulsa «Conceder acceso» para autorizarlo con tu contraseña, o hazlo a mano:\n"
                "    sudo usermod -aG docker $USER   # y vuelve a iniciar sesión"
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
        root = is_root()
        if not self.binary:
            return DockerInfo(available=False, running=False, is_root=root,
                              error="Docker no está instalado o no está en el PATH.")
        sock = docker_socket_path(self) if platform.system() == "Linux" else ""
        base = dict(available=True, running=False, is_root=root, socket_path=sock,
                    can_elevate=elevation_available(),
                    service_state=docker_service_state() if sock else "")
        # Pre-chequeo barato: si el socket existe y no podemos ni leerlo, no hace
        # falta llamar a docker (y en algunas distros `docker version` tarda).
        if sock and socket_access(sock) == "denied":
            return DockerInfo(
                permission_denied=True,
                error=("Sin permiso para usar el socket de Docker "
                       f"({sock}). " + ("Pulsa «Conceder acceso» para autorizarlo."
                                        if base["can_elevate"] else
                                        "Ejecuta: sudo usermod -aG docker $USER y vuelve a iniciar sesión.")),
                **base,
            )
        try:
            raw = self._docker("version", "--format", "{{json .}}", timeout=15)
            data = json.loads(raw or "{}")
        except DockerPermissionDenied as exc:
            return DockerInfo(permission_denied=True, error=str(exc), **base)
        except DockerNotRunning as exc:
            return DockerInfo(error=str(exc), **base)
        except (LabError, json.JSONDecodeError) as exc:
            # `docker version` devuelve rc!=0 si el server no responde pero
            # sigue imprimiendo el JSON del cliente.
            return DockerInfo(error=str(exc), **base)
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
        base["running"] = bool(server)
        return DockerInfo(
            version=server.get("Version") or client.get("Version") or "",
            server_os=server.get("Os", ""),
            server_arch=server.get("Arch", ""),
            is_desktop=is_desktop,
            is_wsl=is_wsl,
            error="" if server else "El daemon no respondió.",
            **base,
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
            "Después, la app te pedirá permiso (diálogo del sistema) para "
            "usar Docker sin sudo.")


def permission_hint(info: "DockerInfo") -> str:
    """Explicación corta del problema de permisos y cómo resolverlo."""
    if info.is_root:
        return "La app se ejecuta como root: no hace falta conceder permisos."
    if info.can_elevate:
        return ("Tu usuario no puede usar Docker sin sudo. Pulsa «Conceder acceso»: "
                "se abrirá el diálogo de autenticación del sistema y se añadirá tu "
                "usuario al grupo docker (sin necesidad de reiniciar la app).")
    return ("Tu usuario no puede usar Docker sin sudo y no se encontró pkexec "
            "(polkit) ni un askpass gráfico. Ejecuta en una terminal:\n"
            "    sudo usermod -aG docker $USER\n"
            "y vuelve a iniciar sesión, o instala policykit-1.")


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
