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
