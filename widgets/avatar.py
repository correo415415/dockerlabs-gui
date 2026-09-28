"""Avatar circular del usuario en la parte inferior del sidebar.

- Sin sesión: círculo con icono y punto rojo "offline".
- Con sesión: foto de perfil descargada de DockerLabs (/img/perfil/<id>) +
  punto verde "online". Si la imagen no se puede descargar usa la inicial.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QByteArray, QObject, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QLabel, QSizePolicy

from theme import ACCENT, BG_LIGHT, BG_SIDEBAR, DANGER, FG_MUTED, FG_PRIMARY, SUCCESS
from widgets.icons import pixmap as svg_pixmap
from workers import BaseWorker


class _AvatarFetcher(BaseWorker):
    finished_data = pyqtSignal(bytes, str)

    def __init__(self, client, url: str, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.client = client
        self.url = url

    def work(self) -> None:
        status, _, data = self.client._request(self.url)
        if status == 200 and data:
            self.finished_data.emit(bytes(data), self.url)
        else:
            self.failed.emit(f"HTTP {status}")


class AvatarCircle(QLabel):
    """QLabel que dibuja un avatar circular con indicador de estado."""

    SIZE = 44
    DOT = 12

    def __init__(self, parent=None, size: Optional[int] = None) -> None:
        super().__init__(parent)
        if size is not None:
            self.SIZE = int(size)
            self.DOT = max(10, int(size * 0.18))
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._pixmap: Optional[QPixmap] = None
        self._initial: str = "?"
        self._online: bool = False
        self._thread: Optional[_AvatarFetcher] = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Sin sesión")

    # ---- API pública ----

    def set_logged_out(self) -> None:
        self._pixmap = None
        self._initial = "?"
        self._online = False
        self.setToolTip("Sin sesión")
        self.update()

    def set_logged_in(self, username: str) -> None:
        self._initial = (username or "?")[:1].upper()
        self._online = True
        self.setToolTip(f"Sesión activa: {username}")
        self.update()

    def set_pixmap_from_bytes(self, data: bytes) -> None:
        image = QImage()
        if image.loadFromData(QByteArray(data)):
            self._pixmap = QPixmap.fromImage(image)
        else:
            self._pixmap = None
        self.update()

    def fetch_image(self, client, profile_path: str) -> None:
        """Descarga la imagen en background sin bloquear la UI."""
        if not profile_path:
            return
        if not profile_path.startswith("http"):
            profile_path = client.base_url.rstrip("/") + profile_path
        try:
            if self._thread and self._thread.isRunning():
                self._thread.cancel()
                self._thread.wait(100)
        except RuntimeError:
            pass  # el worker anterior ya se destruyó (auto deleteLater)
        self._thread = _AvatarFetcher(client, profile_path, self)
        self._thread.finished_data.connect(lambda data, _u: self.set_pixmap_from_bytes(data))
        self._thread.finished.connect(self._forget_thread)
        self._thread.start()

    def _forget_thread(self) -> None:
        self._thread = None

    # ---- Pintado ----

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRect(0, 0, self.SIZE, self.SIZE)
        rectf = QRectF(rect)

        path = QPainterPath()
        path.addEllipse(rectf.adjusted(2, 2, -2, -2))
        painter.setClipPath(path)

        if self._pixmap and not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                self.SIZE - 4,
                self.SIZE - 4,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.SIZE - scaled.width()) // 2
            y = (self.SIZE - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        elif self._online:
            # Fondo gris + inicial centrada
            painter.fillRect(rect, QColor(BG_LIGHT))
            painter.setPen(QPen(QColor(FG_PRIMARY)))
            font = QFont(self.font())
            font.setBold(True)
            font.setPointSize(max(14, int(self.SIZE * 0.35)))
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._initial)
        else:
            # Sin sesión: icono de usuario SVG
            painter.fillRect(rect, QColor(BG_LIGHT))
            user_icon = svg_pixmap("user", FG_MUTED, self.SIZE - 14)
            ix = (self.SIZE - user_icon.width()) // 2
            iy = (self.SIZE - user_icon.height()) // 2
            painter.drawPixmap(ix, iy, user_icon)

        painter.setClipping(False)

        # Borde
        border_color = QColor(ACCENT) if self._online else QColor("#3a3f4b")
        painter.setPen(QPen(border_color, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rectf.adjusted(1, 1, -1, -1))

        # Punto de estado (esquina inferior derecha)
        dot_color = QColor(SUCCESS) if self._online else QColor(DANGER)
        dot_rect = QRect(
            self.SIZE - self.DOT - 1,
            self.SIZE - self.DOT - 1,
            self.DOT,
            self.DOT,
        )
        painter.setPen(QPen(QColor(BG_SIDEBAR), 2))
        painter.setBrush(QBrush(dot_color))
        painter.drawEllipse(dot_rect)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.SIZE, self.SIZE)
