"""Página «Laboratorio»: estado de Docker y máquinas desplegadas."""
from __future__ import annotations

from typing import Dict

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from theme import (
    ACCENT,
    BG_LIGHT,
    BG_MID,
    BORDER_SOFT,
    DANGER,
    FG_MUTED,
    FG_PRIMARY,
    FG_SECONDARY,
    SUCCESS,
    WARNING,
)
from widgets.icons import icon as svg_icon
from widgets.pages import page_header

_PHASE_TEXT = {
    "idle": "Inactivo",
    "extracting": "Extrayendo zip…",
    "loading": "Cargando imagen en Docker…",
    "starting": "Arrancando contenedor…",
    "running": "En ejecución",
    "stopped": "Detenido",
    "stopping": "Deteniendo…",
    "removing": "Eliminando…",
    "error": "Error",
}
_PHASE_COLOR = {
    "running": SUCCESS, "stopped": FG_MUTED, "error": DANGER,
    "extracting": WARNING, "loading": WARNING, "starting": WARNING,
    "stopping": WARNING, "removing": WARNING, "idle": FG_MUTED,
}


def _btn(text: str, icon: str, color: str, klass: str = "ghost") -> QPushButton:
    b = QPushButton(f"  {text}")
    b.setProperty("class", klass)
    b.setIcon(svg_icon(icon, color, 16))
    b.setFixedHeight(32)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class LabItemWidget(QFrame):
    action = pyqtSignal(str, str)   # machine, action

    def __init__(self, machine: str, parent=None) -> None:
        super().__init__(parent)
        self.machine = machine
        self.setObjectName("labItem")
        self.setStyleSheet(
            f"QFrame#labItem {{ background: {BG_MID}; border: 1px solid {BORDER_SOFT};"
            f" border-radius: 12px; }}"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)
        ico = QLabel()
        ico.setPixmap(svg_icon("docker", ACCENT, 20).pixmap(20, 20))
        top.addWidget(ico)
        self.lbl_title = QLabel(machine)
        self.lbl_title.setStyleSheet(f"font-weight: 700; font-size: 15px; color: {FG_PRIMARY};")
        top.addWidget(self.lbl_title, 1)
        self.lbl_state = QLabel("")
        self.lbl_state.setStyleSheet(f"color: {FG_MUTED}; font-size: 11px; font-weight: 600;")
        top.addWidget(self.lbl_state, 0, Qt.AlignmentFlag.AlignRight)
        lay.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setStyleSheet(
            f"QProgressBar {{ background: {BG_LIGHT}; border-radius: 3px; border: none; }}"
            f"QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}"
        )
        lay.addWidget(self.bar)

        self.lbl_access = QLabel("—")
        self.lbl_access.setWordWrap(True)
        self.lbl_access.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_access.setStyleSheet(f"color: {FG_PRIMARY}; font-size: 13px;")
        lay.addWidget(self.lbl_access)

        self.lbl_info = QLabel("")
        self.lbl_info.setWordWrap(True)
        self.lbl_info.setStyleSheet(f"color: {FG_MUTED}; font-size: 11px;")
        lay.addWidget(self.lbl_info)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_copy = _btn("Copiar IP", "ip", FG_PRIMARY)
        self.btn_shell = _btn("Shell", "terminal", FG_PRIMARY)
        self.btn_start = _btn("Iniciar", "play", "#0b1316", "primary")
        self.btn_stop = _btn("Detener", "stop", FG_PRIMARY)
        self.btn_restart = _btn("Reiniciar", "restart", FG_PRIMARY)
        self.btn_remove = _btn("Eliminar", "trash", DANGER, "danger")
        self.btn_cancel = _btn("Cancelar", "x", DANGER, "danger")
        for b, a in ((self.btn_copy, "copy_ip"), (self.btn_shell, "shell"),
                     (self.btn_start, "start"), (self.btn_stop, "stop"),
                     (self.btn_restart, "restart"), (self.btn_remove, "remove"),
                     (self.btn_cancel, "cancel")):
            b.clicked.connect(lambda _c=False, act=a: self.action.emit(self.machine, act))
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self._ip = ""

    def update_state(self, st) -> None:
        phase = st.phase
        self.lbl_state.setText(_PHASE_TEXT.get(phase, phase))
        self.lbl_state.setStyleSheet(
            f"color: {_PHASE_COLOR.get(phase, FG_MUTED)}; font-size: 11px; font-weight: 600;"
        )
        busy = st.busy
        if phase == "extracting":
            self.bar.setRange(0, 100)
            self.bar.setValue(int(st.progress))
        elif busy:
            self.bar.setRange(0, 0)  # indeterminado
        else:
            self.bar.setRange(0, 100)
            self.bar.setValue(100 if phase == "running" else 0)
        self.bar.setVisible(busy)

        c = st.container
        self._ip = (c.ip if c else "") or ""
        if phase == "error":
            self.lbl_access.setText(st.error or "Error desconocido")
            self.lbl_access.setStyleSheet(f"color: {DANGER}; font-size: 12px;")
        elif busy:
            self.lbl_access.setText(st.log_line or _PHASE_TEXT.get(phase, ""))
            self.lbl_access.setStyleSheet(f"color: {FG_SECONDARY}; font-size: 12px;")
        else:
            self.lbl_access.setText(st.access_text or "—")
            self.lbl_access.setStyleSheet(f"color: {FG_PRIMARY}; font-size: 13px;")

        bits = []
        containers = getattr(st, "containers", None) or ([c] if c else [])
        if len(containers) > 1:
            bits.append(f"lab de pivoting · {len(containers)} máquinas")
            bits.append("contenedores " + ", ".join(x.name for x in containers))
        else:
            if st.image:
                bits.append(f"imagen {st.image.repo_tag}")
                if st.image.exposed_ports:
                    bits.append("EXPOSE " + ", ".join(f"{p}/{pr}" for p, pr in st.image.exposed_ports))
            elif c and c.image:
                bits.append(f"imagen {c.image}")
            if c:
                bits.append(f"contenedor {c.name}")
        self.lbl_info.setText("  ·  ".join(bits))
        self.lbl_access.setWordWrap(True)

        running = phase == "running"
        stopped = phase == "stopped"
        self.btn_copy.setVisible(running and bool(self._ip))
        self.btn_shell.setVisible(running)
        self.btn_start.setVisible(stopped or phase == "error" and c is not None)
        self.btn_stop.setVisible(running)
        self.btn_restart.setVisible(running)
        self.btn_remove.setVisible(not busy)
        self.btn_cancel.setVisible(phase in ("extracting", "loading", "starting"))

    def ip(self) -> str:
        return self._ip


