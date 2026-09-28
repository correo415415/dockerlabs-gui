"""Capa Qt sobre `lab_manager`: workers en hilos + estado agregado para la UI."""
from __future__ import annotations

import logging
import platform
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from PyQt6.QtCore import QObject, pyqtSignal

from lab_manager import (
    CONTAINER_PREFIX,
    ContainerStatus,
    DockerClient,
    DockerInfo,
    ImageInfo,
    LabCancelled,
    LabError,
    LabFiles,
    build_deploy_plan,
    choose_network_strategy,
    container_name_for,
    deploy_plan,
    describe_access_multi,
    extract_lab,
    grant_docker_access,
    inspect_image_tar,
    install_hint,
    permission_hint,
    slug_from_name,
    start_docker_service,
)
from workers import BaseWorker, WorkerPool

logger = logging.getLogger(__name__)


@dataclass
class LabState:
    machine: str
    slug: str
    zip_path: Optional[Path] = None
    phase: str = "idle"        # idle | extracting | loading | starting | running | stopped | error | stopping | removing
    progress: float = 0.0      # 0-100 en extracción
    log_line: str = ""
    error: str = ""
    image: Optional[ImageInfo] = None
    container: Optional[ContainerStatus] = None      # principal (compat UI)
    containers: List[ContainerStatus] = field(default_factory=list)   # todos (pivoting)
    images: List[ImageInfo] = field(default_factory=list)
    strategy: str = "bridge"
    access_text: str = ""

    @property
    def busy(self) -> bool:
        return self.phase in ("extracting", "loading", "starting", "stopping", "removing")

    @property
    def container_name(self) -> str:
        return container_name_for(self.slug)

    @property
    def container_names(self) -> List[str]:
        return [c.name for c in self.containers] or [self.container_name]

    @property
    def is_multi(self) -> bool:
        return len(self.containers) > 1

    def set_containers(self, statuses: List[ContainerStatus]) -> None:
        self.containers = list(statuses)
        self.container = statuses[0] if statuses else None
        self.access_text = describe_access_multi(statuses, self.strategy) if statuses else ""


# =====================================================================
# Workers
# =====================================================================

