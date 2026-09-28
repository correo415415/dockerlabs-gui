"""Modelo/vista de máquinas: QAbstractTableModel + proxy de filtro y delegado de badges.

Ventajas frente al QTableWidget anterior: no se reconstruyen 205 filas en cada
cambio de estado (solo `dataChanged`), orden estable por dificultad/fecha reales,
y filtros combinados (texto sin acentos, dificultad, estado) en el proxy.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set

from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QRect,
    QSize,
    QSortFilterProxyModel,
    Qt,
)
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem

from catalog import Machine, difficulty_rank
from theme import ACCENT, FG_MUTED, FG_PRIMARY, SUCCESS, WARNING, difficulty_color
from widgets.icons import icon as svg_icon

COL_DONE, COL_STATE, COL_NAME, COL_DIFF, COL_AUTHOR, COL_DATE = range(6)
HEADERS = ["", "", "Nombre", "Dificultad", "Autor", "Fecha"]

ROLE_MACHINE = Qt.ItemDataRole.UserRole + 1
ROLE_SORT = Qt.ItemDataRole.UserRole + 2
ROLE_STATE = Qt.ItemDataRole.UserRole + 3   # 'running' | 'downloading' | 'downloaded' | ''
ROLE_DONE = Qt.ItemDataRole.UserRole + 4


class MachineTableModel(QAbstractTableModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: List[Machine] = []
        self._index: Dict[str, int] = {}
        self.completed: Set[str] = set()
        self.downloading: Set[str] = set()
        self.downloaded: Set[str] = set()
        self.running: Set[str] = set()

    # ---- datos ----
    def set_machines(self, machines: List[Machine]) -> None:
        self.beginResetModel()
        self._rows = list(machines)
        self._index = {m.name: i for i, m in enumerate(self._rows)}
        self.endResetModel()

    def machines(self) -> List[Machine]:
        return self._rows

    def machine_at(self, row: int) -> Optional[Machine]:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def machine_by_name(self, name: str) -> Optional[Machine]:
        i = self._index.get(name)
        return self._rows[i] if i is not None else None

    def _set_names(self, attr: str, names) -> None:
        old = getattr(self, attr)
        new = set(names or [])
        setattr(self, attr, new)
        changed = old ^ new
        if not changed or not self._rows:
            return
        rows = sorted(i for n in changed if (i := self._index.get(n)) is not None)
        if not rows:
            return
        if len(rows) > 40:   # muchos cambios: un solo dataChanged de rango
            self.dataChanged.emit(self.index(rows[0], 0), self.index(rows[-1], COL_DATE))
            return
        for r in rows:
            self.dataChanged.emit(self.index(r, 0), self.index(r, COL_DATE))

    def set_completed(self, names) -> None:
        self._set_names("completed", names)

    def set_downloading(self, names) -> None:
        self._set_names("downloading", names)

    def set_downloaded(self, names) -> None:
        self._set_names("downloaded", names)

    def set_running(self, names) -> None:
        self._set_names("running", names)

    def state_of(self, name: str) -> str:
        if name in self.running:
            return "running"
        if name in self.downloading:
            return "downloading"
        if name in self.downloaded:
            return "downloaded"
        return ""

    # ---- QAbstractTableModel ----
    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        m = self._rows[index.row()]
        col = index.column()
        if role == ROLE_MACHINE:
            return m
        if role == ROLE_DONE:
            return m.name in self.completed
        if role == ROLE_STATE:
            return self.state_of(m.name)
        if role == ROLE_SORT:
            if col == COL_DONE:
                return 0 if m.name in self.completed else 1
            if col == COL_STATE:
                return {"running": 0, "downloading": 1, "downloaded": 2}.get(self.state_of(m.name), 3)
            if col == COL_NAME:
                return m.name.lower()
            if col == COL_DIFF:
                return difficulty_rank(m.difficulty)
            if col == COL_AUTHOR:
                return m.author.lower()
            if col == COL_DATE:
                return m.date_iso or "0000"
        if role == Qt.ItemDataRole.DisplayRole:
            if col == COL_NAME:
                return m.name
            if col == COL_DIFF:
                return m.difficulty
            if col == COL_AUTHOR:
                return m.author
            if col == COL_DATE:
                return m.date
            return ""
        if role == Qt.ItemDataRole.DecorationRole:
            if col == COL_DONE and m.name in self.completed:
                return svg_icon("check", ACCENT, 16)
            if col == COL_STATE:
                st = self.state_of(m.name)
                if st == "running":
                    return svg_icon("docker", SUCCESS, 16)
                if st == "downloading":
                    return svg_icon("download", WARNING, 16)
                if st == "downloaded":
                    return svg_icon("folder", SUCCESS, 16)
        if role == Qt.ItemDataRole.ToolTipRole:
            if col == COL_DONE:
                return "Completada" if m.name in self.completed else ""
            if col == COL_STATE:
                return {"running": "Laboratorio en ejecución", "downloading": "Descargando…",
                        "downloaded": "Descargada en local"}.get(self.state_of(m.name), "")
            if col == COL_NAME and m.description:
                return m.description
            if col == COL_AUTHOR:
                return m.author_url or m.author
        if role == Qt.ItemDataRole.FontRole and col == COL_NAME:
            f = QFont(); f.setBold(True)
            return f
        if role == Qt.ItemDataRole.ForegroundRole:
            if col == COL_DIFF:
                return QColor(difficulty_color(m.difficulty))
            if col in (COL_AUTHOR, COL_DATE):
                return QColor(FG_MUTED)
            return QColor(FG_PRIMARY)
        if role == Qt.ItemDataRole.TextAlignmentRole and col in (COL_DONE, COL_STATE):
            return Qt.AlignmentFlag.AlignCenter
        return None


class MachineFilterProxy(QSortFilterProxyModel):
    """Filtros: texto (nombre/autor/descripcion, sin acentos), dificultad y estado."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.query = ""
        self.difficulty = "Todas"
        self.state = "Todas"   # Todas | Completadas | Pendientes | Descargadas | En ejecución
        self.setSortRole(ROLE_SORT)
        self.setDynamicSortFilter(True)

    def set_query(self, q: str) -> None:
        self.query = (q or "").strip()
        self.invalidateFilter()

    def set_difficulty(self, d: str) -> None:
        self.difficulty = d or "Todas"
        self.invalidateFilter()

    def set_state(self, s: str) -> None:
        self.state = s or "Todas"
        self.invalidateFilter()

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:  # noqa: N802
        model = self.sourceModel()
        if not isinstance(model, MachineTableModel):
            return True
        m = model.machine_at(row)
        if m is None:
            return False
        if self.query and not m.matches(self.query):
            return False
        if self.difficulty != "Todas" and m.difficulty != self.difficulty:
            return False
        if self.state == "Completadas" and m.name not in model.completed:
            return False
        if self.state == "Pendientes" and m.name in model.completed:
            return False
        if self.state == "Descargadas" and m.name not in model.downloaded:
            return False
        if self.state == "En ejecución" and m.name not in model.running:
            return False
        return True

    def machine_at(self, proxy_index: QModelIndex) -> Optional[Machine]:
        if not proxy_index.isValid():
            return None
        return self.sourceModel().data(self.mapToSource(proxy_index), ROLE_MACHINE)


class DifficultyBadgeDelegate(QStyledItemDelegate):
    """Pinta la dificultad como una píldora de color (como en la web)."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if not text:
            return super().paint(painter, option, index)
        # fondo de fila (selección/alternado) sin el texto
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget else None
        if style:
            from PyQt6.QtWidgets import QStyle
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

        color = QColor(difficulty_color(text))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        fm = option.fontMetrics
        w = fm.horizontalAdvance(text) + 18
        h = min(22, option.rect.height() - 6)
        r = QRect(option.rect.left() + 8, option.rect.center().y() - h // 2, w, h)
        bg = QColor(color); bg.setAlpha(38)
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 110), 1))
        painter.setBrush(bg)
        painter.drawRoundedRect(r, h / 2, h / 2)
        painter.setPen(color)
        f = QFont(option.font); f.setBold(True); f.setPointSizeF(max(8.0, option.font.pointSizeF() - 0.5))
        painter.setFont(f)
        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        s = super().sizeHint(option, index)
        return QSize(max(s.width(), 110), max(s.height(), 30))
