"""Toast in-app: notificación deslizante en la esquina inferior derecha.

- Aparece con animación slide-in + fade-in.
- Permanece 2.8 s (configurable).
- Desaparece con slide-out + fade-out.
- Se apilan verticalmente si llegan varias.
"""
from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from theme import (
    ACCENT,
    BG_LIGHT,
    BG_MID,
    DANGER,
    FG_MUTED,
    FG_PRIMARY,
    SUCCESS,
    WARNING,
)
from widgets.icons import pixmap as svg_pixmap

_KIND_COLORS = {
    "info":    ACCENT,
    "success": SUCCESS,
    "warning": WARNING,
    "error":   DANGER,
}
_KIND_ICONS = {
    "info":    "info",
    "success": "check",
    "warning": "info",
    "error":   "info",
}


class Toast(QFrame):
    closed = pyqtSignal(object)
    clicked = pyqtSignal(object)

    WIDTH = 340
    H_MARGIN = 18
    V_MARGIN = 18
    DURATION_MS = 2800

    def __init__(self, parent: QWidget, title: str, body: str = "",
                 kind: str = "info", duration_ms: Optional[int] = None,
                 clickable: bool = False, payload: Optional[object] = None) -> None:
        super().__init__(parent)
        self._kind = kind if kind in _KIND_COLORS else "info"
        self._duration_ms = duration_ms or self.DURATION_MS
        self._accent = _KIND_COLORS[self._kind]
        self._clickable = clickable
        self.payload = payload

        self.setObjectName("toast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(self.WIDTH)
        self.setStyleSheet(
            f"QFrame#toast {{ background: {BG_MID};"
            f" border: 1px solid #2a2f3a; border-left: 4px solid {self._accent};"
            f" border-radius: 10px; }}"
            f"QFrame#toast:hover {{ background: {BG_LIGHT}; }}"
        )
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Sombra suave
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 140))
        self.setGraphicsEffect(shadow)

        h = QHBoxLayout(self)
        h.setContentsMargins(14, 12, 14, 12)
        h.setSpacing(12)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(svg_pixmap(_KIND_ICONS[self._kind], self._accent, 22))
        icon_lbl.setFixedSize(22, 22)
        h.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignTop)

        text_wrap = QVBoxLayout()
        text_wrap.setContentsMargins(0, 0, 0, 0)
        text_wrap.setSpacing(2)
        self.lbl_title = QLabel(title)
        self.lbl_title.setStyleSheet(
            f"color: {FG_PRIMARY}; font-weight: 700; font-size: 13px;"
        )
        self.lbl_title.setWordWrap(True)
        text_wrap.addWidget(self.lbl_title)
        if body:
            self.lbl_body = QLabel(body)
            self.lbl_body.setStyleSheet(f"color: {FG_MUTED}; font-size: 11px;")
            self.lbl_body.setWordWrap(True)
            text_wrap.addWidget(self.lbl_body)
        h.addLayout(text_wrap, 1)

        # Animaciones — necesitan que el widget esté ya posicionado
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        # No podemos combinar opacity + drop-shadow en el mismo widget;
        # encapsulamos opacidad vía un proxy: usamos setWindowOpacity en su
        # lugar — pero como es child, animamos via QPropertyAnimation sobre
        # 'windowOpacity' del top-level. Aquí preferimos animar 'pos' y la
        # transparencia del color de fondo es estática.
        self._anim_pos = QPropertyAnimation(self, b"pos", self)
        self._anim_pos.setDuration(280)
        self._anim_pos.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)

    def show_at(self, x: int, y_target: int) -> None:
        start_pos = QPoint(x + 30, y_target)
        end_pos = QPoint(x, y_target)
        self.move(start_pos)
        self.show()
        self.raise_()
        self._anim_pos.stop()
        self._anim_pos.setStartValue(start_pos)
        self._anim_pos.setEndValue(end_pos)
        self._anim_pos.start()
        self._timer.start(self._duration_ms)

    def dismiss(self) -> None:
        start = self.pos()
        end = QPoint(start.x() + 40, start.y())
        self._anim_pos.stop()
        self._anim_pos.setStartValue(start)
        self._anim_pos.setEndValue(end)
        try:
            self._anim_pos.finished.disconnect()
        except TypeError:
            pass
        self._anim_pos.finished.connect(self._after_dismiss)
        self._anim_pos.start()

    def _after_dismiss(self) -> None:
        self.closed.emit(self)
        self.hide()
        self.deleteLater()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self)
            self.dismiss()
        super().mouseReleaseEvent(event)


class ToastManager(QObject):
    """Apila varios toasts en la esquina inferior derecha del host."""

    GAP = 10

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.host = host
        self._toasts: List[Toast] = []

    def show(self, title: str, body: str = "", kind: str = "info",
             duration_ms: Optional[int] = None,
             on_click=None, payload: Optional[object] = None):
        toast = Toast(self.host, title=title, body=body, kind=kind,
                      duration_ms=duration_ms,
                      clickable=on_click is not None, payload=payload)
        toast.closed.connect(self._on_closed)
        if on_click is not None:
            toast.clicked.connect(lambda t: on_click(t.payload))
        toast.adjustSize()
        self._toasts.append(toast)
        self._relayout(animate_new=toast)
        return toast

    def _on_closed(self, toast: Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        self._relayout()

    def _relayout(self, animate_new: Optional[Toast] = None) -> None:
        host_rect: QRect = self.host.rect()
        y = host_rect.bottom() - Toast.V_MARGIN
        for t in reversed(self._toasts):
            t.adjustSize()
            y -= t.height()
            x = host_rect.right() - t.width() - Toast.H_MARGIN
            if t is animate_new:
                t.show_at(x, y)
            else:
                # reposicionar suavemente los existentes
                t._anim_pos.stop()
                t._anim_pos.setStartValue(t.pos())
                t._anim_pos.setEndValue(QPoint(x, y))
                t._anim_pos.start()
            y -= self.GAP

    def reposition(self) -> None:
        """Llamar desde el resizeEvent del host."""
        self._relayout()
