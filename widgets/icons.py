"""Iconos SVG embebidos para que se vean igual en cualquier SO.

No depende de fuentes de emoji ni de iconos del sistema. Cada icono se entrega
como `QIcon` cacheado, render correcto incluso en displays HiDPI.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Dict

from PyQt6.QtCore import QByteArray, QSize, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer


# Cada icono es un SVG vectorial 24x24 con stroke en `currentColor`.
# Al renderizarlos sustituimos `currentColor` por el color que queramos.
_SVG: Dict[str, str] = {
    "dashboard": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'
             stroke='currentColor' stroke-width='2' stroke-linecap='round'
             stroke-linejoin='round'>
            <rect x='3' y='3' width='7' height='9' rx='1.5'/>
            <rect x='14' y='3' width='7' height='5' rx='1.5'/>
            <rect x='14' y='12' width='7' height='9' rx='1.5'/>
            <rect x='3' y='16' width='7' height='5' rx='1.5'/>
        </svg>""",
    "machines": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'
             stroke='currentColor' stroke-width='2' stroke-linecap='round'
             stroke-linejoin='round'>
            <rect x='3' y='4' width='18' height='12' rx='2'/>
            <path d='M3 10h18'/>
            <path d='M8 20h8'/>
            <path d='M12 16v4'/>
        </svg>""",
    "download": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'
             stroke='currentColor' stroke-width='2' stroke-linecap='round'
             stroke-linejoin='round'>
            <path d='M21 15v3a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3v-3'/>
            <path d='M7 10l5 5 5-5'/>
            <path d='M12 15V3'/>
        </svg>""",
    "completed": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'
             stroke='currentColor' stroke-width='2' stroke-linecap='round'
             stroke-linejoin='round'>
            <circle cx='12' cy='12' r='9'/>
            <path d='M8 12l3 3 5-6'/>
        </svg>""",
    "session": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'
             stroke='currentColor' stroke-width='2' stroke-linecap='round'
             stroke-linejoin='round'>
            <rect x='3' y='10' width='18' height='11' rx='2'/>
            <path d='M7 10V7a5 5 0 0 1 10 0v3'/>
            <circle cx='12' cy='15.5' r='1.5'/>
        </svg>""",
    "logout": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'
             stroke='currentColor' stroke-width='2' stroke-linecap='round'
             stroke-linejoin='round'>
            <path d='M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3'/>
            <path d='M10 17l-5-5 5-5'/>
            <path d='M5 12h11'/>
        </svg>""",
    "settings": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'
             stroke='currentColor' stroke-width='2' stroke-linecap='round'
             stroke-linejoin='round'>
            <circle cx='12' cy='12' r='3'/>
            <path d='M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z'/>
        </svg>""",
    "info": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'
             stroke='currentColor' stroke-width='2' stroke-linecap='round'
             stroke-linejoin='round'>
            <circle cx='12' cy='12' r='9'/>
            <path d='M12 8h.01'/>
            <path d='M11 12h1v5h1'/>
        </svg>""",
    "menu": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'
             stroke='currentColor' stroke-width='2.2' stroke-linecap='round'
             stroke-linejoin='round'>
            <line x1='4' y1='7' x2='20' y2='7'/>
            <line x1='4' y1='12' x2='20' y2='12'/>
            <line x1='4' y1='17' x2='20' y2='17'/>
        </svg>""",
    "search": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'
             stroke='currentColor' stroke-width='2' stroke-linecap='round'
             stroke-linejoin='round'>
            <circle cx='11' cy='11' r='7'/>
            <line x1='21' y1='21' x2='16.5' y2='16.5'/>
        </svg>""",
    "check": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'
             stroke='currentColor' stroke-width='2.6' stroke-linecap='round'
             stroke-linejoin='round'>
            <path d='M5 12l5 5 9-11'/>
        </svg>""",
    "circle": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'
             stroke='currentColor' stroke-width='2' stroke-linecap='round'
             stroke-linejoin='round'>
            <circle cx='12' cy='12' r='8'/>
        </svg>""",
    "play": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'
             fill='currentColor' stroke='currentColor' stroke-width='1'
             stroke-linejoin='round'>
            <path d='M7 5v14l12-7z'/>
        </svg>""",
    "pause": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'
             fill='currentColor' stroke='currentColor'>
            <rect x='7' y='5' width='3.5' height='14' rx='1'/>
            <rect x='13.5' y='5' width='3.5' height='14' rx='1'/>
        </svg>""",
    "trash": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'
             stroke='currentColor' stroke-width='2' stroke-linecap='round'
             stroke-linejoin='round'>
            <path d='M4 7h16'/>
            <path d='M10 11v6'/><path d='M14 11v6'/>
            <path d='M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2l1-12'/>
            <path d='M9 7V4h6v3'/>
        </svg>""",
    "user": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'
             stroke='currentColor' stroke-width='2' stroke-linecap='round'
             stroke-linejoin='round'>
            <circle cx='12' cy='8' r='4'/>
            <path d='M4 21c0-4 4-7 8-7s8 3 8 7'/>
        </svg>""",
    "refresh": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'
             stroke='currentColor' stroke-width='2' stroke-linecap='round'
             stroke-linejoin='round'>
            <path d='M3 12a9 9 0 0 1 15.6-6.1L21 8'/>
            <path d='M21 4v4h-4'/>
            <path d='M21 12a9 9 0 0 1-15.6 6.1L3 16'/>
            <path d='M3 20v-4h4'/>
        </svg>""",
    "folder": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'
             stroke='currentColor' stroke-width='2' stroke-linecap='round'
             stroke-linejoin='round'>
            <path d='M3 7a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z'/>
        </svg>""",
    "wifi-off": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'
             stroke='currentColor' stroke-width='2' stroke-linecap='round'
             stroke-linejoin='round'>
            <path d='M2 8.5a16 16 0 0 1 3.5-2.3'/>
            <path d='M8.5 5a16 16 0 0 1 13.5 3.5'/>
            <path d='M5 12.5a11 11 0 0 1 4-2.4'/>
            <path d='M12 9.5a11 11 0 0 1 7 3'/>
            <path d='M8.5 16a6 6 0 0 1 7 0'/>
            <line x1='12' y1='20' x2='12' y2='20.01'/>
            <line x1='3' y1='3' x2='21' y2='21'/>
        </svg>""",
    "stop": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><rect x='6' y='6' width='12' height='12' rx='2'/></svg>""",
    "docker": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M3 13h18a4 4 0 0 1-4 5H7a4 4 0 0 1-4-5z'/><rect x='6' y='9' width='3' height='3'/><rect x='10' y='9' width='3' height='3'/><rect x='14' y='9' width='3' height='3'/><rect x='10' y='5' width='3' height='3'/><path d='M21 13c1.5 0 2-1 2-1'/></svg>""",
    "terminal": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><rect x='3' y='4' width='18' height='16' rx='2'/><path d='m7 9 3 3-3 3'/><path d='M12 15h5'/></svg>""",
    "star": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='m12 3 2.9 5.9 6.5.9-4.7 4.6 1.1 6.5L12 17.8 6.2 20.9l1.1-6.5L2.6 9.8l6.5-.9z'/></svg>""",
    "external-link": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M14 4h6v6'/><path d='M20 4 10 14'/><path d='M18 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h6'/></svg>""",
    "flask": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M9 3h6'/><path d='M10 3v6l-5.5 9.5A1.5 1.5 0 0 0 5.8 21h12.4a1.5 1.5 0 0 0 1.3-2.5L14 9V3'/><path d='M7.5 15h9'/></svg>""",
    "restart": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M3 12a9 9 0 1 0 3-6.7'/><path d='M3 4v5h5'/></svg>""",
    "ip": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><circle cx='12' cy='12' r='9'/><path d='M3 12h18'/><path d='M12 3a14 14 0 0 1 0 18'/><path d='M12 3a14 14 0 0 0 0 18'/></svg>""",
    "image": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><rect x='3' y='4' width='18' height='16' rx='2'/><circle cx='8.5' cy='9.5' r='1.5'/><path d='m21 16-5-5-9 9'/></svg>""",
    "chevron-right": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='m9 6 6 6-6 6'/></svg>""",
    "clock": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><circle cx='12' cy='12' r='9'/><path d='M12 7v5l3 2'/></svg>""",
    "list": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M8 6h13'/><path d='M8 12h13'/><path d='M8 18h13'/><path d='M3 6h.01'/><path d='M3 12h.01'/><path d='M3 18h.01'/></svg>""",
    "trophy": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M8 21h8'/><path d='M12 17v4'/><path d='M7 4h10v5a5 5 0 0 1-10 0z'/><path d='M7 6H4v2a3 3 0 0 0 3 3'/><path d='M17 6h3v2a3 3 0 0 1-3 3'/></svg>""",
    "video": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><rect x='3' y='6' width='13' height='12' rx='2'/><path d='m16 10 5-3v10l-5-3'/></svg>""",
    "doc": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V8z'/><path d='M14 3v5h5'/><path d='M9 13h6'/><path d='M9 17h6'/></svg>""",
    "x": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M18 6 6 18'/><path d='m6 6 12 12'/></svg>""",
    "sun": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><circle cx='12' cy='12' r='4'/><path d='M12 2v2'/><path d='M12 20v2'/><path d='m4.9 4.9 1.4 1.4'/><path d='m17.7 17.7 1.4 1.4'/><path d='M2 12h2'/><path d='M20 12h2'/><path d='m6.3 17.7-1.4 1.4'/><path d='m19.1 4.9-1.4 1.4'/></svg>""",
    "moon": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z'/></svg>""",
    "shield": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z'/><path d='m9 12 2 2 4-4'/></svg>""",
    "lock": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><rect width='18' height='11' x='3' y='11' rx='2' ry='2'/><path d='M7 11V7a5 5 0 0 1 10 0v4'/></svg>""",
    "key": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='m15.5 7.5 2.3 2.3a1 1 0 0 0 1.4 0l2.1-2.1a1 1 0 0 0 0-1.4L19 4'/><path d='m21 2-9.6 9.6'/><circle cx='7.5' cy='15.5' r='5.5'/></svg>""",
    "power": """
        <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M12 2v10'/><path d='M18.4 6.6a9 9 0 1 1-12.77.04'/></svg>""",
}


def _render_svg(svg: str, size: int, color: str) -> QPixmap:
    coloured = svg.replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(coloured.encode("utf-8")))
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter)
    painter.end()
    return pix


@lru_cache(maxsize=128)
def icon(name: str, color: str = "#e6e8ec", size: int = 24) -> QIcon:
    """Devuelve un QIcon coloreado a partir del nombre de icono SVG."""
    svg = _SVG.get(name)
    if svg is None:
        return QIcon()
    return QIcon(_render_svg(svg, size, color))


@lru_cache(maxsize=128)
def pixmap(name: str, color: str = "#e6e8ec", size: int = 24) -> QPixmap:
    svg = _SVG.get(name)
    if svg is None:
        return QPixmap()
    return _render_svg(svg, size, color)


def available() -> list[str]:
    return list(_SVG.keys())
