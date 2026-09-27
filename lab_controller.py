"""Capa Qt sobre `lab_manager`: workers en hilos + estado agregado para la UI."""
from __future__ import annotations

import logging
import platform
import shlex
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from lab_manager import (
    CONTAINER_PREFIX,
    ContainerStatus,
    DockerClient,
    DockerInfo,
    ImageInfo,
    LabCancelled,
    LabError,
    LabFiles,
    choose_network_strategy,
    container_name_for,
    describe_access,
    extract_lab,
    inspect_image_tar,
    install_hint,
    plan_port_mappings,
    slug_from_name,
)

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
    container: Optional[ContainerStatus] = None
    strategy: str = "bridge"
    access_text: str = ""

    @property
    def busy(self) -> bool:
        return self.phase in ("extracting", "loading", "starting", "stopping", "removing")

    @property
    def container_name(self) -> str:
        return container_name_for(self.slug)


# =====================================================================
# Workers
# =====================================================================

class _DockerInfoWorker(QThread):
    done = pyqtSignal(object)  # DockerInfo

    def __init__(self, client: DockerClient, parent=None) -> None:
        super().__init__(parent)
        self.client = client

    def run(self) -> None:
        try:
            self.done.emit(self.client.info())
        except Exception as exc:  # noqa: BLE001
            logger.exception("docker info")
            self.done.emit(DockerInfo(available=False, running=False, error=str(exc)))


class _RefreshWorker(QThread):
    done = pyqtSignal(list)   # list[ContainerStatus]
    failed = pyqtSignal(str)

    def __init__(self, client: DockerClient, parent=None) -> None:
        super().__init__(parent)
        self.client = client

    def run(self) -> None:
        try:
            self.done.emit(self.client.list_lab_containers())
        except Exception as exc:  # noqa: BLE001
            logger.warning("refresh labs: %s", exc)
            self.failed.emit(str(exc))


