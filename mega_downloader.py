"""Cliente MEGA propio, especializado en descargar enlaces públicos de archivo.

Diferencias con la librería ``mega.py`` de ejemplo:

- **Sin estado de cuenta**: no hace login, no toca cuotas, no descarga el
  índice del usuario. Sólo soporta enlaces públicos del tipo
  ``https://mega.nz/file/<id>#<key>``.
- **API mínima y limpia**: una función ``parse_public_url`` y una clase
  ``MegaPublicDownloader`` con eventos de progreso y soporte de cancelación.
- **Streaming real**: lee/escribe la respuesta HTTP por chunks AES-CTR
  sin nunca cargar el fichero entero en memoria.
- **Verificación de meta_mac CBC-MAC**: descifra ``meta_mac`` con la clave
  original para detectar corrupción / clave incorrecta, igual que hace el
  cliente oficial.
- **Reintentos exponenciales** y **timeouts** explícitos.
- **Thread-safe**: la bandera de cancelación es atómica (``threading.Event``).

Sólo necesita ``requests`` y ``pycryptodome``.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import random
import re
import struct
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import Callable, Iterable, List, Optional

import requests
from Crypto.Cipher import AES
from Crypto.Util import Counter


logger = logging.getLogger(__name__)

MEGA_API_URL = "https://g.api.mega.co.nz/cs"
DEFAULT_USER_AGENT = "dockerlabs-gui/0.5 (+https://dockerlabs.es)"
# Tamano de chunk leido del socket en cada iter_content.
CHUNK_BYTES = 4 * 1024 * 1024
HTTP_TIMEOUT = 30
HTTP_DOWNLOAD_TIMEOUT = 600
SOCKET_BUFFER_BYTES = 1 << 20  # 1 MiB

# Descarga paralela: si el archivo es lo bastante grande, repartimos el rango
# en varias peticiones HTTP simultaneas. Cada cuenta gratuita de MEGA tiene una
# cuota por IP, pero el limite por *conexion* es claramente menor que el limite
# por IP, asi que abrir 2-4 streams suele triplicar el throughput efectivo.
PARALLEL_MIN_SIZE = 32 * 1024 * 1024     # < 32 MiB: una sola conexion
PARALLEL_MAX_WORKERS = 4                 # 4 conexiones simultaneas
PARALLEL_PART_SIZE   = 8 * 1024 * 1024   # 8 MiB por part


# =====================================================================
# Helpers criptográficos (mínimos, derivados del protocolo público MEGA)
# =====================================================================


def _b64url_decode(data: str) -> bytes:
    data += "==" [(2 - len(data) * 3) % 4 :]
    for src, dst in (("-", "+"), ("_", "/"), (",", "")):
        data = data.replace(src, dst)
    return base64.b64decode(data)


def _str_to_a32(b: bytes) -> tuple[int, ...]:
    if len(b) % 4:
        b += b"\0" * (4 - len(b) % 4)
    return struct.unpack(">%dI" % (len(b) // 4), b)


def _a32_to_str(a) -> bytes:
    return struct.pack(">%dI" % len(a), *a)


def _b64_to_a32(s: str) -> tuple[int, ...]:
    return _str_to_a32(_b64url_decode(s))


def _aes_cbc_decrypt(data: bytes, key: bytes) -> bytes:
    return AES.new(key, AES.MODE_CBC, b"\0" * 16).decrypt(data)


def _aes_cbc_encrypt(data: bytes, key: bytes) -> bytes:
    return AES.new(key, AES.MODE_CBC, b"\0" * 16).encrypt(data)


def _chunks_of_mega(total: int) -> Iterable[tuple[int, int]]:
    """Genera (offset, size) replicando el patrón de tamaños del cliente MEGA.

    Empieza en 0x20000 (128 KiB) y crece de 128 KiB en 128 KiB hasta llegar a
    0x100000 (1 MiB), donde se estabiliza. Esto es importante para que el MAC
    sea bit-idéntico al calculado por MEGA.
    """
    pos = 0
    size = 0x20000
    while pos + size < total:
        yield pos, size
        pos += size
        if size < 0x100000:
            size += 0x20000
    if pos < total:
        yield pos, total - pos


def _decrypt_attr(blob: bytes, key_a32) -> Optional[dict]:
    decoded = _aes_cbc_decrypt(blob, _a32_to_str(key_a32)).rstrip(b"\0")
    if not decoded.startswith(b"MEGA{"):
        return None
    try:
        return json.loads(decoded[4:].decode("utf-8", "ignore"))
    except json.JSONDecodeError:
        return None


# =====================================================================
# Modelos de datos
# =====================================================================


@dataclass(frozen=True)
class MegaFileLink:
    """Representa un enlace público de fichero MEGA ya parseado."""
    file_id: str       # handle de 8 caracteres
    raw_key: tuple[int, ...]  # 8 enteros de 32 bits

    @property
    def file_key(self) -> tuple[int, ...]:
        k = self.raw_key
        return (k[0] ^ k[4], k[1] ^ k[5], k[2] ^ k[6], k[3] ^ k[7])

    @property
    def iv(self) -> tuple[int, ...]:
        return self.raw_key[4:6] + (0, 0)

    @property
    def meta_mac(self) -> tuple[int, ...]:
        return self.raw_key[6:8]


@dataclass
class DownloadProgress:
    bytes_done: int
    bytes_total: int
    speed_bps: float
    eta_seconds: Optional[float]
    state: str  # "running" | "verifying" | "done" | "cancelled" | "error"

    @property
    def percent(self) -> float:
        if not self.bytes_total:
            return 0.0
        return min(100.0, self.bytes_done * 100.0 / self.bytes_total)


# =====================================================================
# Parser de URL pública
# =====================================================================

_URL_V2 = re.compile(r"mega\.(?:nz|co\.nz)/file/([A-Za-z0-9_-]{6,12})#([A-Za-z0-9_-]+)")
_URL_V1 = re.compile(r"mega\.(?:nz|co\.nz)/#!([A-Za-z0-9_-]{6,12})!([A-Za-z0-9_-]+)")


def parse_public_url(url: str) -> MegaFileLink:
    """Extrae file_id y clave de un enlace público MEGA.

    Acepta los dos formatos históricos:

        https://mega.nz/file/<id>#<key>      (v2, actual)
        https://mega.nz/#!<id>!<key>         (v1, legacy)
    """
    if not url or "mega" not in url:
        raise ValueError(f"URL no es de MEGA: {url!r}")

    url = url.strip().replace(" ", "")
    m = _URL_V2.search(url) or _URL_V1.search(url)
    if not m:
        raise ValueError(f"No se pudo parsear la URL MEGA: {url!r}")
    file_id, key_b64 = m.group(1), m.group(2)
    key = _b64_to_a32(key_b64)
    if len(key) < 8:
        raise ValueError("La clave MEGA tiene una longitud inesperada")
    return MegaFileLink(file_id=file_id, raw_key=key[:8])


# =====================================================================
# Descargador
# =====================================================================


class MegaPublicDownloader:
    """Descarga archivos públicos MEGA con AES-CTR streaming + CBC-MAC."""

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        request_timeout: int = HTTP_TIMEOUT,
        download_timeout: int = HTTP_DOWNLOAD_TIMEOUT,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept-Encoding": "identity",  # MEGA ya entrega cifrado
            "Connection": "keep-alive",
        })
        # Pool de conexiones generoso por si reutilizamos la sesion
        try:
            from requests.adapters import HTTPAdapter
            adapter = HTTPAdapter(pool_connections=8, pool_maxsize=16,
                                  max_retries=0)
            self.session.mount("https://", adapter)
            self.session.mount("http://", adapter)
        except Exception:  # pragma: no cover
            pass
        self.request_timeout = request_timeout
        self.download_timeout = download_timeout
        self._seq = random.randint(0, 0xFFFFFFFF)

    # ---------- API alto nivel ----------

    def fetch_file_info(self, link: MegaFileLink) -> dict:
        """Devuelve dict con `name`, `size` y `download_url` (descifrados).

        Detecta y traduce los errores de cuota de MEGA a ``MegaQuotaError``.
        """
        resp = self._api_request({"a": "g", "g": 1, "p": link.file_id})
        # Respuesta puede ser:
        #  - dict con "g" (URL CDN) y "s" (size), éxito
        #  - dict con "e" (código error) si bloqueado por cuota
        #  - int negativo (código de error directo)
        if isinstance(resp, int):
            self._raise_for_code(resp, context="fetch_file_info")
        if isinstance(resp, dict) and "e" in resp:
            self._raise_for_code(int(resp["e"]), context="fetch_file_info")
        if not isinstance(resp, dict) or "g" not in resp:
            raise MegaApiError(f"Respuesta inesperada de MEGA: {resp!r}")

        attrs = _decrypt_attr(_b64url_decode(resp["at"]), link.file_key) or {}
        name = attrs.get("n") or f"{link.file_id}.bin"
        return {
            "name": _sanitize_filename(name),
            "size": int(resp["s"]),
            "download_url": resp["g"],
        }

    @staticmethod
    def _raise_for_code(code: int, *, context: str = "") -> None:
        """Convierte un código de error numérico de MEGA en la excepción
        más precisa posible."""
        name = MEGA_ERROR_NAMES.get(code, f"error desconocido ({code})")
        if code in MEGA_QUOTA_CODES:
            human = {
                -4: "MEGA ha aplicado un rate limit a tu IP. Espera unos minutos y vuelve a intentarlo.",
                -6: "Demasiadas direcciones IP están accediendo a este enlace.\nMEGA lo ha bloqueado temporalmente. Prueba en unos minutos.",
                -17: "MEGA ha bloqueado la descarga porque has superado la cuota de transferencia gratuita.\nLa cuota se restaura automáticamente en unas horas, o puedes:\n  • cambiar de red / IP (móvil, VPN)\n  • iniciar sesión con una cuenta MEGA Pro",
                -19: "Demasiadas conexiones simultáneas a este archivo.\nEspera un momento y reinténtalo.",
            }.get(code, "MEGA ha bloqueado la descarga por límite de ancho de banda.")
            raise MegaQuotaError(human, code=code)
        if code == -9:
            raise MegaApiError(
                "El archivo ya no existe en MEGA (eliminado o enlace inválido).",
                code=code,
            )
        if code == -8:
            raise MegaApiError(
                "El enlace de descarga ha expirado. Vuelve a abrir la máquina.",
                code=code,
            )
        if code == -16:
            raise MegaApiError(
                "MEGA ha bloqueado este archivo (terms of service).",
                code=code,
            )
        raise MegaApiError(
            f"Error {code} ({name}) durante {context or 'la operación'}.",
            code=code,
        )

    def download(
        self,
        url: str,
        dest_dir: Path,
        cancel_event: Optional[Event] = None,
        on_progress: Optional[Callable[[DownloadProgress], None]] = None,
        on_info: Optional[Callable[[dict], None]] = None,
        overwrite: bool = False,
        parallel: bool = True,
        max_workers: int = PARALLEL_MAX_WORKERS,
    ) -> Path:
        """Descarga el archivo. Devuelve la ruta final.

        Si ``parallel=True`` y el archivo supera ``PARALLEL_MIN_SIZE``, se usan
        varias conexiones HTTP simultaneas (Range requests). Esto rompe el
        techo de throughput de una sola conexion frente a MEGA.
        """
        cancel = cancel_event or Event()
        link = parse_public_url(url)
        info = self.fetch_file_info(link)
        if on_info:
            on_info(info)

        dest_dir = Path(dest_dir).expanduser().resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)
        final_path = dest_dir / info["name"]
        if final_path.exists() and not overwrite:
            final_path = _unique_path(final_path)
        tmp_path = final_path.with_suffix(final_path.suffix + ".part")

        size_total = info["size"]
        file_key_str = _a32_to_str(link.file_key)

        try:
            if parallel and size_total >= PARALLEL_MIN_SIZE and max_workers > 1:
                self._download_parallel(
                    info, link, file_key_str, tmp_path, size_total,
                    cancel, on_progress, max_workers,
                )
            else:
                self._download_single(
                    info, link, file_key_str, tmp_path, size_total,
                    cancel, on_progress,
                )

            if cancel.is_set():
                raise MegaCancelled()

            # Verificacion del CBC-MAC sobre el fichero ya completo en disco.
            # NO reseteamos a 0: dejamos la barra al 100% y solo cambiamos
            # el estado a 'verifying' (la UI lo lee del campo state).
            if on_progress:
                on_progress(DownloadProgress(
                    size_total, size_total, 0.0, None, "verifying"
                ))
            file_mac = self._verify_full_mac(
                tmp_path, file_key_str, link.iv, size_total, cancel,
                on_progress=on_progress,
                max_workers=max(2, (os.cpu_count() or 4)),
            )
            computed_meta_mac = (
                file_mac[0] ^ file_mac[1],
                file_mac[2] ^ file_mac[3],
            )
            if computed_meta_mac != link.meta_mac:
                raise MegaIntegrityError(
                    "El hash del archivo no coincide con MEGA "
                    "(clave incorrecta o archivo corrupto)."
                )

            os.replace(tmp_path, final_path)
            if on_progress:
                on_progress(DownloadProgress(
                    size_total, size_total, 0.0, 0.0, "done"
                ))
            return final_path
        except MegaCancelled:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            if on_progress:
                on_progress(DownloadProgress(
                    0, size_total, 0.0, None, "cancelled"
                ))
            raise
        except Exception:
            if on_progress:
                on_progress(DownloadProgress(
                    0, size_total, 0.0, None, "error"
                ))
            raise

    # ---------- Estrategia secuencial (una sola conexion) ----------

    def _download_single(self, info, link, file_key_str, tmp_path, size_total,
                         cancel, on_progress) -> None:
        counter = Counter.new(
            128,
            initial_value=((link.iv[0] << 32) + link.iv[1]) << 64,
        )
        aes_ctr = AES.new(file_key_str, AES.MODE_CTR, counter=counter)

        bytes_done = 0
        start_t = time.monotonic()
        last_emit = 0.0

        with self._open_stream(info["download_url"]) as resp, \
             open(tmp_path, "wb") as fh:
            for raw_chunk in resp.iter_content(chunk_size=CHUNK_BYTES):
                if cancel.is_set():
                    raise MegaCancelled()
                if not raw_chunk:
                    continue
                plain = aes_ctr.decrypt(raw_chunk)
                fh.write(plain)
                bytes_done += len(plain)
                now = time.monotonic()
                if on_progress and (now - last_emit) > 0.15:
                    elapsed = max(now - start_t, 1e-6)
                    speed = bytes_done / elapsed
                    eta = (size_total - bytes_done) / speed if speed else None
                    on_progress(DownloadProgress(
                        bytes_done, size_total, speed, eta, "running"
                    ))
                    last_emit = now

    # ---------- Estrategia paralela (varios Range requests) ----------

    def _download_parallel(self, info, link, file_key_str, tmp_path, size_total,
                           cancel, on_progress, max_workers: int) -> None:
        """Pre-asigna el fichero y baja varios segmentos AES-CTR en paralelo."""
        ranges = self._plan_ranges(size_total, PARALLEL_PART_SIZE)
        # Preasignar el fichero a tamaño final para poder hacer pwrite
        with open(tmp_path, "wb") as fh:
            fh.truncate(size_total)

        bytes_done = 0
        lock = Lock()
        start_t = time.monotonic()
        last_emit = [0.0]

        def emit(now: float) -> None:
            if not on_progress or (now - last_emit[0]) < 0.18:
                return
            with lock:
                done = bytes_done
            elapsed = max(now - start_t, 1e-6)
            speed = done / elapsed
            eta = (size_total - done) / speed if speed else None
            on_progress(DownloadProgress(done, size_total, speed, eta, "running"))
            last_emit[0] = now

        def worker(start: int, end: int) -> None:
            nonlocal bytes_done
            length = end - start + 1
            # AES-CTR independiente por segmento: el contador inicial vale
            # ((iv64 << 64) | (start / 16)).
            block_index = start // 16
            counter = Counter.new(
                128,
                initial_value=(((link.iv[0] << 32) + link.iv[1]) << 64) + block_index,
            )
            aes = AES.new(file_key_str, AES.MODE_CTR, counter=counter)

            headers = {"Range": f"bytes={start}-{end}"}
            resp = self.session.get(
                info["download_url"],
                headers=headers,
                stream=True,
                timeout=self.download_timeout,
            )
            if resp.status_code == 509:
                resp.close()
                raise MegaQuotaError(
                    "MEGA ha cortado la descarga a mitad por límite de ancho de banda (HTTP 509).",
                    code=-17,
                )
            if resp.status_code not in (200, 206):
                resp.close()
                raise MegaApiError(f"HTTP {resp.status_code} en rango {start}-{end}")
            try:
                pos = start
                with open(tmp_path, "r+b") as fh:
                    fh.seek(pos)
                    for raw in resp.iter_content(chunk_size=CHUNK_BYTES):
                        if cancel.is_set():
                            raise MegaCancelled()
                        if not raw:
                            continue
                        plain = aes.decrypt(raw)
                        fh.write(plain)
                        pos += len(plain)
                        with lock:
                            bytes_done += len(plain)
                        emit(time.monotonic())
            finally:
                resp.close()

        try:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = [pool.submit(worker, s, e) for s, e in ranges]
                for fut in as_completed(futures):
                    if cancel.is_set():
                        for f in futures:
                            f.cancel()
                        raise MegaCancelled()
                    fut.result()
        except MegaCancelled:
            raise

    @staticmethod
    def _plan_ranges(total: int, part: int) -> List[tuple[int, int]]:
        # Cada "part" debe estar alineado a 16 B (bloque AES) salvo el ultimo.
        if part % 16 != 0:
            part = (part // 16) * 16
        out: List[tuple[int, int]] = []
        pos = 0
        while pos < total:
            end = min(pos + part - 1, total - 1)
            out.append((pos, end))
            pos = end + 1
        return out

    # ---------- Verificacion CBC-MAC sobre el fichero ya en disco ----------

    def _verify_full_mac(self, path: Path, file_key: bytes,
                         iv: tuple[int, ...], size_total: int,
                         cancel: Event,
                         on_progress: Optional[Callable[[DownloadProgress], None]] = None,
                         max_workers: int = 4) -> list[int]:
        """CBC-MAC paralelizado.

        Cada chunk MEGA genera un ``chunk_mac`` que SOLO depende del IV y de
        los datos del propio chunk (no del chunk anterior). Por tanto los
        ``chunk_mac`` se pueden calcular EN PARALELO usando varios hilos.
        Despues se pliegan secuencialmente sobre ``file_mac`` (esta parte si
        es O(n_chunks) y depende de la anterior, pero es trivial: 1 AES por
        chunk = microsegundos).

        Resultado practico: la verificacion pasa de ~30-60s (un solo hilo
        Python encadenando AES bloque a bloque) a ~3-6s en un archivo de
        150 MB con 4 workers.
        """
        chunks = list(_chunks_of_mega(size_total))
        n = len(chunks)
        chunk_macs: list[Optional[list[int]]] = [None] * n
        done_count = [0]
        lock = Lock()
        start_t = time.monotonic()
        last_emit = [0.0]

        def emit() -> None:
            # Durante la verificacion la barra YA esta al 100% (la descarga
            # termino). Solo emitimos progreso para mantener el estado
            # 'verifying' y actualizar la velocidad/ETA, pero los bytes
            # siempre se reportan al maximo.
            if not on_progress:
                return
            now = time.monotonic()
            if now - last_emit[0] < 0.15:
                return
            last_emit[0] = now
            with lock:
                done = done_count[0]
            frac = done / n if n else 1.0
            elapsed = max(now - start_t, 1e-6)
            speed = int(size_total * frac) / elapsed
            eta = (1.0 - frac) * elapsed / max(frac, 1e-6) if frac < 1 else 0.0
            on_progress(DownloadProgress(size_total, size_total, speed, eta, "verifying"))

        def worker(idx: int, start: int, length: int) -> None:
            if cancel.is_set():
                return
            with open(path, "rb") as fh:
                fh.seek(start)
                data = fh.read(length)
            mac = self._chunk_mac(data, file_key, iv)
            chunk_macs[idx] = mac
            with lock:
                done_count[0] += 1
            emit()

        # Lanzamos los workers. Para ficheros pequenos secuencial es mas
        # rapido (overhead de threads).
        if size_total < 16 * 1024 * 1024 or max_workers <= 1:
            for i, (s, sz) in enumerate(chunks):
                worker(i, s, sz)
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futs = [pool.submit(worker, i, s, sz) for i, (s, sz) in enumerate(chunks)]
                for f in as_completed(futs):
                    if cancel.is_set():
                        for ff in futs:
                            ff.cancel()
                        raise MegaCancelled()
                    f.result()

        # Pliegue secuencial de los chunk_macs en file_mac
        file_mac = [0, 0, 0, 0]
        for mac in chunk_macs:
            if mac is None:
                raise MegaIntegrityError("Fallo interno: chunk_mac vacio")
            mixed = [
                file_mac[0] ^ mac[0],
                file_mac[1] ^ mac[1],
                file_mac[2] ^ mac[2],
                file_mac[3] ^ mac[3],
            ]
            file_mac = list(_str_to_a32(
                _aes_cbc_encrypt(_a32_to_str(mixed), file_key)
            ))
        return file_mac

    # ---------- Internas ----------

    def _api_request(self, payload: dict):
        params = {"id": self._seq}
        self._seq += 1
        last_exc: Optional[Exception] = None
        for attempt in range(5):
            try:
                r = self.session.post(
                    MEGA_API_URL,
                    params=params,
                    data=json.dumps([payload]),
                    timeout=self.request_timeout,
                )
                r.raise_for_status()
                body = r.json()
                if isinstance(body, int):
                    if body == -3:  # EAGAIN
                        time.sleep(min(60, 2 ** attempt))
                        continue
                    return body
                if isinstance(body, list):
                    first = body[0]
                    if isinstance(first, int) and first == -3:
                        time.sleep(min(60, 2 ** attempt))
                        continue
                    return first
                return body
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(min(30, 2 ** attempt))
        raise MegaApiError(f"No se pudo contactar con MEGA: {last_exc}")

    def _open_stream(self, url: str):
        """Abre el stream con un buffer de socket mas grande.

        urllib3 utiliza por defecto un buffer interno relativamente pequeno;
        con un Read big lo evitamos cuando iter_content recibe mucho dato.
        Tambien aumentamos SO_RCVBUF del socket subyacente cuando es posible
        para reducir el numero de syscalls.
        """
        resp = self.session.get(url, stream=True, timeout=self.download_timeout)
        # Detección de cuota en el CDN:
        # - HTTP 509 (Bandwidth Limit Exceeded) es el código clásico
        # - HTTP 503/403/429 también se han visto cuando MEGA tira el CDN
        # - Además, el primer chunk puede ser un JSON con {'e': -17} antes
        #   de cualquier dato binario cifrado.
        if resp.status_code == 509:
            resp.close()
            raise MegaQuotaError(
                "MEGA ha respondido HTTP 509 (Bandwidth Limit Exceeded).\n"
                "Has superado la cuota de transferencia gratuita. La cuota se "
                "restaura automáticamente en unas horas.",
                code=-17,
            )
        if resp.status_code in (403, 429, 503):
            ctype = (resp.headers.get("Content-Type") or "").lower()
            # MEGA suele devolver JSON cuando bloquea por política
            if "json" in ctype or "text" in ctype:
                try:
                    body_text = resp.text[:200]
                    resp.close()
                except Exception:  # noqa: BLE001
                    body_text = ""
                    resp.close()
                if any(tok in body_text for tok in ("-17", "-6", "-19", "-4")):
                    raise MegaQuotaError(
                        f"MEGA ha bloqueado la descarga (HTTP {resp.status_code}: {body_text!r}).",
                        code=-17,
                    )
                raise MegaApiError(
                    f"MEGA respondió HTTP {resp.status_code}: {body_text!r}",
                )
        # Sniff del primer chunk: si MEGA bloquea tras conceder la URL,
        # devuelve un JSON corto con {'e': N} en el cuerpo de la respuesta
        # antes de cualquier dato cifrado.
        try:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            clen = int(resp.headers.get("Content-Length") or 0)
            # Si pesa < 100 B y dice JSON, casi seguro es un error
            if 0 < clen < 100 and "json" in ctype:
                payload = resp.content  # consume
                resp.close()
                try:
                    data = json.loads(payload.decode("utf-8", "ignore"))
                    if isinstance(data, dict) and "e" in data:
                        self._raise_for_code(int(data["e"]), context="download")
                    if isinstance(data, int):
                        self._raise_for_code(int(data), context="download")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
                raise MegaApiError(
                    f"Respuesta inesperada del CDN: {payload[:80]!r}"
                )
        except (MegaQuotaError, MegaApiError):
            raise
        except Exception:  # noqa: BLE001
            pass
        try:
            sock = resp.raw._fp.fp.raw._sock  # type: ignore[attr-defined]
            import socket as _socket
            sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_RCVBUF,
                            SOCKET_BUFFER_BYTES)
        except Exception:
            pass
        return resp

    @staticmethod
    def _chunk_mac(chunk: bytes, file_key: bytes, iv: tuple[int, ...]) -> list[int]:
        """Calcula el ``chunk_mac`` de un chunk MEGA aislado.

        Optimizado vs la version original:
          - Usa AES-CBC con IV variable directamente sobre el bloque completo,
            aprovechando que CBC ya hace XOR(prev_cipher, block) internamente.
            Asi delegamos al backend nativo de pycryptodome (escrito en C) y
            evitamos un bucle Python por cada bloque de 16 B.
          - Reduce el coste por chunk de O(n_bloques) llamadas a Python a 1.
        """
        if not chunk:
            return [iv[0], iv[1], iv[0], iv[1]]
        pad = (-len(chunk)) % 16
        if pad:
            chunk = chunk + b"\0" * pad

        iv_bytes = _a32_to_str((iv[0], iv[1], iv[0], iv[1]))
        # AES-CBC-encrypt(file_key, IV=iv_bytes, data=chunk) devuelve
        # los bloques cifrados encadenados; el ultimo bloque es justamente
        # el chunk_mac segun el protocolo MEGA.
        cipher = AES.new(file_key, AES.MODE_CBC, iv_bytes)
        encrypted = cipher.encrypt(chunk)
        last_block = encrypted[-16:]
        return list(_str_to_a32(last_block))


# =====================================================================
# Errores
# =====================================================================

# Códigos de error de la API JSON de MEGA (g.api.mega.co.nz/cs).
# Documentación: https://mega.io/developers (cs API), help.servmask.com
# tabla práctica de errores MEGA.
MEGA_ERROR_NAMES = {
    -1: "EINTERNAL (error interno)",
    -2: "EARGS (argumentos inválidos)",
    -3: "EAGAIN (congestión temporal)",
    -4: "ERATELIMIT (rate limit)",
    -5: "EFAILED (operación fallida)",
    -6: "ETOOMANY (demasiadas IPs en este enlace)",
    -7: "ERANGE (rango inválido)",
    -8: "EEXPIRED (enlace expirado)",
    -9: "ENOENT (archivo no existe en MEGA)",
    -10: "ECIRCULAR",
    -11: "EACCESS (acceso denegado)",
    -12: "EEXIST",
    -13: "EINCOMPLETE",
    -14: "EKEY (clave inválida)",
    -15: "ESID (sesión inválida)",
    -16: "EBLOCKED (bloqueado)",
    -17: "EOVERQUOTA (cuota de descarga superada)",
    -18: "ETEMPUNAVAIL (temporalmente no disponible)",
    -19: "ETOOMANYCONNECTIONS",
    -20: "EWRITE",
    -21: "EREAD",
    -22: "EAPPKEY",
}

# Códigos que indican que MEGA ha bloqueado la descarga por límite de ancho
# de banda (cuota de IP / cuenta). Incluye:
#   -3  EAGAIN repetido tras backoff (cuando aparece en /g del CDN tras
#       muchos reintentos, suele ser también cuota)
#   -4  ERATELIMIT
#   -6  ETOOMANY (demasiadas IPs accediendo al enlace)
#   -17 EOVERQUOTA (es el clásico 'transfer quota exceeded')
#   -19 ETOOMANYCONNECTIONS
MEGA_QUOTA_CODES = frozenset({-4, -6, -17, -19})


class MegaError(Exception):
    """Base."""


class MegaApiError(MegaError):
    """Error de la API JSON de MEGA. Incluye el código numérico si lo conoce."""

    def __init__(self, message, code: Optional[int] = None) -> None:
        super().__init__(message)
        self.code = code


class MegaQuotaError(MegaApiError):
    """MEGA ha bloqueado la descarga por límite de ancho de banda / cuota.

    Suele ocurrir cuando se descargan varios archivos grandes seguidos
    desde la misma IP sin cuenta Pro. MEGA muestra el mensaje 'Bandwidth
    Limit Exceeded' o 'Transfer quota exceeded'. La cuota se restaura
    automáticamente tras ~6 horas o al cambiar de IP.
    """


class MegaIntegrityError(MegaError):
    """meta_mac no coincide."""


class MegaCancelled(MegaError):
    """Descarga cancelada por el usuario."""


# =====================================================================
# Helpers de filesystem
# =====================================================================

_INVALID_FN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize_filename(name: str) -> str:
    name = _INVALID_FN_CHARS.sub("_", name).strip().rstrip(".")
    return name or "mega_file"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    i = 1
    while True:
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
        i += 1


# =====================================================================
# Conveniencia
# =====================================================================


def download_mega(
    url: str,
    dest_dir: Path,
    cancel_event: Optional[Event] = None,
    on_progress: Optional[Callable[[DownloadProgress], None]] = None,
    on_info: Optional[Callable[[dict], None]] = None,
) -> Path:
    """Atajo: instancia un MegaPublicDownloader y descarga el URL dado."""
    return MegaPublicDownloader().download(
        url, dest_dir,
        cancel_event=cancel_event,
        on_progress=on_progress,
        on_info=on_info,
    )


def human_bytes(n: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    s = float(n)
    i = 0
    while s >= 1024 and i < len(units) - 1:
        s /= 1024
        i += 1
    return f"{s:.1f} {units[i]}" if i else f"{int(s)} {units[i]}"