class _DockerInfoWorker(BaseWorker):
    done = pyqtSignal(object)  # DockerInfo

    def __init__(self, client: DockerClient, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.failed.connect(self._on_failed)

    def work(self) -> None:
        self.done.emit(self.client.info())

    def _on_failed(self, err: str) -> None:
        self.done.emit(DockerInfo(available=False, running=False, error=err))


class _ElevateWorker(BaseWorker):
    """Pide privilegios (diálogo nativo) para conceder acceso o arrancar el servicio."""
    done = pyqtSignal(str, bool, str)   # action, ok, detail

    def __init__(self, action: str, socket_path: str = "", parent=None) -> None:
        super().__init__(parent)
        self.action = action
        self.socket_path = socket_path
        self.failed.connect(lambda err: self.done.emit(self.action, False, err))

    def work(self) -> None:
        if self.action == "grant":
            ok, msg = grant_docker_access(sock=self.socket_path)
        elif self.action == "start_service":
            ok, msg = start_docker_service()
        else:
            ok, msg = False, f"Acción desconocida: {self.action}"
        self.done.emit(self.action, ok, msg)


class _RefreshWorker(BaseWorker):
    done = pyqtSignal(list)   # list[ContainerStatus]

    def __init__(self, client: DockerClient, parent=None) -> None:
        super().__init__(parent)
        self.client = client

    def work(self) -> None:
        self.done.emit(self.client.list_lab_containers())


class _LaunchWorker(BaseWorker):
    """extract → inspect → docker load (si hace falta) → docker run → inspect."""
    phase = pyqtSignal(str, str)                 # machine, phase
    progress = pyqtSignal(str, float)            # machine, percent
    log_line = pyqtSignal(str, str)              # machine, line
    image_ready = pyqtSignal(str, object)        # machine, ImageInfo
    done = pyqtSignal(str, object, str)          # machine, list[ContainerStatus], strategy
    failed = pyqtSignal(str, str)                # machine, error

    def __init__(self, client: DockerClient, machine: str, slug: str, zip_path: Path,
                 labs_dir: Path, strategy: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.machine = machine
        self.slug = slug
        self.zip_path = zip_path
        self.labs_dir = labs_dir
        self.strategy = strategy

    def on_error(self, exc: BaseException) -> None:
        self.failed.emit(self.machine, f"Error inesperado: {exc}")

    def work(self) -> None:
        m = self.machine
        try:
            self.phase.emit(m, "extracting")
            files: LabFiles = extract_lab(
                self.zip_path, self.labs_dir, slug=self.slug,
                on_progress=lambda d, t: self.progress.emit(m, d * 100.0 / max(t, 1)),
                cancel=self._cancel.is_set,
            )
            images = [inspect_image_tar(t) for t in files.tar_paths]
            self.image_ready.emit(m, images[0])
            if len(images) > 1:
                self.log_line.emit(m, f"Lab de pivoting: {len(images)} máquinas "
                                      f"({', '.join(i.repo_tag for i in images)})")

            plan = build_deploy_plan(files, m, self.strategy, images=images)
            statuses = deploy_plan(
                self.client, plan,
                on_phase=lambda ph: self.phase.emit(m, ph),
                on_line=lambda ln: self.log_line.emit(m, ln),
                cancel=self._cancel.is_set,
                tar_paths=files.tar_paths,
            )
            self.done.emit(m, statuses, self.strategy)
        except LabCancelled:
            self.failed.emit(m, "Cancelado por el usuario")
        except LabError as exc:
            self.failed.emit(m, str(exc))


class _ActionWorker(BaseWorker):
    """stop / start / restart / remove sobre un contenedor existente."""
    done = pyqtSignal(str, str, object)   # machine, action, list[ContainerStatus]
    failed = pyqtSignal(str, str, str)    # machine, action, error

    def __init__(self, client: DockerClient, machine: str, slug: str, action: str,
                 image_tags: Sequence[str] = (), names: Sequence[str] = (), parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.machine = machine
        self.slug = slug
        self.action = action
        self.image_tags = [t for t in image_tags if t]
        self.names = list(names)

    def on_error(self, exc: BaseException) -> None:
        self.failed.emit(self.machine, self.action, str(exc))

    def work(self) -> None:
        names = self.names or self.client.container_names_for_slug(self.slug) or [container_name_for(self.slug)]
        if self.action == "stop":
            for n in names:
                self.client.stop_container(n)
        elif self.action == "start":
            for n in names:
                self.client.start_container(n)
        elif self.action == "restart":
            for n in names:
                self.client.restart_container(n)
        elif self.action == "remove":
            # Solo lo de este lab: contenedores + redes pivoting + imágenes (nunca el resto del sistema)
            self.client.teardown_lab(self.slug, image_tags=self.image_tags)
        statuses = [] if self.action == "remove" else self.client.statuses_for(names)
        self.done.emit(self.machine, self.action, statuses)


# =====================================================================
# Controlador
# =====================================================================

class LabController(QObject):
    docker_info_changed = pyqtSignal(object)      # DockerInfo
    lab_changed = pyqtSignal(str)                 # machine
    list_changed = pyqtSignal()
    lab_started = pyqtSignal(str, str)            # machine, access_text
    lab_failed = pyqtSignal(str, str)             # machine, error
    lab_action_done = pyqtSignal(str, str)        # machine, action
    elevation_started = pyqtSignal(str)           # action
    elevation_done = pyqtSignal(str, bool, str)   # action, ok, detail

    def __init__(self, labs_dir: Path, network_preference: str = "auto",
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.labs_dir = Path(labs_dir).expanduser().resolve()
        self.labs_dir.mkdir(parents=True, exist_ok=True)
        self.client = DockerClient()
        self.docker_info = DockerInfo(available=bool(self.client.binary), running=False)
        self.network_preference = network_preference
        self._labs: Dict[str, LabState] = {}
        self._workers = WorkerPool()

    # ---------- consultas ----------

    def all_labs(self) -> List[LabState]:
        return sorted(self._labs.values(), key=lambda s: s.machine.lower())

    def lab(self, machine: str) -> Optional[LabState]:
        return self._labs.get(machine)

    def running_labs(self) -> List[LabState]:
        return [s for s in self._labs.values() if s.phase == "running"]

    def strategy_for_current_platform(self) -> str:
        return choose_network_strategy(self.docker_info, self.network_preference)

    @staticmethod
    def install_hint() -> str:
        return install_hint()

    def permission_hint(self) -> str:
        return permission_hint(self.docker_info)

    @property
    def elevating(self) -> bool:
        return self._workers.any_running(_ElevateWorker)

    # ---------- configuración ----------

    def set_network_preference(self, pref: str) -> None:
        self.network_preference = pref or "auto"

    def set_labs_dir(self, path: Path) -> None:
        self.labs_dir = Path(path).expanduser().resolve()
        self.labs_dir.mkdir(parents=True, exist_ok=True)

    # ---------- operaciones ----------

    def refresh_docker_info(self) -> None:
        w = _DockerInfoWorker(self.client, parent=self)
        w.done.connect(self._on_docker_info)
        self._track(w)

    def grant_access(self) -> bool:
        """Linux: pide la contraseña con el diálogo del sistema y añade el usuario
        al grupo docker (+ ACL inmediata sobre el socket). No bloquea la UI."""
        return self._elevate("grant")

    def start_service(self) -> bool:
        """Linux: `systemctl start docker` con elevación gráfica."""
        return self._elevate("start_service")

    def _elevate(self, action: str) -> bool:
        if self.elevating:
            return False
        w = _ElevateWorker(action, self.docker_info.socket_path, parent=self)
        w.done.connect(self._on_elevation_done)
        self.elevation_started.emit(action)
        self._track(w)
        return True

    def _on_elevation_done(self, action: str, ok: bool, detail: str) -> None:
        self.elevation_done.emit(action, ok, detail)
        # Re-evaluar el estado de Docker en cualquier caso
        self.refresh_docker_info()

    def refresh_containers(self) -> None:
        if not self.client.binary:
            return
        w = _RefreshWorker(self.client, parent=self)
        w.done.connect(self._on_refresh_done)
        self._track(w)

    def launch(self, machine: str, zip_path: Path, slug: Optional[str] = None) -> bool:
        slug = slug or slug_from_name(machine)
        st = self._labs.get(machine)
        if st is not None and st.busy:
            return False
        strategy = self.strategy_for_current_platform()
        st = LabState(machine=machine, slug=slug, zip_path=Path(zip_path),
                      phase="extracting", strategy=strategy)
        self._labs[machine] = st
        self.list_changed.emit()
        self.lab_changed.emit(machine)

        w = _LaunchWorker(self.client, machine, slug, Path(zip_path), self.labs_dir,
                          strategy, parent=self)
        w.phase.connect(self._on_phase)
        w.progress.connect(self._on_progress)
        w.log_line.connect(self._on_log)
        w.image_ready.connect(self._on_image)
        w.done.connect(self._on_launched)
        w.failed.connect(self._on_launch_failed)
        self._track(w)
        return True

    def cancel_launch(self, machine: str) -> None:
        for w in self._workers:
            if isinstance(w, _LaunchWorker) and w.machine == machine and w.isRunning():
                w.cancel()

    def stop(self, machine: str) -> None:
        self._action(machine, "stop")

    def start(self, machine: str) -> None:
        self._action(machine, "start")

    def restart(self, machine: str) -> None:
        self._action(machine, "restart")

    def remove(self, machine: str, remove_image: bool = True) -> None:
        st = self._labs.get(machine)
        tags: List[str] = []
        if st and remove_image:
            tags = [i.repo_tag for i in st.images] or [c.image for c in st.containers if c.image]
            if not tags and st.image:
                tags = [st.image.repo_tag]
        self._action(machine, "remove", image_tags=tags)

    def stop_all(self) -> None:
        """Parada sincrónica de todos los labs en marcha (al cerrar la app)."""
        for st in self.running_labs():
            for name in st.container_names:
                try:
                    self.client.stop_container(name, timeout=5)
                except LabError as exc:
                    logger.warning("stop_all %s/%s: %s", st.machine, name, exc)

    def open_shell(self, machine: str, index: int = 0) -> bool:
        """Abre una terminal del sistema con `docker exec -it` (índice = máquina del lab de pivoting)."""
        st = self._labs.get(machine)
        if st is None:
            return False
        names = st.container_names
        name = names[index] if 0 <= index < len(names) else names[0]
        return open_in_terminal(self.client.exec_shell_command(name))

    def shutdown(self) -> None:
        self._workers.shutdown(3000)

    # ---------- slots ----------

    def _track(self, w: BaseWorker) -> None:
        self._workers.track(w)

    def _on_docker_info(self, info: DockerInfo) -> None:
        self.docker_info = info
        self.docker_info_changed.emit(info)
        if info.running:
            self.refresh_containers()

    def _on_refresh_done(self, containers: list) -> None:
        # Agrupamos por lab (label slug; fallback: nombre) — los labs de pivoting tienen N contenedores.
        groups: Dict[str, List[ContainerStatus]] = {}
        for c in containers:
            slug = c.slug or self._slug_from_container_name(c.name)
            groups.setdefault(slug, []).append(c)
        seen: set[str] = set()
        for slug, cs in groups.items():
            cs.sort(key=lambda c: c.name)
            machine = cs[0].machine
            seen.add(machine)
            st = self._labs.get(machine)
            if st is None:
                st = LabState(machine=machine, slug=slug)
                self._labs[machine] = st
            if st.busy:
                continue
            running = any(c.is_running for c in cs)
            st.phase = "running" if running else "stopped"
            if any(c.ports for c in cs):
                st.strategy = "bridge+ports"
            elif st.strategy == "bridge":
                st.strategy = self.strategy_for_current_platform()
            st.set_containers(cs)
        # Labs que ya no existen en Docker (borrados fuera de la app)
        for machine in list(self._labs):
            st = self._labs[machine]
            if machine not in seen and not st.busy and st.phase in ("running", "stopped"):
                del self._labs[machine]
        self.list_changed.emit()

    @staticmethod
    def _slug_from_container_name(name: str) -> str:
        base = name.replace(CONTAINER_PREFIX, "", 1)
        # dockerlabs_grandma_2 → grandma
        head, sep, tail = base.rpartition("_")
        return head if sep and tail.isdigit() else base

    def _on_phase(self, machine: str, phase: str) -> None:
        st = self._labs.get(machine)
        if st:
            st.phase = phase
            self.lab_changed.emit(machine)

    def _on_progress(self, machine: str, pct: float) -> None:
        st = self._labs.get(machine)
        if st:
            st.progress = pct
            self.lab_changed.emit(machine)

    def _on_log(self, machine: str, line: str) -> None:
        st = self._labs.get(machine)
        if st:
            st.log_line = line
            self.lab_changed.emit(machine)

    def _on_image(self, machine: str, image: ImageInfo) -> None:
        st = self._labs.get(machine)
        if st:
            st.image = image
            if image not in st.images:
                st.images.append(image)
            self.lab_changed.emit(machine)

    def _on_launched(self, machine: str, statuses: object, strategy: str) -> None:
        st = self._labs.get(machine)
        if st is None:
            return
        sts: List[ContainerStatus] = list(statuses) if isinstance(statuses, (list, tuple)) else [statuses]  # type: ignore[list-item]
        st.strategy = strategy
        st.set_containers(sts)
        st.phase = "running" if any(c.is_running for c in sts) else "stopped"
        st.error = ""
        self.lab_changed.emit(machine)
        self.list_changed.emit()
        self.lab_started.emit(machine, st.access_text)

    def _on_launch_failed(self, machine: str, error: str) -> None:
        st = self._labs.get(machine)
        if st is None:
            return
        st.phase = "error"
        st.error = error
        self.lab_changed.emit(machine)
        self.list_changed.emit()
        self.lab_failed.emit(machine, error)

    def _action(self, machine: str, action: str, image_tags: Sequence[str] = ()) -> None:
        st = self._labs.get(machine)
        if st is None or st.busy:
            return
        st.phase = {"stop": "stopping", "remove": "removing"}.get(action, "starting")
        self.lab_changed.emit(machine)
        names = [c.name for c in st.containers]
        w = _ActionWorker(self.client, machine, st.slug, action, image_tags=image_tags, names=names,
                          parent=self)
        w.done.connect(self._on_action_done)
        w.failed.connect(self._on_action_failed)
        self._track(w)

    def _on_action_done(self, machine: str, action: str, statuses) -> None:
        st = self._labs.get(machine)
        if st is None:
            return
        sts: List[ContainerStatus] = list(statuses or [])
        if action == "remove" or not sts:
            self._labs.pop(machine, None)
        else:
            st.set_containers(sts)
            st.phase = "running" if any(c.is_running for c in sts) else "stopped"
        self.lab_changed.emit(machine)
        self.list_changed.emit()
        self.lab_action_done.emit(machine, action)

    def _on_action_failed(self, machine: str, action: str, error: str) -> None:
        st = self._labs.get(machine)
        if st is None:
            return
        st.phase = "error"
        st.error = error
        self.lab_changed.emit(machine)
        self.list_changed.emit()
        self.lab_failed.emit(machine, error)


# =====================================================================
# Abrir terminal del sistema
# =====================================================================

def open_in_terminal(cmd: List[str]) -> bool:
    """Lanza `cmd` en una terminal nueva del SO. Devuelve True si se pudo."""
    sysname = platform.system()
    try:
        if sysname == "Windows":
            if shutil.which("wt"):
                subprocess.Popen(["wt", *cmd])
            else:
                subprocess.Popen(["cmd", "/c", "start", "", *cmd])
            return True
        if sysname == "Darwin":
            script = " ".join(shlex.quote(c) for c in cmd).replace('"', '\\"')
            osa = f'tell application "Terminal" to do script "{script}"'
            subprocess.Popen(["osascript", "-e", osa])
            return True
        joined = " ".join(shlex.quote(c) for c in cmd)
        candidates = [
            ("x-terminal-emulator", ["-e", "sh", "-c", joined]),
            ("gnome-terminal", ["--", "sh", "-c", joined]),
            ("konsole", ["-e", "sh", "-c", joined]),
            ("xfce4-terminal", ["-e", f"sh -c {shlex.quote(joined)}"]),
            ("kitty", ["sh", "-c", joined]),
            ("alacritty", ["-e", "sh", "-c", joined]),
            ("tilix", ["-e", f"sh -c {shlex.quote(joined)}"]),
            ("mate-terminal", ["-e", f"sh -c {shlex.quote(joined)}"]),
            ("qterminal", ["-e", f"sh -c {shlex.quote(joined)}"]),
            ("xterm", ["-e", "sh", "-c", joined]),
        ]
        for binary, args in candidates:
            if shutil.which(binary):
                subprocess.Popen([binary, *args], start_new_session=True)
                return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("open_in_terminal: %s", exc)
    return False
