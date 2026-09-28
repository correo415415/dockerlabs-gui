"""Caché en disco de imágenes y valoraciones de máquinas (`~/.dockerlabs-gui/cache/`).

* Imágenes: `images/<sha1(url)>.<ext>`; no caducan (la URL cambia si cambia la imagen).
* Valoraciones: `ratings.json` con `{nombre: {"data": {...}, "at": epoch}}`; se sirven
  al instante y se refrescan en segundo plano si tienen más de `RATING_TTL` segundos.

Sin Qt: lo usan los workers del panel de detalle. Escrituras atómicas (tmp + replace)
y tolerante a fallos de disco (la caché nunca debe romper la app).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

RATING_TTL = 6 * 3600          # 6 h: las valoraciones cambian poco
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _ext_for(data: bytes, url: str) -> str:
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    suffix = Path(url.split("?")[0]).suffix.lower()
    return suffix if suffix in (".webp", ".png", ".jpg", ".jpeg", ".gif", ".svg") else ".img"


class MediaCache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.images_dir = self.root / "images"
        self.ratings_file = self.root / "ratings.json"
        self._lock = threading.RLock()
        self._ratings: Optional[Dict[str, dict]] = None   # carga perezosa
        try:
            self.images_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("no se pudo crear la caché %s: %s", self.root, exc)

    # ---------------- imágenes ----------------

    def _image_path(self, url: str) -> Optional[Path]:
        key = hashlib.sha1(url.encode("utf-8")).hexdigest()
        try:
            for p in self.images_dir.glob(key + ".*"):
                return p
        except OSError:
            return None
        return None

    def get_image(self, url: str) -> Optional[bytes]:
        p = self._image_path(url)
        if p is None:
            return None
        try:
            data = p.read_bytes()
        except OSError:
            return None
        return data or None

    def put_image(self, url: str, data: bytes) -> None:
        if not data or len(data) > MAX_IMAGE_BYTES:
            return
        key = hashlib.sha1(url.encode("utf-8")).hexdigest()
        target = self.images_dir / (key + _ext_for(data, url))
        _atomic_write(target, data)

    # ---------------- valoraciones ----------------

    def _load_ratings(self) -> Dict[str, dict]:
        with self._lock:
            if self._ratings is None:
                try:
                    raw = json.loads(self.ratings_file.read_text(encoding="utf-8"))
                    self._ratings = raw if isinstance(raw, dict) else {}
                except (OSError, ValueError):
                    self._ratings = {}
            return self._ratings

    def get_rating(self, name: str) -> Tuple[Optional[dict], bool]:
        """(data, fresh). `data` es None si no hay; `fresh` False si conviene refrescar."""
        entry = self._load_ratings().get(name)
        if not isinstance(entry, dict) or not isinstance(entry.get("data"), dict):
            return None, False
        age = time.time() - float(entry.get("at") or 0)
        return entry["data"], age < RATING_TTL

    def put_rating(self, name: str, data: dict) -> None:
        if not isinstance(data, dict):
            return
        with self._lock:
            ratings = self._load_ratings()
            ratings[name] = {"data": data, "at": time.time()}
            try:
                payload = json.dumps(ratings, ensure_ascii=False).encode("utf-8")
            except (TypeError, ValueError):
                return
            _atomic_write(self.ratings_file, payload)

    # ---------------- mantenimiento ----------------

    def clear(self) -> None:
        with self._lock:
            self._ratings = {}
            try:
                if self.ratings_file.exists():
                    self.ratings_file.unlink()
                for p in self.images_dir.iterdir():
                    if p.is_file():
                        p.unlink()
            except OSError as exc:
                logger.warning("limpiando caché: %s", exc)

    def size_bytes(self) -> int:
        total = 0
        try:
            for p in self.images_dir.iterdir():
                if p.is_file():
                    total += p.stat().st_size
            if self.ratings_file.exists():
                total += self.ratings_file.stat().st_size
        except OSError:
            pass
        return total


def _atomic_write(target: Path, data: bytes) -> None:
    tmp = target.with_suffix(target.suffix + f".{os.getpid()}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        os.replace(tmp, target)
    except OSError as exc:
        logger.debug("caché: no se pudo escribir %s: %s", target, exc)
        try:
            tmp.unlink()
        except OSError:
            pass
