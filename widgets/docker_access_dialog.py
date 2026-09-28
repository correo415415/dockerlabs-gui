"""Diálogo «Permitir acceso a Docker» (Linux).

Cuando el usuario no puede hablar con el socket de Docker se ofrecen dos vías:

1. **Usar sudo (recomendado)** — se pide la contraseña una vez; se valida con
   `sudo -v` y, si es correcta, la app ejecuta `docker` con `sudo -S`. La
   contraseña solo vive en memoria mientras la app está abierta: no toca la
   configuración del sistema ni requiere cerrar sesión.
2. **Añadir mi usuario al grupo docker** — solución permanente: se abre el
   diálogo de autenticación del sistema (pkexec/polkit) y se ejecuta
   `usermod -aG docker` + ACL sobre el socket para que funcione sin re-login.
   Equivale a dar acceso root sin contraseña a ese usuario (cualquiera con su
   sesión puede usar Docker), por eso no es la opción recomendada.

El diálogo no ejecuta nada: devuelve la elección (`choice`) y, en el caso de
sudo, la contraseña (`password`). El controlador hace el trabajo en un hilo.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from theme import BG_MID, BORDER_SOFT, FG_MUTED, FG_PRIMARY, WARNING
from widgets.icons import icon as svg_icon

CHOICE_SUDO = "sudo"
CHOICE_GROUP = "group"


class DockerAccessDialog(QDialog):
    """Elige cómo obtener permiso para usar Docker."""

    def __init__(self, parent: Optional[QWidget] = None, *, can_sudo: bool = True,
                 can_elevate: bool = True, error: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Permitir acceso a Docker")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.choice: str = ""
        self.password: str = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        head = QHBoxLayout()
        ico = QLabel()
        ico.setPixmap(svg_icon("lock", WARNING, 28).pixmap(28, 28))
        head.addWidget(ico, 0, Qt.AlignmentFlag.AlignTop)
        title_box = QVBoxLayout()
        title = QLabel("Tu usuario no puede usar Docker")
        title.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {FG_PRIMARY};")
        sub = QLabel("Docker necesita permisos de administrador. Elige cómo quieres concederlos:")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {FG_MUTED};")
        title_box.addWidget(title)
        title_box.addWidget(sub)
        head.addLayout(title_box, 1)
        root.addLayout(head)

        if error:
            err = QLabel(error.splitlines()[0])
            err.setWordWrap(True)
            err.setStyleSheet(f"color: {FG_MUTED}; font-size: 11px;")
            root.addWidget(err)

        # ---- Opción 1: sudo ----
        self.opt_sudo = QRadioButton("Usar sudo con mi contraseña  (recomendado)")
        self.opt_sudo.setEnabled(can_sudo)
        self.opt_sudo.setStyleSheet("font-weight: 600;")
        root.addWidget(self.opt_sudo)
        sudo_card = self._card(
            "La contraseña se comprueba con sudo y se guarda solo en memoria mientras la app "
            "está abierta. No se modifica nada en el sistema ni hace falta cerrar sesión."
            + ("" if can_sudo else "\n\nNo se encontró `sudo` en este sistema.")
        )
        self.in_pwd = QLineEdit()
        self.in_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.in_pwd.setPlaceholderText("Contraseña de tu usuario (sudo)")
        self.in_pwd.setMinimumHeight(34)
        self.in_pwd.returnPressed.connect(self._accept)
        sudo_card.layout().addWidget(self.in_pwd)
        root.addWidget(sudo_card)
        self._sudo_card = sudo_card

        # ---- Opción 2: grupo docker ----
        self.opt_group = QRadioButton("Añadir mi usuario al grupo docker  (permanente)")
        self.opt_group.setEnabled(can_elevate)
        self.opt_group.setStyleSheet("font-weight: 600;")
        root.addWidget(self.opt_group)
        group_card = self._card(
            "Se abrirá el diálogo de autenticación del sistema (pkexec) y se ejecutará "
            "`usermod -aG docker` más una ACL sobre el socket para que funcione sin cerrar sesión.\n"
            "Ten en cuenta que pertenecer al grupo docker equivale a tener root sin contraseña."
            + ("" if can_elevate else "\n\nNo hay pkexec/polkit ni askpass gráfico disponible.")
        )
        root.addWidget(group_card)

        # ---- Botones ----
        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_cancel = QPushButton("Cancelar")
        self.btn_cancel.setProperty("class", "ghost")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok = QPushButton("Continuar")
        self.btn_ok.setProperty("class", "primary")
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self._accept)
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_ok)
        root.addLayout(btns)

        self.opt_sudo.toggled.connect(self._sync)
        self.opt_group.toggled.connect(self._sync)
        if can_sudo:
            self.opt_sudo.setChecked(True)
        elif can_elevate:
            self.opt_group.setChecked(True)
        self._sync()

    # ---- helpers ----

    @staticmethod
    def _card(text: str) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background: {BG_MID}; border: 1px solid {BORDER_SOFT}; border-radius: 10px; }}"
            f"QLabel {{ border: 0; background: transparent; }}"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(8)
        lb = QLabel(text)
        lb.setWordWrap(True)
        lb.setStyleSheet(f"color: {FG_MUTED}; font-size: 12px;")
        lay.addWidget(lb)
        return card

    def _sync(self) -> None:
        sudo = self.opt_sudo.isChecked()
        self.in_pwd.setEnabled(sudo)
        self.btn_ok.setEnabled(sudo or self.opt_group.isChecked())
        self.btn_ok.setText("Usar sudo" if sudo else "Abrir diálogo del sistema")
        if sudo:
            self.in_pwd.setFocus()

    def _accept(self) -> None:
        if self.opt_sudo.isChecked():
            pwd = self.in_pwd.text()
            if not pwd:
                self.in_pwd.setPlaceholderText("Escribe tu contraseña para continuar")
                self.in_pwd.setFocus()
                return
            self.choice, self.password = CHOICE_SUDO, pwd
        elif self.opt_group.isChecked():
            self.choice, self.password = CHOICE_GROUP, ""
        else:
            return
        self.accept()


def ask_docker_access(parent: Optional[QWidget], *, can_sudo: bool, can_elevate: bool,
                      error: str = "") -> tuple[str, str]:
    """Muestra el diálogo y devuelve (choice, password); choice == "" si se cancela."""
    dlg = DockerAccessDialog(parent, can_sudo=can_sudo, can_elevate=can_elevate, error=error)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return dlg.choice, dlg.password
    return "", ""
