"""Panel lateral de detalle de máquina: imagen, badges, descripción, autor,
valoración, writeups y acciones (descargar / lanzar / completar / abrir web)."""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QImage, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from catalog import Machine, Writeup
from theme import (
    ACCENT,
    BG_LIGHT,
    BG_MID,
    BORDER_SOFT,
    DANGER,
    FG_MUTED,
    FG_PRIMARY,
    FG_SECONDARY,
    ON_ACCENT,
    SUCCESS,
    WARNING,
    difficulty_color,
)
from widgets.icons import icon as svg_icon
from workers import BaseWorker, WorkerPool

logger = logging.getLogger(__name__)

_IMG_W, _IMG_H = 300, 168


class _ImageFetcher(BaseWorker):
    done = pyqtSignal(str, bytes)
    failed = pyqtSignal(str, str)   # url, error

    def __init__(self, client, url: str, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.client, self.url = client, url

    def work(self) -> None:
        self.done.emit(self.url, self.client.fetch_bytes(self.url))

    def on_error(self, exc: BaseException) -> None:
        self.failed.emit(self.url, self.format_error(exc))


class _RatingFetcher(BaseWorker):
    done = pyqtSignal(str, dict)
    failed = pyqtSignal(str, str)   # name, error

    def __init__(self, client, name: str, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.client, self.name = client, name

    def work(self) -> None:
        self.done.emit(self.name, self.client.machine_rating(self.name))

    def on_error(self, exc: BaseException) -> None:
        self.failed.emit(self.name, self.format_error(exc))


def _rounded(pm: QPixmap, radius: int = 12) -> QPixmap:
    out = QPixmap(pm.size())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0.0, 0.0, float(pm.width()), float(pm.height()), float(radius), float(radius))
    p.setClipPath(path)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


def badge(text: str, color: str) -> QLabel:
    lb = QLabel(text)
    lb.setStyleSheet(
        f"QLabel {{ color: {color}; background: {color}26; border: 1px solid {color}66;"
        f" border-radius: 10px; padding: 2px 10px; font-weight: 700; font-size: 11px; }}"
    )
    return lb


def _btn(text: str, icon: str, color: str, klass: str = "ghost") -> QPushButton:
    b = QPushButton(f"  {text}")
    b.setProperty("class", klass)
    b.setIcon(svg_icon(icon, color, 16))
    b.setFixedHeight(34)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def stars(avg: float) -> str:
    full = max(0, min(5, int(round(avg))))
    return "★" * full + "☆" * (5 - full)


class MachineDetailPanel(QFrame):
    """Panel de detalle. Emite acciones; el estado (descargada/completada/…)
    se lo inyecta la página con `set_status`."""

    request_download = pyqtSignal(str, str)     # name, url
    request_cancel_download = pyqtSignal(str)
    request_launch = pyqtSignal(str)
    request_toggle_completed = pyqtSignal(str)
    request_close = pyqtSignal()

    def __init__(self, client=None, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.machine: Optional[Machine] = None
        self._writeups: List[Writeup] = []
        self._img_cache: Dict[str, QPixmap] = {}
        self._rating_cache: Dict[str, dict] = {}
        self._workers = WorkerPool()
        self._status = dict(done=False, downloading=False, downloaded=False, running=False)

        self.setObjectName("detailPanel")
        self.setStyleSheet(
            f"QFrame#detailPanel {{ background: {BG_MID}; border-left: 1px solid {BORDER_SOFT}; }}"
        )
        self.setMinimumWidth(340)
        self.setMaximumWidth(380)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(16, 12, 10, 8)
        self.lbl_title = QLabel("Detalle")
        self.lbl_title.setStyleSheet(f"font-size: 16px; font-weight: 800; color: {FG_PRIMARY};")
        head.addWidget(self.lbl_title, 1)
        b_close = QPushButton()
        b_close.setIcon(svg_icon("x", FG_MUTED, 16))
        b_close.setFixedSize(28, 28)
        b_close.setProperty("class", "ghost")
        b_close.setCursor(Qt.CursorShape.PointingHandCursor)
        b_close.setToolTip("Cerrar (Esc)")
        b_close.clicked.connect(self.request_close.emit)
        head.addWidget(b_close)
        outer.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        host = QWidget()
        host.setObjectName("detailHost")
        # Con selector: una regla sin selector se hereda por todos los hijos y pisaba
        # el fondo de los botones `primary` (se veían grises como si estuvieran deshabilitados).
        host.setStyleSheet("QWidget#detailHost { background: transparent; }")
        lay = QVBoxLayout(host)
        lay.setContentsMargins(16, 4, 16, 16)
        lay.setSpacing(12)

        self.img = QLabel()
        self.img.setFixedSize(_IMG_W, _IMG_H)
        self.img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img.setStyleSheet(f"background: {BG_LIGHT}; border-radius: 12px; color: {FG_MUTED};")
        lay.addWidget(self.img, 0, Qt.AlignmentFlag.AlignHCenter)

        self.badges = QHBoxLayout()
        self.badges.setSpacing(6)
        self.badges.addStretch(1)
        lay.addLayout(self.badges)

        self.lbl_desc = QLabel()
        self.lbl_desc.setWordWrap(True)
        self.lbl_desc.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_desc.setStyleSheet(f"color: {FG_SECONDARY}; font-size: 12px;")
        lay.addWidget(self.lbl_desc)

        meta = QFrame()
        meta.setStyleSheet(f"QFrame {{ background: {BG_LIGHT}; border-radius: 10px; }}")
        ml = QVBoxLayout(meta)
        ml.setContentsMargins(12, 10, 12, 10)
        ml.setSpacing(6)
        self.lbl_author = QLabel()
        self.lbl_author.setOpenExternalLinks(True)
        self.lbl_author.setStyleSheet(f"color: {FG_PRIMARY}; font-size: 12px; background: transparent;")
        self.lbl_date = QLabel()
        self.lbl_date.setStyleSheet(f"color: {FG_MUTED}; font-size: 12px; background: transparent;")
        self.lbl_rating = QLabel("Valoración: …")
        self.lbl_rating.setStyleSheet(f"color: {WARNING}; font-size: 12px; background: transparent;")
        self.lbl_rating_detail = QLabel()
        self.lbl_rating_detail.setWordWrap(True)
        self.lbl_rating_detail.setStyleSheet(f"color: {FG_MUTED}; font-size: 11px; background: transparent;")
        for w in (self.lbl_author, self.lbl_date, self.lbl_rating, self.lbl_rating_detail):
            ml.addWidget(w)
        lay.addWidget(meta)

        self.btn_download = _btn("Descargar", "download", ON_ACCENT, "primary")
        self.btn_cancel = _btn("Cancelar descarga", "x", DANGER, "danger")
        self.btn_launch = _btn("Lanzar laboratorio", "docker", ON_ACCENT, "primary")
        self.btn_lab = _btn("Ver en Laboratorio", "docker", SUCCESS)
        self.btn_done = _btn("Marcar como completada", "check", ACCENT)
        self.btn_web = _btn("Abrir en dockerlabs.es", "external-link", FG_PRIMARY)
        self.btn_download.clicked.connect(self._emit_download)
        self.btn_cancel.clicked.connect(self._emit_cancel)
        self.btn_launch.clicked.connect(self._emit_launch)
        self.btn_lab.clicked.connect(self._emit_launch)
        self.btn_done.clicked.connect(self._emit_toggle)
        self.btn_web.clicked.connect(self._open_web)
        for b in (self.btn_download, self.btn_cancel, self.btn_launch, self.btn_lab,
                  self.btn_done, self.btn_web):
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            lay.addWidget(b)

        self.lbl_wu_title = QLabel("Writeups")
        self.lbl_wu_title.setStyleSheet(f"font-weight: 700; color: {FG_PRIMARY}; margin-top: 6px;")
        lay.addWidget(self.lbl_wu_title)
        self.wu_host = QVBoxLayout()
        self.wu_host.setSpacing(4)
        lay.addLayout(self.wu_host)
        self.btn_more_wu = _btn("Ver todos en la web", "external-link", FG_MUTED)
        self.btn_more_wu.clicked.connect(self._open_web)
        lay.addWidget(self.btn_more_wu)

        lay.addStretch(1)
        scroll.setWidget(host)
        outer.addWidget(scroll, 1)
        self.setVisible(False)

    # ------------------------------------------------------------------ API
    def show_machine(self, m: Machine, writeups: List[Writeup]) -> None:
        self.machine = m
        self._writeups = writeups
        self.lbl_title.setText(m.name)
        self.lbl_desc.setText(m.description or "Sin descripción.")
        while self.badges.count() > 1:
            it = self.badges.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.badges.insertWidget(0, badge(m.difficulty, difficulty_color(m.difficulty)))
        if m.author_url:
            self.lbl_author.setText(
                f'Autor: <a style="color:{ACCENT}; text-decoration:none" href="{m.author_url}">{m.author}</a>')
        else:
            self.lbl_author.setText(f"Autor: {m.author or '—'}")
        self.lbl_date.setText(f"Publicada: {m.date or '—'}")

        self.img.setPixmap(QPixmap())
        self.img.setText("Cargando imagen…")
        pm = self._img_cache.get(m.image_url)
        if pm is not None:
            self._set_image(pm)
        elif self.client is not None and m.image_url:
            w = _ImageFetcher(self.client, m.image_url, parent=self)
            w.done.connect(self._on_image)
            w.failed.connect(self._on_image_failed)
            self._track(w)
        else:
            self.img.setText("Sin imagen")

        r = self._rating_cache.get(m.name)
        if r is not None:
            self._apply_rating(r)
        else:
            self.lbl_rating.setText("Valoración: …")
            self.lbl_rating_detail.setText("")
            if self.client is not None:
                w = _RatingFetcher(self.client, m.name, parent=self)
                w.done.connect(self._on_rating)
                w.failed.connect(self._on_rating_failed)
                self._track(w)

        while self.wu_host.count():
            it = self.wu_host.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not writeups:
            lb = QLabel("Todavía no hay writeups publicados.")
            lb.setStyleSheet(f"color: {FG_MUTED}; font-size: 11px;")
            self.wu_host.addWidget(lb)
        for wu in writeups[:8]:
            self.wu_host.addWidget(self._writeup_row(wu))
        extra = len(writeups) - 8
        self.lbl_wu_title.setText(f"Writeups ({len(writeups)})" if writeups else "Writeups")
        self.btn_more_wu.setText(f"  Ver {extra} más en la web" if extra > 0 else "  Ver todos en la web")
        self._refresh_buttons()
        self.setVisible(True)

    def set_status(self, done: bool, downloading: bool, downloaded: bool, running: bool) -> None:
        self._status = dict(done=done, downloading=downloading, downloaded=downloaded, running=running)
        self._refresh_buttons()

    def shutdown(self) -> None:
        self._workers.shutdown(1000)

    # ------------------------------------------------------------ internos
    def _writeup_row(self, wu: Writeup) -> QWidget:
        row = QPushButton()
        row.setProperty("class", "ghost")
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.setStyleSheet("QPushButton { text-align: left; padding: 6px 8px; }")
        is_video = wu.kind == "video"
        row.setIcon(svg_icon("video" if is_video else "doc", DANGER if is_video else ACCENT, 14))
        row.setText(f"  {wu.author or '—'}")
        row.setToolTip(wu.url)
        row.clicked.connect(lambda _c=False, u=wu.url: QDesktopServices.openUrl(QUrl(u)))
        return row

    def _refresh_buttons(self) -> None:
        st = self._status
        self.btn_download.setVisible(not st["downloading"] and not st["downloaded"])
        self.btn_download.setEnabled(bool(self.machine and self.machine.download_url))
        self.btn_cancel.setVisible(st["downloading"])
        self.btn_launch.setVisible(st["downloaded"] and not st["running"])
        self.btn_lab.setVisible(st["running"])
        self.btn_done.setText("  Desmarcar completada" if st["done"] else "  Marcar como completada")
        self.btn_done.setIcon(svg_icon("circle" if st["done"] else "check",
                                       FG_MUTED if st["done"] else ACCENT, 16))

    def _emit_download(self) -> None:
        if self.machine and self.machine.download_url:
            self.request_download.emit(self.machine.name, self.machine.download_url)

    def _emit_cancel(self) -> None:
        if self.machine:
            self.request_cancel_download.emit(self.machine.name)

    def _emit_launch(self) -> None:
        if self.machine:
            self.request_launch.emit(self.machine.name)

    def _emit_toggle(self) -> None:
        if self.machine:
            self.request_toggle_completed.emit(self.machine.name)

    def _open_web(self) -> None:
        if self.machine:
            QDesktopServices.openUrl(QUrl(self.machine.machine_page_url))

    def _track(self, w: BaseWorker) -> None:
        self._workers.track(w)

    def _on_image(self, url: str, data: bytes) -> None:
        img = QImage.fromData(data)
        if img.isNull():
            self._on_image_failed(url, "formato no soportado")
            return
        pm = QPixmap.fromImage(img).scaled(_IMG_W, _IMG_H, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation)
        pm = _rounded(pm)
        self._img_cache[url] = pm
        if self.machine and self.machine.image_url == url:
            self._set_image(pm)

    def _on_image_failed(self, url: str, err: str) -> None:
        logger.debug("imagen %s: %s", url, err)
        if self.machine and self.machine.image_url == url:
            self.img.setText("Sin imagen")

    def _set_image(self, pm: QPixmap) -> None:
        self.img.setText("")
        self.img.setPixmap(pm)

    def _on_rating(self, name: str, data: dict) -> None:
        self._rating_cache[name] = data
        if self.machine and self.machine.name == name:
            self._apply_rating(data)

    def _on_rating_failed(self, name: str, err: str) -> None:
        logger.debug("rating %s: %s", name, err)
        if self.machine and self.machine.name == name:
            self.lbl_rating.setText("Valoración no disponible")

    def _apply_rating(self, data: dict) -> None:
        try:
            avg = float(data.get("average") or 0)
            count = int(data.get("count") or 0)
        except (TypeError, ValueError):
            avg, count = 0.0, 0
        if not count:
            self.lbl_rating.setText("Valoración: sin votos")
            self.lbl_rating_detail.setText("")
            return
        self.lbl_rating.setText(f"Valoración: {stars(avg)} {avg:.1f}  ({count} votos)")
        det = data.get("details") or {}
        names = {"dificultad": "Dificultad", "aprendizaje": "Aprendizaje",
                 "recomendaria": "Recomendable", "diversion": "Diversión"}
        parts = []
        for k, v in det.items():
            try:
                parts.append(f"{names.get(k, k)} {float(v):.1f}")
            except (TypeError, ValueError):
                continue
        self.lbl_rating_detail.setText("  ·  ".join(parts))
