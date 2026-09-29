"""Diálogo «Permitir acceso a Docker» (Linux).

Cuando el usuario no puede hablar con el socket de Docker se ofrecen dos vías,
ambas con el **diálogo de autenticación del sistema** (pkexec/polkit o
`sudo -A` con askpass gráfico): la app nunca ve la contraseña.

1. **Solo esta sesión (recomendado)** — se aplica una ACL sobre el socket de
   Docker para el usuario actual. No se modifica ningún grupo ni la
   configuración; el permiso desaparece cuando se reinicia el servicio o el
   equipo. No hace falta cerrar sesión.
2. **Añadir mi usuario al grupo docker** — solución permanente: `usermod -aG
   docker` + ACL sobre el socket para que funcione sin re-login. Equivale a dar
   acceso root sin contraseña a ese usuario, por eso no es la recomendada.

Si no hay pkexec ni askpass gráfico (`can_elevate=False`) pero sí `sudo`, se
muestra un fallback: la contraseña se introduce en la app, se valida con
`sudo -v` y `docker` se ejecuta con `sudo -S` (contraseña solo en memoria).

El diálogo no ejecuta nada: devuelve la elección (`choice`) y, en el fallback
de sudo, la contraseña (`password`). El controlador hace el trabajo en un hilo.
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

CHOICE_TEMP = "temp"      # ACL sobre el socket (diálogo del sistema) — recomendado
CHOICE_GROUP = "group"    # usermod -aG docker (diálogo del sistema) — permanente
CHOICE_SUDO = "sudo"      # fallback: contraseña en la app, docker vía sudo -S


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
        self.can_elevate = can_elevate
        self.can_sudo = can_sudo
        # Fallback de contraseña en la app solo cuando no hay diálogo del sistema
        self.sudo_fallback = bool(can_sudo and not can_elevate)

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
        sub = QLabel("Docker necesita permisos de administrador. Elige cómo quieres concederlos; "
                     "la contraseña se pedirá con el diálogo de autenticación del sistema."
                     if can_elevate else
                     "Docker necesita permisos de administrador.")
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

        # ---- Opción 1: acceso temporal (diálogo del sistema) ----
        self.opt_temp = QRadioButton("Solo esta sesión  (recomendado)")
        self.opt_temp.setEnabled(can_elevate)
        self.opt_temp.setStyleSheet("font-weight: 600;")
        root.addWidget(self.opt_temp)
        root.addWidget(self._card(
            "Se te pedirá la contraseña con el diálogo del sistema y se dará permiso a tu usuario "
            "sobre el socket de Docker. No se modifica ningún grupo ni la configuración: el permiso "
            "desaparece al reiniciar el servicio o el equipo. No hace falta cerrar sesión."
            + ("" if can_elevate else "\n\nNo hay pkexec/polkit ni askpass gráfico disponible.")
        ))

        # ---- Opción 2: grupo docker (diálogo del sistema) ----
        self.opt_group = QRadioButton("Añadir mi usuario al grupo docker  (permanente)")
        self.opt_group.setEnabled(can_elevate)
        self.opt_group.setStyleSheet("font-weight: 600;")
        root.addWidget(self.opt_group)
        root.addWidget(self._card(
            "Con el mismo diálogo del sistema se ejecutará `usermod -aG docker` más una ACL sobre "
            "el socket para que funcione sin cerrar sesión.\n"
            "Ten en cuenta que pertenecer al grupo docker equivale a tener root sin contraseña."
            + ("" if can_elevate else "\n\nNo hay pkexec/polkit ni askpass gráfico disponible.")
        ))

        # ---- Fallback: sudo con contraseña en la app (solo sin pkexec) ----
        self.opt_sudo = QRadioButton("Usar sudo con mi contraseña  (solo esta sesión)")
        self.opt_sudo.setEnabled(self.sudo_fallback)
        self.opt_sudo.setStyleSheet("font-weight: 600;")
        self.opt_sudo.setVisible(self.sudo_fallback)
        root.addWidget(self.opt_sudo)
        sudo_card = self._card(
            "No se encontró pkexec/polkit, así que no se puede usar el diálogo del sistema. "
            "La contraseña se comprueba con `sudo -v` y se guarda solo en memoria mientras la app "
            "está abierta; docker se ejecutará con sudo."
        )
        self.in_pwd = QLineEdit()
        self.in_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.in_pwd.setPlaceholderText("Contraseña de tu usuario (sudo)")
        self.in_pwd.setMinimumHeight(34)
        self.in_pwd.returnPressed.connect(self._accept)
        sudo_card.layout().addWidget(self.in_pwd)
        sudo_card.setVisible(self.sudo_fallback)
        root.addWidget(sudo_card)
        self._sudo_card = sudo_card

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

        for opt in (self.opt_temp, self.opt_group, self.opt_sudo):
            opt.toggled.connect(self._sync)
        if can_elevate:
            self.opt_temp.setChecked(True)
        elif self.sudo_fallback:
            self.opt_sudo.setChecked(True)
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
        system = self.opt_temp.isChecked() or self.opt_group.isChecked()
        self.in_pwd.setEnabled(sudo)
        self.btn_ok.setEnabled(sudo or system)
        self.btn_ok.setText("Usar sudo" if sudo else "Abrir diálogo del sistema")
        if sudo:
            self.in_pwd.setFocus()

    def _accept(self) -> None:
        if self.opt_temp.isChecked():
            self.choice, self.password = CHOICE_TEMP, ""
        elif self.opt_group.isChecked():
            self.choice, self.password = CHOICE_GROUP, ""
        elif self.opt_sudo.isChecked():
            pwd = self.in_pwd.text()
            if not pwd:
                self.in_pwd.setPlaceholderText("Escribe tu contraseña para continuar")
                self.in_pwd.setFocus()
                return
            self.choice, self.password = CHOICE_SUDO, pwd
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
