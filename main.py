"""Aplicación PyQt6 — DockerLabs GUI.

Cambios v0.5:
- Toasts in-app animados (esquina inferior derecha).
- Notificaciones del SO opcionales (winotify/notify-send/osascript/plyer).
- Página Ajustes funcional: cambiar carpeta de descargas + toggles.
- Combos de filtros disimulados.
- Descargas HTTP directas desde gestion-maquinas.dockerlabs.es (con reintentos).
"""
from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QWidget,
)

from app_logging import install_excepthook, setup_logging
from completed_store import CompletedStore
from dockerlabs_api import (
    AuthResult,
    DockerLabsError,
    build_session_payload,
    load_env,
    save_env,
)
from dockerlabs_api_ext import DockerLabsExtClient
from download_manager import DownloadManager
from lab_controller import LabController
from notifier import notify_os, os_backend_available
from settings_store import SettingsStore, UserSettings
from theme import QSS
from widgets.icons import icon as svg_icon
from widgets.lab_page import LabPage
from widgets.pages import (
    AboutPage,
    CompletedPage,
    DashboardPage,
    DownloadsPage,
    ExportWorker,
    MachinesPage,
    SessionPage,
    SettingsPage,
)
from widgets.sidebar import Sidebar
from widgets.toast import ToastManager

logger = logging.getLogger(__name__)


APP_DIR = Path.home() / ".dockerlabs-gui"
APP_DIR.mkdir(parents=True, exist_ok=True)
CSV_DIR = APP_DIR / "csv"
CSV_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_DOWNLOADS_DIR = APP_DIR / "downloads"
DEFAULT_DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
LABS_DIR = APP_DIR / "labs"
SETTINGS_FILE = APP_DIR / "settings.json"
DEFAULT_CSV = CSV_DIR / "dockerlabs_maquinas.csv"
ENV_FILE = APP_DIR / ".env"
COMPLETED_FILE = APP_DIR / "completed.json"

# Compat con la versión anterior
LEGACY_CSV = Path.home() / ".dockerlabs-qt" / "csv" / "dockerlabs_maquinas.csv"


# -----------------------------------------------------------------------------
# Workers
# -----------------------------------------------------------------------------