class _LaunchWorker(QThread):
    """extract → inspect → docker load (si hace falta) → docker run → inspect."""
    phase = pyqtSignal(str, str)                 # machine, phase
    progress = pyqtSignal(str, float)            # machine, percent
    log_line = pyqtSignal(str, str)              # machine, line
    image_ready = pyqtSignal(str, object)        # machine, ImageInfo
    done = pyqtSignal(str, object, str)          # machine, ContainerStatus, strategy
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
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        m = self.machine
        try:
            self.phase.emit(m, "extracting")
            files: LabFiles = extract_lab(
                self.zip_path, self.labs_dir, slug=self.slug,
                on_progress=lambda d, t: self.progress.emit(m, d * 100.0 / max(t, 1)),
                cancel=self._cancel.is_set,
            )
            image = inspect_image_tar(files.tar_path)
            self.image_ready.emit(m, image)

            if not self.client.image_exists(image.repo_tag):
                self.phase.emit(m, "loading")
                self.client.load_image(
                    files.tar_path,
                    on_line=lambda ln: self.log_line.emit(m, ln),
                    cancel=self._cancel.is_set,
                )
            if self._cancel.is_set():
                raise LabCancelled()

            self.phase.emit(m, "starting")
            ports = plan_port_mappings(image.exposed_ports) if self.strategy == "bridge+ports" else []
            self.client.run_container(
                image, m, self.slug, ports=ports,
                network_mode="host" if self.strategy == "host" else "bridge",
            )
            status = self.client.container_status(container_name_for(self.slug))
            if status is None:
                raise LabError("El contenedor se creó pero no se pudo inspeccionar.")
            self.done.emit(m, status, self.strategy)
        except LabCancelled:
            self.failed.emit(m, "Cancelado por el usuario")
        except LabError as exc:
            self.failed.emit(m, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("launch %s", m)
            self.failed.emit(m, f"Error inesperado: {exc}")


class _ActionWorker(QThread):
    """stop / start / restart / remove sobre un contenedor existente."""
    done = pyqtSignal(str, str, object)   # machine, action, ContainerStatus|None
    failed = pyqtSignal(str, str, str)    # machine, action, error

    def __init__(self, client: DockerClient, machine: str, slug: str, action: str,
                 image_tag: str = "", parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.machine = machine
        self.slug = slug
        self.action = action
        self.image_tag = image_tag

    def run(self) -> None:
        name = container_name_for(self.slug)
        try:
            if self.action == "stop":
                self.client.stop_container(name)
            elif self.action == "start":
                self.client.start_container(name)
            elif self.action == "restart":
                self.client.restart_container(name)
            elif self.action == "remove":
                self.client.remove_container(name)
                if self.image_tag:
                    self.client.remove_image(self.image_tag)
            status = None if self.action == "remove" else self.client.container_status(name)
            self.done.emit(self.machine, self.action, status)
        except LabError as exc:
            self.failed.emit(self.machine, self.action, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s %s", self.action, self.machine)
            self.failed.emit(self.machine, self.action, str(exc))


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

    def __init__(self, labs_dir: Path, network_preference: str = "auto",
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.labs_dir = Path(labs_dir).expanduser().resolve()
        self.labs_dir.mkdir(parents=True, exist_ok=True)
        self.client = DockerClient()
        self.docker_info = DockerInfo(available=bool(self.client.binary), running=False)
        self.network_preference = network_preference
        self._labs: Dict[str, LabState] = {}
        self._workers: List[QThread] = []

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
        tag = ""
        if st and remove_image:
            tag = st.image.repo_tag if st.image else (st.container.image if st.container else "")
        self._action(machine, "remove", image_tag=tag)

    def stop_all(self) -> None:
        """Parada sincrónica de todos los labs en marcha (al cerrar la app)."""
        for st in self.running_labs():
            try:
                self.client.stop_container(st.container_name, timeout=5)
            except LabError as exc:
                logger.warning("stop_all %s: %s", st.machine, exc)

    def open_shell(self, machine: str) -> bool:
        """Abre una terminal del sistema con `docker exec -it`."""
        st = self._labs.get(machine)
        if st is None:
            return False
        return open_in_terminal(self.client.exec_shell_command(st.container_name))

    def shutdown(self) -> None:
        for w in list(self._workers):
            try:
                if isinstance(w, _LaunchWorker):
                    w.cancel()
                if w.isRunning():
                    w.wait(3000)
            except RuntimeError:
                pass

    # ---------- slots ----------

    def _track(self, w: QThread) -> None:
        self._workers.append(w)
        w.finished.connect(lambda: self._untrack(w))
        w.start()

    def _untrack(self, w: QThread) -> None:
        if w in self._workers:
            self._workers.remove(w)
        w.deleteLater()

    def _on_docker_info(self, info: DockerInfo) -> None:
        self.docker_info = info
        self.docker_info_changed.emit(info)
        if info.running:
            self.refresh_containers()

    def _on_refresh_done(self, containers: list) -> None:
        seen: set[str] = set()
        for c in containers:
            machine = c.machine
            seen.add(machine)
            st = self._labs.get(machine)
            if st is None:
                st = LabState(machine=machine, slug=c.name.replace(CONTAINER_PREFIX, "", 1))
                self._labs[machine] = st
            if st.busy:
                continue
            st.container = c
            st.phase = "running" if c.is_running else "stopped"
            if c.ports:
                st.strategy = "bridge+ports"
            elif st.strategy == "bridge":
                st.strategy = self.strategy_for_current_platform()
            st.access_text = describe_access(c, st.strategy)
        # Labs que ya no existen en Docker (borrados fuera de la app)
        for machine in list(self._labs):
            st = self._labs[machine]
            if machine not in seen and not st.busy and st.phase in ("running", "stopped"):
                del self._labs[machine]
        self.list_changed.emit()

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
            self.lab_changed.emit(machine)

    def _on_launched(self, machine: str, status: ContainerStatus, strategy: str) -> None:
        st = self._labs.get(machine)
        if st is None:
            return
        st.container = status
        st.phase = "running" if status.is_running else "stopped"
        st.strategy = strategy
        st.access_text = describe_access(status, strategy)
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

    def _action(self, machine: str, action: str, image_tag: str = "") -> None:
        st = self._labs.get(machine)
        if st is None or st.busy:
            return
        st.phase = {"stop": "stopping", "remove": "removing"}.get(action, "starting")
        self.lab_changed.emit(machine)
        w = _ActionWorker(self.client, machine, st.slug, action, image_tag, parent=self)
        w.done.connect(self._on_action_done)
        w.failed.connect(self._on_action_failed)
        self._track(w)

    def _on_action_done(self, machine: str, action: str, status) -> None:
        st = self._labs.get(machine)
        if st is None:
            return
        if action == "remove" or status is None:
            self._labs.pop(machine, None)
        else:
            st.container = status
            st.phase = "running" if status.is_running else "stopped"
            st.access_text = describe_access(status, st.strategy)
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
