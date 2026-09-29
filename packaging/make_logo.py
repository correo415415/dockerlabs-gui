#!/usr/bin/env python3
"""Genera el logo de la app a partir de la imagen de la máquina 138 de DockerLabs.

    python packaging/make_logo.py [--src FICHERO|URL]

Pasos:
  1. Descarga https://dockerlabs.es/img/maquina/138 (o usa --src).
  2. Elimina el fondo blanco/casi blanco si lo hubiera (flood-fill desde los
     bordes → transparente) y recorta al contenido.
  3. Superpone las letras «GUI» centradas en la parte inferior, sobre una
     pastilla redondeada oscura para que se lean en cualquier fondo.
  4. Guarda `assets/logo.png` (512×512), `assets/logo_256.png` y
     `packaging/icon.ico` (16…256 px) para PyInstaller/Windows.

Requiere Pillow (`pip install pillow`).
"""
from __future__ import annotations

import argparse
import io
import sys
import urllib.request
from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SRC_URL = "https://dockerlabs.es/img/maquina/138"
ACCENT = (34, 211, 238, 255)       # cian del tema (theme.ACCENT)
PILL = (17, 24, 39, 230)           # fondo de la pastilla
WHITE_THRESHOLD = 235              # >= en R, G y B → se considera "blanco"

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)


def load_source(src: str) -> Image.Image:
    if src.startswith(("http://", "https://")):
        req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0 dockerlabs-gui"})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            data = resp.read()
        return Image.open(io.BytesIO(data)).convert("RGBA")
    return Image.open(src).convert("RGBA")


def strip_white_background(im: Image.Image) -> Image.Image:
    """Hace transparente el blanco conectado con los bordes (no el blanco interior)."""
    im = im.copy()
    w, h = im.size
    px = im.load()

    def is_white(x: int, y: int) -> bool:
        r, g, b, a = px[x, y]
        return a > 0 and r >= WHITE_THRESHOLD and g >= WHITE_THRESHOLD and b >= WHITE_THRESHOLD

    seen = bytearray(w * h)
    q: deque[tuple[int, int]] = deque()
    for x in range(w):
        for y in (0, h - 1):
            if is_white(x, y):
                q.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if is_white(x, y):
                q.append((x, y))
    cleared = 0
    while q:
        x, y = q.popleft()
        i = y * w + x
        if seen[i]:
            continue
        seen[i] = 1
        if not is_white(x, y):
            continue
        r, g, b, _ = px[x, y]
        px[x, y] = (r, g, b, 0)
        cleared += 1
        if x > 0:
            q.append((x - 1, y))
        if x < w - 1:
            q.append((x + 1, y))
        if y > 0:
            q.append((x, y - 1))
        if y < h - 1:
            q.append((x, y + 1))
    if cleared:
        print(f"  fondo blanco eliminado: {cleared} px")
    else:
        print("  la imagen ya tenía fondo transparente")
    return im


def fit_square(im: Image.Image, size: int = 512, margin: float = 0.04) -> Image.Image:
    bbox = im.getchannel("A").getbbox() or (0, 0, *im.size)
    im = im.crop(bbox)
    inner = int(size * (1 - 2 * margin))
    im.thumbnail((inner, inner), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(im, ((size - im.width) // 2, (size - im.height) // 2))
    return canvas


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for cand in FONT_CANDIDATES:
        if Path(cand).is_file():
            return ImageFont.truetype(cand, size)
    return ImageFont.load_default()


def add_gui_text(im: Image.Image, text: str = "GUI") -> Image.Image:
    im = im.copy()
    size = im.width
    font = _font(int(size * 0.26))
    draw = ImageDraw.Draw(im)
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    tw, th = r - l, b - t
    pad_x, pad_y = int(size * 0.05), int(size * 0.03)
    pw, ph = tw + 2 * pad_x, th + 2 * pad_y
    x0 = (size - pw) // 2
    y0 = size - ph - int(size * 0.035)
    # Pastilla oscura semitransparente con borde de acento
    pill = Image.new("RGBA", im.size, (0, 0, 0, 0))
    pd = ImageDraw.Draw(pill)
    pd.rounded_rectangle((x0, y0, x0 + pw, y0 + ph), radius=ph // 2, fill=PILL,
                         outline=ACCENT, width=max(2, size // 128))
    im.alpha_composite(pill)
    draw = ImageDraw.Draw(im)
    draw.text((x0 + pad_x - l, y0 + pad_y - t), text, font=font, fill=ACCENT)
    return im


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", default=SRC_URL, help="Fichero o URL de origen")
    ap.add_argument("--out", default=str(ASSETS), help="Directorio de salida (assets/)")
    ap.add_argument("--ico", default=str(ROOT / "packaging" / "icon.ico"),
                    help="Ruta del .ico para Windows/PyInstaller")
    args = ap.parse_args(argv)

    print(f"Origen: {args.src}")
    src = load_source(args.src)
    print(f"  {src.size[0]}×{src.size[1]} {src.mode}")
    src = strip_white_background(src)
    logo = add_gui_text(fit_square(src, 512))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    logo.save(out / "logo.png")
    logo.resize((256, 256), Image.LANCZOS).save(out / "logo_256.png")
    ico = Path(args.ico)
    ico.parent.mkdir(parents=True, exist_ok=True)
    logo.save(ico, format="ICO", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print(f"Escrito: {out / 'logo.png'}, {out / 'logo_256.png'}, {ico}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
