"""Almacenamiento local de máquinas marcadas como completadas.

- Persistente en JSON dentro de ~/.dockerlabs-gui/completed.json
- Una entrada por usuario (con clave "_anon" si no hay sesión)
- API mínima: load/save/add/remove/all_for_user/merge

Cuando el usuario inicia sesión, las máquinas locales se combinan con las
que ya tiene marcadas en la web mediante `sync_with_server`, y las que
solo estaban en local se envían al servidor con toggle_completed para
quedar todas sincronizadas en ambos lados.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


class CompletedStore:
    """Persiste un set de nombres completados por usuario."""

    ANON = "_anon"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, list[str]] = {}
        self._load()

    # ---- Persistencia ----

    def _load(self) -> None:
        try:
            if self.path.exists():
                with self.path.open("r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    self._data = {
                        str(k): sorted({str(x) for x in (v or []) if x})
                        for k, v in raw.items()
                    }
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("No se pudo leer completed.json: %s", exc)
            self._data = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2, sort_keys=True)
            tmp.replace(self.path)
        except OSError as exc:
            logger.error("No se pudo escribir completed.json: %s", exc)

    # ---- API pública ----

    def all_for_user(self, username: Optional[str]) -> set[str]:
        """Devuelve el set persistido para ese usuario."""
        key = username or self.ANON
        with self._lock:
            return set(self._data.get(key, []))

    def set_for_user(self, username: Optional[str], names: Iterable[str]) -> None:
        """Sobrescribe el set completo para ese usuario."""
        key = username or self.ANON
        clean = sorted({str(n).strip() for n in names if n})
        with self._lock:
            self._data[key] = clean
            self._save()

    def add(self, username: Optional[str], name: str) -> bool:
        """Añade una máquina; devuelve True si era nueva."""
        key = username or self.ANON
        with self._lock:
            current = set(self._data.get(key, []))
            if name in current:
                return False
            current.add(name)
            self._data[key] = sorted(current)
            self._save()
            return True

    def remove(self, username: Optional[str], name: str) -> bool:
        """Quita una máquina; devuelve True si existía."""
        key = username or self.ANON
        with self._lock:
            current = set(self._data.get(key, []))
            if name not in current:
                return False
            current.discard(name)
            self._data[key] = sorted(current)
            self._save()
            return True

    def merge_into_user(self, username: str, extra: Iterable[str]) -> set[str]:
        """Une `extra` con el set actual del usuario y devuelve el resultado.

        Se usa al loguear: combinamos las completadas locales (anónimas o de
        ese usuario) con las que vienen del servidor para no perder progreso
        que el usuario hubiera marcado sin estar logueado.
        """
        with self._lock:
            current = set(self._data.get(username, []))
            current.update(str(x) for x in extra if x)
            self._data[username] = sorted(current)
            self._save()
            return set(current)

    def drain_anonymous(self) -> set[str]:
        """Lee y borra el set 'anónimo' (usado para migrarlo al usuario al login)."""
        with self._lock:
            anon = set(self._data.pop(self.ANON, []))
            if anon:
                self._save()
            return anon
