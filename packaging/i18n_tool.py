#!/usr/bin/env python3
"""Herramienta de i18n: extrae las cadenas `tr("…")` del código y sincroniza `locales/*.json`.

    python packaging/i18n_tool.py extract          # lista cadenas fuente (español)
    python packaging/i18n_tool.py sync [en]        # añade a locales/en.json las que falten ("" = sin traducir)
    python packaging/i18n_tool.py sync en --prune  # además elimina claves huérfanas
    python packaging/i18n_tool.py check [en]       # rc 1 si hay claves sin traducir o huérfanas

Las claves son el texto en español tal cual aparece en `tr(...)`; el valor es la
traducción. No se usan f-strings dentro de `tr()` (el texto cambiaría por
ejecución): se usa `tr("… {n} …").format(n=…)`.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Dict, List, Set

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "locales"
SOURCES = [ROOT / "main.py", *sorted((ROOT / "widgets").glob("*.py")),
           ROOT / "lab_controller.py", ROOT / "lab_manager.py", ROOT / "download_manager.py",
           ROOT / "session_controller.py", ROOT / "catalog_controller.py", ROOT / "notifier.py",
           ROOT / "i18n.py", ROOT / "app_logging.py", ROOT / "http_downloader.py"]


def _is_tr_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return (isinstance(f, ast.Name) and f.id == "tr") or (isinstance(f, ast.Attribute) and f.attr == "tr")


def extract(paths: List[Path] | None = None) -> Dict[str, List[str]]:
    """Devuelve {cadena: [fichero:línea, …]}."""
    found: Dict[str, List[str]] = {}
    for path in paths or SOURCES:
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not _is_tr_call(node) or not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.setdefault(arg.value, []).append(f"{path.relative_to(ROOT)}:{node.lineno}")
            elif isinstance(arg, ast.JoinedStr):
                print(f"AVISO: f-string dentro de tr() en {path.relative_to(ROOT)}:{node.lineno}",
                      file=sys.stderr)
    return found


def _load(lang: str) -> Dict[str, str]:
    p = LOCALES / f"{lang}.json"
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _save(lang: str, data: Dict[str, str]) -> None:
    LOCALES.mkdir(exist_ok=True)
    p = LOCALES / f"{lang}.json"
    p.write_text(json.dumps(dict(sorted(data.items(), key=lambda kv: kv[0].lower())),
                            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sync(lang: str = "en", prune: bool = False) -> int:
    keys: Set[str] = set(extract())
    data = _load(lang)
    added = [k for k in keys if k not in data]
    for k in added:
        data[k] = ""
    orphan = [k for k in data if k not in keys]
    if prune:
        for k in orphan:
            del data[k]
    _save(lang, data)
    print(f"{lang}: {len(keys)} cadenas, {len(added)} nuevas, {len(orphan)} huérfanas"
          + (" (eliminadas)" if prune else ""))
    return 0


def check(lang: str = "en") -> int:
    keys = set(extract())
    data = _load(lang)
    missing = sorted(k for k in keys if not data.get(k))
    orphan = sorted(k for k in data if k not in keys)
    for k in missing:
        print(f"SIN TRADUCIR [{lang}]: {k!r}")
    for k in orphan:
        print(f"HUÉRFANA [{lang}]: {k!r}")
    print(f"{lang}: {len(keys)} cadenas, {len(missing)} sin traducir, {len(orphan)} huérfanas")
    return 1 if (missing or orphan) else 0


def main(argv: List[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "extract"
    lang = next((a for a in argv[1:] if not a.startswith("--")), "en")
    if cmd == "extract":
        for k, where in sorted(extract().items(), key=lambda kv: kv[0].lower()):
            print(f"{k!r}    # {', '.join(where[:3])}")
        return 0
    if cmd == "sync":
        return sync(lang, prune="--prune" in argv)
    if cmd == "check":
        return check(lang)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