class LabPage(QWidget):
    request_action = pyqtSignal(str, str)   # machine, action
    request_refresh = pyqtSignal()
    request_go_machines = pyqtSignal()
    request_grant_access = pyqtSignal()     # Linux: pedir sudo con diálogo nativo
    request_start_service = pyqtSignal()    # Linux: systemctl start docker (elevado)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._widgets: Dict[str, LabItemWidget] = {}
        self._elevating = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(page_header("Laboratorio", "Máquinas desplegadas con Docker"))

        body = QVBoxLayout()
        body.setContentsMargins(24, 16, 24, 24)
        body.setSpacing(12)

        # ---- Estado de Docker ----
        self.docker_card = QFrame()
        self.docker_card.setObjectName("dockerCard")
        self.docker_card.setStyleSheet(
            f"QFrame#dockerCard {{ background: {BG_MID}; border: 1px solid {BORDER_SOFT};"
            f" border-radius: 12px; }}"
        )
        dl = QHBoxLayout(self.docker_card)
        dl.setContentsMargins(16, 12, 16, 12)
        dl.setSpacing(12)
        self.docker_icon = QLabel()
        dl.addWidget(self.docker_icon)
        txt = QVBoxLayout()
        txt.setSpacing(2)
        self.lbl_docker_title = QLabel("Comprobando Docker…")
        self.lbl_docker_title.setStyleSheet(f"font-weight: 700; color: {FG_PRIMARY};")
        self.lbl_docker_sub = QLabel("")
        self.lbl_docker_sub.setWordWrap(True)
        self.lbl_docker_sub.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_docker_sub.setStyleSheet(f"color: {FG_MUTED}; font-size: 11px;")
        txt.addWidget(self.lbl_docker_title)
        txt.addWidget(self.lbl_docker_sub)
        dl.addLayout(txt, 1)
        btns = QVBoxLayout()
        btns.setSpacing(6)
        self.btn_grant = _btn("Conceder acceso", "shield", "#0b1316", "primary")
        self.btn_grant.setToolTip(
            "Se abrirá el diálogo de autenticación del sistema (pkexec/sudo) para añadir tu "
            "usuario al grupo docker y dar acceso inmediato al socket.")
        self.btn_grant.clicked.connect(self.request_grant_access.emit)
        self.btn_start_service = _btn("Iniciar servicio", "power", "#0b1316", "primary")
        self.btn_start_service.setToolTip("Arranca el servicio docker (pide permisos de administrador).")
        self.btn_start_service.clicked.connect(self.request_start_service.emit)
        self.btn_refresh = _btn("Actualizar", "refresh", FG_PRIMARY)
        self.btn_refresh.clicked.connect(self.request_refresh.emit)
        for b in (self.btn_grant, self.btn_start_service, self.btn_refresh):
            btns.addWidget(b)
        btns.addStretch(1)
        dl.addLayout(btns, 0)
        body.addWidget(self.docker_card)

        self.lbl_summary = QLabel("Sin máquinas desplegadas")
        self.lbl_summary.setStyleSheet(f"color: {FG_MUTED};")
        body.addWidget(self.lbl_summary)

        # ---- Lista con scroll ----
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.list_host = QWidget()
        self.list_host.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(10)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_host)
        body.addWidget(self.scroll, 1)

        self.empty = QFrame()
        el = QVBoxLayout(self.empty)
        el.setContentsMargins(0, 24, 0, 0)
        el.setSpacing(10)
        e_ico = QLabel()
        e_ico.setPixmap(svg_icon("flask", FG_MUTED, 40).pixmap(40, 40))
        e_ico.setAlignment(Qt.AlignmentFlag.AlignCenter)
        el.addWidget(e_ico)
        e_txt = QLabel(
            "Aún no has desplegado ninguna máquina.\n"
            "Ve a Máquinas, descarga una y elige «Lanzar laboratorio» con el clic derecho."
        )
        e_txt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        e_txt.setWordWrap(True)
        e_txt.setStyleSheet(f"color: {FG_MUTED}; font-size: 13px;")
        el.addWidget(e_txt)
        e_btn = _btn("Ir a Máquinas", "machines", "#0b1316", "primary")
        e_btn.clicked.connect(self.request_go_machines.emit)
        eb = QHBoxLayout(); eb.addStretch(1); eb.addWidget(e_btn); eb.addStretch(1)
        el.addLayout(eb)
        el.addStretch(1)
        body.addWidget(self.empty, 1)

        wrap = QFrame(); wrap.setLayout(body)
        root.addWidget(wrap, 1)
        self.set_docker_info(None, "")

    # ---- API ----

    def set_elevating(self, active: bool) -> None:
        """Mientras el diálogo del sistema está abierto, bloquear los botones."""
        self._elevating = active
        self.btn_grant.setEnabled(not active)
        self.btn_start_service.setEnabled(not active)
        self.btn_refresh.setEnabled(not active)
        if active:
            self.lbl_docker_title.setText("Esperando autorización del sistema…")
            self.lbl_docker_sub.setText(
                "Introduce tu contraseña en el diálogo que acaba de abrirse.")
            self.lbl_docker_sub.setStyleSheet(f"color: {FG_MUTED}; font-size: 11px;")

    def set_docker_info(self, info, install_hint: str, permission_hint: str = "") -> None:
        self.btn_grant.setVisible(False)
        self.btn_start_service.setVisible(False)
        if info is None:
            self.docker_icon.setPixmap(svg_icon("docker", FG_MUTED, 28).pixmap(28, 28))
            self.lbl_docker_title.setText("Comprobando Docker…")
            self.lbl_docker_sub.setText("")
            return
        if not info.available:
            self.docker_icon.setPixmap(svg_icon("docker", DANGER, 28).pixmap(28, 28))
            self.lbl_docker_title.setText("Docker no está instalado")
            self.lbl_docker_sub.setText(install_hint)
            self.lbl_docker_sub.setStyleSheet(f"color: {WARNING}; font-size: 11px;")
        elif getattr(info, "needs_elevation", False):
            self.docker_icon.setPixmap(svg_icon("lock", WARNING, 28).pixmap(28, 28))
            self.lbl_docker_title.setText("Docker instalado, pero tu usuario no tiene permiso")
            self.lbl_docker_sub.setText(permission_hint or info.error)
            self.lbl_docker_sub.setStyleSheet(f"color: {WARNING}; font-size: 11px;")
            self.btn_grant.setVisible(bool(getattr(info, "can_elevate", False)))
        elif not info.running:
            self.docker_icon.setPixmap(svg_icon("docker", WARNING, 28).pixmap(28, 28))
            self.lbl_docker_title.setText("Docker instalado, pero el daemon no responde")
            self.lbl_docker_sub.setText(info.error)
            self.lbl_docker_sub.setStyleSheet(f"color: {WARNING}; font-size: 11px;")
            state = getattr(info, "service_state", "")
            # Servicio parado en Linux con systemd → ofrecer arrancarlo con elevación
            if state in ("inactive", "failed") and (
                    getattr(info, "can_elevate", False) or getattr(info, "is_root", False)):
                self.btn_start_service.setVisible(True)
        else:
            self.docker_icon.setPixmap(svg_icon("docker", SUCCESS, 28).pixmap(28, 28))
            kind = "Docker Desktop" if info.is_desktop else "Docker Engine"
            wsl = " (WSL2)" if info.is_wsl else ""
            root = " · root" if getattr(info, "is_root", False) else ""
            self.lbl_docker_title.setText(f"{kind} {info.version}{wsl}{root} · listo")
            if info.bridge_ip_reachable:
                net = "Red: bridge — la IP del contenedor es accesible directamente (como auto_deploy.sh)."
            else:
                net = ("Red: los puertos EXPOSE de cada máquina se publican en 127.0.0.1 "
                       "(la IP interna no es accesible desde Docker Desktop).")
            self.lbl_docker_sub.setText(f"Servidor {info.server_os}/{info.server_arch}  ·  {net}")
            self.lbl_docker_sub.setStyleSheet(f"color: {FG_MUTED}; font-size: 11px;")

    def render_labs(self, labs) -> None:
        existing = {s.machine for s in labs}
        for m in list(self._widgets):
            if m not in existing:
                w = self._widgets.pop(m)
                self.list_layout.removeWidget(w)
                w.setParent(None)
                w.deleteLater()
        for st in labs:
            w = self._widgets.get(st.machine)
            if w is None:
                w = LabItemWidget(st.machine)
                w.action.connect(self._on_item_action)
                self._widgets[st.machine] = w
                self.list_layout.insertWidget(self.list_layout.count() - 1, w)
            w.update_state(st)
        n = len(labs)
        running = sum(1 for s in labs if s.phase == "running")
        self.empty.setVisible(n == 0)
        self.scroll.setVisible(n > 0)
        self.lbl_summary.setText(
            "Sin máquinas desplegadas" if n == 0
            else f"{n} máquina{'s' if n != 1 else ''} · {running} en ejecución"
        )

    def update_lab(self, st) -> None:
        w = self._widgets.get(st.machine)
        if w is not None:
            w.update_state(st)

    def _on_item_action(self, machine: str, action: str) -> None:
        if action == "copy_ip":
            w = self._widgets.get(machine)
            if w and w.ip():
                QGuiApplication.clipboard().setText(w.ip())
        self.request_action.emit(machine, action)
