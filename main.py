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

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QCloseEvent, QDesktopServices, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QWidget,
)

import theme as _theme
from app_logging import install_excepthook, setup_logging
from catalog_controller import CatalogController
from download_manager import DownloadManager
from i18n import set_language, tr  # noqa: F401 - tr se usa al envolver textos
from lab_controller import LabController
from media_cache import MediaCache
from notifier import notify_os, os_backend_available
from session_controller import SessionController
from settings_store import SettingsStore, UserSettings
from theme import DEFAULT_THEME, THEMES, apply_theme
from widgets.icons import app_icon
from widgets.lab_page import LabPage
from widgets.pages import (
    AboutPage,
    CompletedPage,
    DashboardPage,
    DownloadsPage,
    MachinesPage,
    SessionPage,
    SettingsPage,
)
from widgets.sidebar import Sidebar
from widgets.toast import ToastManager
from workers import WorkerPool

logger = logging.getLogger(__name__)


APP_DIR = Path.home() / ".dockerlabs-gui"
APP_DIR.mkdir(parents=True, exist_ok=True)
CATALOG_FILE = APP_DIR / "catalog.json"
DEFAULT_DOWNLOADS_DIR = APP_DIR / "downloads"
DEFAULT_DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
LABS_DIR = APP_DIR / "labs"
SETTINGS_FILE = APP_DIR / "settings.json"
ENV_FILE = APP_DIR / ".env"
COMPLETED_FILE = APP_DIR / "completed.json"
CACHE_DIR = APP_DIR / "cache"          # imágenes y valoraciones de máquinas


# -----------------------------------------------------------------------------
# Workers
# -----------------------------------------------------------------------------

def _load_theme_pref() -> str:
    """Lee el tema guardado antes de crear la ventana (para no parpadear)."""
    try:
        return SettingsStore(SETTINGS_FILE).load().theme or DEFAULT_THEME
    except Exception:  # noqa: BLE001
        return DEFAULT_THEME


def _load_language_pref() -> str:
    """Preferencia de idioma (`auto|es|en`) antes de construir cualquier widget."""
    try:
        return SettingsStore(SETTINGS_FILE).load().language or "auto"
    except Exception:  # noqa: BLE001
        return "auto"


