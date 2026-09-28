"""Placeholders de carga: skeleton de tabla con efecto shimmer y spinner.

Se usan mientras el catálogo se descarga por primera vez (sin caché) para que
la tabla no aparezca vacía sin más.
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

import theme


class SkeletonRows(QWidget):
    """Pinta N filas grises con un brillo que se desplaza (shimmer)."""

    ROW_H = 34
    GAP = 8
    # anchos relativos de las "celdas" por fila: [icono, nombre, dificultad, autor, fecha]
    CELLS = ((0.03, 0.02), (0.08, 0.28), (0.40, 0.10), (0.54, 0.24), (0.84, 0.12))

    def __init__(self, rows: int = 9, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows = rows
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self.setMinimumHeight(rows * (self.ROW_H + self.GAP))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    # ---- ciclo de vida ----

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self.stop()
        super().hideEvent(event)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.02) % 1.0
        self.update()

    # ---- pintado ----

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width()
        base = QColor(theme.BG_LIGHT)
        glow = QColor(theme.BG_HOVER)
        glow_hi = QColor(theme.BORDER)
        # banda de brillo que recorre el ancho
        x0 = (self._phase * 1.6 - 0.3) * w
        grad = QLinearGradient(x0 - w * 0.25, 0, x0 + w * 0.25, 0)
        grad.setColorAt(0.0, base)
        grad.setColorAt(0.5, glow_hi if theme.CURRENT_THEME == "dark" else glow)
        grad.setColorAt(1.0, base)
        p.setPen(Qt.PenStyle.NoPen)
        for r in range(self.rows):
            y = r * (self.ROW_H + self.GAP)
            # fondo suave de la fila
            p.setBrush(QColor(theme.BG_MID))
            p.drawRoundedRect(QRectF(0, y, w, self.ROW_H), 8, 8)
            p.setBrush(grad)
            for (rx, rw) in self.CELLS:
                cw = max(14.0, rw * w)
                ch = 12.0 if rx > 0.05 else 14.0
                cy = y + (self.ROW_H - ch) / 2
                p.drawRoundedRect(QRectF(rx * w, cy, cw, ch), 6, 6)
        p.end()


class Spinner(QWidget):
    """Arco giratorio simple (acento del tema)."""

    def __init__(self, size: int = 22, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._angle = 0
        self._size = size
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self.stop()
        super().hideEvent(event)

    def _tick(self) -> None:
        self._angle = (self._angle + 6) % 360
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(theme.ACCENT), 2.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        m = 3
        rect = QRectF(m, m, self._size - 2 * m, self._size - 2 * m)
        # arco de 270º que gira; el "-" es porque Qt cuenta en sentido antihorario
        p.drawArc(rect, int(-self._angle * 16), int(270 * 16))
        p.end()


class LoadingPanel(QWidget):
    """Spinner + texto + skeleton de filas. `set_text()` cambia el mensaje."""

    def __init__(self, rows: int = 9, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(4, 4, 4, 4)
        hl.setSpacing(10)
        self.spinner = Spinner(20)
        self.label = QLabel("Cargando catálogo…")
        self.label.setStyleSheet(f"color: {theme.FG_SECONDARY}; font-size: 13px;")
        hl.addWidget(self.spinner)
        hl.addWidget(self.label, 1)
        lay.addWidget(head)
        self.skeleton = SkeletonRows(rows)
        lay.addWidget(self.skeleton, 1)

    def set_text(self, text: str) -> None:
        self.label.setText(text)

    @property
    def phase(self) -> float:
        """Sólo para tests: fase actual del shimmer (0..1)."""
        return self.skeleton._phase  # noqa: SLF001


__all__ = ("SkeletonRows", "Spinner", "LoadingPanel")
