"""Logo de la app (assets/logo.png) y generador packaging/make_logo.py."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from widgets import icons  # noqa: E402

_app = QApplication.instance() or QApplication([])  # noqa: F841
ROOT = Path(__file__).resolve().parent.parent


def test_logo_assets_exist_and_load():
    assert (ROOT / "assets" / "logo.png").is_file()
    assert (ROOT / "packaging" / "icon.ico").is_file()
    assert icons.app_logo_path() is not None
    assert not icons.app_icon().isNull()
    pix = icons.app_logo_pixmap(30)
    assert not pix.isNull() and pix.width() == 30 and pix.height() == 30


def test_app_icon_falls_back_to_svg_without_png(monkeypatch, tmp_path):
    monkeypatch.setattr(icons, "_assets_dir", lambda: tmp_path)
    icons.app_icon.cache_clear()
    icons.app_logo_pixmap.cache_clear()
    try:
        assert icons.app_logo_path() is None
        assert not icons.app_icon().isNull()          # SVG "machines"
        assert icons.app_logo_pixmap(30).isNull()
    finally:
        icons.app_icon.cache_clear()
        icons.app_logo_pixmap.cache_clear()


def test_make_logo_strips_white_and_adds_text(tmp_path):
    PIL = pytest.importorskip("PIL")
    import importlib.util

    from PIL import Image
    spec = importlib.util.spec_from_file_location("make_logo", ROOT / "packaging" / "make_logo.py")
    ml = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ml)

    # Orca ficticia: círculo azul sobre fondo blanco opaco, con un punto blanco interior
    src = Image.new("RGBA", (200, 200), (255, 255, 255, 255))
    from PIL import ImageDraw
    d = ImageDraw.Draw(src)
    d.ellipse((40, 40, 160, 160), fill=(20, 60, 120, 255))
    d.ellipse((95, 95, 105, 105), fill=(255, 255, 255, 255))   # blanco interior (ojo)
    p = tmp_path / "src.png"
    src.save(p)

    stripped = ml.strip_white_background(Image.open(p).convert("RGBA"))
    assert stripped.getpixel((0, 0))[3] == 0                      # borde → transparente
    assert stripped.getpixel((100, 100))[3] == 255                # blanco interior se conserva
    assert stripped.getpixel((100, 60))[3] == 255                 # el círculo se conserva

    logo = ml.add_gui_text(ml.fit_square(stripped, 256))
    assert logo.size == (256, 256)
    # Hay píxeles de acento (texto GUI) en la franja inferior
    band = logo.crop((0, 190, 256, 256))
    accent = sum(1 for px in band.getdata() if px[3] > 200 and px[2] > 200 and px[0] < 80)
    assert accent > 100

    assert PIL is not None
    rc = ml.main(["--src", str(p), "--out", str(tmp_path / "out"), "--ico", str(tmp_path / "out" / "i.ico")])
    assert rc == 0 and (tmp_path / "out" / "logo.png").is_file() and (tmp_path / "out" / "i.ico").is_file()
