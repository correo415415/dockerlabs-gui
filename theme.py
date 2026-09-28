"""Paleta y QSS centralizados para dockerlabs-qt.

Dos temas: `dark` (por defecto, grisáceo oscuro/profesional, acentos cian
DockerLabs) y `light`. Los widgets importan las constantes de color de este
módulo; `apply_theme()` las actualiza en caliente (también en los módulos que
ya las importaron) y devuelve el QSS a aplicar en la QApplication.
"""
from __future__ import annotations

import sys
from typing import Dict

# ---------- Paletas ----------
PALETTES: Dict[str, Dict[str, str]] = {
    "dark": dict(
        BG_DARK="#1f2229",        # fondo ventana principal
        BG_MID="#262a33",         # paneles
        BG_LIGHT="#2f343f",       # tarjetas / inputs
        BG_HOVER="#363b46",
        BG_SIDEBAR="#191c22",     # menú lateral
        BG_SIDEBAR_HV="#272c36",
        FG_PRIMARY="#e6e8ec",
        FG_SECONDARY="#9ba2af",
        FG_MUTED="#6e7480",
        ACCENT="#22d3ee",         # cian DockerLabs
        ACCENT_DIM="#0e7490",
        ACCENT_HOVER="#67e3f5",
        ON_ACCENT="#0b1316",      # texto/iconos sobre el acento
        DANGER="#ef4444",
        SUCCESS="#22c55e",
        WARNING="#f59e0b",
        BORDER="#333845",         # gris azulado neutro
        BORDER_SOFT="#2a2f3a",    # divisor más suave
    ),
    "light": dict(
        BG_DARK="#f2f4f7",
        BG_MID="#ffffff",
        BG_LIGHT="#eceff3",
        BG_HOVER="#e1e5ea",
        BG_SIDEBAR="#e7eaee",
        BG_SIDEBAR_HV="#d9dde3",
        FG_PRIMARY="#1a1d23",
        FG_SECONDARY="#4b5260",
        FG_MUTED="#7a8190",
        ACCENT="#0e93ad",
        ACCENT_DIM="#3aa7bf",
        ACCENT_HOVER="#0b7f96",
        ON_ACCENT="#ffffff",
        DANGER="#dc2626",
        SUCCESS="#16a34a",
        WARNING="#d97706",
        BORDER="#d3d8df",
        BORDER_SOFT="#e2e6eb",
    ),
}
THEMES = tuple(PALETTES)
DEFAULT_THEME = "dark"
PALETTE_KEYS = tuple(PALETTES[DEFAULT_THEME])

# Colores de dificultad (iguales en ambos temas: son los de la web)
DIFF_VFACIL   = "#43959b"
DIFF_FACIL    = "#8bc34a"
DIFF_MEDIO    = "#e0a553"
DIFF_DIFICIL  = "#d83c31"

# ---------- Constantes activas (tema oscuro por defecto) ----------
_D = PALETTES[DEFAULT_THEME]
BG_DARK       = _D["BG_DARK"]
BG_MID        = _D["BG_MID"]
BG_LIGHT      = _D["BG_LIGHT"]
BG_HOVER      = _D["BG_HOVER"]
BG_SIDEBAR    = _D["BG_SIDEBAR"]
BG_SIDEBAR_HV = _D["BG_SIDEBAR_HV"]
FG_PRIMARY    = _D["FG_PRIMARY"]
FG_SECONDARY  = _D["FG_SECONDARY"]
FG_MUTED      = _D["FG_MUTED"]
ACCENT        = _D["ACCENT"]
ACCENT_DIM    = _D["ACCENT_DIM"]
ACCENT_HOVER  = _D["ACCENT_HOVER"]
ON_ACCENT     = _D["ON_ACCENT"]
DANGER        = _D["DANGER"]
SUCCESS       = _D["SUCCESS"]
WARNING       = _D["WARNING"]
BORDER        = _D["BORDER"]
BORDER_SOFT   = _D["BORDER_SOFT"]

CURRENT_THEME = DEFAULT_THEME


def apply_theme(theme: str) -> str:
    """Activa `theme`: actualiza las constantes de este módulo y de los módulos
    que ya las importaron (`from theme import ACCENT`) y devuelve su QSS.
    Los widgets ya creados conservan sus estilos inline hasta reiniciar.
    """
    global CURRENT_THEME  # noqa: PLW0603
    if theme not in PALETTES:
        theme = DEFAULT_THEME
    palette = PALETTES[theme]
    me = sys.modules[__name__]
    for key, value in palette.items():
        setattr(me, key, value)
    for name, mod in list(sys.modules.items()):
        if mod is None or mod is me:
            continue
        if not (name.startswith("widgets") or name in ("main", "__main__")):
            continue
        for key, value in palette.items():
            if hasattr(mod, key):
                try:
                    setattr(mod, key, value)
                except Exception:  # noqa: BLE001
                    pass
    CURRENT_THEME = theme
    return build_qss(theme)


