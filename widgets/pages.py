"""Páginas (vistas) del QStackedWidget central."""
from __future__ import annotations

from typing import Dict, Optional

from PyQt6.QtCore import QObject, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QPalette
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
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
    QSpinBox,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from download_manager import human_eta, human_size
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
from widgets.avatar import AvatarCircle
from widgets.icons import icon as svg_icon
from workers import BaseWorker

# ============================================================
# Workers compartidos
# ============================================================

class FetchAPIWorker(BaseWorker):
    finished_data = pyqtSignal(dict)

    def __init__(self, client, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.client = client

    def work(self) -> None:
        self.finished_data.emit(self.client.fetch_api_data())


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

        from theme import ACCENT, BG_LIGHT, BG_MID, FG_MUTED, FG_PRIMARY
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
        from PyQt6.QtCore import QPoint
        from PyQt6.QtGui import QPolygon
        cx = rect.right() - 16
        cy = rect.center().y() + 1
        tri = QPolygon([QPoint(cx - 5, cy - 3), QPoint(cx + 5, cy - 3), QPoint(cx, cy + 3)])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(ACCENT) if hovered else QColor(FG_MUTED))
        p.drawPolygon(tri)
        p.end()


def ghost_combo(options: list[tuple[str, str]], min_width: int = 150, height: int = 38) -> QComboBox:
    """Crea un `_GhostComboBox` con items (data, label) y paleta acorde al tema activo."""
    from theme import BG_HOVER, BG_MID, FG_PRIMARY
    combo = _GhostComboBox()
    for data, label in options:
        combo.addItem(label, data)
    combo.setMinimumHeight(height)
    combo.setMinimumWidth(min_width)
    combo.setCursor(Qt.CursorShape.PointingHandCursor)
    pal = combo.palette()
    mid = QColor(BG_MID)
    hover = QColor(BG_HOVER)
    fg = QColor(FG_PRIMARY)
    for role in (QPalette.ColorRole.Highlight, QPalette.ColorRole.Base,
                 QPalette.ColorRole.Button, QPalette.ColorRole.Window):
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
    combo.setItemDelegate(_ComboItemDelegate(combo))
    combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return combo


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
        f"QFrame#topbar {{ background: {BG_MID}; border-bottom: 1px solid {BORDER_SOFT}; }}"
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


class _Panel(QFrame):
    body: QVBoxLayout


def _panel(title: str) -> _Panel:
    f = _Panel()
    f.setObjectName("dashPanel")
    f.setStyleSheet(
        f"QFrame#dashPanel {{ background: {BG_MID}; border: 1px solid {BORDER_SOFT}; border-radius: 12px; }}"
    )
    lay = QVBoxLayout(f)
    lay.setContentsMargins(18, 14, 18, 16)
    lay.setSpacing(10)
    t = QLabel(title)
    t.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {FG_PRIMARY}; background: transparent;")
    lay.addWidget(t)
    f.body = lay
    return f