class LoginWorker(QThread):
    success = pyqtSignal(str, str)
    failed = pyqtSignal(str)

    def __init__(self, client: DockerLabsExtClient, user: str, pwd: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client; self.user = user; self.pwd = pwd

    def run(self) -> None:
        try:
            res = self.client.login(self.user, self.pwd)
            if not res.success:
                self.failed.emit(res.message or "Login rechazado"); return
            profile = self.client.author_profile(self.user)
            self.success.emit(self.user, profile.get("profile_image_url", "") or "")
        except DockerLabsError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Error: {exc}")


class CompletedWorker(QThread):
    done = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, client: DockerLabsExtClient, parent=None) -> None:
        super().__init__(parent)
        self.client = client

    def run(self) -> None:
        try:
            self.done.emit(self.client.completed_machines_from_home())
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class ToggleWorker(QThread):
    done = pyqtSignal(str, bool)
    failed = pyqtSignal(str, str)

    def __init__(self, client: DockerLabsExtClient, name: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client; self.name = name

    def run(self) -> None:
        try:
            self.done.emit(self.name, self.client.toggle_completed(self.name))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(self.name, str(exc))


class SessionRestoreWorker(QThread):
    """Intenta reutilizar la cookie guardada en .env para auto-loguear."""
    success = pyqtSignal(str, str)       # username, profile_image_url
    failed = pyqtSignal(str)

    def __init__(self, client: DockerLabsExtClient, env_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.env_path = env_path

    def run(self) -> None:
        try:
            env = load_env(self.env_path)
            cookie = env.get("DOCKERLABS_SESSION") or ""
            csrf = env.get("DOCKERLABS_CSRF") or ""
            user_hint = env.get("DOCKERLABS_USERNAME") or ""
            if not cookie:
                self.failed.emit("no-cookie"); return
            self.client.inject_session_cookie(cookie)
            if csrf:
                self.client._csrf_token = csrf  # noqa: SLF001
            if not self.client.is_session_valid():
                self.failed.emit("expired"); return
            user = self.client.current_user_from_home() or user_hint
            if not user:
                self.failed.emit("no-user"); return
            # Refrescamos el CSRF actual desde la home autenticada para que
            # las acciones de toggle funcionen.
            try:
                self.client.fetch_root_csrf()
            except Exception as exc:  # noqa: BLE001
                logger.debug("fetch_root_csrf: %s", exc)
            profile_url = ""
            try:
                profile = self.client.author_profile(user)
                profile_url = profile.get("profile_image_url", "") or ""
            except Exception as exc:  # noqa: BLE001
                logger.debug("author_profile(%s): %s", user, exc)
            self.success.emit(user, profile_url)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class SyncCompletedWorker(QThread):
    """Combina las completadas locales con las del servidor.

    1. Lee las completadas del servidor.
    2. Las locales que no estaban en el servidor se envían con toggle.
    3. Emite el set final unificado.
    """
    done = pyqtSignal(list, list)   # final_list, newly_pushed
    failed = pyqtSignal(str)

    def __init__(self, client: DockerLabsExtClient, local: set[str], parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.local = set(local)

    def run(self) -> None:
        try:
            server = set(self.client.completed_machines_from_home())
            to_push = sorted(self.local - server)
            pushed: list[str] = []
            for name in to_push:
                try:
                    new_state = self.client.toggle_completed(name)
                    if new_state:
                        pushed.append(name)
                except Exception:  # noqa: BLE001
                    # Si una falla, seguimos con las demás
                    continue
            final = sorted(server | set(pushed))
            self.done.emit(final, pushed)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


# -----------------------------------------------------------------------------
# Main window
# -----------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DockerLabs GUI")
        self.resize(1180, 760); self.setMinimumSize(960, 620)
        self.setWindowIcon(svg_icon("machines", "#22d3ee", 64))

        self.settings_store = SettingsStore(SETTINGS_FILE)
        self.settings: UserSettings = self.settings_store.load()
        if not self.settings.downloads_dir:
            self.settings.downloads_dir = str(DEFAULT_DOWNLOADS_DIR)
            self.settings_store.save(self.settings)
        self._os_backend = os_backend_available()

        self.client = DockerLabsExtClient()
        self._username: Optional[str] = None
        self._completed: set[str] = set()
        self._workers: list[QThread] = []
        self._completed_store = CompletedStore(COMPLETED_FILE)
        # Cargamos las completadas anónimas como base inicial (se mostrarán
        # incluso sin sesión y se fusionarán con las del servidor al loguear).
        self._completed = self._completed_store.all_for_user(None)

        # ---- layout ----
        root = QWidget(); root.setObjectName("rootWidget")
        h = QHBoxLayout(root); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.nav_changed.connect(self._go)
        self.sidebar.login_clicked.connect(self._open_session_page)
        self.sidebar.logout_clicked.connect(self._do_logout)
        # Tambien al hacer click en el avatar/pill
        self.sidebar.profile_clicked.connect(self._open_session_page)
        h.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        h.addWidget(self.stack, 1)

        # download manager
        self.downloads = DownloadManager(Path(self.settings.downloads_dir),
                                         max_concurrent=self.settings.max_concurrent_downloads,
                                         parent=self)
        self.downloads.state_changed.connect(self._on_dm_state)
        self.downloads.list_changed.connect(self._on_dm_list)
        self.downloads.download_completed.connect(self._on_dm_completed)
        self.downloads.download_failed.connect(self._on_dm_failed)

        # lab (Docker) controller
        self.labs = LabController(LABS_DIR, network_preference=self.settings.docker_network,
                                  parent=self)
        self.labs.docker_info_changed.connect(self._on_docker_info)
        self.labs.list_changed.connect(self._on_labs_list)
        self.labs.lab_changed.connect(self._on_lab_changed)
        self.labs.lab_started.connect(self._on_lab_started)
        self.labs.lab_failed.connect(self._on_lab_failed)
        self.labs.lab_action_done.connect(self._on_lab_action_done)
        self.labs.elevation_started.connect(self._on_elevation_started)
        self.labs.elevation_done.connect(self._on_elevation_done)

        # páginas
        self.page_dashboard = DashboardPage()
        self.page_machines = MachinesPage(DEFAULT_CSV)
        self.page_downloads = DownloadsPage()
        self.page_lab = LabPage()
        self.page_completed = CompletedPage()
        self.page_session = SessionPage()
        self.page_settings = SettingsPage()
        self.page_about = AboutPage()
        # 'session' no aparece en el sidebar (se accede desde el pill)
        # pero sigue siendo una página del stack accesible por _go.
        self._pages = {
            "dashboard": self.page_dashboard,
            "machines":  self.page_machines,
            "downloads": self.page_downloads,
            "lab":       self.page_lab,
            "completed": self.page_completed,
            "settings":  self.page_settings,
            "about":     self.page_about,
            "session":   self.page_session,
        }
        for w in self._pages.values():
            self.stack.addWidget(w)

        # wiring
        self.page_machines.request_toggle_completed.connect(self._toggle_completed)
        self.page_machines.request_download.connect(self._start_download)
        self.page_machines.request_cancel_download.connect(self._cancel_download)
        self.page_downloads.request_cancel.connect(self._cancel_download)
        self.page_downloads.request_remove.connect(self._remove_download)
        self.page_downloads.request_open.connect(self._open_download_folder)
        self.page_machines.request_launch.connect(self._launch_lab)
        self.page_lab.request_action.connect(self._lab_action)
        self.page_lab.request_refresh.connect(self._refresh_docker)
        self.page_lab.request_go_machines.connect(lambda: self._go("machines"))
        self.page_lab.request_grant_access.connect(self._grant_docker_access)
        self.page_lab.request_start_service.connect(self._start_docker_service)
        self.page_settings.request_set_docker_network.connect(self._set_docker_network)
        self.page_session.request_login.connect(self._do_login)
        self.page_session.request_logout.connect(self._do_logout)
        self.page_completed.request_refresh.connect(self._refresh_completed)
        self.page_settings.request_change_downloads_dir.connect(self._set_downloads_dir)
        self.page_settings.request_set_os_notifications.connect(self._set_os_notifications)
        self.page_settings.request_set_in_app_notifications.connect(self._set_in_app_notifications)
        self.page_settings.request_open_downloads_dir.connect(self._open_downloads_dir)

        self.setCentralWidget(root)
        # No usamos QStatusBar: la barra inferior se sustituye por toasts.
        # Sobreescribimos `statusBar()` con un objeto silencioso para que las
        # llamadas existentes a `self.statusBar().showMessage(...)` no fallen.
        class _SilentStatus:
            def showMessage(self, *_args, **_kwargs):
                pass
            def clearMessage(self):
                pass
        self._silent_status = _SilentStatus()

        # toasts (en el central widget para que se posicionen relativos)
        self.toasts = ToastManager(root)

        # estado inicial de la página Ajustes
        self._refresh_settings_page()

        # arranque
        # Pintamos primero las completadas locales (anónimas) para que
        # estén visibles desde el principio.
        self.page_machines.set_completed(self._completed)
        self.page_completed.set_items(self._completed)
        self.page_dashboard.set_done(len(self._completed))

        self._load_cached_csv()
        self._refresh_csv_background()
        self._refresh_downloaded_state()
        # Auto-login si hay sesión guardada
        self._try_restore_session()
        # Docker
        self._refresh_docker()

    # ---- Navegación ----

    def _go(self, key: str) -> None:
        widget = self._pages.get(key)
        if widget is None:
            return
        self.stack.setCurrentWidget(widget)
        # Si la página destino no está en el sidebar (p.ej. 'session'),
        # se desmarcan los botones del menú.
        self.sidebar.set_current(key)

    def _open_session_page(self) -> None:
        self._go("session")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        try:
            self.toasts.reposition()
        except RuntimeError as exc:  # widget ya destruido durante el cierre
            logger.debug("toasts.reposition: %s", exc)

    def statusBar(self):  # type: ignore[override]
        """Devolvemos un stub silencioso en lugar de la QStatusBar nativa."""
        return self._silent_status

    # ---- Notificaciones ----

    def notify(self, title: str, body: str = "", kind: str = "info",
               on_click=None, payload=None) -> None:
        """Dispara toast y/o notificación del SO según preferencias."""
        if self.settings.in_app_notifications:
            try:
                self.toasts.show(title, body, kind=kind,
                                 on_click=on_click, payload=payload)
            except Exception:  # noqa: BLE001
                logger.exception("toast in-app")
        if self.settings.os_notifications and self._os_backend:
            try:
                notify_os(title, body, app_id="DockerLabs GUI")
            except Exception as exc:  # noqa: BLE001
                logger.warning("notificación del SO: %s", exc)

    # ---- CSV ----

    def _load_cached_csv(self) -> None:
        loaded = False
        if DEFAULT_CSV.exists():
            self.page_machines.load_csv(DEFAULT_CSV)
            loaded = True
        elif LEGACY_CSV.exists():
            self.page_machines.load_csv(LEGACY_CSV)
            loaded = True
        else:
            self.page_machines.lbl_count.setText("Sin CSV. Descargando catálogo…")
        self._update_totals_from_csv()
        # Cruzar archivos de la carpeta de descargas con los nombres reales
        # del CSV para que las maquinas ya bajadas aparezcan como
        # 'descargadas' y no se vuelvan a descargar.
        if loaded:
            self._reconcile_downloads_with_csv()

    def _refresh_csv_background(self) -> None:
        worker = ExportWorker(self.client, str(CSV_DIR), "dockerlabs", parent=self)
        worker.done.connect(self._on_csv_refreshed)
        worker.failed.connect(self._on_csv_refresh_failed)
        worker.finished.connect(lambda: self._workers.remove(worker)
                                if worker in self._workers else None)
        self._workers.append(worker); worker.start()
        self.statusBar().showMessage("Actualizando catálogo…")

    def _on_csv_refreshed(self, summary: dict) -> None:
        path = Path(summary["files"]["machines_csv"])
        self.page_machines.load_csv(path)
        self._update_totals_from_csv()
        self.statusBar().showMessage(
            f"Catálogo actualizado · {summary['machine_count']} máquinas"
        )
        self._reconcile_downloads_with_csv()

    def _on_csv_refresh_failed(self, err: str) -> None:
        msg = "Sin conexión: usando catálogo cacheado"
        if not DEFAULT_CSV.exists() and not LEGACY_CSV.exists():
            msg = f"Sin internet y sin CSV cacheado: {err}"
            self.page_machines.lbl_count.setText(msg)
            self.notify("Sin catálogo", msg, kind="warning")
        self.statusBar().showMessage(msg)

    # ---- Sesión ----

    def _try_restore_session(self) -> None:
        if not ENV_FILE.exists():
            return
        worker = SessionRestoreWorker(self.client, ENV_FILE, parent=self)
        worker.success.connect(self._on_restore_ok)
        worker.failed.connect(self._on_restore_fail)
        worker.finished.connect(lambda: self._workers.remove(worker)
                                if worker in self._workers else None)
        self._workers.append(worker); worker.start()

    def _on_restore_ok(self, username: str, profile_url: str) -> None:
        self.notify("Sesión restaurada", f"Bienvenido de nuevo, {username}", kind="info")
        self._apply_login(username, profile_url, sync_anonymous=True)

    def _on_restore_fail(self, reason: str) -> None:
        if reason == "no-cookie":
            return  # no había nada que restaurar, silencio total
        self.statusBar().showMessage("Sesión guardada expirada; inicia sesión de nuevo.")

    def _do_login(self, user: str, pwd: str) -> None:
        if not user or not pwd:
            self.notify("Faltan datos", "Usuario y contraseña obligatorios", kind="warning")
            return
        self.statusBar().showMessage("Autenticando…")
        # Toast in-app de 'autenticando' (SOLO in-app, nunca al SO).
        # Lo emitimos directamente con el ToastManager para saltarnos
        # los ajustes de notificaciones.
        try:
            self.toasts.show("Autenticando…",
                             f"Conectando como {user}", kind="info")
        except Exception:  # noqa: BLE001
            logger.exception("toast autenticando")
        # Nuevo cliente para evitar arrastrar cookies viejas
        self.client = DockerLabsExtClient()
        worker = LoginWorker(self.client, user, pwd, parent=self)
        worker.success.connect(self._on_login_ok)
        worker.failed.connect(self._on_login_fail)
        worker.finished.connect(lambda: self._workers.remove(worker)
                                if worker in self._workers else None)
        self._workers.append(worker); worker.start()

    def _on_login_ok(self, username: str, profile_url: str) -> None:
        self.statusBar().showMessage(f"Sesión iniciada como {username}")
        self.notify("Sesión iniciada", f"Bienvenido, {username}", kind="success")
        # Persistir la sesión en .env (cookie + csrf)
        try:
            cookie = self.client.session_cookie_value() or ""
            csrf = self.client.csrf_token or ""
            res = AuthResult(
                success=True, message="", redirect_url=None,
                username=username, session_cookie=cookie, csrf_token=csrf,
                raw_response={},
            )
            save_env(ENV_FILE, build_session_payload(res, self.client.base_url))
        except Exception:  # noqa: BLE001
            logger.exception("no se pudo guardar la sesión en %s", ENV_FILE)
        self._apply_login(username, profile_url, sync_anonymous=True)

    def _on_login_fail(self, msg: str) -> None:
        self.statusBar().showMessage("Login fallido")
        self.notify("Login fallido", msg, kind="error")

    def _apply_login(self, username: str, profile_url: str, sync_anonymous: bool) -> None:
        """Aplica el estado lógico de 'logueado' tras un login nuevo o restaurado."""
        self._username = username
        self.sidebar.set_logged_in(username)
        self.page_dashboard.set_session(username)
        self.page_session.set_logged_in(username, len(self._completed))
        if profile_url:
            self.sidebar.avatar.fetch_image(self.client, profile_url)
            # También para el avatar grande de la página Sesión
            self._fetch_big_avatar(profile_url)
        # Migrar las completadas anónimas (si las hay) al usuario:
        if sync_anonymous:
            anon = self._completed_store.drain_anonymous()
            if anon:
                self._completed_store.merge_into_user(username, anon)
        # Base local del usuario
        local_user = self._completed_store.all_for_user(username)
        self._completed = set(local_user)
        self.page_machines.set_completed(self._completed)
        self.page_completed.set_items(self._completed)
        self.page_dashboard.set_done(len(self._completed))
        self.page_session.update_stats(len(self._completed))
        # Sincronizar con servidor en background
        self._sync_completed_with_server()

    def _fetch_big_avatar(self, profile_url: str) -> None:
        """Reutiliza el fetcher del avatar pequeño para también alimentar el grande."""
        try:
            from widgets.avatar import _AvatarFetcher
            if not profile_url.startswith("http"):
                profile_url = self.client.base_url.rstrip("/") + profile_url
            fetcher = _AvatarFetcher(self.client, profile_url, self.page_session)
            fetcher.finished_data.connect(
                lambda data, _u: self.page_session.set_avatar_pixmap(bytes(data))
            )
            fetcher.finished.connect(
                lambda: self._workers.remove(fetcher) if fetcher in self._workers else None
            )
            self._workers.append(fetcher)
            fetcher.start()
        except Exception:  # noqa: BLE001
            logger.exception("no se pudo lanzar la sincronización de completadas")

    def _do_logout(self) -> None:
        # Borrar la sesión persistente para que al reiniciar NO se auto-loguee
        try:
            if ENV_FILE.exists():
                save_env(ENV_FILE, {
                    "DOCKERLABS_USERNAME": "",
                    "DOCKERLABS_SESSION": "",
                    "DOCKERLABS_CSRF": "",
                    "DOCKERLABS_LOGIN_AT": "",
                    "DOCKERLABS_BASE_URL": self.client.base_url,
                })
        except Exception:  # noqa: BLE001
            logger.exception("no se pudo limpiar la sesión persistente")
        self.client = DockerLabsExtClient()
        self._username = None
        # No vaciamos el set local; el usuario sigue viendo lo que tenía
        # marcado offline. Lo que sí reseteamos es el 'visible'.
        self._completed = self._completed_store.all_for_user(None)
        self.sidebar.set_logged_out()
        self.page_dashboard.set_session(None)
        self.page_dashboard.set_done(len(self._completed))
        self.page_machines.set_completed(self._completed)
        self.page_completed.set_items(self._completed)
        self.page_session.set_logged_out()
        self.statusBar().showMessage("Sesión cerrada")
        self.notify("Sesión cerrada", kind="info")
        # Tras cerrar sesión, llevamos al usuario al dashboard
        self._go("dashboard")

    # ---- Completadas ----

    def _refresh_completed(self) -> None:
        """Refresco manual (botón 'Sincronizar' de la página Completadas).

        Si no hay sesión, mostramos sólo un toast informativo (sin
        redirigir al login: el usuario decide cuándo ir).
        """
        if not self._username:
            self.notify("Inicia sesión",
                        "Necesitas iniciar sesión para sincronizar con DockerLabs.",
                        kind="warning")
            return
        self._sync_completed_with_server()

    def _sync_completed_with_server(self) -> None:
        if not self._username:
            return
        self.statusBar().showMessage("Sincronizando completadas con DockerLabs…")
        worker = SyncCompletedWorker(self.client, self._completed, parent=self)
        worker.done.connect(self._on_sync_done)
        worker.failed.connect(self._on_sync_fail)
        worker.finished.connect(lambda: self._workers.remove(worker)
                                if worker in self._workers else None)
        self._workers.append(worker); worker.start()

    def _on_sync_done(self, final: list, pushed: list) -> None:
        self._completed = set(final)
        if self._username:
            self._completed_store.set_for_user(self._username, self._completed)
        self.page_machines.set_completed(self._completed)
        self.page_completed.set_items(self._completed)
        self.page_dashboard.set_done(len(self._completed))
        self.page_session.update_stats(len(self._completed))
        if pushed:
            self.statusBar().showMessage(
                f"{len(self._completed)} completadas · {len(pushed)} subidas al servidor"
            )
            self.notify("Completadas sincronizadas",
                        f"Se subieron {len(pushed)} máquinas locales al servidor.",
                        kind="success")
        else:
            self.statusBar().showMessage(f"{len(self._completed)} completadas")

    def _on_sync_fail(self, err: str) -> None:
        self.statusBar().showMessage(f"Error sincronizando: {err}")
        self.notify("Error sincronizando", err, kind="error")

    def _on_completed_done(self, names: list[str]) -> None:
        self._completed = set(names)
        self.page_machines.set_completed(self._completed)
        self.page_completed.set_items(self._completed)
        self.page_dashboard.set_done(len(self._completed))
        self.statusBar().showMessage(f"{len(self._completed)} máquinas completadas")

    def _toggle_completed(self, name: str) -> None:
        if not self._username:
            # Sin sesión: marcamos/desmarcamos en local; al iniciar sesión
            # estas marcas se sincronizarán con el servidor.
            if name in self._completed:
                self._completed.discard(name)
                self._completed_store.remove(None, name)
                self.notify("Desmarcada (local)",
                            f"{name} desmarcada localmente. Inicia sesión para sincronizar.",
                            kind="info")
            else:
                self._completed.add(name)
                self._completed_store.add(None, name)
                self.notify("Completada (local)",
                            f"{name} marcada localmente. Se subirá al iniciar sesión.",
                            kind="info")
            self.page_machines.set_completed(self._completed)
            self.page_completed.set_items(self._completed)
            self.page_dashboard.set_done(len(self._completed))
            return
        self.statusBar().showMessage(f"Alternando estado de {name}…")
        worker = ToggleWorker(self.client, name, parent=self)
        worker.done.connect(self._on_toggle_ok)
        worker.failed.connect(self._on_toggle_fail)
        worker.finished.connect(lambda: self._workers.remove(worker)
                                if worker in self._workers else None)
        self._workers.append(worker); worker.start()

    def _on_toggle_ok(self, name: str, new_state: bool) -> None:
        if new_state:
            self._completed.add(name)
            if self._username:
                self._completed_store.add(self._username, name)
        else:
            self._completed.discard(name)
            if self._username:
                self._completed_store.remove(self._username, name)
        self.page_machines.set_completed(self._completed)
        self.page_completed.set_items(self._completed)
        self.page_dashboard.set_done(len(self._completed))
        self.page_session.update_stats(len(self._completed))
        verb_long  = "marcada como completada" if new_state else "desmarcada"
        verb_short = "Completada" if new_state else "Desmarcada"
        self.statusBar().showMessage(f"{name} {verb_long}")
        self.notify(f"{verb_short}: {name}", kind="success" if new_state else "info")

    def _on_toggle_fail(self, name: str, msg: str) -> None:
        self.statusBar().showMessage(f"Error toggle {name}: {msg}")
        self.notify(f"Error con {name}", msg, kind="error")

    # ---- Descargas ----

    def _start_download(self, name: str, url: str) -> None:
        if not url:
            self.notify("Sin enlace de descarga",
                        f"{name} no tiene enlace de descarga en el catálogo.", kind="warning")
            return
        # Si ya esta en disco, no relanzar la descarga: abrir la carpeta.
        existing = self.downloads.downloaded_paths().get(name)
        if existing and existing.exists():
            self.notify("Ya descargada",
                        f"{name} ya está en {existing.parent}.", kind="info")
            self._open_in_explorer(existing)
            return
        ok = self.downloads.start(name, url)
        if not ok:
            # start() devuelve False tambien si acaba de detectar que ya estaba
            # descargada (red de seguridad).
            existing = self.downloads.downloaded_paths().get(name)
            if existing and existing.exists():
                self.notify("Ya descargada",
                            f"{name} ya está en {existing.parent}.", kind="info")
                self._open_in_explorer(existing)
            else:
                self.notify("Descarga en curso",
                            f"Ya hay una descarga activa para {name}.", kind="info")
            self._refresh_downloaded_state()
            return
        self._refresh_downloaded_state()
        self.statusBar().showMessage(f"Descargando {name}…")
        self.notify("Descarga iniciada", name, kind="info")
        self._go("downloads")

    def _cancel_download(self, name: str) -> None:
        self.downloads.cancel(name)
        self.statusBar().showMessage(f"Cancelando descarga de {name}…")

    def _remove_download(self, name: str) -> None:
        self.downloads.remove(name, also_delete_file=False)
        self._refresh_downloaded_state()

    def _open_download_folder(self, name: str) -> None:
        """Abre la carpeta donde está el .zip de la máquina.

        En Linux usa xdg-open / nautilus; en Windows usa el explorador con
        ``/select,<file>`` para resaltar el archivo; en macOS usa ``open -R``.
        Como red de seguridad cae a ``QDesktopServices`` si falla todo.
        """
        # 1) state de la sesión actual; 2) archivo persistido en _completed_paths
        target: Optional[Path] = None
        state = self.downloads.state(name)
        if state and state.final_path and state.final_path.exists():
            target = state.final_path
        else:
            target = self.downloads.downloaded_paths().get(name)
        if target is None or not target.exists():
            # Último recurso: abrir la carpeta de descargas tal cual
            self._open_in_explorer(self.downloads.dest_dir)
            self.notify("Archivo no encontrado",
                        f"No se encontró el .zip de {name} en disco.",
                        kind="warning")
            return
        self._open_in_explorer(target)

    def _open_downloads_dir(self) -> None:
        self._open_in_explorer(self.downloads.dest_dir)

    def _open_in_explorer(self, target: Path) -> bool:
        """Abre el explorador de archivos en `target`. Si es un archivo, lo
        resalta dentro de su carpeta cuando el SO lo soporta. Devuelve True
        si lo consiguió con un método nativo.

        Orden de preferencia en Linux:
          1) gestores con bandera ``--select`` (Nautilus, Dolphin, Nemo)
             que resaltan el fichero,
          2) ``xdg-open`` sobre la carpeta padre,
          3) ``QDesktopServices`` como red de seguridad.
        """
        target = Path(target).expanduser()
        if not target.exists():
            # Si el fichero no existe, intentamos al menos abrir la carpeta
            # que debería contenerlo. Así nunca se queda en silencio.
            target = target.parent if target.parent.exists() else Path.home()

        def _try(cmd: list[str]) -> bool:
            """Lanza un comando capturando stdout/stderr para no contaminar la
            consola y filtrando FileNotFoundError silenciosamente."""
            try:
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return True
            except (FileNotFoundError, PermissionError, OSError):
                return False

        def _has(bin_name: str) -> bool:
            from shutil import which
            return which(bin_name) is not None

        system = platform.system()
        try:
            if target.is_file():
                if system == "Windows":
                    # /select resalta el archivo dentro de la carpeta
                    if _try(["explorer", f"/select,{target}"]):
                        return True
                elif system == "Darwin":
                    if _try(["open", "-R", str(target)]):
                        return True
                else:
                    # Linux: primero managers con --select (resaltan)
                    if _has("nautilus") and _try(["nautilus", "--select", str(target)]):
                        return True
                    if _has("dolphin") and _try(["dolphin", "--select", str(target)]):
                        return True
                    if _has("nemo") and _try(["nemo", str(target)]):
                        return True
                    if _has("caja") and _try(["caja", "--no-desktop", str(target.parent)]):
                        return True
                    if _has("thunar") and _try(["thunar", str(target.parent)]):
                        return True
                    if _has("pcmanfm") and _try(["pcmanfm", str(target.parent)]):
                        return True
                    # Sin manager específico: xdg-open sobre la carpeta
                    if _has("xdg-open") and _try(["xdg-open", str(target.parent)]):
                        return True
            else:
                # Es una carpeta (o un fichero inexistente: fallback)
                folder = target if target.is_dir() else target.parent
                if system == "Windows":
                    try:
                        os.startfile(str(folder))  # type: ignore[attr-defined]
                        return True
                    except OSError:
                        if _try(["explorer", str(folder)]):
                            return True
                elif system == "Darwin":
                    if _try(["open", str(folder)]):
                        return True
                else:
                    if _has("xdg-open") and _try(["xdg-open", str(folder)]):
                        return True
                    if _has("nautilus") and _try(["nautilus", str(folder)]):
                        return True
                    if _has("dolphin") and _try(["dolphin", str(folder)]):
                        return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("abrir explorador nativo: %s", exc)

        # Red de seguridad: QDesktopServices con file:// URI
        try:
            folder = target if target.is_dir() else target.parent
            ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
            return bool(ok)
        except Exception:  # noqa: BLE001
            return False

    def _on_dm_state(self, machine: str) -> None:
        self.page_downloads.render_states(self.downloads.all_states())

    def _on_dm_list(self) -> None:
        self.page_downloads.render_states(self.downloads.all_states())
        self._refresh_downloaded_state()

    def _on_dm_completed(self, machine: str, final_path: str) -> None:
        self.statusBar().showMessage(f"Descarga terminada: {machine}")
        # Toast clicable: al pulsar abre la carpeta donde está el .zip.
        self.notify(
            "Descarga terminada",
            f"{machine}  ·  Pulsa para abrir la carpeta",
            kind="success",
            on_click=lambda path=final_path: self._open_path(path),
            payload=final_path,
        )
        self._refresh_downloaded_state()

    def _on_dm_failed(self, machine: str, kind: str, error_msg: str) -> None:
        """Una descarga ha fallado (tras los reintentos internos)."""
        titles = {
            "not_found": "Máquina no disponible",
            "unsupported": "Enlace no soportado",
            "integrity": "Archivo corrupto",
            "server": "Servidor de DockerLabs no responde",
        }
        title = titles.get(kind, "Error de descarga")
        first_line = (error_msg or "").splitlines()[0] if error_msg else ""
        self.statusBar().showMessage(f"{title}: {machine}")
        self.notify(title, f"{machine}: {first_line}", kind="error")
        self._refresh_downloaded_state()

    def _open_path(self, path: str) -> None:
        """Abre la carpeta del fichero descargado en el explorador del SO."""
        try:
            self._open_in_explorer(Path(path))
        except Exception:  # noqa: BLE001
            logger.exception("abrir ruta %s", path)

    def _refresh_downloaded_state(self) -> None:
        states = self.downloads.all_states()
        downloading = {s.machine for s in states
                       if s.state in ("running", "verifying", "queued")}
        downloaded = set(self.downloads.downloaded_paths().keys())
        for s in states:
            if s.state == "done":
                downloaded.add(s.machine)
        self.page_machines.set_downloading(downloading)
        self.page_machines.set_downloaded(downloaded)
        self.page_dashboard.set_downloads(len(downloaded))

    def _reconcile_downloads_with_csv(self) -> None:
        """Cruza los archivos en la carpeta de descargas con los nombres
        reales del CSV. Tras esto, las máquinas ya bajadas aparecen como
        descargadas en la lista de Máquinas, pero NO en la página
        'Descargas' (esa solo muestra las de la sesión actual)."""
        try:
            rows = getattr(self.page_machines, "_all_rows", [])
            names = [r.get("nombre", "") for r in rows if r.get("nombre")]
            if names:
                self.downloads.reconcile_with_catalog(names)
        except Exception:  # noqa: BLE001
            logger.exception("reconcile_with_catalog")
        self._refresh_downloaded_state()

    # ---- Laboratorio (Docker) ----

    def _refresh_docker(self) -> None:
        self.page_lab.set_docker_info(None, "")
        self.labs.refresh_docker_info()

    def _on_docker_info(self, info) -> None:
        self.page_lab.set_docker_info(info, self.labs.install_hint(),
                                      self.labs.permission_hint())
        self.page_dashboard.set_docker(info)

    # ---- Permisos de Docker (Linux: pkexec / sudo -A) ----

    def _grant_docker_access(self) -> None:
        info = self.labs.docker_info
        if not info.needs_elevation:
            self.notify("No hace falta", "Tu usuario ya puede usar Docker.", kind="info")
            return
        if not self.labs.grant_access():
            self.notify("Ya hay una autorización en curso", kind="info")

    def _start_docker_service(self) -> None:
        if not self.labs.start_service():
            self.notify("Ya hay una autorización en curso", kind="info")

    def _on_elevation_started(self, action: str) -> None:
        self.page_lab.set_elevating(True)

    def _on_elevation_done(self, action: str, ok: bool, detail: str) -> None:
        self.page_lab.set_elevating(False)
        titles = {"grant": "Acceso a Docker", "start_service": "Servicio Docker"}
        title = titles.get(action, action)
        if ok:
            self.notify(title, detail.splitlines()[0] if detail else "Hecho", kind="success")
        else:
            self.notify(f"{title}: no se pudo", detail.splitlines()[0] if detail else "",
                        kind="warning" if "cancelad" in detail.lower() else "error")

    def _on_labs_list(self) -> None:
        labs = self.labs.all_labs()
        self.page_lab.render_labs(labs)
        self.page_machines.set_running({s.machine for s in labs if s.phase == "running"})
        self.page_dashboard.set_labs_running(sum(1 for s in labs if s.phase == "running"))

    def _on_lab_changed(self, machine: str) -> None:
        st = self.labs.lab(machine)
        if st is not None:
            self.page_lab.update_lab(st)

    def _launch_lab(self, name: str) -> None:
        info = self.labs.docker_info
        if not info.available:
            self.notify("Docker no está instalado", self.labs.install_hint().splitlines()[0],
                        kind="error")
            self._go("lab")
            return
        if info.needs_elevation:
            self._go("lab")
            if info.can_elevate:
                res = QMessageBox.question(
                    self, "Permiso para usar Docker",
                    "Tu usuario no puede usar Docker sin sudo.\n\n"
                    "¿Conceder acceso ahora? Se abrirá el diálogo de autenticación del sistema "
                    "y se añadirá tu usuario al grupo docker.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if res == QMessageBox.StandardButton.Yes:
                    self._grant_docker_access()
            else:
                self.notify("Sin permiso para usar Docker",
                            "Ejecuta: sudo usermod -aG docker $USER (y reinicia sesión)",
                            kind="error")
            return
        if not info.running:
            self.notify("Docker no responde", (info.error or "").splitlines()[0], kind="error")
            self._go("lab")
            return
        zip_path = self.downloads.downloaded_paths().get(name)
        if zip_path is None:
            self.notify("Primero descarga la máquina",
                        f"{name} no está en la carpeta de descargas.", kind="warning")
            return
        if not self.labs.launch(name, zip_path):
            self.notify("Ya en marcha", f"{name} ya se está desplegando.", kind="info")
            return
        self.notify("Desplegando laboratorio", name, kind="info")
        self._go("lab")

    def _lab_action(self, machine: str, action: str) -> None:
        if action == "copy_ip":
            self.notify("IP copiada", "La IP de la máquina está en el portapapeles.", kind="success")
        elif action == "shell":
            if not self.labs.open_shell(machine):
                st = self.labs.lab(machine)
                cmd = " ".join(self.labs.client.exec_shell_command(st.container_name)) if st else ""
                self.notify("No se pudo abrir la terminal",
                            f"Ejecuta manualmente: {cmd}", kind="warning")
        elif action == "start":
            self.labs.start(machine)
        elif action == "stop":
            self.labs.stop(machine)
        elif action == "restart":
            self.labs.restart(machine)
        elif action == "cancel":
            self.labs.cancel_launch(machine)
        elif action == "remove":
            res = QMessageBox.question(
                self, "Eliminar laboratorio",
                f"¿Eliminar el contenedor y la imagen Docker de «{machine}»?\n"
                "El .zip descargado se conserva; podrás volver a lanzarla.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if res == QMessageBox.StandardButton.Yes:
                self.labs.remove(machine, remove_image=True)

    def _on_lab_started(self, machine: str, access_text: str) -> None:
        self.notify("Laboratorio en marcha", f"{machine}\n{access_text}", kind="success",
                    on_click=lambda _p: self._go("lab"), payload=machine)

    def _on_lab_failed(self, machine: str, error: str) -> None:
        self.notify(f"Error en {machine}", error.splitlines()[0] if error else "", kind="error")

    def _on_lab_action_done(self, machine: str, action: str) -> None:
        verbs = {"stop": "detenida", "start": "iniciada", "restart": "reiniciada",
                 "remove": "eliminada"}
        self.notify(f"{machine} {verbs.get(action, action)}", kind="info")

    def _set_docker_network(self, mode: str) -> None:
        self.settings.docker_network = mode or "auto"
        self.settings_store.save(self.settings)
        self.labs.set_network_preference(self.settings.docker_network)

    # ---- Ajustes ----

    def _refresh_settings_page(self) -> None:
        self.page_settings.set_state(
            downloads_dir=str(self.downloads.dest_dir),
            os_notifications=self.settings.os_notifications,
            in_app_notifications=self.settings.in_app_notifications,
            os_backend_available=self._os_backend,
            docker_network=self.settings.docker_network,
        )

    def _set_downloads_dir(self, path: str) -> None:
        try:
            self.downloads.set_dest_dir(Path(path))
            self.settings.downloads_dir = str(self.downloads.dest_dir)
            self.settings_store.save(self.settings)
            self._refresh_settings_page()
            self._refresh_downloaded_state()
            self.notify("Carpeta de descargas",
                        f"Nueva ubicación: {self.downloads.dest_dir}", kind="success")
        except Exception as exc:  # noqa: BLE001
            self.notify("Error", f"No se pudo cambiar la carpeta: {exc}", kind="error")

    def _set_os_notifications(self, enabled: bool) -> None:
        self.settings.os_notifications = bool(enabled) and self._os_backend
        self.settings_store.save(self.settings)
        if enabled and self._os_backend:
            self.notify("Notificaciones del SO activas",
                        "Verás los avisos también en el buzón del sistema.",
                        kind="success")

    def _set_in_app_notifications(self, enabled: bool) -> None:
        self.settings.in_app_notifications = bool(enabled)
        self.settings_store.save(self.settings)
        if enabled:
            self.toasts.show("Notificaciones in-app activas",
                             "Las verás aquí, en la esquina inferior.",
                             kind="info")

    # ---- Totales ----

    def _update_totals_from_csv(self) -> None:
        try:
            n = len(self.page_machines._all_rows)  # noqa: SLF001
            self.page_dashboard.set_total(n)
        except (AttributeError, TypeError) as exc:
            logger.debug("update_totals: %s", exc)

    # ---- Cierre ----

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        running = self.labs.running_labs()
        if running:
            names = ", ".join(s.machine for s in running)
            res = QMessageBox.question(
                self, "Laboratorios en ejecución",
                f"Hay {len(running)} laboratorio(s) en marcha: {names}.\n\n"
                "¿Quieres detenerlos antes de salir?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
            )
            if res == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if res == QMessageBox.StandardButton.Yes:
                self.labs.stop_all()
        try:
            self.labs.shutdown()
        except Exception as exc:  # noqa: BLE001
            logger.warning("labs.shutdown: %s", exc)
        try:
            self.downloads.shutdown()
        except Exception as exc:  # noqa: BLE001
            logger.warning("downloads.shutdown: %s", exc)
        for worker in list(self._workers):
            try:
                if worker.isRunning():
                    worker.quit(); worker.wait(1500)
            except RuntimeError:
                pass
        super().closeEvent(event)


def main() -> int:
    log_file = setup_logging(APP_DIR)
    logger.info("DockerLabs GUI arrancando · %s %s · Python %s",
                platform.system(), platform.release(), platform.python_version())
    app = QApplication(sys.argv)
    app.setApplicationName("DockerLabs GUI")
    app.setStyleSheet(QSS)
    win = MainWindow()
    install_excepthook(lambda: win)
    win.show()
    logger.debug("log en %s", log_file)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
