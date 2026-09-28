"""Diálogo de error inesperado con traceback, copiar y abrir carpeta de logs."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from theme import DANGER, FG_MUTED

_open: list = []  # evita apilar diálogos si el error se repite en bucle


class CrashDialog(QDialog):
    def __init__(self, text: str, log_file: Optional[Path], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("DockerLabs GUI — error inesperado")
        self.setMinimumSize(640, 420)
        lay = QVBoxLayout(self)
        title = QLabel("Se ha producido un error inesperado")
        title.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {DANGER};")
        lay.addWidget(title)
        sub = QLabel("La aplicación puede seguir funcionando. Si el problema se repite, "
                     "copia el detalle y ábrelo como incidencia en GitHub.")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {FG_MUTED};")
        lay.addWidget(sub)
        self.text = QPlainTextEdit(text)
        self.text.setReadOnly(True)
        self.text.setStyleSheet("font-family: monospace; font-size: 11px;")
        lay.addWidget(self.text, 1)
        if log_file:
            lf = QLabel(f"Log: {log_file}")
            lf.setStyleSheet(f"color: {FG_MUTED}; font-size: 11px;")
            lay.addWidget(lf)
        row = QHBoxLayout()
        b_copy = QPushButton("Copiar detalle")
        b_copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.text.toPlainText()))
        row.addWidget(b_copy)
        if log_file:
            b_log = QPushButton("Abrir carpeta de logs")
            b_log.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(log_file).parent))))
            row.addWidget(b_log)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        row.addWidget(bb)
        lay.addLayout(row)


def show_crash_dialog(text: str, log_file: Optional[Path], parent: Optional[QWidget] = None) -> None:
    if _open:
        # Ya hay uno abierto: añadir el nuevo traceback al mismo diálogo
        dlg = _open[0]
        try:
            dlg.text.appendPlainText("\n" + "=" * 70 + "\n" + text)
            return
        except RuntimeError:
            _open.clear()
    dlg = CrashDialog(text, log_file, parent)
    _open.append(dlg)
    dlg.finished.connect(lambda _r: _open.clear())
    dlg.show()
