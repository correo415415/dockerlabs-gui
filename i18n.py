"""Internacionalización ligera (español / inglés).

El texto fuente del código está en **español** y sirve de clave; las
traducciones viven en `locales/<idioma>.json` (`{"texto en español": "text in
English"}`). `tr()` devuelve la traducción del idioma activo o, si falta, el
texto original, de modo que la app nunca muestra claves vacías.

    from i18n import tr
    lbl.setText(tr("Máquinas"))
    toast(tr("{n} máquinas descargadas").format(n=3))

Idioma activo:
  * `auto` (por defecto): el del sistema (variables de entorno / QLocale);
    inglés si no es español.
  * `es` / `en`: forzado por el usuario en Ajustes.

`set_language()` debe llamarse **antes** de construir la ventana principal; los
widgets ya construidos no se retraducen (la app ofrece reiniciar al cambiar).
"""
from __future__ import annotations

import json
import locale
import logging
import os
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

SOURCE_LANGUAGE = "es"
LANGUAGES: Tuple[str, ...] = ("auto", "es", "en")
LANGUAGE_NAMES: Dict[str, str] = {"auto": "Automático (idioma del sistema)", "es": "Español", "en": "English"}

_current: str = SOURCE_LANGUAGE
_catalog: Dict[str, str] = {}


def _locales_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "locales"
    return Path(__file__).resolve().parent / "locales"


def available_languages() -> Tuple[str, ...]:
    """Códigos con traducción disponible (más el idioma fuente)."""
    langs = {SOURCE_LANGUAGE}
    try:
        for p in _locales_dir().glob("*.json"):
            langs.add(p.stem)
    except OSError:
        pass
    return tuple(sorted(langs))


def _normalize(code: str) -> str:
    """'es_ES.UTF-8' → 'es'; 'en-US' → 'en'; '' / 'C' / 'POSIX' → ''."""
    code = (code or "").strip().split(":")[0]
    code = code.split(".")[0].split("_")[0].split("-")[0].split("@")[0].lower()
    return "" if code in ("", "c", "posix") else code


def detect_system_language(env: Optional[dict] = None, qt: bool = True) -> str:
    """`es` si el sistema está en español; `en` en cualquier otro caso.

    Orden: variables LANGUAGE/LC_ALL/LC_MESSAGES/LANG → QLocale del sistema (Qt,
    cubre Windows/macOS donde no suele haber LANG) → `locale.getlocale()`.
    Se puede pasar `env` (dict) para tests.
    """
    env = os.environ if env is None else env
    langs = available_languages()
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        code = _normalize(env.get(var, ""))
        if code:
            return code if code in langs else "en"
    if qt:
        try:
            from PyQt6.QtCore import QLocale
            code = _normalize(QLocale.system().name())      # p.ej. 'es_ES'
            if code:
                return code if code in langs else "en"
        except Exception:  # noqa: BLE001 - Qt puede no estar disponible (tests, CLI)
            pass
    try:
        code = _normalize(locale.getlocale()[0] or "")
        if code:
            return code if code in langs else "en"
    except (ValueError, TypeError):
        pass
    return "en"


def resolve_language(pref: str) -> str:
    """Convierte la preferencia (`auto|es|en`) en el idioma efectivo."""
    if pref in available_languages():
        return pref
    return detect_system_language()


def load_catalog(lang: str) -> Dict[str, str]:
    if lang == SOURCE_LANGUAGE:
        return {}
    path = _locales_dir() / f"{lang}.json"
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("No se pudo cargar el idioma %s (%s): %s", lang, path, exc)
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v}


def set_language(pref: str) -> str:
    """Activa el idioma (`auto`, `es`, `en`…) y devuelve el código efectivo."""
    global _current, _catalog
    lang = resolve_language(pref or "auto")
    _catalog = load_catalog(lang)
    _current = lang
    logger.info("Idioma: %s (preferencia %s)", lang, pref)
    return lang


def current_language() -> str:
    return _current


def tr(text: str) -> str:
    """Traduce `text` (español) al idioma activo; si no hay traducción, lo devuelve tal cual."""
    if _current == SOURCE_LANGUAGE or not text:
        return text
    return _catalog.get(text, text)
