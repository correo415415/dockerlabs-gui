"""Persistencia de preferencias del usuario en ~/.dockerlabs-gui/settings.json."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class UserSettings:
    downloads_dir: str = ""
    os_notifications: bool = True
    in_app_notifications: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "UserSettings":
        return cls(
            downloads_dir=str(data.get("downloads_dir", "") or ""),
            os_notifications=bool(data.get("os_notifications", True)),
            in_app_notifications=bool(data.get("in_app_notifications", True)),
        )


class SettingsStore:
    def __init__(self, path: Path, defaults: Optional[UserSettings] = None) -> None:
        self.path = Path(path)
        self._defaults = defaults or UserSettings()
        self._cached: Optional[UserSettings] = None

    def load(self) -> UserSettings:
        if self._cached is not None:
            return self._cached
        try:
            if self.path.exists():
                with self.path.open("r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                merged = asdict(self._defaults)
                merged.update(raw or {})
                self._cached = UserSettings.from_dict(merged)
                return self._cached
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("No se pudo leer settings.json: %s", exc)
        self._cached = UserSettings(**asdict(self._defaults))
        return self._cached

    def save(self, settings: UserSettings) -> None:
        self._cached = UserSettings.from_dict(asdict(settings))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(asdict(self._cached), fh, ensure_ascii=False, indent=2)
            tmp.replace(self.path)
        except OSError as exc:
            logger.error("No se pudo escribir settings.json: %s", exc)

    @property
    def current(self) -> UserSettings:
        return self.load()
