"""Construye un ejecutable one-folder con PyInstaller (usado por el workflow Release).

Uso:  python packaging/build.py --name dockerlabs-gui-linux-x86_64
"""
from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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
    icon = ROOT / "packaging" / "icon.ico"
    if icon.exists() and platform.system() == "Windows":
        cmd += ["--icon", str(icon)]
    cmd.append(str(ROOT / "main.py"))
    print(" ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
