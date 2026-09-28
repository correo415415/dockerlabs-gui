"""Descargador HTTP directo para las máquinas de DockerLabs.

Desde 2026 el catálogo de DockerLabs sirve los `.zip` directamente desde
``https://gestion-maquinas.dockerlabs.es/dl/<slug>.zip`` (ya no se usa MEGA).

Características del endpoint (observadas empíricamente):

- Solo acepta ``GET`` (``HEAD`` devuelve 405).
- Devuelve ``Content-Length`` y ``Content-Disposition: attachment; filename="x.zip"``.
- **No** soporta ``Range`` (siempre responde 200 con el fichero completo), por lo
  que no hay reanudación ni descarga paralela.
- Devuelve **500 intermitentes**; con un reintento suele funcionar.
- 404 → ``{"detail": "No encontrado"}``.

Este módulo es independiente de Qt para poder testearse con ``pytest``.
"""
from __future__ import annotations

import logging
import os
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable, Optional
from urllib.parse import unquote, urlparse

import requests

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "dockerlabs-gui/1.0 (+https://github.com/correo415415/dockerlabs-gui)"
CHUNK_BYTES = 1024 * 1024          # 1 MiB por iteración
# Medido el 2026-09-28 contra gestion-maquinas.dockerlabs.es (nginx, HTTP/2):
#   * ~540 KB/s por conexión, tanto con curl como con requests → el límite lo
#     impone el servidor por conexión, no este código.
#   * NO soporta `Range` (devuelve 200 + fichero completo) → imposible partir la
#     descarga en trozos paralelos ni reanudar.
#   * 3 conexiones simultáneas obtuvieron ~500 KB/s CADA UNA → el límite es por
#     conexión, así que la única forma de ir más rápido es descargar VARIAS
#     máquinas a la vez (ajuste «Descargas simultáneas»), no una más deprisa.
SERVER_PER_CONNECTION_LIMIT_KBPS = 540
CONNECT_TIMEOUT = 20
READ_TIMEOUT = 120
MAX_RETRIES = 5
RETRY_BACKOFF_BASE = 1.5           # segundos; crece exponencialmente
RETRYABLE_STATUS = frozenset({500, 502, 503, 504, 429})
PROGRESS_INTERVAL = 0.15           # segundos entre emisiones de progreso


# =====================================================================
# Errores
# =====================================================================

class DownloadError(Exception):
    """Base de los errores del descargador."""


class DownloadCancelled(DownloadError):
    """El usuario canceló la descarga."""


class DownloadNotFound(DownloadError):
    """El servidor respondió 404: la máquina ya no está publicada."""


class DownloadServerError(DownloadError):
    """El servidor respondió 5xx de forma persistente tras los reintentos."""


class DownloadIntegrityError(DownloadError):
    """El fichero descargado no es un zip válido o está truncado."""


class UnsupportedLinkError(DownloadError):
    """El enlace no es descargable por HTTP directo (p.ej. MEGA antiguo)."""


# =====================================================================
# Modelos
# =====================================================================

@dataclass
class DownloadProgress:
    bytes_done: int
    bytes_total: int
    speed_bps: float
    eta_seconds: Optional[float]
    state: str  # queued | running | verifying | done | cancelled | error

    @property
    def percent(self) -> float:
        if not self.bytes_total:
            return 0.0
        return min(100.0, self.bytes_done * 100.0 / self.bytes_total)


@dataclass
class DownloadInfo:
    name: str
    size: int
    url: str


# =====================================================================
# Helpers
# =====================================================================

_INVALID_FN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_CD_FILENAME_STAR = re.compile(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)")
_CD_FILENAME = re.compile(r'filename\s*=\s*"?([^";]+)"?')


def sanitize_filename(name: str) -> str:
    name = _INVALID_FN_CHARS.sub("_", name).strip().rstrip(".")
    return name or "dockerlabs_machine.zip"


def filename_from_headers(headers, url: str) -> str:
    """Extrae el nombre de fichero de ``Content-Disposition`` o de la URL."""
    cd = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
    m = _CD_FILENAME_STAR.search(cd)
    if m:
        return sanitize_filename(unquote(m.group(1).strip()))
    m = _CD_FILENAME.search(cd)
    if m:
        return sanitize_filename(m.group(1).strip())
    path = urlparse(url).path
    tail = unquote(path.rsplit("/", 1)[-1]) if path else ""
    return sanitize_filename(tail or "dockerlabs_machine.zip")


