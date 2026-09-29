"""Construye un ejecutable one-folder con PyInstaller (usado por el workflow Release).

Uso:  python packaging/build.py --name dockerlabs-gui-linux-x86_64
"""
from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"


def _ensure_pillow() -> None:
    """PyInstaller necesita Pillow para convertir el PNG a .icns/.ico; se instala si falta."""
    try:
        import PIL  # noqa: F401
    except ImportError:
        subprocess.call([sys.executable, "-m", "pip", "install", "-q", "pillow"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default=f"dockerlabs-gui-{platform.system().lower()}")
    ap.add_argument("--onefile", action="store_true")
    args = ap.parse_args()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", args.name,
        "--windowed",
        "--paths", str(ROOT),
        # módulos importados dinámicamente
        "--hidden-import", "widgets.crash_dialog",
        "--collect-submodules", "widgets",
        "--exclude-module", "tkinter",
        "--exclude-module", "pytest",
    ]
    if args.onefile:
        cmd.append("--onefile")
    # Logo (assets/logo.png → icono de ventana en tiempo de ejecución)
    if ASSETS.is_dir():
        cmd += ["--add-data", f"{ASSETS}{os.pathsep}assets"]
    # Icono del ejecutable: .ico en Windows; PNG en Linux/macOS (PyInstaller lo convierte
    # a .icns en macOS si Pillow está instalado).
    ico = ROOT / "packaging" / "icon.ico"
    png = ASSETS / "logo.png"
    if platform.system() == "Windows" and ico.exists():
        cmd += ["--icon", str(ico)]
    elif png.exists():
        _ensure_pillow()
        cmd += ["--icon", str(png)]
    cmd.append(str(ROOT / "main.py"))
    print(" ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