def build_qss(theme: str = DEFAULT_THEME) -> str:
    """Genera la hoja de estilos completa para el tema indicado."""
    p = PALETTES.get(theme, PALETTES[DEFAULT_THEME])
    BG_DARK, BG_MID, BG_LIGHT, BG_HOVER = p["BG_DARK"], p["BG_MID"], p["BG_LIGHT"], p["BG_HOVER"]
    BG_SIDEBAR, BG_SIDEBAR_HV = p["BG_SIDEBAR"], p["BG_SIDEBAR_HV"]
    FG_PRIMARY, FG_SECONDARY, FG_MUTED = p["FG_PRIMARY"], p["FG_SECONDARY"], p["FG_MUTED"]
    ACCENT, ACCENT_DIM, ACCENT_HOVER, ON_ACCENT = p["ACCENT"], p["ACCENT_DIM"], p["ACCENT_HOVER"], p["ON_ACCENT"]
    DANGER = p["DANGER"]
    BORDER, BORDER_SOFT = p["BORDER"], p["BORDER_SOFT"]
    return f"""

* {{
    font-family: "Inter", "Segoe UI", "Noto Sans", "Helvetica Neue", Arial, sans-serif;
    color: {FG_PRIMARY};
    font-size: 13px;
    outline: none;
}}

QMainWindow, QWidget#rootWidget {{
    background: {BG_DARK};
}}

/* ---------- Sidebar ---------- */
QFrame#sidebar {{
    background: {BG_SIDEBAR};
    border-right: 1px solid {BORDER};
}}
QFrame#sidebarHeader {{
    background: transparent;
    border-bottom: 1px solid {BORDER};
}}
QLabel#brand {{
    color: {FG_PRIMARY};
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 0.4px;
}}
QLabel#brandSub {{
    color: {FG_MUTED};
    font-size: 10px;
    letter-spacing: 2px;
    text-transform: uppercase;
}}

QPushButton#hamburger {{
    background: transparent;
    border: none;
    color: {FG_PRIMARY};
    font-size: 20px;
    padding: 6px;
    border-radius: 6px;
}}
QPushButton#hamburger:hover {{
    background: {BG_SIDEBAR_HV};
}}

QPushButton.navItem {{
    background: transparent;
    border: none;
    color: {FG_SECONDARY};
    text-align: left;
    padding: 10px 14px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 500;
}}
QPushButton.navItem:hover {{
    background: {BG_SIDEBAR_HV};
    color: {FG_PRIMARY};
}}
QPushButton.navItem:checked {{
    background: {BG_SIDEBAR_HV};
    color: {ACCENT};
    border-left: 3px solid {ACCENT};
    padding-left: 11px;
}}

/* ---------- Footer del sidebar / avatar ---------- */
QFrame#userPill {{
    background: {BG_SIDEBAR_HV};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QFrame#userPill:hover {{
    background: {BG_HOVER};
    border: 1px solid {ACCENT_DIM};
}}
QLabel#userName {{
    color: {FG_PRIMARY};
    font-weight: 600;
    font-size: 13px;
}}
QLabel#userStatus {{
    color: {FG_MUTED};
    font-size: 11px;
}}

/* ---------- Topbar ---------- */
QFrame#topbar {{
    background: {BG_MID};
    border-bottom: 1px solid {BORDER};
}}
QLabel#pageTitle {{
    font-size: 20px;
    font-weight: 700;
    color: {FG_PRIMARY};
}}
QLabel#pageSubtitle {{
    color: {FG_MUTED};
    font-size: 12px;
}}

/* ---------- Tarjetas ---------- */
QFrame.card {{
    background: {BG_MID};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QLabel.cardTitle {{
    color: {FG_PRIMARY};
    font-weight: 700;
    font-size: 14px;
}}
QLabel.cardValue {{
    color: {ACCENT};
    font-weight: 800;
    font-size: 22px;
}}
QLabel.muted {{
    color: {FG_MUTED};
}}

/* ---------- Inputs ---------- */
QLineEdit, QPlainTextEdit, QTextEdit {{
    background: {BG_LIGHT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 10px;
    color: {FG_PRIMARY};
    selection-background-color: {ACCENT_DIM};
}}
QLineEdit:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit#search {{
    padding-left: 34px;  /* deja sitio al icono */
}}

QComboBox {{
    background: {BG_MID};
    border: 0;
    border-radius: 8px;
    padding: 6px 32px 6px 12px;
    color: {FG_SECONDARY};
    font-weight: 500;
    outline: 0;
    selection-background-color: transparent;
    selection-color: {FG_PRIMARY};
}}
QComboBox:hover {{
    background: {BG_LIGHT};
    border: 0;
    color: {FG_PRIMARY};
}}
QComboBox:focus {{
    background: {BG_LIGHT};
    border: 0;
    color: {FG_PRIMARY};
    outline: 0;
}}
QComboBox:on {{
    background: {BG_LIGHT};
    border: 0;
    color: {FG_PRIMARY};
    outline: 0;
}}
QComboBox::drop-down {{
    border: 0;
    background: transparent;
    width: 24px;
    subcontrol-origin: padding;
    subcontrol-position: top right;
}}
QComboBox::drop-down:hover, QComboBox::drop-down:on {{
    border: 0;
    background: transparent;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {FG_MUTED};
    width: 0;
    height: 0;
    margin-right: 10px;
}}
QComboBox:hover::down-arrow, QComboBox:on::down-arrow {{
    border-top: 5px solid {ACCENT};
}}
QComboBox QAbstractItemView {{
    background: {BG_MID};
    border: 1px solid {BORDER};
    border-radius: 8px;
    selection-background-color: {BG_HOVER};
    selection-color: {ACCENT};
    color: {FG_PRIMARY};
    padding: 4px;
    outline: 0;
}}
QComboBox QAbstractItemView::item {{
    padding: 6px 10px;
    border-radius: 6px;
    min-height: 22px;
}}
QComboBox QAbstractItemView::item:hover {{
    background: {BG_HOVER};
}}
QComboBox QLineEdit {{
    background: transparent;
    border: 0;
    color: {FG_PRIMARY};
    selection-background-color: transparent;
    selection-color: {FG_PRIMARY};
}}
QComboBox QAbstractItemView QScrollBar:horizontal {{
    height: 0;
    background: transparent;
}}

/* ---------- SpinBox ---------- */
QSpinBox {{
    background: {BG_LIGHT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px 8px;
    color: {FG_PRIMARY};
    selection-background-color: {ACCENT_DIM};
}}
QSpinBox:focus {{ border: 1px solid {ACCENT}; }}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 18px;
    background: transparent;
    border: none;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {BG_HOVER}; }}

/* ---------- Botones ---------- */
QPushButton.primary {{
    background: {ACCENT};
    border: none;
    border-radius: 8px;
    color: {ON_ACCENT};
    font-weight: 700;
    padding: 9px 18px;
}}
QPushButton.primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton.primary:disabled {{ background: {ACCENT_DIM}; color: {ON_ACCENT}; }}

QPushButton.ghost {{
    background: transparent;
    border: 1px solid {BORDER};
    border-radius: 8px;
    color: {FG_PRIMARY};
    padding: 8px 14px;
}}
QPushButton.ghost:hover {{ background: {BG_HOVER}; }}
QPushButton.ghost:disabled {{ color: {FG_MUTED}; border-color: {BORDER_SOFT}; }}

QCheckBox {{
    color: {FG_PRIMARY};
    spacing: 10px;
    padding: 4px 0;
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border-radius: 5px;
    border: 1px solid {BORDER};
    background: {BG_LIGHT};
}}
QCheckBox::indicator:hover {{ border: 1px solid {ACCENT_DIM}; }}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    image: none;
}}
QCheckBox::indicator:disabled {{ background: {BORDER_SOFT}; border-color: {BORDER_SOFT}; }}

QPushButton.danger {{
    background: transparent;
    border: 1px solid {DANGER};
    color: {DANGER};
    border-radius: 8px;
    padding: 8px 14px;
}}
QPushButton.danger:hover {{ background: {DANGER}; color: white; }}

/* ---------- Tabla ---------- */
QTableView, QTableWidget {{
    background: {BG_MID};
    alternate-background-color: {BG_LIGHT};
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: 8px;
    color: {FG_PRIMARY};
    selection-background-color: {ACCENT_DIM};
    selection-color: {ON_ACCENT};
}}
QHeaderView::section {{
    background: {BG_LIGHT};
    color: {FG_SECONDARY};
    padding: 8px 10px;
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    font-weight: 600;
}}
QTableCornerButton::section {{ background: {BG_LIGHT}; border: none; }}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 4px 2px;
}}
QScrollBar::handle:vertical {{
    background: {BG_HOVER};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {ACCENT_DIM}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; background: transparent; }}
QScrollBar:horizontal {{ height: 10px; background: transparent; margin: 2px 4px; }}
QScrollBar::handle:horizontal {{ background: {BG_HOVER}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; background: transparent; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------- Tooltips ---------- */
QToolTip {{
    background: {BG_LIGHT};
    color: {FG_PRIMARY};
    border: 1px solid {ACCENT};
    border-radius: 6px;
    padding: 6px 8px;
}}

QStatusBar {{
    background: {BG_SIDEBAR};
    color: {FG_MUTED};
    border-top: 1px solid {BORDER};
}}
"""  # noqa: E501


QSS = build_qss(DEFAULT_THEME)


def difficulty_color(name: str) -> str:
    n = (name or "").strip().lower()
    if "muy" in n:
        return DIFF_VFACIL
    if "fácil" in n or "facil" in n:
        return DIFF_FACIL
    if "medio" in n:
        return DIFF_MEDIO
    if "difícil" in n or "dificil" in n:
        return DIFF_DIFICIL
    return FG_MUTED
