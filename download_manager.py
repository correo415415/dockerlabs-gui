"""Gestor de descargas con QThread para integrarse con la UI.

- Cada descarga es un `DownloadJob` (QThread) que baja un fichero por HTTP
  directo emitiendo señales `progress`, `info_ready`, `finished_ok`, `failed`.
- `DownloadManager` (QObject) mantiene una cola con un límite de descargas
  simultáneas, el registro de estados y el índice de ficheros ya en disco.
"""
from __future__ import annotations

import logging
import re
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from http_downloader import (
    DownloadCancelled,
    DownloadError,
    DownloadInfo,
    DownloadIntegrityError,
    DownloadNotFound,
    DownloadProgress,
    DownloadServerError,
    HttpDownloader,
    UnsupportedLinkError,
)

logger = logging.getLogger(__name__)

ARCHIVE_SUFFIXES = {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz"}


@dataclass
class DownloadState:
    machine: str
    url: str
    dest_dir: Path
    filename: str = ""
    size_total: int = 0
    bytes_done: int = 0
    speed_bps: float = 0.0
    eta_seconds: Optional[float] = None
    state: str = "queued"  # queued | running | verifying | done | error | cancelled
    error: str = ""
    final_path: Optional[Path] = None

    @property
    def percent(self) -> float:
        if not self.size_total:
            return 0.0
        return min(100.0, self.bytes_done * 100.0 / self.size_total)

    @property
    def is_active(self) -> bool:
        return self.state in ("queued", "running", "verifying")


class DownloadJob(QThread):
    progress = pyqtSignal(str, object)         # machine, DownloadProgress
    info_ready = pyqtSignal(str, object)       # machine, DownloadInfo
    finished_ok = pyqtSignal(str, str)         # machine, final_path
    failed = pyqtSignal(str, str, str)         # machine, kind, error_msg

    def __init__(self, machine: str, url: str, dest_dir: Path,
                 preferred_name: Optional[str] = None,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.machine = machine
        self.url = url
        self.dest_dir = dest_dir
        self.preferred_name = preferred_name
        self._cancel = threading.Event()
        self._downloader = HttpDownloader()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            final = self._downloader.download(
                self.url,
                self.dest_dir,
                cancel_event=self._cancel,
                on_progress=lambda p: self.progress.emit(self.machine, p),
                on_info=lambda i: self.info_ready.emit(self.machine, i),
                preferred_name=self.preferred_name,
            )
            self.finished_ok.emit(self.machine, str(final))
        except DownloadCancelled:
            self.failed.emit(self.machine, "cancelled", "Cancelada por el usuario")
        except DownloadNotFound as exc:
            self.failed.emit(self.machine, "not_found", str(exc))
        except UnsupportedLinkError as exc:
            self.failed.emit(self.machine, "unsupported", str(exc))
        except DownloadIntegrityError as exc:
            self.failed.emit(self.machine, "integrity", str(exc))
        except DownloadServerError as exc:
            self.failed.emit(self.machine, "server", str(exc))
        except DownloadError as exc:
            self.failed.emit(self.machine, "error", str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error inesperado descargando %s", self.machine)
            self.failed.emit(self.machine, "error", str(exc))


class DownloadManager(QObject):
    """Gestor global de descargas con cola y límite de concurrencia."""

    state_changed = pyqtSignal(str)                 # machine
    list_changed = pyqtSignal()
    download_completed = pyqtSignal(str, str)       # machine, final_path
    download_failed = pyqtSignal(str, str, str)     # machine, kind, error

    def __init__(self, dest_dir: Path, max_concurrent: int = 2,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.dest_dir = Path(dest_dir).expanduser().resolve()
        self.dest_dir.mkdir(parents=True, exist_ok=True)
        self.max_concurrent = max(1, int(max_concurrent))
        self._jobs: Dict[str, DownloadJob] = {}
        self._queue: List[str] = []
        self._states: Dict[str, DownloadState] = {}
        self._completed_paths: Dict[str, Path] = {}
        self._scan_existing()

    # ---------- Configuración ----------

    def set_max_concurrent(self, n: int) -> None:
        self.max_concurrent = max(1, int(n))
        self._pump()

    def set_dest_dir(self, new_dir: Path) -> None:
        """Cambia la carpeta de destino en caliente (las descargas en curso
        siguen en la carpeta antigua) y re-escanea la nueva."""
        new_dir = Path(new_dir).expanduser().resolve()
        new_dir.mkdir(parents=True, exist_ok=True)
        if new_dir == self.dest_dir:
            return
        self.dest_dir = new_dir
        self._completed_paths = {
            m: p for m, p in self._completed_paths.items()
            if p.exists() and p.is_absolute()
        }
        self._scan_existing()
        self.list_changed.emit()

    # ---------- API ----------

    def start(self, machine: str, url: str,
              preferred_name: Optional[str] = None) -> bool:
        """Encola una descarga. Devuelve False si ya está activa o en disco."""
        st = self._states.get(machine)
        if st is not None and st.is_active:
            return False
        existing = self._completed_paths.get(machine)
        if existing and existing.exists():
            self.list_changed.emit()
            return False
        state = DownloadState(machine=machine, url=url, dest_dir=self.dest_dir,
                              state="queued", filename=preferred_name or "")
        self._states[machine] = state
        if machine not in self._queue:
            self._queue.append(machine)
        self.list_changed.emit()
        self.state_changed.emit(machine)
        self._pump()
        return True

    def cancel(self, machine: str) -> None:
        job = self._jobs.get(machine)
        if job is not None and job.isRunning():
            job.cancel()
            return
        if machine in self._queue:  # en cola, aún no había empezado
            self._queue.remove(machine)
            s = self._states.get(machine)
            if s is not None:
                s.state = "cancelled"
                s.error = "Cancelada por el usuario"
            self.state_changed.emit(machine)
            self.list_changed.emit()

    def state(self, machine: str) -> Optional[DownloadState]:
        return self._states.get(machine)

    def all_states(self) -> list[DownloadState]:
        return list(self._states.values())

    def active_count(self) -> int:
        return sum(1 for s in self._states.values() if s.is_active)

    def downloaded_paths(self) -> Dict[str, Path]:
        """Mapa máquina → fichero final, filtrando los que aún existen."""
        return {m: p for m, p in self._completed_paths.items() if p.exists()}

    def reconcile_with_catalog(self, machine_names: Iterable[str]) -> None:
        """Cruza los archivos detectados en la carpeta con los nombres reales
        del catálogo (ignorando mayúsculas, acentos y no alfanuméricos)."""
        by_norm: Dict[str, str] = {}
        for raw in machine_names:
            if raw:
                by_norm.setdefault(normalize_name(raw), raw)
        real_names = set(by_norm.values())
        renamed: Dict[str, Path] = {}
        for key, path in self._completed_paths.items():
            if key in real_names:
                renamed[key] = path
                continue
            stem = Path(path).stem
            real = by_norm.get(normalize_name(stem))
            if real is None:
                cleaned = re.sub(r"\s*\(\d+\)$", "", stem)
                real = by_norm.get(normalize_name(cleaned))
            renamed[real if real is not None else key] = path
        self._completed_paths = renamed
        self.list_changed.emit()

    reconcile_with_csv = reconcile_with_catalog  # compat

    def remove(self, machine: str, also_delete_file: bool = False) -> None:
        self._states.pop(machine, None)
        if machine in self._queue:
            self._queue.remove(machine)
        path = self._completed_paths.pop(machine, None)
        if also_delete_file and path and path.exists():
            try:
                path.unlink()
            except OSError as exc:
                logger.warning("No se pudo borrar %s: %s", path, exc)
        self.list_changed.emit()

    def clear_finished(self) -> None:
        """Quita de la lista las descargas terminadas / fallidas / canceladas."""
        for m in [m for m, s in self._states.items() if not s.is_active]:
            self._states.pop(m, None)
        self.list_changed.emit()

    def shutdown(self) -> None:
        self._queue.clear()
        for job in list(self._jobs.values()):
            try:
                job.cancel()
                if job.isRunning():
                    job.wait(3000)
            except RuntimeError:
                pass

    # ---------- Cola ----------

    def _pump(self) -> None:
        running = sum(1 for j in self._jobs.values() if j.isRunning())
        while self._queue and running < self.max_concurrent:
            machine = self._queue.pop(0)
            st = self._states.get(machine)
            if st is None or st.state != "queued":
                continue
            job = DownloadJob(machine, st.url, self.dest_dir,
                              preferred_name=st.filename or None, parent=self)
            job.info_ready.connect(self._on_info)
            job.progress.connect(self._on_progress)
            job.finished_ok.connect(self._on_finished_ok)
            job.failed.connect(self._on_failed)
            job.finished.connect(lambda m=machine: self._cleanup(m))
            self._jobs[machine] = job
            job.start()
            running += 1

    # ---------- Slots de los workers ----------

    def _on_info(self, machine: str, info: DownloadInfo) -> None:
        s = self._states.get(machine)
        if s is None:
            return
        s.filename = info.name
        s.size_total = int(info.size)
        s.state = "running"
        self.state_changed.emit(machine)

    def _on_progress(self, machine: str, p: DownloadProgress) -> None:
        s = self._states.get(machine)
        if s is None:
            return
        s.bytes_done = p.bytes_done
        if p.bytes_total:
            s.size_total = p.bytes_total
        s.speed_bps = p.speed_bps
        s.eta_seconds = p.eta_seconds
        if p.state in ("running", "verifying"):
            s.state = p.state
        self.state_changed.emit(machine)

    def _on_finished_ok(self, machine: str, final_path: str) -> None:
        s = self._states.get(machine)
        path = Path(final_path)
        if s is not None:
            s.state = "done"
            s.final_path = path
            s.bytes_done = s.size_total
            s.filename = path.name
        self._completed_paths[machine] = path
        self.state_changed.emit(machine)
        self.download_completed.emit(machine, str(path))
        self.list_changed.emit()

    def _on_failed(self, machine: str, kind: str, error: str) -> None:
        s = self._states.get(machine)
        if s is not None:
            s.state = "cancelled" if kind == "cancelled" else "error"
            s.error = error
        self.state_changed.emit(machine)
        self.list_changed.emit()
        if kind != "cancelled":
            self.download_failed.emit(machine, kind, error)

    def _cleanup(self, machine: str) -> None:
        job = self._jobs.pop(machine, None)
        if job is not None:
            job.deleteLater()
        self._pump()

    def _scan_existing(self) -> None:
        """Indexa los archivos de la carpeta de descargas con clave provisional
        (stem); `reconcile_with_catalog` la sustituye por el nombre real."""
        if not self.dest_dir.exists():
            return
        for p in self.dest_dir.iterdir():
            if not p.is_file() or p.name.startswith("."):
                continue
            if p.name.endswith(".part"):
                continue
            if p.suffix.lower() not in ARCHIVE_SUFFIXES:
                continue
            self._completed_paths.setdefault(p.stem, p)


# =====================================================================
# Helpers
# =====================================================================

def normalize_name(name: str) -> str:
    """'Pequeñas-Mentirosas' -> 'pequenasmentirosas'"""
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", stripped.lower())


_normalize_name = normalize_name  # compat


def human_size(n: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    s = float(n)
    i = 0
    while s >= 1024 and i < len(units) - 1:
        s /= 1024
        i += 1
    return f"{s:.1f} {units[i]}" if i else f"{int(s)} B"


def human_eta(sec: Optional[float]) -> str:
    if sec is None or sec <= 0 or sec == float("inf"):
        return "—"
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m {sec % 60:02d}s"
    return f"{sec // 3600}h {(sec % 3600) // 60:02d}m"
