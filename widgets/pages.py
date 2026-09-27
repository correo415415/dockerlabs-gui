"""Páginas (vistas) del QStackedWidget central."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import QObject, QSize, Qt, QThread, QPoint, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QPalette
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QTableWidget,
    QTableWidgetItem,
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
    FG_SECONDARY,
    SUCCESS,
    WARNING,
    difficulty_color,
)
from download_manager import human_eta, human_size
from widgets.avatar import AvatarCircle
from widgets.icons import icon as svg_icon


# ============================================================
# Workers compartidos
# ============================================================

class FetchAPIWorker(QThread):
    finished_data = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, client, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.client = client

    def run(self) -> None:
        try:
            data = self.client.fetch_api_data()
            self.finished_data.emit(data)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class ExportWorker(QThread):
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, client, output_dir: str, prefix: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.output_dir = output_dir
        self.prefix = prefix

    def run(self) -> None:
        try:
            from dockerlabs_api import API_URL
            from dockerlabs_csv import export_machines
            data = self.client.fetch_api_data()
            machines = data.get("info_maquinas", [])
            summary = export_machines(
                machines,
                Path(self.output_dir),
                prefix=self.prefix,
                api_url=API_URL,
            )
            self.done.emit(summary)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


# ============================================================
# Helpers UI
# ============================================================

class _ComboItemDelegate(QStyledItemDelegate):
    """Delegate que neutraliza el Highlight cian que Qt6 pinta sobre el item
    'current' del combo cuando el popup está cerrado.
    """
    def paint(self, painter, option, index):  # type: ignore[override]
        opt = QStyleOptionViewItem(option)
        from PyQt6.QtWidgets import QStyle
        opt.state &= ~QStyle.StateFlag.State_Selected
        opt.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, opt, index)


class _GhostComboBox(QComboBox):
    """QComboBox que se pinta enteramente con QSS, ignorando el dibujo nativo
    (que en algunos estilos añade una franja highlight cian).
    """
    def paintEvent(self, event):  # type: ignore[override]
        # No llamamos al estilo nativo. Pintamos: fondo, texto y flecha.
        from PyQt6.QtGui import QPainter, QPen
        from theme import BG_MID, BG_LIGHT, FG_PRIMARY, FG_MUTED, ACCENT
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()
        hovered = self.underMouse() or self.view().isVisible()
        bg = QColor(BG_LIGHT) if hovered else QColor(BG_MID)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(rect, 8, 8)
        # Texto
        p.setPen(QPen(QColor(FG_PRIMARY)))
        text_rect = rect.adjusted(14, 0, -32, 0)
        p.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self.currentText())
        # Flecha triángulo
        from PyQt6.QtGui import QPolygon
        from PyQt6.QtCore import QPoint
        cx = rect.right() - 16
        cy = rect.center().y() + 1
        tri = QPolygon([QPoint(cx - 5, cy - 3), QPoint(cx + 5, cy - 3), QPoint(cx, cy + 3)])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(ACCENT) if hovered else QColor(FG_MUTED))
        p.drawPolygon(tri)
        p.end()


def make_card(title: str, value: str, sub: str = "") -> QFrame:
    card = QFrame()
    card.setProperty("class", "card")
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(4)
    t = QLabel(title)
    t.setProperty("class", "cardTitle")
    t.setObjectName("cardTitle")
    v = QLabel(value)
    v.setProperty("class", "cardValue")
    v.setObjectName("cardValue")
    v.setStyleSheet(f"color: {ACCENT}; font-size: 22px; font-weight: 800;")
    layout.addWidget(t)
    layout.addWidget(v)
    s_label = None
    if sub:
        s_label = QLabel(sub)
        s_label.setProperty("class", "muted")
        s_label.setStyleSheet(f"color: {FG_MUTED}; font-size: 11px;")
        layout.addWidget(s_label)
    card.value_label = v  # type: ignore[attr-defined]
    card.sub_label = s_label  # type: ignore[attr-defined]
    return card


def page_header(title: str, subtitle: str) -> QFrame:
    bar = QFrame()
    bar.setObjectName("topbar")
    bar.setStyleSheet(
        f"QFrame#topbar {{ background: {BG_MID}; border-bottom: 1px solid #2a2f3a; }}"
    )
    bar.setFixedHeight(72)
    layout = QVBoxLayout(bar)
    layout.setContentsMargins(24, 12, 24, 12)
    layout.setSpacing(2)
    t = QLabel(title)
    t.setObjectName("pageTitle")
    s = QLabel(subtitle)
    s.setObjectName("pageSubtitle")
    layout.addWidget(t)
    layout.addWidget(s)
    return bar


# ============================================================
# Dashboard
# ============================================================

class DashboardPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(page_header("Dashboard", "Estado general de DockerLabs"))

        content = QVBoxLayout()
        content.setContentsMargins(24, 18, 24, 24)
        content.setSpacing(18)

        grid = QGridLayout()
        grid.setSpacing(14)
        self.card_total = make_card("Máquinas totales", "—", "Catálogo público")
        self.card_done = make_card("Completadas", "—", "Por el usuario actual")
        self.card_downloads = make_card("Descargas", "—", "Máquinas en local")
        self.card_labs = make_card("Laboratorio", "—", "Comprobando Docker…")
        self.card_session = make_card("Sesión", "Sin sesión", "Login para sincronizar")
        grid.addWidget(self.card_total, 0, 0)
        grid.addWidget(self.card_done, 0, 1)
        grid.addWidget(self.card_downloads, 0, 2)
        grid.addWidget(self.card_labs, 0, 3)
        grid.addWidget(self.card_session, 0, 4)
        content.addLayout(grid)

        hello = QFrame()
        hello.setObjectName("helloCard")
        hello.setStyleSheet(
            f"QFrame#helloCard {{ background: {BG_MID}; border: 1px solid #2a2f3a;"
            f" border-radius: 12px; }}"
        )
        hl = QVBoxLayout(hello)
        hl.setContentsMargins(20, 18, 20, 18)
        hl.setSpacing(6)
        title = QLabel("Bienvenido a DockerLabs GUI")
        title.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {FG_PRIMARY};")
        body = QLabel(
            "Cliente de escritorio para DockerLabs. Usa el menú lateral para navegar:\n"
            "• Máquinas: catálogo con búsqueda + clic derecho para marcar como hecha o descargar.\n"
            "• Descargas: progreso en tiempo real de las máquinas que estás bajando.\n"
            "• Laboratorio: lanza las máquinas descargadas en Docker y gestiona los contenedores.\n"
            "• Completadas: máquinas marcadas como hechas en tu cuenta.\n"
            "• Sesión: inicia sesión para sincronizar tu progreso y obtener tu avatar.\n\n"
            "El catálogo se actualiza automáticamente al iniciar (si hay internet)."
        )
        body.setStyleSheet(f"color: {FG_SECONDARY}; font-size: 13px;")
        body.setWordWrap(True)
        hl.addWidget(title)
        hl.addWidget(body)
        content.addWidget(hello)

        content.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))
        wrap = QFrame()
        wrap.setLayout(content)
        root.addWidget(wrap, 1)

    def set_total(self, n: int) -> None:
        self.card_total.value_label.setText(str(n))

    def set_done(self, n: int) -> None:
        self.card_done.value_label.setText(str(n))

    def set_downloads(self, n: int) -> None:
        self.card_downloads.value_label.setText(str(n))

    def set_labs_running(self, n: int) -> None:
        self._labs_running = n
        self.card_labs.value_label.setText(str(n))

    def set_docker(self, info) -> None:
        sub = self.card_labs.sub_label
        if sub is None:
            return
        if not info.available:
            sub.setText("Docker no instalado")
        elif getattr(info, "needs_elevation", False):
            sub.setText("Docker: falta permiso (ver Laboratorio)")
        elif not info.running:
            sub.setText("Docker no responde")
        else:
            sub.setText(f"Docker {info.version} listo · en ejecución")

    def set_session(self, username: Optional[str]) -> None:
        if username:
            self.card_session.value_label.setText(username)
            if self.card_session.sub_label:
                self.card_session.sub_label.setText("Sesión activa")
        else:
            self.card_session.value_label.setText("Sin sesión")
            if self.card_session.sub_label:
                self.card_session.sub_label.setText("Login para sincronizar")


# ============================================================
# Máquinas
# ============================================================

DIFICULTADES = ["Todas", "Muy Fácil", "Fácil", "Medio", "Difícil"]


class MachinesPage(QWidget):
    request_toggle_completed = pyqtSignal(str)
    request_download = pyqtSignal(str, str)   # machine, url
    request_cancel_download = pyqtSignal(str)
    request_launch = pyqtSignal(str)          # machine

    def __init__(self, csv_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.csv_path = csv_path
        self._all_rows: List[Dict[str, str]] = []
        self._completed_names: set[str] = set()
        self._downloading: set[str] = set()
        self._downloaded: set[str] = set()
        self._running: set[str] = set()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(page_header("Máquinas", "Catálogo público de DockerLabs"))

        body = QVBoxLayout()
        body.setContentsMargins(24, 16, 24, 24)
        body.setSpacing(12)

        # Barra de búsqueda con icono SVG
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(10)

        search_wrap = QFrame()
        search_wrap.setObjectName("searchWrap")
        search_wrap.setStyleSheet(
            f"QFrame#searchWrap {{ background: {BG_LIGHT}; border: 1px solid #2a2f3a;"
            f" border-radius: 8px; }}"
        )
        sl = QHBoxLayout(search_wrap)
        sl.setContentsMargins(10, 0, 10, 0)
        sl.setSpacing(8)
        search_icon = QLabel()
        search_icon.setPixmap(svg_icon("search", FG_MUTED, 18).pixmap(18, 18))
        sl.addWidget(search_icon)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar por nombre, autor o categoría…")
        self.search.setFrame(False)
        self.search.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: none;"
            f" color: {FG_PRIMARY}; padding: 8px 0; }}"
        )
        self.search.textChanged.connect(self._apply_filters)
        sl.addWidget(self.search, 1)
        search_wrap.setMinimumHeight(38)
        filter_bar.addWidget(search_wrap, 2)

        self.combo_diff = self._make_combo("Dificultad", DIFICULTADES)
        self.combo_diff.currentIndexChanged.connect(self._apply_filters)
        filter_bar.addWidget(self.combo_diff, 0)

        self.combo_state = self._make_combo(
            "Estado", ["Todas", "Completadas", "Pendientes", "Descargadas"]
        )
        self.combo_state.currentIndexChanged.connect(self._apply_filters)
        filter_bar.addWidget(self.combo_state, 0)

        body.addLayout(filter_bar)

        # Tabla (sin la columna 'Categoría' porque coincide con 'Dificultad')
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["", "", "Nombre", "Dificultad", "Autor", "Fecha"]
        )
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for col, w in ((0, 36), (1, 36)):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(col, w)
        self.table.setSortingEnabled(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        body.addWidget(self.table, 1)

        # Footer
        self.lbl_count = QLabel("Cargando…")
        self.lbl_count.setStyleSheet(f"color: {FG_MUTED};")
        body.addWidget(self.lbl_count)

        wrap = QFrame(); wrap.setLayout(body)
        root.addWidget(wrap, 1)

    def _make_combo(self, prefix: str, options: list[str]) -> QComboBox:
        """ComboBox 'ghost' que muestra 'prefix: opcion' y se disimula con el tema."""
        from theme import BG_MID, BG_LIGHT, BG_HOVER, FG_PRIMARY
        combo = _GhostComboBox()
        for opt in options:
            combo.addItem(f"{prefix}: {opt}", opt)
        combo.setMinimumHeight(38)
        combo.setMinimumWidth(150)
        combo.setCursor(Qt.CursorShape.PointingHandCursor)
        # 1) Sobrescribir paleta para neutralizar el Highlight cian nativo
        pal = combo.palette()
        mid = QColor(BG_MID)
        hover = QColor(BG_HOVER)
        fg = QColor(FG_PRIMARY)
        for role in (QPalette.ColorRole.Highlight,
                     QPalette.ColorRole.Base,
                     QPalette.ColorRole.Button,
                     QPalette.ColorRole.Window):
            pal.setColor(role, mid)
        pal.setColor(QPalette.ColorRole.HighlightedText, fg)
        pal.setColor(QPalette.ColorRole.ButtonText, fg)
        pal.setColor(QPalette.ColorRole.WindowText, fg)
        pal.setColor(QPalette.ColorRole.Text, fg)
        combo.setPalette(pal)
        view = combo.view()
        view_pal = view.palette()
        view_pal.setColor(QPalette.ColorRole.Highlight, hover)
        view_pal.setColor(QPalette.ColorRole.HighlightedText, fg)
        view_pal.setColor(QPalette.ColorRole.Base, mid)
        view.setPalette(view_pal)
        # 2) Delegado propio que ignora el flag 'State_Selected' del current
        #    cuando el popup está cerrado, evitando la franja cian.
        delegate = _ComboItemDelegate(combo)
        combo.setItemDelegate(delegate)
        combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return combo

    # ---- API pública ----

    def load_csv(self, path: Path) -> None:
        if not path.exists():
            self.lbl_count.setText("CSV no disponible aún…")
            return
        try:
            rows: List[Dict[str, str]] = []
            with path.open(encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for r in reader:
                    rows.append(r)
            self._all_rows = rows
            self.csv_path = path
            self._render()
        except Exception as exc:  # noqa: BLE001
            self.lbl_count.setText(f"Error cargando CSV: {exc}")

    def set_completed(self, names) -> None:
        self._completed_names = set(names or [])
        self._render()

    def set_downloading(self, names) -> None:
        self._downloading = set(names or [])
        self._render()

    def set_downloaded(self, names) -> None:
        self._downloaded = set(names or [])
        self._render()

    def set_running(self, names) -> None:
        self._running = set(names or [])
        self._render()

    # ---- Render / filtros ----

    def _render(self) -> None:
        rows = self._filtered_rows()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            name = r.get("nombre", "")
            done = name in self._completed_names
            downloading = name in self._downloading
            downloaded = name in self._downloaded

            it_done = QTableWidgetItem("")
            if done:
                it_done.setIcon(svg_icon("check", ACCENT, 16))
                it_done.setToolTip("Completada")
            it_done.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 0, it_done)

            it_dl = QTableWidgetItem("")
            if name in self._running:
                it_dl.setIcon(svg_icon("docker", SUCCESS, 16))
                it_dl.setToolTip("Laboratorio en ejecución")
            elif downloading:
                it_dl.setIcon(svg_icon("download", WARNING, 16))
                it_dl.setToolTip("Descargando…")
            elif downloaded:
                it_dl.setIcon(svg_icon("folder", SUCCESS, 16))
                it_dl.setToolTip("Descargada en local")
            it_dl.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 1, it_dl)

            it_name = QTableWidgetItem(name)
            f = QFont(it_name.font()); f.setBold(True)
            it_name.setFont(f)
            self.table.setItem(i, 2, it_name)

            diff = r.get("dificultad", "")
            it_diff = QTableWidgetItem(diff)
            it_diff.setForeground(QColor(difficulty_color(diff)))
            self.table.setItem(i, 3, it_diff)

            self.table.setItem(i, 4, QTableWidgetItem(r.get("autor", "")))
            self.table.setItem(i, 5, QTableWidgetItem(r.get("fecha", "")))

        self.table.setSortingEnabled(True)
        total = len(self._all_rows)
        shown = len(rows)
        done = sum(1 for r in self._all_rows if r.get("nombre", "") in self._completed_names)
        dl = sum(1 for r in self._all_rows if r.get("nombre", "") in self._downloaded)
        self.lbl_count.setText(
            f"{shown} de {total} máquinas · {done} completadas · {dl} descargadas"
        )

    def _filtered_rows(self) -> List[Dict[str, str]]:
        q = self.search.text().strip().lower()
        diff = self.combo_diff.currentData() or "Todas"
        state = self.combo_state.currentData() or "Todas"
        out: List[Dict[str, str]] = []
        for r in self._all_rows:
            blob = " ".join([
                r.get("nombre", ""),
                r.get("autor", ""),
                r.get("clase", ""),
                r.get("autores_parseados", ""),
            ]).lower()
            if q and q not in blob:
                continue
            if diff != "Todas" and (r.get("dificultad", "").strip().lower() != diff.lower()):
                continue
            done = r.get("nombre", "") in self._completed_names
            downloaded = r.get("nombre", "") in self._downloaded
            if state == "Completadas" and not done:
                continue
            if state == "Pendientes" and done:
                continue
            if state == "Descargadas" and not downloaded:
                continue
            out.append(r)
        return out

    def _apply_filters(self) -> None:
        self._render()

    # ---- Acciones / context menu ----

    def _row_at(self, pos: QPoint) -> Optional[Dict[str, str]]:
        index = self.table.indexAt(pos)
        if not index.isValid():
            return None
        row = index.row()
        name_item = self.table.item(row, 2)
        if name_item is None:
            return None
        name = name_item.text()
        for r in self._all_rows:
            if r.get("nombre", "") == name:
                return r
        return None

    def _show_context_menu(self, pos: QPoint) -> None:
        row = self._row_at(pos)
        if not row:
            return
        name = row.get("nombre", "")
        url = row.get("link_descarga", "")
        done = name in self._completed_names
        downloading = name in self._downloading
        downloaded = name in self._downloaded

        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {BG_MID}; color: {FG_PRIMARY};"
            f" border: 1px solid #2a2f3a; padding: 6px; border-radius: 8px; }}"
            f"QMenu::item {{ padding: 8px 28px 8px 14px; border-radius: 6px; }}"
            f"QMenu::item:selected {{ background: {BG_LIGHT}; color: {ACCENT}; }}"
            f"QMenu::separator {{ height: 1px; background: #2a2f3a; margin: 4px 8px; }}"
        )

        # toggle completada
        toggle_text = "Desmarcar como completada" if done else "Marcar como completada"
        act_toggle = QAction(svg_icon("check" if not done else "circle",
                                      ACCENT if not done else FG_MUTED, 16),
                             toggle_text, self)
        act_toggle.triggered.connect(lambda: self.request_toggle_completed.emit(name))
        menu.addAction(act_toggle)

        menu.addSeparator()

        # descargar / cancelar
        if downloading:
            act_dl = QAction(svg_icon("trash", DANGER, 16), "Cancelar descarga", self)
            act_dl.triggered.connect(lambda: self.request_cancel_download.emit(name))
            menu.addAction(act_dl)
        elif downloaded:
            running = name in self._running
            act_launch = QAction(svg_icon("docker", SUCCESS if not running else FG_MUTED, 16),
                                 "Ver en Laboratorio" if running else "Lanzar laboratorio (Docker)",
                                 self)
            act_launch.triggered.connect(lambda: self.request_launch.emit(name))
            menu.addAction(act_launch)
            act_dl = QAction(svg_icon("folder", FG_MUTED, 16), "Ya descargada", self)
            act_dl.setEnabled(False)
            menu.addAction(act_dl)
        else:
            act_dl = QAction(svg_icon("download", ACCENT, 16), "Descargar", self)
            if not url:
                act_dl.setEnabled(False)
                act_dl.setText("Sin enlace de descarga")
            act_dl.triggered.connect(lambda: self.request_download.emit(name, url))
            menu.addAction(act_dl)

        menu.exec(self.table.viewport().mapToGlobal(pos))


# ============================================================
# Descargas — lista con progreso por máquina
# ============================================================

class DownloadItemWidget(QFrame):
    cancel_clicked = pyqtSignal(str)
    open_clicked = pyqtSignal(str)
    remove_clicked = pyqtSignal(str)

    def __init__(self, machine: str, parent=None) -> None:
        super().__init__(parent)
        self.machine = machine
        self.setProperty("class", "card")
        self.setObjectName("downloadItem")
        self.setStyleSheet(
            f"QFrame#downloadItem {{ background: {BG_MID}; border: 1px solid #2a2f3a;"
            f" border-radius: 12px; }}"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.lbl_title = QLabel(machine)
        self.lbl_title.setStyleSheet(
            f"font-weight: 700; font-size: 14px; color: {FG_PRIMARY};"
        )
        top.addWidget(self.lbl_title, 1)
        self.lbl_state = QLabel("En cola")
        self.lbl_state.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.lbl_state.setStyleSheet(
            f"color: {FG_MUTED}; font-size: 11px; font-weight: 600;"
        )
        top.addWidget(self.lbl_state, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.setStyleSheet(
            f"QProgressBar {{ background: {BG_LIGHT}; border-radius: 4px; border: none; }}"
            f"QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}"
        )
        layout.addWidget(self.bar)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        self.lbl_info = QLabel("—")
        self.lbl_info.setStyleSheet(f"color: {FG_MUTED}; font-size: 11px;")
        bottom.addWidget(self.lbl_info, 1)

        self.btn_action = QPushButton("Cancelar")
        self.btn_action.setProperty("class", "ghost")
        self.btn_action.setFixedHeight(30)
        self.btn_action.setMinimumWidth(110)
        self.btn_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_action.clicked.connect(self._on_action)
        bottom.addWidget(self.btn_action, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(bottom)
        self._state_text = "queued"

    def _on_action(self) -> None:
        if self._state_text in ("running", "verifying", "queued"):
            self.cancel_clicked.emit(self.machine)
        elif self._state_text == "done":
            self.open_clicked.emit(self.machine)
        else:
            self.remove_clicked.emit(self.machine)

    def update_state(self, state) -> None:
        self._state_text = state.state
        self.bar.setValue(int(state.percent))
        self.lbl_state.setText({
            "queued": "En cola",
            "running": "Descargando",
            "verifying": "Verificando…",
            "done": "Completada",
            "error": "Error",
            "cancelled": "Cancelada",
        }.get(state.state, state.state))
        color = {
            "queued": FG_MUTED, "running": WARNING, "verifying": ACCENT,
            "done": SUCCESS, "error": DANGER, "cancelled": FG_MUTED,
        }.get(state.state, FG_MUTED)
        self.lbl_state.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: 600;")

        size_done = human_size(state.bytes_done)
        size_total = human_size(state.size_total) if state.size_total else "—"
        speed = f"{human_size(state.speed_bps)}/s" if state.speed_bps else ""
        eta = f"ETA {human_eta(state.eta_seconds)}" if state.eta_seconds else ""
        fname = f"  ·  {state.filename}" if state.filename else ""
        parts = [f"{size_done} / {size_total}"]
        if speed:
            parts.append(speed)
        if eta:
            parts.append(eta)
        info_line = "   ".join(parts) + fname
        if state.state == "error" and state.error:
            info_line = state.error
        self.lbl_info.setText(info_line)

        if state.state in ("running", "verifying", "queued"):
            self.btn_action.setText("Cancelar")
        elif state.state == "done":
            self.btn_action.setText("Abrir carpeta")
        else:
            self.btn_action.setText("Quitar")


class DownloadsPage(QWidget):
    request_cancel = pyqtSignal(str)
    request_remove = pyqtSignal(str)
    request_open = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._widgets: Dict[str, DownloadItemWidget] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(page_header("Descargas",
                                   "Máquinas que se están descargando desde DockerLabs"))

        body = QVBoxLayout()
        body.setContentsMargins(24, 16, 24, 24)
        body.setSpacing(10)

        self.lbl_summary = QLabel("Sin descargas")
        self.lbl_summary.setStyleSheet(f"color: {FG_MUTED};")
        body.addWidget(self.lbl_summary)

        # contenedor de items
        self.list_host = QFrame()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(10)
        body.addWidget(self.list_host, 1)

        self.empty = QLabel(
            "Aún no hay descargas. Ve a Máquinas, haz clic derecho sobre una y elige\n"
            "“Descargar”. Aparecerá aquí con su barra de progreso."
        )
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet(f"color: {FG_MUTED}; font-size: 13px; padding: 24px 0;")
        body.addWidget(self.empty)
        body.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

        wrap = QFrame(); wrap.setLayout(body)
        root.addWidget(wrap, 1)

    def render_states(self, states) -> None:
        # quitar widgets cuyo estado ya no existe
        existing = {s.machine for s in states}
        for machine in list(self._widgets.keys()):
            if machine not in existing:
                w = self._widgets.pop(machine)
                w.setParent(None)
                w.deleteLater()

        # añadir / actualizar
        for state in states:
            w = self._widgets.get(state.machine)
            if w is None:
                w = DownloadItemWidget(state.machine)
                w.cancel_clicked.connect(self.request_cancel.emit)
                w.open_clicked.connect(self.request_open.emit)
                w.remove_clicked.connect(self.request_remove.emit)
                self._widgets[state.machine] = w
                self.list_layout.addWidget(w)
            w.update_state(state)

        n = len(states)
        active = sum(1 for s in states if s.state in ("running", "verifying", "queued"))
        done = sum(1 for s in states if s.state == "done")
        self.empty.setVisible(n == 0)
        self.list_host.setVisible(n > 0)
        if n == 0:
            self.lbl_summary.setText("Sin descargas")
        else:
            self.lbl_summary.setText(
                f"{n} descargas · {active} en curso · {done} completadas"
            )


# ============================================================
# Completadas
# ============================================================

class CompletedPage(QWidget):
    request_refresh = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(page_header("Completadas",
                                   "Máquinas marcadas como hechas en tu cuenta"))

        body = QVBoxLayout()
        body.setContentsMargins(24, 16, 24, 24)
        body.setSpacing(12)

        top = QHBoxLayout()
        self.lbl = QLabel("Inicia sesión para sincronizar.")
        self.lbl.setStyleSheet(f"color: {FG_SECONDARY};")
        top.addWidget(self.lbl, 1)
        self.btn_refresh = QPushButton("Sincronizar")
        self.btn_refresh.setProperty("class", "primary")
        self.btn_refresh.setIcon(svg_icon("refresh", "#0b1316", 16))
        self.btn_refresh.setMinimumHeight(34)
        self.btn_refresh.clicked.connect(self.request_refresh.emit)
        top.addWidget(self.btn_refresh)
        body.addLayout(top)

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(
            f"QListWidget {{ background: {BG_MID}; border: 1px solid #2a2f3a;"
            f" border-radius: 12px; padding: 8px; color: {FG_PRIMARY}; }}"
            f"QListWidget::item {{ padding: 8px 12px; border: none; }}"
        )
        body.addWidget(self.list_widget, 1)

        wrap = QFrame(); wrap.setLayout(body)
        root.addWidget(wrap, 1)

    def set_items(self, names) -> None:
        self.list_widget.clear()
        names = list(names)
        if not names:
            it = QListWidgetItem("Sin máquinas completadas")
            it.setForeground(QColor(FG_MUTED))
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list_widget.addItem(it)
            self.lbl.setText("0 máquinas completadas")
            return
        for n in sorted(names, key=str.lower):
            it = QListWidgetItem(f"  {n}")
            it.setIcon(svg_icon("check", ACCENT, 16))
            it.setForeground(QColor(FG_PRIMARY))
            self.list_widget.addItem(it)
        self.lbl.setText(f"{len(names)} máquinas completadas")


# ============================================================
# Sesión
# ============================================================

class SessionPage(QWidget):
    request_login = pyqtSignal(str, str)
    request_logout = pyqtSignal()
    request_check = pyqtSignal()
    request_avatar_fetch = pyqtSignal()  # pedir al main que refresque el avatar grande

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._completed_count = 0
        self._username: Optional[str] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.header = page_header("Sesión", "Inicia sesión en DockerLabs")
        root.addWidget(self.header)

        body = QVBoxLayout()
        body.setContentsMargins(24, 18, 24, 24)
        body.setSpacing(14)

        # --- Modo 1: formulario de login ---
        self.login_card = QFrame()
        self.login_card.setObjectName("loginCard")
        self.login_card.setStyleSheet(
            f"QFrame#loginCard {{ background: {BG_MID}; border: 1px solid #2a2f3a;"
            f" border-radius: 12px; }}"
        )
        cl = QGridLayout(self.login_card)
        cl.setContentsMargins(20, 20, 20, 20)
        cl.setHorizontalSpacing(12); cl.setVerticalSpacing(10)
        cl.addWidget(QLabel("Usuario"), 0, 0)
        self.in_user = QLineEdit(); self.in_user.setMinimumHeight(36)
        cl.addWidget(self.in_user, 0, 1)
        cl.addWidget(QLabel("Contraseña"), 1, 0)
        self.in_pass = QLineEdit(); self.in_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.in_pass.setMinimumHeight(36)
        cl.addWidget(self.in_pass, 1, 1)

        self.btn_login = QPushButton("Iniciar sesión")
        self.btn_login.setProperty("class", "primary")
        self.btn_login.setIcon(svg_icon("session", "#0b1316", 16))
        self.btn_login.setMinimumHeight(40)
        self.btn_login.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_login.clicked.connect(self._emit_login)
        # Enter en cualquiera de los campos dispara el login
        self.in_user.returnPressed.connect(self._emit_login)
        self.in_pass.returnPressed.connect(self._emit_login)
        cl.addWidget(self.btn_login, 2, 0, 1, 2)

        info = QLabel(
            "Tu sesión se guardará cifrada (cookie de Flask) en un .env de la "
            "carpeta de la app para que no tengas que volver a iniciar sesión cada vez."
        )
        info.setStyleSheet(f"color: {FG_MUTED}; font-size: 12px;")
        info.setWordWrap(True)
        cl.addWidget(info, 3, 0, 1, 2)

        body.addWidget(self.login_card)

        # --- Modo 2: vista de perfil tras login ---
        self.profile_card = QFrame()
        self.profile_card.setObjectName("profileCard")
        self.profile_card.setStyleSheet(
            f"QFrame#profileCard {{ background: {BG_MID}; border: 1px solid #2a2f3a;"
            f" border-radius: 12px; }}"
        )
        pl = QVBoxLayout(self.profile_card)
        pl.setContentsMargins(28, 28, 28, 28)
        pl.setSpacing(14)
        pl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        # Avatar grande (140 px)
        self.big_avatar = AvatarCircle(size=140)
        self.big_avatar.setCursor(Qt.CursorShape.ArrowCursor)
        avatar_row = QHBoxLayout()
        avatar_row.addStretch(1)
        avatar_row.addWidget(self.big_avatar)
        avatar_row.addStretch(1)
        pl.addLayout(avatar_row)

        # Nombre de usuario
        self.lbl_username = QLabel("")
        self.lbl_username.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_username.setStyleSheet(
            f"color: {FG_PRIMARY}; font-weight: 700; font-size: 22px;"
        )
        pl.addWidget(self.lbl_username)

        # Sub-línea: sesión activa
        sub = QLabel("Sesión activa  ·  conectado a DockerLabs")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet(f"color: {SUCCESS}; font-size: 12px; font-weight: 600;")
        pl.addWidget(sub)

        # Estadísticas
        self.lbl_stats = QLabel("—")
        self.lbl_stats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_stats.setStyleSheet(f"color: {FG_SECONDARY}; font-size: 13px;")
        pl.addWidget(self.lbl_stats)

        # Botón cerrar sesión
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_logout = QPushButton("  Cerrar sesión")
        self.btn_logout.setIcon(svg_icon("logout", "#0b1316", 16))
        self.btn_logout.setProperty("class", "primary")
        self.btn_logout.setMinimumHeight(40)
        self.btn_logout.setMinimumWidth(180)
        self.btn_logout.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_logout.clicked.connect(self.request_logout.emit)
        btn_row.addWidget(self.btn_logout)
        btn_row.addStretch(1)
        pl.addSpacing(6)
        pl.addLayout(btn_row)

        body.addWidget(self.profile_card)
        body.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

        wrap = QFrame(); wrap.setLayout(body)
        root.addWidget(wrap, 1)

        # Estado inicial: sin sesión
        self.set_logged_out()

    def _emit_login(self) -> None:
        user = self.in_user.text().strip()
        pwd = self.in_pass.text()
        self.request_login.emit(user, pwd)

    # API pública
    def set_logged_in(self, username: str, completed_count: int = 0) -> None:
        self._username = username
        self.lbl_username.setText(username)
        self.big_avatar.set_logged_in(username)
        self.update_stats(completed_count)
        self.login_card.setVisible(False)
        self.profile_card.setVisible(True)
        # limpiamos las credenciales por seguridad
        self.in_pass.setText("")
        # Actualizar título
        title = self.header.findChild(QLabel, "pageTitle")
        sub = self.header.findChild(QLabel, "pageSubtitle")
        if title:
            title.setText("Mi cuenta")
        if sub:
            sub.setText("Estado de tu sesión en DockerLabs")

    def set_logged_out(self) -> None:
        self._username = None
        self.lbl_username.setText("")
        self.big_avatar.set_logged_out()
        self.login_card.setVisible(True)
        self.profile_card.setVisible(False)
        title = self.header.findChild(QLabel, "pageTitle")
        sub = self.header.findChild(QLabel, "pageSubtitle")
        if title:
            title.setText("Sesión")
        if sub:
            sub.setText("Inicia sesión en DockerLabs")

    def update_stats(self, completed_count: int) -> None:
        self._completed_count = int(completed_count)
        plural = "s" if self._completed_count != 1 else ""
        self.lbl_stats.setText(
            f"{self._completed_count} máquina{plural} completada{plural} sincronizada{plural}"
        )

    def set_avatar_pixmap(self, data: bytes) -> None:
        """Inyecta los bytes del PNG/JPG del avatar al avatar grande."""
        self.big_avatar.set_pixmap_from_bytes(data)


# ============================================================
# Ajustes
# ============================================================

class SettingsPage(QWidget):
    request_change_downloads_dir = pyqtSignal(str)
    request_set_os_notifications = pyqtSignal(bool)
    request_set_in_app_notifications = pyqtSignal(bool)
    request_open_downloads_dir = pyqtSignal()
    request_set_docker_network = pyqtSignal(str)

    DOCKER_NET_OPTIONS = [
        ("auto", "Automático (recomendado)"),
        ("bridge", "Bridge: IP interna del contenedor (Linux nativo)"),
        ("bridge+ports", "Publicar puertos EXPOSE en 127.0.0.1 (Docker Desktop)"),
        ("host", "Red del host (solo Linux)"),
    ]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current_dir = ""
        self._os_backend_available = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(page_header("Ajustes", "Configuración del cliente"))

        body = QVBoxLayout()
        body.setContentsMargins(24, 18, 24, 24)
        body.setSpacing(14)

        # ---------- Carpeta de descargas ----------
        card_dir = self._make_card("Carpeta de descargas")
        cdl = card_dir.layout()
        sub_dir = QLabel(
            "Aquí se guardan los .zip de las máquinas descargadas. El catálogo"
            " se gestiona internamente y no es configurable."
        )
        sub_dir.setStyleSheet(f"color: {FG_MUTED}; font-size: 12px;")
        sub_dir.setWordWrap(True)
        cdl.addWidget(sub_dir)

        row_dir = QHBoxLayout()
        row_dir.setSpacing(10)
        self.in_dir = QLineEdit()
        self.in_dir.setReadOnly(True)
        self.in_dir.setMinimumHeight(36)
        self.in_dir.setObjectName("dirInput")
        self.in_dir.setStyleSheet(
            f"QLineEdit#dirInput {{ background: {BG_LIGHT}; border: 1px solid #2a2f3a;"
            f" border-radius: 8px; padding: 8px 10px; color: {FG_PRIMARY}; }}"
        )
        row_dir.addWidget(self.in_dir, 1)

        self.btn_browse = QPushButton("Cambiar…")
        self.btn_browse.setProperty("class", "primary")
        self.btn_browse.setIcon(svg_icon("folder", "#0b1316", 16))
        self.btn_browse.setMinimumHeight(36)
        self.btn_browse.clicked.connect(self._on_browse)
        row_dir.addWidget(self.btn_browse)

        self.btn_open = QPushButton("Abrir")
        self.btn_open.setProperty("class", "ghost")
        self.btn_open.setIcon(svg_icon("folder", FG_PRIMARY, 16))
        self.btn_open.setMinimumHeight(36)
        self.btn_open.clicked.connect(self.request_open_downloads_dir.emit)
        row_dir.addWidget(self.btn_open)
        cdl.addLayout(row_dir)
        body.addWidget(card_dir)

        # ---------- Notificaciones ----------
        card_notif = self._make_card("Notificaciones")
        cnl = card_notif.layout()

        self.chk_in_app = QCheckBox("Mostrar notificaciones in-app (esquina inferior derecha)")
        self.chk_in_app.stateChanged.connect(
            lambda s: self.request_set_in_app_notifications.emit(bool(s))
        )
        cnl.addWidget(self.chk_in_app)

        self.chk_os = QCheckBox("Enviar notificaciones al sistema operativo (buzón del SO)")
        self.chk_os.stateChanged.connect(
            lambda s: self.request_set_os_notifications.emit(bool(s))
        )
        cnl.addWidget(self.chk_os)

        self.lbl_backend = QLabel("Detectando backend…")
        self.lbl_backend.setStyleSheet(f"color: {FG_MUTED}; font-size: 11px;")
        self.lbl_backend.setWordWrap(True)
        cnl.addWidget(self.lbl_backend)
        body.addWidget(card_notif)

        # ---------- Docker ----------
        card_docker = self._make_card("Laboratorio (Docker)")
        cdk = card_docker.layout()
        sub_dk = QLabel(
            "Cómo se expone la máquina al lanzarla. En Linux con Docker Engine la IP del "
            "contenedor es accesible directamente; en Docker Desktop (Windows/macOS) no, así "
            "que se publican los puertos EXPOSE en localhost."
        )
        sub_dk.setStyleSheet(f"color: {FG_MUTED}; font-size: 12px;")
        sub_dk.setWordWrap(True)
        cdk.addWidget(sub_dk)
        self.combo_docker_net = QComboBox()
        for key, label in self.DOCKER_NET_OPTIONS:
            self.combo_docker_net.addItem(label, key)
        self.combo_docker_net.setMinimumHeight(36)
        self.combo_docker_net.currentIndexChanged.connect(
            lambda _i: self.request_set_docker_network.emit(self.combo_docker_net.currentData() or "auto")
        )
        cdk.addWidget(self.combo_docker_net)
        body.addWidget(card_docker)

        # ---------- Info técnica ----------
        card_info = self._make_card("Información")
        cil = card_info.layout()
        info = QLabel(
            "• Datos cacheados en: ~/.dockerlabs-gui/\n"
            "• El catálogo se actualiza automáticamente al arrancar si hay internet.\n"
            "• Si no hay conexión, se carga el último CSV disponible.\n"
            "• Los labs se extraen en ~/.dockerlabs-gui/labs/<máquina>/ y los contenedores se llaman dockerlabs_<máquina>."
        )
        info.setStyleSheet(f"color: {FG_SECONDARY}; font-size: 12px;")
        info.setWordWrap(True)
        cil.addWidget(info)
        body.addWidget(card_info)

        body.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))
        wrap = QFrame(); wrap.setLayout(body)
        root.addWidget(wrap, 1)

    def _make_card(self, title: str) -> QFrame:
        card = QFrame()
        card.setObjectName("settingsCard")
        card.setStyleSheet(
            f"QFrame#settingsCard {{ background: {BG_MID}; border: 1px solid #2a2f3a;"
            f" border-radius: 12px; }}"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)
        t = QLabel(title)
        t.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {FG_PRIMARY};")
        lay.addWidget(t)
        return card

    # ---- API ----

    def set_state(self, downloads_dir: str, os_notifications: bool,
                  in_app_notifications: bool, os_backend_available: bool,
                  docker_network: str = "auto") -> None:
        self._current_dir = downloads_dir
        self.combo_docker_net.blockSignals(True)
        idx = self.combo_docker_net.findData(docker_network or "auto")
        self.combo_docker_net.setCurrentIndex(max(0, idx))
        self.combo_docker_net.blockSignals(False)
        self._os_backend_available = os_backend_available
        self.in_dir.setText(downloads_dir)
        self.in_dir.setToolTip(downloads_dir)

        self.chk_in_app.blockSignals(True)
        self.chk_in_app.setChecked(in_app_notifications)
        self.chk_in_app.blockSignals(False)

        self.chk_os.blockSignals(True)
        self.chk_os.setChecked(os_notifications and os_backend_available)
        self.chk_os.setEnabled(os_backend_available)
        self.chk_os.blockSignals(False)

        if os_backend_available:
            self.lbl_backend.setText(
                "Backend del sistema detectado correctamente."
            )
            self.lbl_backend.setStyleSheet(f"color: {SUCCESS}; font-size: 11px;")
        else:
            self.lbl_backend.setText(
                "No se ha detectado un backend de notificaciones del SO."
                " En Linux instala 'libnotify-bin' (notify-send), en Windows 'winotify',"
                " o como fallback 'plyer'. Mientras tanto se usarán las notificaciones in-app."
            )
            self.lbl_backend.setStyleSheet(f"color: {WARNING}; font-size: 11px;")

    def _on_browse(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Carpeta para las descargas", self._current_dir
        )
        if path:
            self.request_change_downloads_dir.emit(path)


# ============================================================
# Acerca de
# ============================================================

class AboutPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(page_header("Acerca de", "DockerLabs GUI"))
        body = QVBoxLayout()
        body.setContentsMargins(24, 18, 24, 24)
        body.setSpacing(10)
        info = QLabel(
            "DockerLabs GUI · cliente de escritorio no oficial para dockerlabs.es\n"
            "Stack: PyQt6, urllib (stdlib), requests.\n\n"
            "Construido sobre la API pública /api, los endpoints internos del\n"
            "frontend (toggle_completed_machine, completed_machines, author_profile)\n"
            "y descargas HTTP directas desde gestion-maquinas.dockerlabs.es\n"
            "con reintentos y verificación del zip."
        )
        info.setStyleSheet(f"color: {FG_SECONDARY};")
        info.setWordWrap(True)
        body.addWidget(info)
        body.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))
        wrap = QFrame(); wrap.setLayout(body)
        root.addWidget(wrap, 1)