def _muted(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setStyleSheet(f"color: {FG_MUTED}; font-size: 11px; background: transparent;")
    lb.setWordWrap(True)
    return lb


def _clear_layout(lay) -> None:
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()


class _DiffRow(QWidget):
    """Fila `● Dificultad  3/40` con mini barra de progreso coloreada."""

    def __init__(self, difficulty: str, parent=None) -> None:
        super().__init__(parent)
        color = difficulty_color(difficulty)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(6)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {color}; font-size: 10px; background: transparent;")
        name = QLabel(difficulty)
        name.setStyleSheet(f"color: {FG_SECONDARY}; font-size: 12px; background: transparent;")
        self.lbl_count = QLabel("—")
        self.lbl_count.setStyleSheet(f"color: {FG_PRIMARY}; font-size: 12px; font-weight: 600; background: transparent;")
        top.addWidget(dot)
        top.addWidget(name)
        top.addStretch(1)
        top.addWidget(self.lbl_count)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setStyleSheet(
            f"QProgressBar {{ background: {BG_LIGHT}; border: 0; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}"
        )
        lay.addLayout(top)
        lay.addWidget(self.bar)

    def set_values(self, done: int, total: int) -> None:
        self.lbl_count.setText(f"{done}/{total}")
        self.bar.setValue(int(round(100 * done / total)) if total else 0)


class _LatestRow(QFrame):
    def __init__(self, m, done: bool, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(10)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {difficulty_color(m.difficulty)}; font-size: 10px;")
        name = QLabel(m.name)
        name.setStyleSheet(f"color: {FG_PRIMARY}; font-size: 12px; font-weight: 600;")
        diff = QLabel(m.difficulty)
        diff.setStyleSheet(f"color: {FG_MUTED}; font-size: 11px;")
        author = QLabel(m.author or "")
        author.setStyleSheet(f"color: {FG_SECONDARY}; font-size: 11px;")
        date = QLabel(m.date or "")
        date.setStyleSheet(f"color: {FG_MUTED}; font-size: 11px;")
        lay.addWidget(dot)
        lay.addWidget(name)
        lay.addWidget(diff)
        if done:
            ok = QLabel("✓")
            ok.setStyleSheet(f"color: {SUCCESS}; font-weight: 800; font-size: 12px;")
            ok.setToolTip("Completada")
            lay.addWidget(ok)
        lay.addStretch(1)
        lay.addWidget(author)
        lay.addWidget(date)


class _RankRow(QFrame):
    def __init__(self, pos: int, name: str, count: int, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(10)
        medal = {1: "#facc15", 2: "#cbd5e1", 3: "#d97706"}.get(pos, FG_MUTED)
        num = QLabel(f"{pos}.")
        num.setFixedWidth(20)
        num.setStyleSheet(f"color: {medal}; font-weight: 800; font-size: 12px;")
        lbl = QLabel(name)
        lbl.setStyleSheet(f"color: {FG_PRIMARY}; font-size: 12px;")
        cnt = QLabel(f"{count} máquinas")
        cnt.setStyleSheet(f"color: {FG_SECONDARY}; font-size: 11px;")
        lay.addWidget(num)
        lay.addWidget(lbl)
        lay.addStretch(1)
        lay.addWidget(cnt)


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

        # --- Progreso global ---------------------------------------------------
        prog = _panel("Tu progreso")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(10)
        self.progress.setStyleSheet(
            f"QProgressBar {{ background: {BG_LIGHT}; border: 0; border-radius: 5px; }}"
            f"QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}"
        )
        self.lbl_progress = QLabel("Cargando catálogo…")
        self.lbl_progress.setStyleSheet(f"color: {FG_SECONDARY}; font-size: 12px; background: transparent;")
        prog.body.addWidget(self.lbl_progress)
        prog.body.addWidget(self.progress)
        self.diff_rows: Dict[str, "_DiffRow"] = {}
        diff_grid = QGridLayout()
        diff_grid.setHorizontalSpacing(14)
        diff_grid.setVerticalSpacing(6)
        for i, d in enumerate(("Muy Fácil", "Fácil", "Medio", "Difícil")):
            row = _DiffRow(d)
            self.diff_rows[d] = row
            diff_grid.addWidget(row, i // 2, i % 2)
        prog.body.addLayout(diff_grid)
        content.addWidget(prog)

        # --- Últimas máquinas + ranking ------------------------------------------
        cols = QHBoxLayout()
        cols.setSpacing(14)
        self.panel_latest = _panel("Últimas máquinas publicadas")
        self.latest_host = QVBoxLayout()
        self.latest_host.setSpacing(4)
        self.panel_latest.body.addLayout(self.latest_host)
        self.panel_latest.body.addStretch(1)
        self.panel_ranking = _panel("Top creadores")
        self.ranking_host = QVBoxLayout()
        self.ranking_host.setSpacing(4)
        self.panel_ranking.body.addLayout(self.ranking_host)
        self.panel_ranking.body.addStretch(1)
        cols.addWidget(self.panel_latest, 3)
        cols.addWidget(self.panel_ranking, 2)
        content.addLayout(cols, 1)
        for host in (self.latest_host, self.ranking_host):
            lb = QLabel("Sin datos todavía — el catálogo se carga al iniciar (F5 para actualizar).")
            lb.setStyleSheet(f"color: {FG_MUTED}; font-size: 11px; background: transparent;")
            lb.setWordWrap(True)
            host.addWidget(lb)

        wrap = QFrame()
        wrap.setLayout(content)
        root.addWidget(wrap, 1)
        self._catalog = None
        self._completed: set = set()

    # ------------------------------------------------------------------ catálogo
    def set_catalog(self, catalog, completed=None) -> None:
        """Rellena progreso, desglose por dificultad, últimas máquinas y ranking."""
        self._catalog = catalog
        if completed is not None:
            self._completed = set(completed)
        total = len(catalog.machines)
        names = set(catalog.names())
        done = len(self._completed & names) if names else len(self._completed)
        self.set_total(total)
        self.set_done(len(self._completed))
        pct = int(round(100 * done / total)) if total else 0
        self.progress.setValue(pct)
        self.lbl_progress.setText(
            f"<b style='color:{FG_PRIMARY}'>{done}</b> de {total} máquinas completadas · {pct}%"
            if total else "Catálogo vacío"
        )
        by_all = catalog.counts_by_difficulty()
        by_done = catalog.counts_by_difficulty(self._completed)
        for d, row in self.diff_rows.items():
            row.set_values(by_done.get(d, 0), by_all.get(d, 0))

        _clear_layout(self.latest_host)
        for m in catalog.latest(6):
            self.latest_host.addWidget(_LatestRow(m, m.name in self._completed))
        if not catalog.machines:
            self.latest_host.addWidget(_muted("El catálogo está vacío."))

        _clear_layout(self.ranking_host)
        ranking = sorted(catalog.ranking_creators, key=lambda r: -int(r.get("maquinas", 0) or 0))[:6]
        for i, r in enumerate(ranking, 1):
            self.ranking_host.addWidget(_RankRow(i, str(r.get("nombre", "—")), int(r.get("maquinas", 0) or 0)))
        if not ranking:
            self.ranking_host.addWidget(_muted("Sin ranking disponible."))

    def set_completed(self, completed) -> None:
        self._completed = set(completed or [])
        if self._catalog is not None:
            self.set_catalog(self._catalog)
        self.set_done(len(self._completed))

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
    """Catálogo: tabla (modelo/proxy) + panel de detalle lateral."""

    request_toggle_completed = pyqtSignal(str)
    request_download = pyqtSignal(str, str)   # machine, url
    request_cancel_download = pyqtSignal(str)
    request_launch = pyqtSignal(str)          # machine
    request_refresh_catalog = pyqtSignal()

    STATES = ["Todas", "Completadas", "Pendientes", "Descargadas", "En ejecución"]

    def __init__(self, client=None, parent=None) -> None:
        super().__init__(parent)
        from PyQt6.QtWidgets import QSplitter, QTableView

        from widgets.machine_detail import MachineDetailPanel
        from widgets.machine_model import (
            COL_AUTHOR,
            COL_DATE,
            COL_DIFF,
            COL_DONE,
            COL_NAME,
            COL_STATE,
            DifficultyBadgeDelegate,
            MachineFilterProxy,
            MachineTableModel,
        )
        self._catalog = None
        self._completed_names: set[str] = set()
        self._downloading: set[str] = set()
        self._downloaded: set[str] = set()
        self._running: set[str] = set()
        self._col = dict(done=COL_DONE, state=COL_STATE, name=COL_NAME, diff=COL_DIFF,
                         author=COL_AUTHOR, date=COL_DATE)

        self.model = MachineTableModel(self)
        self.proxy = MachineFilterProxy(self)
        self.proxy.setSourceModel(self.model)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(page_header("Máquinas", "Catálogo público de DockerLabs"))

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)

        left = QWidget()
        body = QVBoxLayout(left)
        body.setContentsMargins(24, 16, 24, 24)
        body.setSpacing(12)

        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(10)
        search_wrap = QFrame()
        search_wrap.setObjectName("searchWrap")
        search_wrap.setStyleSheet(
            f"QFrame#searchWrap {{ background: {BG_LIGHT}; border: 1px solid {BORDER_SOFT};"
            f" border-radius: 8px; }}"
        )
        sl = QHBoxLayout(search_wrap)
        sl.setContentsMargins(10, 0, 10, 0)
        sl.setSpacing(8)
        search_icon = QLabel()
        search_icon.setPixmap(svg_icon("search", FG_MUTED, 18).pixmap(18, 18))
        sl.addWidget(search_icon)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar por nombre, autor o descripción…  (Ctrl+F)")
        self.search.setFrame(False)
        self.search.setClearButtonEnabled(True)
        self.search.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: none;"
            f" color: {FG_PRIMARY}; padding: 8px 0; }}"
        )
        self.search.textChanged.connect(self.proxy.set_query)
        sl.addWidget(self.search, 1)
        search_wrap.setMinimumHeight(38)
        filter_bar.addWidget(search_wrap, 2)

        self.combo_diff = self._make_combo("Dificultad", DIFICULTADES)
        self.combo_diff.currentIndexChanged.connect(
            lambda _i: self.proxy.set_difficulty(self.combo_diff.currentData() or "Todas"))
        filter_bar.addWidget(self.combo_diff, 0)
        self.combo_state = self._make_combo("Estado", self.STATES)
        self.combo_state.currentIndexChanged.connect(
            lambda _i: self.proxy.set_state(self.combo_state.currentData() or "Todas"))
        filter_bar.addWidget(self.combo_state, 0)

        self.btn_refresh = QPushButton()
        self.btn_refresh.setIcon(svg_icon("refresh", FG_PRIMARY, 16))
        self.btn_refresh.setToolTip("Actualizar catálogo (F5)")
        self.btn_refresh.setFixedSize(38, 38)
        self.btn_refresh.setProperty("class", "ghost")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.clicked.connect(self.request_refresh_catalog.emit)
        filter_bar.addWidget(self.btn_refresh, 0)
        body.addLayout(filter_bar)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(COL_NAME, Qt.SortOrder.AscendingOrder)
        self.table.setItemDelegateForColumn(COL_DIFF, DifficultyBadgeDelegate(self.table))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hh.setHighlightSections(False)
        for col, w in ((COL_DONE, 36), (COL_STATE, 36)):
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(col, w)
        hh.setSectionResizeMode(COL_DIFF, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(COL_DIFF, 120)
        hh.setSectionResizeMode(COL_DATE, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(COL_DATE, 100)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.selectionModel().currentRowChanged.connect(self._on_current_changed)
        self.table.doubleClicked.connect(lambda _i: self._open_detail_for_current(force=True))
        body.addWidget(self.table, 1)

        # Estado vacío / cargando
        self.empty = QLabel("Cargando catálogo…")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet(f"color: {FG_MUTED}; font-size: 13px; padding: 24px;")
        body.addWidget(self.empty)

        self.lbl_count = QLabel("Cargando…")
        self.lbl_count.setStyleSheet(f"color: {FG_MUTED};")
        body.addWidget(self.lbl_count)
        splitter.addWidget(left)

        self.detail = MachineDetailPanel(client=client)
        self.detail.request_download.connect(self.request_download)
        self.detail.request_cancel_download.connect(self.request_cancel_download)
        self.detail.request_launch.connect(self.request_launch)
        self.detail.request_toggle_completed.connect(self.request_toggle_completed)
        self.detail.request_close.connect(self.close_detail)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        root.addWidget(splitter, 1)

        self.proxy.rowsInserted.connect(lambda *_: self._update_count())
        self.proxy.rowsRemoved.connect(lambda *_: self._update_count())
        self.proxy.modelReset.connect(self._update_count)
        self.proxy.layoutChanged.connect(self._update_count)
        self._update_count()

    def _make_combo(self, prefix: str, options: list[str]) -> QComboBox:
        """ComboBox 'ghost' que muestra 'prefix: opcion' y se disimula con el tema."""
        return ghost_combo([(opt, f"{prefix}: {opt}") for opt in options], min_width=150)

    # ---- API pública ----

    def set_catalog(self, catalog) -> None:
        self._catalog = catalog
        self.model.set_machines(catalog.machines if catalog else [])
        has = bool(catalog and catalog.machines)
        self.table.setVisible(has)
        self.empty.setVisible(not has)
        if not has:
            self.empty.setText("Sin catálogo. Pulsa Actualizar cuando tengas conexión.")
        self._update_count()
        # refrescar el detalle si la máquina sigue existiendo
        if self.detail.machine and catalog:
            m = catalog.by_name().get(self.detail.machine.name)
            if m:
                self.detail.show_machine(m, catalog.writeups_for(m.name))
                self._push_status_to_detail()

    def set_loading(self, loading: bool, text: str = "") -> None:
        self.btn_refresh.setEnabled(not loading)
        if loading and not self.model.rowCount():
            self.empty.setText(text or "Cargando catálogo…")
            self.empty.setVisible(True)
            self.table.setVisible(False)

    @property
    def machines(self):
        return self.model.machines()

    @property
    def names(self) -> list[str]:
        return [m.name for m in self.model.machines()]

    def machine(self, name: str):
        return self.model.machine_by_name(name)

    def set_completed(self, names) -> None:
        self._completed_names = set(names or [])
        self.model.set_completed(self._completed_names)
        self._update_count(); self._push_status_to_detail()

    def set_downloading(self, names) -> None:
        self._downloading = set(names or [])
        self.model.set_downloading(self._downloading)
        self._push_status_to_detail()

    def set_downloaded(self, names) -> None:
        self._downloaded = set(names or [])
        self.model.set_downloaded(self._downloaded)
        self._update_count(); self._push_status_to_detail()

    def set_running(self, names) -> None:
        self._running = set(names or [])
        self.model.set_running(self._running)
        self._push_status_to_detail()

    def focus_search(self) -> None:
        self.search.setFocus(); self.search.selectAll()

    def show_detail(self, name: str) -> None:
        m = self.model.machine_by_name(name)
        if not m:
            return
        wu = self._catalog.writeups_for(name) if self._catalog else []
        self.detail.show_machine(m, wu)
        self._push_status_to_detail()

    def close_detail(self) -> None:
        self.detail.setVisible(False)

    # ---- internos ----

    def _update_count(self) -> None:
        total = self.model.rowCount()
        shown = self.proxy.rowCount()
        done = sum(1 for n in self.names if n in self._completed_names)
        dl = sum(1 for n in self.names if n in self._downloaded)
        self.lbl_count.setText(
            f"{shown} de {total} máquinas · {done} completadas · {dl} descargadas"
            if total else "Sin catálogo"
        )
        if total and shown == 0:
            self.empty.setText("Ninguna máquina coincide con los filtros.")
            self.empty.setVisible(True)
        elif total:
            self.empty.setVisible(False)

    def _push_status_to_detail(self) -> None:
        m = self.detail.machine
        if not m:
            return
        n = m.name
        self.detail.set_status(done=n in self._completed_names, downloading=n in self._downloading,
                               downloaded=n in self._downloaded, running=n in self._running)

    def _on_current_changed(self, current, _previous) -> None:
        if self.detail.isVisible():
            self._open_detail_for_current()

    def _open_detail_for_current(self, force: bool = False) -> None:
        idx = self.table.currentIndex()
        m = self.proxy.machine_at(idx)
        if m and (force or self.detail.isVisible()):
            self.show_detail(m.name)

    def _machine_at(self, pos: QPoint):
        return self.proxy.machine_at(self.table.indexAt(pos))

    def _show_context_menu(self, pos: QPoint) -> None:
        m = self._machine_at(pos)
        if not m:
            return
        name, url = m.name, m.download_url
        done = name in self._completed_names
        downloading = name in self._downloading
        downloaded = name in self._downloaded

        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {BG_MID}; color: {FG_PRIMARY};"
            f" border: 1px solid {BORDER_SOFT}; padding: 6px; border-radius: 8px; }}"
            f"QMenu::item {{ padding: 8px 28px 8px 14px; border-radius: 6px; }}"
            f"QMenu::item:selected {{ background: {BG_LIGHT}; color: {ACCENT}; }}"
            f"QMenu::separator {{ height: 1px; background: {BORDER_SOFT}; margin: 4px 8px; }}"
        )
        act_detail = QAction(svg_icon("info", FG_PRIMARY, 16), "Ver detalle", self)
        act_detail.triggered.connect(lambda: self.show_detail(name))
        menu.addAction(act_detail)
        menu.addSeparator()

        toggle_text = "Desmarcar como completada" if done else "Marcar como completada"
        act_toggle = QAction(svg_icon("check" if not done else "circle",
                                      ACCENT if not done else FG_MUTED, 16), toggle_text, self)
        act_toggle.triggered.connect(lambda: self.request_toggle_completed.emit(name))
        menu.addAction(act_toggle)
        menu.addSeparator()

        if downloading:
            act_dl = QAction(svg_icon("trash", DANGER, 16), "Cancelar descarga", self)
            act_dl.triggered.connect(lambda: self.request_cancel_download.emit(name))
            menu.addAction(act_dl)
        elif downloaded:
            running = name in self._running
            act_launch = QAction(svg_icon("docker", SUCCESS if not running else FG_MUTED, 16),
                                 "Ver en Laboratorio" if running else "Lanzar laboratorio (Docker)", self)
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
            f"QFrame#downloadItem {{ background: {BG_MID}; border: 1px solid {BORDER_SOFT};"
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
    request_clear_finished = pyqtSignal()

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

        top = QHBoxLayout()
        top.setSpacing(10)
        self.lbl_summary = QLabel("Sin descargas")
        self.lbl_summary.setStyleSheet(f"color: {FG_MUTED};")
        self.btn_clear = QPushButton("  Limpiar terminadas")
        self.btn_clear.setProperty("class", "ghost")
        self.btn_clear.setIcon(svg_icon("trash", FG_SECONDARY, 15))
        self.btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear.setToolTip("Quita de la lista las descargas completadas, fallidas o canceladas")
        self.btn_clear.setEnabled(False)
        self.btn_clear.clicked.connect(self.request_clear_finished.emit)
        top.addWidget(self.lbl_summary)
        top.addStretch(1)
        top.addWidget(self.btn_clear)
        body.addLayout(top)

        # contenedor de items dentro de un scroll (muchas descargas no deben desbordar la vista)
        from PyQt6.QtWidgets import QScrollArea

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.list_host = QWidget()
        self.list_host.setObjectName("downloadsHost")
        self.list_host.setStyleSheet("QWidget#downloadsHost { background: transparent; }")
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(10)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_host)
        body.addWidget(self.scroll, 1)

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
                self.list_layout.insertWidget(self.list_layout.count() - 1, w)   # antes del stretch
            w.update_state(state)

        n = len(states)
        active = sum(1 for s in states if s.state in ("running", "verifying", "queued"))
        done = sum(1 for s in states if s.state == "done")
        finished = n - active
        self.empty.setVisible(n == 0)
        self.scroll.setVisible(n > 0)
        self.btn_clear.setEnabled(finished > 0)
        self.btn_clear.setText(f"  Limpiar terminadas ({finished})" if finished else "  Limpiar terminadas")
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
    """Máquinas completadas: búsqueda, agrupación por dificultad y desmarcar."""

    request_refresh = pyqtSignal()
    request_toggle_completed = pyqtSignal(str)
    request_open_machine = pyqtSignal(str)

    ROLE_NAME = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._names: list = []
        self._catalog = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(page_header("Completadas",
                                   "Máquinas marcadas como hechas en tu cuenta"))

        body = QVBoxLayout()
        body.setContentsMargins(24, 16, 24, 24)
        body.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.lbl = QLabel("Inicia sesión para sincronizar.")
        self.lbl.setStyleSheet(f"color: {FG_SECONDARY};")
        top.addWidget(self.lbl, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(self._rebuild)
        top.addWidget(self.search)
        self.chk_group = QCheckBox("Agrupar por dificultad")
        self.chk_group.setChecked(True)
        self.chk_group.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.chk_group.toggled.connect(self._rebuild)
        top.addWidget(self.chk_group)
        self.btn_refresh = QPushButton("  Sincronizar")
        self.btn_refresh.setProperty("class", "primary")
        self.btn_refresh.setIcon(svg_icon("refresh", ON_ACCENT, 16))
        self.btn_refresh.setMinimumHeight(34)
        self.btn_refresh.clicked.connect(self.request_refresh.emit)
        top.addWidget(self.btn_refresh)
        body.addLayout(top)

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(
            f"QListWidget {{ background: {BG_MID}; border: 1px solid {BORDER_SOFT};"
            f" border-radius: 12px; padding: 8px; color: {FG_PRIMARY}; }}"
            f"QListWidget::item {{ padding: 8px 12px; border: none; border-radius: 6px; }}"
            f"QListWidget::item:selected {{ background: {BG_LIGHT}; }}"
        )
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._context_menu)
        self.list_widget.itemDoubleClicked.connect(self._on_double_click)
        body.addWidget(self.list_widget, 1)

        hint = QLabel("Doble clic abre la máquina en el catálogo · clic derecho para desmarcar.")
        hint.setStyleSheet(f"color: {FG_MUTED}; font-size: 11px;")
        body.addWidget(hint)

        wrap = QFrame(); wrap.setLayout(body)
        root.addWidget(wrap, 1)

    # ------------------------------------------------------------------ API
    def set_catalog(self, catalog) -> None:
        self._catalog = catalog
        self._rebuild()

    def set_items(self, names) -> None:
        self._names = sorted(set(names or []), key=str.lower)
        self._rebuild()

    @property
    def names(self) -> list:
        return list(self._names)

    # ------------------------------------------------------------------ interno
    def _difficulty(self, name: str) -> str:
        if self._catalog is None:
            return ""
        m = self._catalog.by_name().get(name)
        return m.difficulty if m else ""

    def _rebuild(self) -> None:
        self.list_widget.clear()
        q = self.search.text().strip().lower()
        names = [n for n in self._names if q in n.lower()]
        total = len(self._names)
        if total == 0:
            self._add_placeholder("Sin máquinas completadas")
            self.lbl.setText("0 máquinas completadas")
            return
        self.lbl.setText(f"{total} máquinas completadas" + (f" · {len(names)} coinciden" if q else ""))
        if not names:
            self._add_placeholder(f"Ninguna completada coincide con «{self.search.text().strip()}»")
            return
        group = self.chk_group.isChecked() and self._catalog is not None
        if not group:
            for n in names:
                self._add_machine(n)
            return
        from catalog import DIFFICULTY_ORDER

        buckets: Dict[str, list] = {d: [] for d in DIFFICULTY_ORDER}
        buckets[""] = []
        for n in names:
            buckets.setdefault(self._difficulty(n), []).append(n)
        for d in list(DIFFICULTY_ORDER) + [""]:
            items = buckets.get(d) or []
            if not items:
                continue
            self._add_group_header(d or "Sin clasificar", len(items), difficulty_color(d) if d else FG_MUTED)
            for n in items:
                self._add_machine(n, indent=True)

    def _add_placeholder(self, text: str) -> None:
        it = QListWidgetItem(text)
        it.setForeground(QColor(FG_MUTED))
        it.setFlags(Qt.ItemFlag.NoItemFlags)
        self.list_widget.addItem(it)

    def _add_group_header(self, title: str, count: int, color: str) -> None:
        it = QListWidgetItem(f"{title}  ·  {count}")
        it.setForeground(QColor(color))
        f = it.font(); f.setBold(True); it.setFont(f)
        it.setFlags(Qt.ItemFlag.NoItemFlags)
        self.list_widget.addItem(it)

    def _add_machine(self, name: str, indent: bool = False) -> None:
        it = QListWidgetItem(("      " if indent else "  ") + name)
        it.setIcon(svg_icon("check", ACCENT, 16))
        it.setForeground(QColor(FG_PRIMARY))
        it.setData(self.ROLE_NAME, name)
        d = self._difficulty(name)
        if d:
            it.setToolTip(f"{name} · {d}")
        self.list_widget.addItem(it)

    def _item_name(self, item) -> str:
        return str(item.data(self.ROLE_NAME) or "") if item is not None else ""

    def _on_double_click(self, item) -> None:
        n = self._item_name(item)
        if n:
            self.request_open_machine.emit(n)

    def _context_menu(self, pos: QPoint) -> None:
        item = self.list_widget.itemAt(pos)
        n = self._item_name(item)
        if not n:
            return
        menu = QMenu(self)
        act_open = QAction(svg_icon("machines", FG_PRIMARY, 14), "Ver en el catálogo", menu)
        act_open.triggered.connect(lambda: self.request_open_machine.emit(n))
        act_undo = QAction(svg_icon("x", DANGER, 14), "Desmarcar como completada", menu)
        act_undo.triggered.connect(lambda: self.request_toggle_completed.emit(n))
        menu.addAction(act_open)
        menu.addSeparator()
        menu.addAction(act_undo)
        menu.exec(self.list_widget.viewport().mapToGlobal(pos))


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
            f"QFrame#loginCard {{ background: {BG_MID}; border: 1px solid {BORDER_SOFT};"
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
        self.btn_login.setIcon(svg_icon("session", ON_ACCENT, 16))
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
            f"QFrame#profileCard {{ background: {BG_MID}; border: 1px solid {BORDER_SOFT};"
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
        self.btn_logout.setIcon(svg_icon("logout", ON_ACCENT, 16))
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
    request_set_max_concurrent = pyqtSignal(int)
    request_set_theme = pyqtSignal(str)

    THEME_OPTIONS = [
        ("dark", "Oscuro (por defecto)"),
        ("light", "Claro"),
    ]

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
            f"QLineEdit#dirInput {{ background: {BG_LIGHT}; border: 1px solid {BORDER_SOFT};"
            f" border-radius: 8px; padding: 8px 10px; color: {FG_PRIMARY}; }}"
        )
        row_dir.addWidget(self.in_dir, 1)

        self.btn_browse = QPushButton("Cambiar…")
        self.btn_browse.setProperty("class", "primary")
        self.btn_browse.setIcon(svg_icon("folder", ON_ACCENT, 16))
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

        row_conc = QHBoxLayout()
        row_conc.setSpacing(10)
        lbl_conc = QLabel("Descargas simultáneas")
        lbl_conc.setStyleSheet(f"color: {FG_PRIMARY}; font-size: 12px;")
        self.spin_concurrent = QSpinBox()
        self.spin_concurrent.setRange(1, 6)
        self.spin_concurrent.setValue(2)
        self.spin_concurrent.setFixedWidth(84)
        self.spin_concurrent.setButtonSymbols(QSpinBox.ButtonSymbols.PlusMinus)
        self.spin_concurrent.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.spin_concurrent.setMinimumHeight(32)
        self.spin_concurrent.setToolTip("Número máximo de máquinas descargándose a la vez; el resto espera en cola")
        self.spin_concurrent.valueChanged.connect(self.request_set_max_concurrent.emit)
        hint_conc = QLabel(
            "El servidor de DockerLabs limita cada conexión a ~0,5 MB/s y no admite descargas por "
            "partes, así que una máquina no puede ir más rápido; sí puedes bajar varias a la vez. "
            "Con muchas conexiones a veces devuelve errores 500 (se reintenta solo)."
        )
        hint_conc.setStyleSheet(f"color: {FG_MUTED}; font-size: 11px;")
        hint_conc.setWordWrap(True)
        row_conc.addWidget(lbl_conc)
        row_conc.addWidget(self.spin_concurrent)
        row_conc.addWidget(hint_conc, 1)
        cdl.addLayout(row_conc)
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
        self.combo_docker_net = ghost_combo(self.DOCKER_NET_OPTIONS, min_width=320, height=36)
        self.combo_docker_net.setStyleSheet(
            f"QComboBox {{ background: {BG_LIGHT}; border: 1px solid {BORDER_SOFT}; }}")
        self.combo_docker_net.currentIndexChanged.connect(
            lambda _i: self.request_set_docker_network.emit(self.combo_docker_net.currentData() or "auto")
        )
        cdk.addWidget(self.combo_docker_net)
        body.addWidget(card_docker)

        # ---------- Apariencia ----------
        card_theme = self._make_card("Apariencia")
        ctl = card_theme.layout()
        row_theme = QHBoxLayout()
        row_theme.setSpacing(10)
        lbl_theme = QLabel("Tema")
        lbl_theme.setStyleSheet(f"color: {FG_PRIMARY}; font-size: 12px;")
        self.combo_theme = ghost_combo(self.THEME_OPTIONS, min_width=220, height=36)
        self.combo_theme.currentIndexChanged.connect(
            lambda _i: self.request_set_theme.emit(self.combo_theme.currentData() or "dark")
        )
        self.lbl_theme_hint = QLabel(
            "El cambio se aplica al instante a la mayoría de la interfaz; "
            "algunos paneles ya abiertos se repintan por completo al reiniciar."
        )
        self.lbl_theme_hint.setStyleSheet(f"color: {FG_MUTED}; font-size: 11px;")
        self.lbl_theme_hint.setWordWrap(True)
        row_theme.addWidget(lbl_theme)
        row_theme.addWidget(self.combo_theme)
        row_theme.addWidget(self.lbl_theme_hint, 1)
        ctl.addLayout(row_theme)
        body.addWidget(card_theme)

        # ---------- Info técnica ----------
        card_info = self._make_card("Información")
        cil = card_info.layout()
        info = QLabel(
            "• Datos cacheados en: ~/.dockerlabs-gui/ (catálogo, ajustes, logs).\n"
            "• El catálogo se actualiza automáticamente al arrancar si hay internet (F5 para forzar).\n"
            "• Si no hay conexión, se carga el último catálogo guardado (catalog.json).\n"
            "• Los labs se extraen en ~/.dockerlabs-gui/labs/<máquina>/ y los contenedores se llaman dockerlabs_<máquina>."
        )
        info.setStyleSheet(f"color: {FG_SECONDARY}; font-size: 12px;")
        info.setWordWrap(True)
        cil.addWidget(info)
        body.addWidget(card_info)

        body.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))
        wrap = QFrame(); wrap.setObjectName("settingsBody"); wrap.setLayout(body)
        from PyQt6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; }"
                             " QScrollArea > QWidget > QWidget#settingsBody { background: transparent; }")
        scroll.setWidget(wrap)
        root.addWidget(scroll, 1)

    def _make_card(self, title: str) -> QFrame:
        card = QFrame()
        card.setObjectName("settingsCard")
        card.setStyleSheet(
            f"QFrame#settingsCard {{ background: {BG_MID}; border: 1px solid {BORDER_SOFT};"
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
                  docker_network: str = "auto", max_concurrent: int = 2,
                  theme: str = "dark") -> None:
        self._current_dir = downloads_dir
        self.combo_theme.blockSignals(True)
        t_idx = self.combo_theme.findData(theme or "dark")
        self.combo_theme.setCurrentIndex(max(0, t_idx))
        self.combo_theme.blockSignals(False)
        self.spin_concurrent.blockSignals(True)
        self.spin_concurrent.setValue(max(1, min(6, int(max_concurrent or 2))))
        self.spin_concurrent.blockSignals(False)
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