def is_direct_http_link(url: str) -> bool:
    """True si el enlace se puede bajar con un GET normal (no MEGA/Drive)."""
    if not url:
        return False
    u = url.strip().lower()
    if not (u.startswith("http://") or u.startswith("https://")):
        return False
    host = urlparse(u).netloc
    blocked = ("mega.nz", "mega.co.nz", "mega.io", "drive.google.com", "mediafire.com")
    return not any(b in host for b in blocked)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    i = 1
    while True:
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def verify_zip(path: Path) -> None:
    """Comprueba que el fichero es un zip válido (firma + tabla central + CRC)."""
    try:
        with path.open("rb") as fh:
            sig = fh.read(4)
    except OSError as exc:
        raise DownloadIntegrityError(f"No se pudo leer el fichero: {exc}") from exc
    if sig[:2] != b"PK":
        raise DownloadIntegrityError(
            "El fichero descargado no es un zip (firma inválida). "
            "Puede que el servidor devolviera una página de error."
        )
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
    except zipfile.BadZipFile as exc:
        raise DownloadIntegrityError(f"Zip corrupto o truncado: {exc}") from exc
    if bad is not None:
        raise DownloadIntegrityError(f"CRC incorrecto en '{bad}' dentro del zip")


# =====================================================================
# Descargador
# =====================================================================

