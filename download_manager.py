"""Gestor de descargas con QThread para integrarse con la UI.

- Cada descarga es un `DownloadJob` (QThread) que descarga un enlace MEGA
  emitiendo señales `progress`, `info_ready`, `finished_ok`, `failed`.
- `DownloadManager` (QObject) mantiene el registro global de descargas
  activas/terminadas y emite señales agregadas para la UI.
"""
from __future__ import annotations

import logging
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from mega_downloader import (
    DownloadProgress,
    MegaApiError,
    MegaCancelled,
    MegaIntegrityError,
    MegaPublicDownloader,
    MegaQuotaError,
)

logger = logging.getLogger(__name__)


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


class DownloadJob(QThread):
    progress = pyqtSignal(str, DownloadProgress)         # machine, prog
    info_ready = pyqtSignal(str, dict)                   # machine, info
    finished_ok = pyqtSignal(str, str)                   # machine, final_path
    failed = pyqtSignal(str, str)                        # machine, error_msg
    quota_exceeded = pyqtSignal(str, str)                # machine, error_msg

    def __init__(self, machine: str, url: str, dest_dir: Path,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.machine = machine
        self.url = url
        self.dest_dir = dest_dir
        self._cancel = threading.Event()
        self._downloader = MegaPublicDownloader()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            def _on_info(info: dict) -> None:
                self.info_ready.emit(self.machine, info)

            def _on_progress(p: DownloadProgress) -> None:
                self.progress.emit(self.machine, p)

            final = self._downloader.download(
                self.url,
                self.dest_dir,
                cancel_event=self._cancel,
                on_progress=_on_progress,
                on_info=_on_info,
            )
            self.finished_ok.emit(self.machine, str(final))
        except MegaCancelled:
            self.failed.emit(self.machine, "Cancelada por el usuario")
        except MegaQuotaError as exc:
            # MEGA ha bloqueado la descarga por límite de ancho de banda.
            # Señal específica para que la UI muestre un toast distinto.
            logger.warning("MEGA quota: %s", exc)
            self.quota_exceeded.emit(self.machine, str(exc))
        except MegaApiError as exc:
            self.failed.emit(self.machine, str(exc))
        except MegaIntegrityError as exc:
            self.failed.emit(self.machine, f"Integridad: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Download error")
            self.failed.emit(self.machine, str(exc))


class DownloadManager(QObject):
    """Gestor global de descargas (un job por máquina simultáneamente)."""

    state_changed = pyqtSignal(str)        # machine
    list_changed = pyqtSignal()
    download_completed = pyqtSignal(str, str)  # machine, final_path
    quota_exceeded = pyqtSignal(str, str)  # machine, error_msg

    def __init__(self, dest_dir: Path, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.dest_dir = Path(dest_dir).expanduser().resolve()
        self.dest_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: Dict[str, DownloadJob] = {}
        self._states: Dict[str, DownloadState] = {}
        self._completed_paths: Dict[str, Path] = {}
        self._scan_existing()

    def set_dest_dir(self, new_dir: Path) -> None:
        """Cambia la carpeta de destino en caliente.

        Las descargas en curso siguen usando la carpeta antigua; las nuevas
        usaran la nueva ruta. Tambien re-escanea la nueva carpeta para
        descubrir ficheros que ya esten alli.
        """
        new_dir = Path(new_dir).expanduser().resolve()
        new_dir.mkdir(parents=True, exist_ok=True)
        if new_dir == self.dest_dir:
            return
        self.dest_dir = new_dir
        # Reiniciamos el indice de "ya descargados" basandonos solo en la nueva
        # carpeta. Conservamos los states de jobs en curso/terminados de la
        # sesion actual (los path absolutos siguen siendo validos).
        self._completed_paths = {
            m: p for m, p in self._completed_paths.items()
            if p.exists() and p.is_absolute()
        }
        self._scan_existing()
        self.list_changed.emit()

    # ---------- API ----------

    def start(self, machine: str, url: str) -> bool:
        if machine in self._jobs and self._jobs[machine].isRunning():
            return False
        # Si ya tenemos la maquina descargada y el fichero sigue ahi, no
        # volvemos a descargar. La UI debe interceptar antes, pero esto
        # es la red de seguridad final.
        #
        # IMPORTANTE: NO creamos `_states[machine]` para no contaminar la
        # pagina 'Descargas' (esa pagina solo muestra descargas de la
        # sesion actual). El mapa `_completed_paths` ya basta para que la
        # UI sepa que la maquina esta en disco.
        existing = self._completed_paths.get(machine)
        if existing and existing.exists():
            self.list_changed.emit()
            return False
        state = DownloadState(machine=machine, url=url, dest_dir=self.dest_dir,
                              state="queued")
        self._states[machine] = state
        job = DownloadJob(machine, url, self.dest_dir, parent=self)
        job.info_ready.connect(self._on_info)
        job.progress.connect(self._on_progress)
        job.finished_ok.connect(self._on_finished_ok)
        job.failed.connect(self._on_failed)
        job.quota_exceeded.connect(self._on_quota_exceeded)
        job.finished.connect(lambda m=machine: self._cleanup(m))
        self._jobs[machine] = job
        job.start()
        self.list_changed.emit()
        self.state_changed.emit(machine)
        return True

    def cancel(self, machine: str) -> None:
        job = self._jobs.get(machine)
        if job is not None and job.isRunning():
            job.cancel()

    def state(self, machine: str) -> Optional[DownloadState]:
        return self._states.get(machine)

    def all_states(self) -> list[DownloadState]:
        return list(self._states.values())

    def downloaded_paths(self) -> Dict[str, Path]:
        """Mapa de máquina → fichero final, filtrando los que aún existen."""
        return {
            m: p for m, p in self._completed_paths.items() if p.exists()
        }

    def reconcile_with_csv(self, machine_names: Iterable[str]) -> None:
        """Cruza los archivos detectados en la carpeta con la lista de
        nombres reales del CSV.

        Al arrancar, `_scan_existing` registra ficheros con clave provisional
        (el ``stem`` del fichero, p.ej. ``psycho``). Cuando el CSV ya está
        cargado, este método los reemplaza por el nombre EXACTO de la
        máquina (``Psycho``), de modo que el cruce con el estado de la UI
        funcione y futuras descargas de la misma máquina se detecten como
        ya existentes.

        El emparejamiento ignora mayúsculas, acentos y caracteres no
        alfanuméricos para resistir variaciones triviales del CSV vs el
        nombre que MEGA guarda en disco.
        """
        # Mapa normalizado -> nombre real del CSV
        by_norm: Dict[str, str] = {}
        for raw in machine_names:
            if raw:
                by_norm.setdefault(_normalize_name(raw), raw)

        renamed: Dict[str, Path] = {}
        for key, path in self._completed_paths.items():
            # Si la clave ya es un nombre real del CSV, lo dejamos tal cual
            if key in by_norm.values():
                renamed[key] = path
                continue
            # Intentamos resolver por nombre del fichero
            norm = _normalize_name(Path(path).stem)
            real = by_norm.get(norm)
            if real is None:
                # Probamos quitando sufijos tipo ' (1)' o '.zip.part'
                cleaned = re.sub(r"\s*\(\d+\)$", "", Path(path).stem)
                real = by_norm.get(_normalize_name(cleaned))
            if real is not None:
                renamed[real] = path
            else:
                # No emparejado: lo dejamos con su clave provisional para no
                # perder la referencia (el usuario podrá borrarlo a mano).
                renamed[key] = path

        self._completed_paths = renamed
        self.list_changed.emit()

    def remove(self, machine: str, also_delete_file: bool = False) -> None:
        state = self._states.pop(machine, None)
        path = self._completed_paths.pop(machine, None)
        if also_delete_file and path and path.exists():
            try:
                path.unlink()
            except OSError:
                pass
        self.list_changed.emit()

    def shutdown(self) -> None:
        for job in list(self._jobs.values()):
            try:
                job.cancel()
                if job.isRunning():
                    job.quit()
                    job.wait(1500)
            except RuntimeError:
                pass

    # ---------- Slots de los workers ----------

    def _on_info(self, machine: str, info: dict) -> None:
        s = self._states.get(machine)
        if s is None:
            return
        s.filename = info.get("name", "")
        s.size_total = int(info.get("size", 0))
        s.state = "running"
        self.state_changed.emit(machine)

    def _on_progress(self, machine: str, p: DownloadProgress) -> None:
        s = self._states.get(machine)
        if s is None:
            return
        s.bytes_done = p.bytes_done
        s.size_total = p.bytes_total or s.size_total
        s.speed_bps = p.speed_bps
        s.eta_seconds = p.eta_seconds
        s.state = p.state
        self.state_changed.emit(machine)

    def _on_finished_ok(self, machine: str, final_path: str) -> None:
        s = self._states.get(machine)
        path = Path(final_path)
        if s is not None:
            s.state = "done"
            s.final_path = path
            s.bytes_done = s.size_total
        self._completed_paths[machine] = path
        self.state_changed.emit(machine)
        self.download_completed.emit(machine, str(path))
        # Auto-eliminar el state de la lista de descargas activas: ya está
        # en `_completed_paths`, así que la máquina seguirá reconociéndose
        # como descargada, pero no aparecerá en la página 'Descargas'.
        self._states.pop(machine, None)
        self.list_changed.emit()

    def _on_failed(self, machine: str, error: str) -> None:
        s = self._states.get(machine)
        if s is not None:
            s.state = "cancelled" if "Cancelada" in error else "error"
            s.error = error
        self.state_changed.emit(machine)
        self.list_changed.emit()

    def _on_quota_exceeded(self, machine: str, error: str) -> None:
        """Slot dedicado: MEGA ha bloqueado la descarga por cuota.

        Marca el state como error y reemite la señal hacia la UI para
        que muestre un toast específico (no un toast genérico de error).
        """
        s = self._states.get(machine)
        if s is not None:
            s.state = "error"
            s.error = error
        self.state_changed.emit(machine)
        self.list_changed.emit()
        self.quota_exceeded.emit(machine, error)

    def _cleanup(self, machine: str) -> None:
        # No borramos del registro: queremos seguir mostrando el ítem
        self._jobs.pop(machine, None)

    def _scan_existing(self) -> None:
        """Indexa los archivos válidos de la carpeta de descargas.

        Se usa una clave provisional basada en el nombre del fichero. Más
        tarde `reconcile_with_csv` la sustituye por el nombre real de la
        máquina cuando el CSV está disponible.
        """
        if not self.dest_dir.exists():
            return
        for p in self.dest_dir.iterdir():
            if not p.is_file():
                continue
            if p.name.endswith(".part"):
                continue
            if p.name.startswith("."):
                continue
            # Solo aceptamos extensiones plausibles de máquina (zip/7z/rar/tar)
            if p.suffix.lower() not in {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".tar.gz"}:
                continue
            # clave provisional = nombre sin extensión
            key = p.stem
            # Evita pisar entradas ya resueltas con el nombre real del CSV
            if key not in self._completed_paths:
                self._completed_paths[key] = p


def _normalize_name(name: str) -> str:
    """Normaliza un nombre para comparaciones fuzzy (lowercase, sin acentos,
    sin caracteres no alfanuméricos).

    'Pequeñas-Mentirosas' -> 'pequenasmentirosas'
    'WhereIsMyWebShell'    -> 'whereismywebshell'
    """
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", stripped.lower())


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