# -----------------------------------------------------------------------------
# Main window
# -----------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DockerLabs GUI")
        self.resize(1180, 760); self.setMinimumSize(960, 620)
        self.setWindowIcon(app_icon())

        self.settings_store = SettingsStore(SETTINGS_FILE)
        self.settings: UserSettings = self.settings_store.load()
        if not self.settings.downloads_dir:
            self.settings.downloads_dir = str(DEFAULT_DOWNLOADS_DIR)
            self.settings_store.save(self.settings)
        self._os_backend = os_backend_available()

        self._workers = WorkerPool()
        self._restart_requested = False
        # Sesión + completadas viven en su controlador (sin UI). Arranca con
        # las completadas anónimas locales como base.
        self.session = SessionController(ENV_FILE, COMPLETED_FILE, parent=self)
        self.session.logged_in.connect(self._on_logged_in)
        self.session.logged_out.connect(self._on_logged_out)
        self.session.completed_changed.connect(self._on_completed_changed)
        self.session.status.connect(lambda msg: self.statusBar().showMessage(msg))
        self.session.notify.connect(lambda t, b, k: self.notify(t, b, kind=k))
        self.session.login_started.connect(self._on_login_started)

        # ---- layout ----
        root = QWidget(); root.setObjectName("rootWidget")
        h = QHBoxLayout(root); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.nav_changed.connect(self._go)
        self.sidebar.login_clicked.connect(self._open_session_page)
        self.sidebar.logout_clicked.connect(self.session.logout)
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
        self.catalogs = CatalogController(
            CATALOG_FILE, lambda: self.session.client.fetch_api_data(), parent=self)
        self.catalogs.catalog_changed.connect(self._apply_catalog)
        self.catalogs.loading.connect(self._on_catalog_loading)
        self.catalogs.refresh_failed.connect(self._on_catalog_refresh_failed)
        self.media_cache = MediaCache(CACHE_DIR)
        self.page_machines = MachinesPage(client=self.session.client, media_cache=self.media_cache)
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
        self.page_machines.request_toggle_completed.connect(self.session.toggle)
        self.page_machines.request_download.connect(self._start_download)
        self.page_machines.request_cancel_download.connect(self._cancel_download)
        self.page_downloads.request_cancel.connect(self._cancel_download)
        self.page_downloads.request_remove.connect(self._remove_download)
        self.page_downloads.request_open.connect(self._open_download_folder)
        self.page_downloads.request_clear_finished.connect(self.downloads.clear_finished)
        self.page_machines.request_launch.connect(self._launch_lab)
        self.page_machines.request_refresh_catalog.connect(self.catalogs.refresh)
        self.page_lab.request_action.connect(self._lab_action)
        self.page_lab.request_refresh.connect(self._refresh_docker)
        self.page_lab.request_go_machines.connect(lambda: self._go("machines"))
        self.page_lab.request_grant_access.connect(self._grant_docker_access)
        self.page_lab.request_start_service.connect(self._start_docker_service)
        self.page_lab.request_forget_sudo.connect(self._forget_sudo)
        self.page_settings.request_set_docker_network.connect(self._set_docker_network)
        self.page_session.request_login.connect(self.session.login)
        self.page_session.request_logout.connect(self.session.logout)
        self.page_completed.request_refresh.connect(lambda: self.session.sync(manual=True))
        self.page_completed.request_toggle_completed.connect(self.session.toggle)
        self.page_completed.request_open_machine.connect(self._open_machine_detail)
        self.page_settings.request_change_downloads_dir.connect(self._set_downloads_dir)
        self.page_settings.request_set_os_notifications.connect(self._set_os_notifications)
        self.page_settings.request_set_in_app_notifications.connect(self._set_in_app_notifications)
        self.page_settings.request_open_downloads_dir.connect(self._open_downloads_dir)
        self.page_settings.request_set_max_concurrent.connect(self._set_max_concurrent)
        self.page_settings.request_set_theme.connect(self._set_theme)

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
        self._on_completed_changed(self.session.completed)

        self.catalogs.load_cached()
        self.catalogs.refresh()
        self._install_shortcuts()
        self._refresh_downloaded_state()
        # Auto-login si hay sesión guardada
        self.session.restore()
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

    # ---- Catálogo (la lógica vive en CatalogController) ----

    @property
    def catalog(self):
        return self.catalogs.catalog

    def _on_catalog_loading(self, active: bool, msg: str) -> None:
        self.page_machines.set_loading(active, msg)

    def _apply_catalog(self, cat) -> None:
        self.page_machines.set_catalog(cat)
        self.page_dashboard.set_catalog(cat, self.session.completed)
        self.page_completed.set_catalog(cat)
        self._reconcile_downloads_with_catalog()

    def _on_catalog_refresh_failed(self, err: str, has_cache: bool) -> None:
        if not has_cache:
            self.page_machines.set_catalog(None)
            self.notify("Sin catálogo", f"Sin internet y sin caché: {err}", kind="warning")
        else:
            self.notify("Sin conexión", "Usando el catálogo cacheado", kind="info")

    # ---- Sesión (la lógica vive en SessionController; aquí sólo se pinta) ----

    def _on_login_started(self, user: str) -> None:
        # Toast in-app de 'autenticando' (SOLO in-app, nunca al SO): lo
        # emitimos directamente con el ToastManager para saltarnos los
        # ajustes de notificaciones.
        try:
            self.toasts.show("Autenticando…", f"Conectando como {user}", kind="info")
        except Exception:  # noqa: BLE001
            logger.exception("toast autenticando")

    def _on_logged_in(self, username: str, profile_url: str) -> None:
        self.sidebar.set_logged_in(username)
        self.page_dashboard.set_session(username)
        self.page_session.set_logged_in(username, len(self.session.completed))
        if profile_url:
            self.sidebar.avatar.fetch_image(self.session.client, profile_url)
            # También para el avatar grande de la página Sesión
            self._fetch_big_avatar(profile_url)

    def _on_logged_out(self) -> None:
        self.sidebar.set_logged_out()
        self.page_dashboard.set_session(None)
        self.page_session.set_logged_out()
        # Tras cerrar sesión, llevamos al usuario al dashboard
        self._go("dashboard")

    def _on_completed_changed(self, completed) -> None:
        completed = set(completed)
        self.page_machines.set_completed(completed)
        self.page_completed.set_items(completed)
        self.page_dashboard.set_completed(completed)
        self.page_session.update_stats(len(completed))

    def _fetch_big_avatar(self, profile_url: str) -> None:
        """Reutiliza el fetcher del avatar pequeño para también alimentar el grande."""
        try:
            from widgets.avatar import _AvatarFetcher
            client = self.session.client
            if not profile_url.startswith("http"):
                profile_url = client.base_url.rstrip("/") + profile_url
            fetcher = _AvatarFetcher(client, profile_url, self.page_session)
            fetcher.finished_data.connect(
                lambda data, _u: self.page_session.set_avatar_pixmap(bytes(data))
            )
            self._workers.track(fetcher)
        except Exception:  # noqa: BLE001
            logger.exception("no se pudo lanzar la descarga del avatar grande")

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

    def _reconcile_downloads_with_catalog(self) -> None:
        """Cruza los archivos de la carpeta de descargas con los nombres reales
        del catálogo para marcar como descargadas las máquinas ya bajadas."""
        try:
            names = self.page_machines.names
            if names:
                self.downloads.reconcile_with_catalog(names)
        except Exception:  # noqa: BLE001
            logger.exception("reconcile_with_catalog")
        self._refresh_downloaded_state()

    # ---- Atajos de teclado ----

    def _install_shortcuts(self) -> None:
        keys = ["dashboard", "machines", "downloads", "lab", "completed", "settings", "about"]
        for i, key in enumerate(keys, start=1):
            QShortcut(QKeySequence(f"Ctrl+{i}"), self, activated=lambda k=key: self._go(k))
        QShortcut(QKeySequence.StandardKey.Find, self, activated=self._focus_search)
        QShortcut(QKeySequence("F5"), self, activated=self.catalogs.refresh)
        QShortcut(QKeySequence("Escape"), self, activated=self.page_machines.close_detail)

    def _focus_search(self) -> None:
        self._go("machines")
        self.page_machines.focus_search()

    def _open_machine_detail(self, name: str) -> None:
        """Navega al catálogo y abre el panel de detalle de `name` (desde Completadas, Laboratorio…)."""
        self._go("machines")
        self.page_machines.show_detail(name)

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
        """Abre el diálogo con las opciones (solo esta sesión / grupo docker; ambas con el
        diálogo de autenticación del sistema; fallback sudo con contraseña si no hay pkexec)."""
        info = self.labs.docker_info
        if not info.needs_elevation:
            self.notify("No hace falta", "Tu usuario ya puede usar Docker.", kind="info")
            return
        if self.labs.elevating:
            self.notify("Ya hay una autorización en curso", kind="info")
            return
        if not (info.can_sudo or info.can_elevate):
            self.notify("Sin permiso para usar Docker",
                        "Ejecuta como administrador: usermod -aG docker $USER (y reinicia sesión)",
                        kind="error")
            return
        from widgets.docker_access_dialog import (
            CHOICE_GROUP,
            CHOICE_SUDO,
            CHOICE_TEMP,
            ask_docker_access,
        )
        choice, password = ask_docker_access(self, can_sudo=info.can_sudo,
                                             can_elevate=info.can_elevate, error=info.error)
        actions = {
            CHOICE_TEMP: lambda: self.labs.grant_temp_access(),
            CHOICE_GROUP: lambda: self.labs.grant_access(),
            CHOICE_SUDO: lambda: self.labs.use_sudo(password),
        }
        act = actions.get(choice)
        if act is not None and not act():
            self.notify("Ya hay una autorización en curso", kind="info")

    def _forget_sudo(self) -> None:
        self.labs.forget_sudo()
        self.notify("sudo desactivado", "Docker volverá a ejecutarse con tu usuario.", kind="info")

    def _start_docker_service(self) -> None:
        if not self.labs.start_service():
            self.notify("Ya hay una autorización en curso", kind="info")

    def _on_elevation_started(self, action: str) -> None:
        self.page_lab.set_elevating(True)

    def _on_elevation_done(self, action: str, ok: bool, detail: str) -> None:
        self.page_lab.set_elevating(False)
        titles = {"grant": "Acceso a Docker", "temp": "Acceso a Docker (esta sesión)",
                  "start_service": "Servicio Docker", "sudo": "Docker con sudo"}
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
            # Diálogo con las dos opciones (sudo recomendado / grupo docker)
            self._grant_docker_access()
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
            max_concurrent=self.settings.max_concurrent_downloads,
            theme=self.settings.theme,
        )

    def _set_theme(self, theme: str) -> None:
        theme = theme if theme in THEMES else DEFAULT_THEME
        if theme == self.settings.theme and theme == _theme.CURRENT_THEME:
            return
        self.settings.theme = theme
        self.settings_store.save(self.settings)
        # El QSS global se aplica al instante (tabla, botones, inputs...). Los
        # estilos inline de paneles ya construidos no cambian: ofrecemos reiniciar.
        app = QApplication.instance()
        qss = apply_theme(theme)
        if app is not None:
            app.setStyleSheet(qss)
        res = QMessageBox.question(
            self, "Tema cambiado",
            "El tema se aplica por completo al reiniciar la aplicación.\n"
            "¿Reiniciar ahora? (las descargas en curso se cancelarán)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if res == QMessageBox.StandardButton.Yes:
            self._restart_requested = True
            self.close()
        else:
            self.notify("Tema guardado", "Se aplicará del todo la próxima vez que abras la app.", kind="info")

    def _set_max_concurrent(self, n: int) -> None:
        self.settings.max_concurrent_downloads = max(1, min(6, int(n)))
        self.settings_store.save(self.settings)
        self.downloads.set_max_concurrent(self.settings.max_concurrent_downloads)

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

    def _update_totals(self) -> None:
        self.page_dashboard.set_total(len(self.page_machines.names))

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
        self.session.shutdown()
        self.catalogs.shutdown()
        self._workers.shutdown(1500)
        super().closeEvent(event)


def main() -> int:
    log_file = setup_logging(APP_DIR)
    logger.info("DockerLabs GUI arrancando · %s %s · Python %s",
                platform.system(), platform.release(), platform.python_version())
    app = QApplication(sys.argv)
    app.setApplicationName("DockerLabs GUI")
    app.setDesktopFileName("dockerlabs-gui")
    app.setWindowIcon(app_icon())
    set_language(_load_language_pref())
    app.setStyleSheet(apply_theme(_load_theme_pref()))
    win = MainWindow()
    install_excepthook(lambda: win)
    win.show()
    logger.debug("log en %s", log_file)
    code = app.exec()
    if getattr(win, "_restart_requested", False):
        _relaunch()
    return code


def _relaunch() -> None:
    """Relanza la app con el mismo intérprete y argumentos (tras cambiar de tema)."""
    try:
        if getattr(sys, "frozen", False):
            args = [sys.executable, *sys.argv[1:]]
        else:
            args = [sys.executable, *sys.argv]
        logger.info("reiniciando: %s", " ".join(args))
        subprocess.Popen(args, close_fds=True)  # noqa: S603
    except Exception:  # noqa: BLE001
        logger.exception("no se pudo relanzar la aplicación")


if __name__ == "__main__":
    raise SystemExit(main())