class HttpDownloader:
    """Descarga un fichero por HTTP con reintentos, progreso y cancelación."""

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        user_agent: str = DEFAULT_USER_AGENT,
        max_retries: int = MAX_RETRIES,
        connect_timeout: float = CONNECT_TIMEOUT,
        read_timeout: float = READ_TIMEOUT,
        verify_archive: bool = True,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "application/octet-stream, application/zip, */*;q=0.5",
        })
        self.max_retries = max(1, int(max_retries))
        self.timeout = (connect_timeout, read_timeout)
        self.verify_archive = verify_archive
        self._sleep = sleep

    # ---------- API ----------

    def download(
        self,
        url: str,
        dest_dir: Path,
        cancel_event: Optional[Event] = None,
        on_progress: Optional[Callable[[DownloadProgress], None]] = None,
        on_info: Optional[Callable[[DownloadInfo], None]] = None,
        overwrite: bool = False,
        preferred_name: Optional[str] = None,
    ) -> Path:
        """Descarga ``url`` en ``dest_dir`` y devuelve la ruta final.

        Reintenta la petición completa (el servidor no soporta Range) ante
        errores de red o 5xx. Escribe en ``<nombre>.part`` y renombra al
        terminar y verificar.
        """
        if not is_direct_http_link(url):
            raise UnsupportedLinkError(
                "Este enlace no es una descarga HTTP directa. Actualiza el catálogo: "
                "DockerLabs ya sirve todas las máquinas desde su propio servidor."
            )
        cancel = cancel_event or Event()
        dest_dir = Path(dest_dir).expanduser().resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            if cancel.is_set():
                raise DownloadCancelled()
            try:
                return self._attempt(url, dest_dir, cancel, on_progress, on_info,
                                     overwrite, preferred_name, attempt)
            except (DownloadCancelled, DownloadNotFound, UnsupportedLinkError):
                raise
            except DownloadIntegrityError as exc:
                # Un zip truncado suele ser una conexión cortada: reintentamos.
                last_exc = exc
                logger.warning("Intento %d/%d: integridad fallida: %s",
                               attempt, self.max_retries, exc)
            except DownloadServerError as exc:
                last_exc = exc
                logger.warning("Intento %d/%d: %s", attempt, self.max_retries, exc)
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning("Intento %d/%d: error de red: %s",
                               attempt, self.max_retries, exc)
            if attempt < self.max_retries:
                delay = min(30.0, RETRY_BACKOFF_BASE * (2 ** (attempt - 1)))
                self._wait(delay, cancel)
        if on_progress:
            on_progress(DownloadProgress(0, 0, 0.0, None, "error"))
        if isinstance(last_exc, DownloadError):
            raise last_exc
        raise DownloadServerError(
            f"No se pudo descargar tras {self.max_retries} intentos: {last_exc}"
        )

    # ---------- Internas ----------

    def _wait(self, seconds: float, cancel: Event) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if cancel.is_set():
                raise DownloadCancelled()
            self._sleep(min(0.2, max(0.0, end - time.monotonic())))

    def _attempt(self, url, dest_dir, cancel, on_progress, on_info,
                 overwrite, preferred_name, attempt) -> Path:
        tmp_path: Optional[Path] = None
        resp = self.session.get(url, stream=True, timeout=self.timeout,
                                allow_redirects=True)
        try:
            status = resp.status_code
            if status == 404:
                raise DownloadNotFound(
                    "El servidor de DockerLabs respondió 404: la máquina no está "
                    "disponible (puede haber sido retirada o renombrada)."
                )
            if status in RETRYABLE_STATUS:
                raise DownloadServerError(
                    f"El servidor respondió HTTP {status} (intento {attempt})."
                )
            if status >= 400:
                body = ""
                try:
                    body = resp.text[:200]
                except Exception:  # noqa: BLE001
                    pass
                raise DownloadError(f"HTTP {status} al descargar: {body!r}")

            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "text/html" in ctype:
                # Un HTML con 200 es casi seguro una página de error/captcha.
                raise DownloadServerError(
                    "El servidor devolvió una página HTML en lugar del zip."
                )

            total = int(resp.headers.get("Content-Length") or 0)
            name = preferred_name or filename_from_headers(resp.headers, url)
            if not name.lower().endswith((".zip", ".7z", ".rar", ".tar", ".gz", ".tgz")):
                name += ".zip"
            info = DownloadInfo(name=name, size=total, url=url)
            if on_info:
                on_info(info)

            final_path = dest_dir / name
            if final_path.exists() and not overwrite:
                final_path = unique_path(final_path)
            tmp_path = final_path.with_suffix(final_path.suffix + ".part")

            self._stream_to_file(resp, tmp_path, total, cancel, on_progress)

            if total and tmp_path.stat().st_size != total:
                raise DownloadIntegrityError(
                    f"Tamaño incorrecto: {tmp_path.stat().st_size} de {total} bytes"
                )

            if self.verify_archive and name.lower().endswith(".zip"):
                if on_progress:
                    on_progress(DownloadProgress(total, total, 0.0, 0.0, "verifying"))
                verify_zip(tmp_path)

            os.replace(tmp_path, final_path)
            if on_progress:
                on_progress(DownloadProgress(total, total, 0.0, 0.0, "done"))
            return final_path
        except DownloadCancelled:
            self._cleanup_part(tmp_path)
            if on_progress:
                on_progress(DownloadProgress(0, 0, 0.0, None, "cancelled"))
            raise
        except Exception:
            self._cleanup_part(tmp_path)
            raise
        finally:
            resp.close()

    @staticmethod
    def _cleanup_part(tmp_path: Optional[Path]) -> None:
        if tmp_path is None:
            return
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass

    def _stream_to_file(self, resp, tmp_path: Path, total: int,
                        cancel: Event, on_progress) -> None:
        bytes_done = 0
        start_t = time.monotonic()
        last_emit = 0.0
        # Ventana móvil de 5 s para suavizar la velocidad
        window: list[tuple[float, int]] = []

        with tmp_path.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=CHUNK_BYTES):
                if cancel.is_set():
                    raise DownloadCancelled()
                if not chunk:
                    continue
                fh.write(chunk)
                bytes_done += len(chunk)
                now = time.monotonic()
                window.append((now, bytes_done))
                while window and now - window[0][0] > 5.0:
                    window.pop(0)
                if on_progress and (now - last_emit) >= PROGRESS_INTERVAL:
                    if len(window) >= 2:
                        dt = window[-1][0] - window[0][0]
                        db = window[-1][1] - window[0][1]
                        speed = db / dt if dt > 0 else 0.0
                    else:
                        elapsed = max(now - start_t, 1e-6)
                        speed = bytes_done / elapsed
                    eta = ((total - bytes_done) / speed) if (speed > 0 and total) else None
                    on_progress(DownloadProgress(bytes_done, total, speed, eta, "running"))
                    last_emit = now


# =====================================================================
# Conveniencia
# =====================================================================

def download_file(url: str, dest_dir: Path, **kwargs) -> Path:
    """Atajo: instancia un ``HttpDownloader`` y descarga."""
    return HttpDownloader().download(url, dest_dir, **kwargs)


def human_bytes(n: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    s = float(n)
    i = 0
    while s >= 1024 and i < len(units) - 1:
        s /= 1024
        i += 1
    return f"{s:.1f} {units[i]}" if i else f"{int(s)} {units[i]}"
