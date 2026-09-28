"""Sidebar colapsable con menú hamburguesa animado e iconos SVG.

Mejoras v0.6:
- Al colapsar, el avatar queda centrado en la columna estrecha (antes se
  desplazaba a la izquierda con padding y se veía cortado).
- El texto del pill de usuario y los nombres del menú se desvanecen con
  animación de opacidad sincronizada con la anchura del sidebar (antes
  desaparecían de golpe con setVisible).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QSize,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from theme import ACCENT, FG_PRIMARY, FG_SECONDARY
from widgets.avatar import AvatarCircle
from widgets.icons import icon as svg_icon


class NavButton(QPushButton):
    """Botón del menú con icono SVG + texto que se oculta al colapsar."""

    ICON_SIZE = 20
    HEIGHT = 42

    def __init__(self, icon_name: str, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        self._text = text
        self._collapsed = False
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("class", "navItem")
        self.setObjectName("navItem")
        self.setText(f"   {text}")
        self.setIcon(svg_icon(icon_name, FG_SECONDARY, self.ICON_SIZE))
        self.setIconSize(QSize(self.ICON_SIZE, self.ICON_SIZE))
        font = QFont(self.font())
        font.setPointSize(11)
        self.setFont(font)
        self.setMinimumHeight(self.HEIGHT)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.toggled.connect(self._refresh_icon_color)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        if collapsed:
            self.setText("")
            self.setToolTip(self._text)
        else:
            self.setText(f"   {self._text}")
            self.setToolTip("")

    def _refresh_icon_color(self, checked: bool) -> None:
        color = ACCENT if checked else FG_SECONDARY
        self.setIcon(svg_icon(self._icon_name, color, self.ICON_SIZE))


def _wrap_with_opacity(widget: QWidget) -> QGraphicsOpacityEffect:
    """Adjunta un QGraphicsOpacityEffect al widget y devuelve la referencia."""
    eff = QGraphicsOpacityEffect(widget)
    eff.setOpacity(1.0)
    widget.setGraphicsEffect(eff)
    return eff


class Sidebar(QFrame):
    """Sidebar con animación de colapso, menú principal y pill de usuario."""

    EXPANDED_W = 240
    COLLAPSED_W = 64

    nav_changed = pyqtSignal(str)
    login_clicked = pyqtSignal()
    logout_clicked = pyqtSignal()
    profile_clicked = pyqtSignal()   # clic en el avatar / pill de usuario

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(self.EXPANDED_W)
        self._collapsed = False
        self._buttons: Dict[str, NavButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---------- Cabecera ----------
        header = QFrame()
        header.setObjectName("sidebarHeader")
        header.setFixedHeight(64)
        h = QHBoxLayout(header)
        h.setContentsMargins(14, 10, 10, 10)
        h.setSpacing(10)

        self.hamburger = QPushButton()
        self.hamburger.setIcon(svg_icon("menu", FG_PRIMARY, 22))
        self.hamburger.setIconSize(QSize(22, 22))
        self.hamburger.setObjectName("hamburger")
        self.hamburger.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hamburger.setFixedSize(36, 36)
        self.hamburger.clicked.connect(self.toggle)
        h.addWidget(self.hamburger)

        self.brand_wrap = QFrame()
        brand_box = QVBoxLayout(self.brand_wrap)
        brand_box.setContentsMargins(0, 0, 0, 0)
        brand_box.setSpacing(0)
        self.brand = QLabel("DockerLabs")
        self.brand.setObjectName("brand")
        self.brand_sub = QLabel("client · gui")
        self.brand_sub.setObjectName("brandSub")
        brand_box.addWidget(self.brand)
        brand_box.addWidget(self.brand_sub)
        h.addWidget(self.brand_wrap, 1)
        self._brand_opacity = _wrap_with_opacity(self.brand_wrap)
        outer.addWidget(header)

        # ---------- Items de navegación ----------
        nav_wrap = QFrame()
        nav_layout = QVBoxLayout(nav_wrap)
        nav_layout.setContentsMargins(10, 14, 10, 10)
        nav_layout.setSpacing(4)

        nav_items: List[tuple[str, str, str]] = [
            ("dashboard",  "dashboard", "Dashboard"),
            ("machines",   "machines",  "Máquinas"),
            ("downloads",  "download",  "Descargas"),
            ("lab",        "docker",    "Laboratorio"),
            ("completed",  "completed", "Completadas"),
            ("settings",   "settings",  "Ajustes"),
            ("about",      "info",      "Acerca de"),
        ]
        for key, icon_name, text in nav_items:
            btn = NavButton(icon_name, text)
            btn.clicked.connect(lambda _checked=False, k=key: self._on_nav_clicked(k))
            self._group.addButton(btn)
            self._buttons[key] = btn
            nav_layout.addWidget(btn)
        nav_layout.addItem(
            QSpacerItem(0, 10, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        )
        outer.addWidget(nav_wrap, 1)

        # ---------- Pill de usuario (abajo) ----------
        # Usamos un layout horizontal con stretch a ambos lados del avatar para
        # que se centre automáticamente cuando el sidebar se colapsa.
        self.pill_wrap = QFrame()
        self.pill_wrap.setObjectName("pillWrap")
        pill_root = QHBoxLayout(self.pill_wrap)
        pill_root.setContentsMargins(4, 4, 4, 12)
        pill_root.setSpacing(0)

        # Contenedor central que aloja avatar + texto + botón
        # Margenes laterales = 6px para que en colapsado (64 px de sidebar)
        # el avatar de 44 px quede centrado: 4+6+44+6+4 = 64 px exactos.
        self.user_pill = QFrame()
        self.user_pill.setObjectName("userPill")
        # Todo el pill es clicable; el cursor de puntero le indica al usuario
        # que puede pulsarlo para ir a la página de sesión.
        self.user_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.user_pill.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        pill_layout = QHBoxLayout(self.user_pill)
        pill_layout.setContentsMargins(6, 8, 6, 8)
        pill_layout.setSpacing(10)

        # Avatar dentro de un wrapper que SIEMPRE mide AvatarCircle.SIZE+gap
        # asi al colapsar queda perfectamente centrado en los 64 px.
        self.avatar = AvatarCircle()
        self.avatar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        pill_layout.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignVCenter)
        # Clic en el pill o en el avatar → profile_clicked (sin monkey-patching)
        self.user_pill.installEventFilter(self)
        self.avatar.installEventFilter(self)

        # Texto (nombre + estado)
        self.user_text_wrap = QFrame()
        self.user_text_wrap.setObjectName("userTextWrap")
        # Los labels internos heredan el cursor de puntero del pill.
        self.user_text_wrap.setCursor(Qt.CursorShape.PointingHandCursor)
        text_layout = QVBoxLayout(self.user_text_wrap)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(0)
        self.user_name = QLabel("Invitado")
        self.user_name.setObjectName("userName")
        self.user_name.setCursor(Qt.CursorShape.PointingHandCursor)
        self.user_status = QLabel("No autenticado")
        self.user_status.setObjectName("userStatus")
        self.user_status.setCursor(Qt.CursorShape.PointingHandCursor)
        text_layout.addWidget(self.user_name)
        text_layout.addWidget(self.user_status)
        pill_layout.addWidget(self.user_text_wrap, 1)
        self._text_opacity = _wrap_with_opacity(self.user_text_wrap)

        # Botón de sesión
        self.session_btn = QPushButton("Login")
        self.session_btn.setProperty("class", "primary")
        self.session_btn.setObjectName("sessionBtn")
        self.session_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.session_btn.setFixedHeight(32)
        self.session_btn.setMinimumWidth(64)
        self.session_btn.setStyleSheet(
            "QPushButton { padding: 4px 12px; font-weight: 700; }"
        )
        self.session_btn.clicked.connect(self.login_clicked.emit)
        pill_layout.addWidget(self.session_btn)
        self._btn_opacity = _wrap_with_opacity(self.session_btn)

        pill_root.addWidget(self.user_pill, 1)
        outer.addWidget(self.pill_wrap)

        # selección por defecto
        self.set_current("dashboard")

        # ---------- Animaciones ----------
        self._anim_min = QPropertyAnimation(self, b"minimumWidth", self)
        self._anim_min.setDuration(260)
        self._anim_min.setEasingCurve(QEasingCurve.Type.InOutCubic)

        self._anim_max = QPropertyAnimation(self, b"maximumWidth", self)
        self._anim_max.setDuration(260)
        self._anim_max.setEasingCurve(QEasingCurve.Type.InOutCubic)

        # Animaciones de opacidad para los 3 bloques textuales
        self._anim_brand = QPropertyAnimation(self._brand_opacity, b"opacity", self)
        self._anim_brand.setDuration(180)
        self._anim_brand.setEasingCurve(QEasingCurve.Type.InOutQuad)

        self._anim_text = QPropertyAnimation(self._text_opacity, b"opacity", self)
        self._anim_text.setDuration(180)
        self._anim_text.setEasingCurve(QEasingCurve.Type.InOutQuad)

        self._anim_btn = QPropertyAnimation(self._btn_opacity, b"opacity", self)
        self._anim_btn.setDuration(180)
        self._anim_btn.setEasingCurve(QEasingCurve.Type.InOutQuad)

        # Anchura máxima animada del texto del pill para colapsarlo suavemente
        self.user_text_wrap.setMaximumWidth(200)
        self._anim_text_w = QPropertyAnimation(self.user_text_wrap, b"maximumWidth", self)
        self._anim_text_w.setDuration(220)
        self._anim_text_w.setEasingCurve(QEasingCurve.Type.InOutCubic)

        self.session_btn.setMaximumWidth(120)
        self._anim_btn_w = QPropertyAnimation(self.session_btn, b"maximumWidth", self)
        self._anim_btn_w.setDuration(220)
        self._anim_btn_w.setEasingCurve(QEasingCurve.Type.InOutCubic)

        # Brand wrap: animar maximumWidth para que la cabecera no quede con
        # un hueco gigante al colapsar
        self.brand_wrap.setMaximumWidth(200)
        self._anim_brand_w = QPropertyAnimation(self.brand_wrap, b"maximumWidth", self)
        self._anim_brand_w.setDuration(220)
        self._anim_brand_w.setEasingCurve(QEasingCurve.Type.InOutCubic)

        self._group_anim = QParallelAnimationGroup(self)
        for a in (self._anim_min, self._anim_max,
                  self._anim_brand, self._anim_text, self._anim_btn,
                  self._anim_text_w, self._anim_btn_w, self._anim_brand_w):
            self._group_anim.addAnimation(a)

    # ---------- API ----------

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj in (self.user_pill, self.avatar) and event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton:
                self.profile_clicked.emit()
                return True
        return super().eventFilter(obj, event)

    def set_current(self, key: str) -> None:
        btn = self._buttons.get(key)
        if btn is not None:
            btn.setChecked(True)

    def _on_nav_clicked(self, key: str) -> None:
        self.nav_changed.emit(key)

    def toggle(self) -> None:
        self._collapsed = not self._collapsed
        self._animate_to(self._collapsed)
        # El texto de los nav items lo cambiamos al inicio: el ancho del
        # botón ya está fijado por el contenedor, así que no parpadea.
        for btn in self._buttons.values():
            btn.set_collapsed(self._collapsed)

    def _animate_to(self, collapsed: bool) -> None:
        target_w = self.COLLAPSED_W if collapsed else self.EXPANDED_W
        text_opacity = 0.0 if collapsed else 1.0
        text_w_target = 0 if collapsed else 200
        btn_w_target = 0 if collapsed else 120
        brand_w_target = 0 if collapsed else 200

        self._group_anim.stop()

        cur_w = self.width()
        self._anim_min.setStartValue(cur_w); self._anim_min.setEndValue(target_w)
        self._anim_max.setStartValue(cur_w); self._anim_max.setEndValue(target_w)

        self._anim_brand.setStartValue(self._brand_opacity.opacity())
        self._anim_brand.setEndValue(text_opacity)
        self._anim_text.setStartValue(self._text_opacity.opacity())
        self._anim_text.setEndValue(text_opacity)
        self._anim_btn.setStartValue(self._btn_opacity.opacity())
        self._anim_btn.setEndValue(text_opacity)

        self._anim_brand_w.setStartValue(self.brand_wrap.maximumWidth())
        self._anim_brand_w.setEndValue(brand_w_target)
        self._anim_text_w.setStartValue(self.user_text_wrap.maximumWidth())
        self._anim_text_w.setEndValue(text_w_target)
        self._anim_btn_w.setStartValue(self.session_btn.maximumWidth())
        self._anim_btn_w.setEndValue(btn_w_target)

        self._group_anim.start()

    # ---- estado de sesión ----

    def set_logged_in(self, username: str) -> None:
        self.user_name.setText(username)
        self.user_status.setText("Sesión activa")
        self.session_btn.setText("Salir")
        try:
            self.session_btn.clicked.disconnect()
        except TypeError:
            pass
        self.session_btn.clicked.connect(self.logout_clicked.emit)
        self.avatar.set_logged_in(username)

    def set_logged_out(self) -> None:
        self.user_name.setText("Invitado")
        self.user_status.setText("No autenticado")
        self.session_btn.setText("Login")
        try:
            self.session_btn.clicked.disconnect()
        except TypeError:
            pass
        self.session_btn.clicked.connect(self.login_clicked.emit)
        self.avatar.set_logged_out()
