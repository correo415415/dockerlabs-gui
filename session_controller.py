"""Controlador de sesión y de máquinas completadas (sin UI).

Extraído de `MainWindow` para que la ventana sólo pinte: aquí vive el estado
lógico (cliente HTTP, usuario, set de completadas, persistencia en .env y en
completed.json) y los hilos que hablan con dockerlabs.es.

La UI se engancha a las señales:
    logged_in(username, profile_url)     tras login o restauración de sesión
    logged_out()
    completed_changed(set)               cada vez que cambia el set visible
    status(str)                          mensaje corto para la barra de estado
    notify(title, body, kind)            toast / notificación
    login_failed(msg)
    restore_failed(reason)               "no-cookie" | "expired" | "no-user" | error
    sync_finished(total, pushed)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from completed_store import CompletedStore
from dockerlabs_api import AuthResult, DockerLabsError, build_session_payload, load_env, save_env
from dockerlabs_api_ext import DockerLabsExtClient
from workers import BaseWorker, WorkerPool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

class LoginWorker(BaseWorker):
    success = pyqtSignal(str, str)   # username, profile_image_url

    def __init__(self, client: DockerLabsExtClient, user: str, pwd: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client; self.user = user; self.pwd = pwd

    def work(self) -> None:
        res = self.client.login(self.user, self.pwd)
        if not res.success:
            self.failed.emit(res.message or "Login rechazado"); return
        profile = self.client.author_profile(self.user)
        self.success.emit(self.user, profile.get("profile_image_url", "") or "")

    def format_error(self, exc: BaseException) -> str:
        if isinstance(exc, DockerLabsError):
            return str(exc)
        return f"Error: {exc}"


class ToggleWorker(BaseWorker):
    done = pyqtSignal(str, bool)
    failed = pyqtSignal(str, str)   # (nombre, error)

    def __init__(self, client: DockerLabsExtClient, name: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client; self.name = name

    def work(self) -> None:
        self.done.emit(self.name, self.client.toggle_completed(self.name))

    def on_error(self, exc: BaseException) -> None:
        self.failed.emit(self.name, self.format_error(exc))


class SessionRestoreWorker(BaseWorker):
    """Intenta reutilizar la cookie guardada en .env para auto-loguear."""
    success = pyqtSignal(str, str)       # username, profile_image_url

    def __init__(self, client: DockerLabsExtClient, env_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.env_path = env_path

    def work(self) -> None:
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


class SyncCompletedWorker(BaseWorker):
    """Combina las completadas locales con las del servidor.

    1. Lee las completadas del servidor.
    2. Las locales que no estaban en el servidor se envían con toggle.
    3. Emite el set final unificado.
    """
    done = pyqtSignal(list, list)   # final_list, newly_pushed

    def __init__(self, client: DockerLabsExtClient, local: set[str], parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.local = set(local)

    def work(self) -> None:
        server = set(self.client.completed_machines_from_home())
        to_push = sorted(self.local - server)
        pushed: list[str] = []
        for name in to_push:
            if self.is_cancelled:
                break
            try:
                if self.client.toggle_completed(name):
                    pushed.append(name)
            except Exception:  # noqa: BLE001
                # Si una falla, seguimos con las demás
                continue
        self.done.emit(sorted(server | set(pushed)), pushed)


# ---------------------------------------------------------------------------
# Controlador
# ---------------------------------------------------------------------------

class SessionController(QObject):
    logged_in = pyqtSignal(str, str)        # username, profile_url
    logged_out = pyqtSignal()
    completed_changed = pyqtSignal(object)  # set[str]
    status = pyqtSignal(str)
    notify = pyqtSignal(str, str, str)      # title, body, kind
    login_started = pyqtSignal(str)         # username
    login_failed = pyqtSignal(str)
    restore_failed = pyqtSignal(str)
    sync_finished = pyqtSignal(int, int)    # total, pushed

    def __init__(self, env_path: Path, completed_path: Path,
                 client_factory=DockerLabsExtClient, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.env_path = Path(env_path)
        self._client_factory = client_factory
        self.client: DockerLabsExtClient = client_factory()
        self.store = CompletedStore(Path(completed_path))
        self.username: Optional[str] = None
        self.completed: set[str] = set(self.store.all_for_user(None))
        self._workers = WorkerPool()

    # ---- consultas ----

    @property
    def logged(self) -> bool:
        return bool(self.username)

    # ---- sesión ----

    def restore(self) -> None:
        """Auto-login con la cookie persistida en .env (si existe)."""
        if not self.env_path.exists():
            return
        w = SessionRestoreWorker(self.client, self.env_path, parent=self)
        w.success.connect(self._on_restore_ok)
        w.failed.connect(self._on_restore_fail)
        self._workers.track(w)

    def login(self, user: str, pwd: str) -> None:
        if not user or not pwd:
            self.notify.emit("Faltan datos", "Usuario y contraseña obligatorios", "warning")
            return
        self.status.emit("Autenticando…")
        self.login_started.emit(user)
        # Cliente nuevo para no arrastrar cookies viejas
        self.client = self._client_factory()
        w = LoginWorker(self.client, user, pwd, parent=self)
        w.success.connect(self._on_login_ok)
        w.failed.connect(self._on_login_fail)
        self._workers.track(w)

    def logout(self) -> None:
        try:
            if self.env_path.exists():
                save_env(self.env_path, {
                    "DOCKERLABS_USERNAME": "",
                    "DOCKERLABS_SESSION": "",
                    "DOCKERLABS_CSRF": "",
                    "DOCKERLABS_LOGIN_AT": "",
                    "DOCKERLABS_BASE_URL": self.client.base_url,
                })
        except Exception:  # noqa: BLE001
            logger.exception("no se pudo limpiar la sesión persistente")
        self.client = self._client_factory()
        self.username = None
        # El usuario sigue viendo lo marcado offline (anónimo).
        self._set_completed(self.store.all_for_user(None))
        self.logged_out.emit()
        self.status.emit("Sesión cerrada")
        self.notify.emit("Sesión cerrada", "", "info")

    # ---- completadas ----

    def sync(self, *, manual: bool = False) -> None:
        """Sincroniza con el servidor. `manual=True` avisa si no hay sesión."""
        if not self.username:
            if manual:
                self.notify.emit("Inicia sesión",
                                 "Necesitas iniciar sesión para sincronizar con DockerLabs.",
                                 "warning")
            return
        if self._workers.any_running(SyncCompletedWorker):
            return
        self.status.emit("Sincronizando completadas con DockerLabs…")
        w = SyncCompletedWorker(self.client, self.completed, parent=self)
        w.done.connect(self._on_sync_done)
        w.failed.connect(self._on_sync_fail)
        self._workers.track(w)

    def toggle(self, name: str) -> None:
        if not self.username:
            # Sin sesión: marcamos/desmarcamos en local; al iniciar sesión
            # estas marcas se sincronizarán con el servidor.
            if name in self.completed:
                self.completed.discard(name)
                self.store.remove(None, name)
                self.notify.emit("Desmarcada (local)",
                                 f"{name} desmarcada localmente. Inicia sesión para sincronizar.",
                                 "info")
            else:
                self.completed.add(name)
                self.store.add(None, name)
                self.notify.emit("Completada (local)",
                                 f"{name} marcada localmente. Se subirá al iniciar sesión.",
                                 "info")
            self.completed_changed.emit(set(self.completed))
            return
        self.status.emit(f"Alternando estado de {name}…")
        w = ToggleWorker(self.client, name, parent=self)
        w.done.connect(self._on_toggle_ok)
        w.failed.connect(self._on_toggle_fail)
        self._workers.track(w)

    def shutdown(self) -> None:
        self._workers.shutdown(1500)

    # ---- internos ----

    def _set_completed(self, names) -> None:
        self.completed = set(names)
        self.completed_changed.emit(set(self.completed))

    def _apply_login(self, username: str, profile_url: str) -> None:
        self.username = username
        # Migrar las completadas anónimas (si las hay) al usuario
        anon = self.store.drain_anonymous()
        if anon:
            self.store.merge_into_user(username, anon)
        self._set_completed(self.store.all_for_user(username))
        self.logged_in.emit(username, profile_url or "")
        self.sync()

    def _persist_session(self, username: str) -> None:
        try:
            res = AuthResult(
                success=True, message="", redirect_url=None,
                username=username,
                session_cookie=self.client.session_cookie_value() or "",
                csrf_token=self.client.csrf_token or "",
                raw_response={},
            )
            save_env(self.env_path, build_session_payload(res, self.client.base_url))
        except Exception:  # noqa: BLE001
            logger.exception("no se pudo guardar la sesión en %s", self.env_path)

    # slots

    def _on_restore_ok(self, username: str, profile_url: str) -> None:
        self.notify.emit("Sesión restaurada", f"Bienvenido de nuevo, {username}", "info")
        self._apply_login(username, profile_url)

    def _on_restore_fail(self, reason: str) -> None:
        if reason != "no-cookie":
            self.status.emit("Sesión guardada expirada; inicia sesión de nuevo.")
        self.restore_failed.emit(reason)

    def _on_login_ok(self, username: str, profile_url: str) -> None:
        self.status.emit(f"Sesión iniciada como {username}")
        self.notify.emit("Sesión iniciada", f"Bienvenido, {username}", "success")
        self._persist_session(username)
        self._apply_login(username, profile_url)

    def _on_login_fail(self, msg: str) -> None:
        self.status.emit("Login fallido")
        self.notify.emit("Login fallido", msg, "error")
        self.login_failed.emit(msg)

    def _on_sync_done(self, final: list, pushed: list) -> None:
        if self.username:
            self.store.set_for_user(self.username, final)
        self._set_completed(final)
        if pushed:
            self.status.emit(f"{len(self.completed)} completadas · {len(pushed)} subidas al servidor")
            self.notify.emit("Completadas sincronizadas",
                             f"Se subieron {len(pushed)} máquinas locales al servidor.", "success")
        else:
            self.status.emit(f"{len(self.completed)} completadas")
        self.sync_finished.emit(len(self.completed), len(pushed))

    def _on_sync_fail(self, err: str) -> None:
        self.status.emit(f"Error sincronizando: {err}")
        self.notify.emit("Error sincronizando", err, "error")

    def _on_toggle_ok(self, name: str, new_state: bool) -> None:
        if new_state:
            self.completed.add(name)
            if self.username:
                self.store.add(self.username, name)
        else:
            self.completed.discard(name)
            if self.username:
                self.store.remove(self.username, name)
        self.completed_changed.emit(set(self.completed))
        verb_long = "marcada como completada" if new_state else "desmarcada"
        verb_short = "Completada" if new_state else "Desmarcada"
        self.status.emit(f"{name} {verb_long}")
        self.notify.emit(f"{verb_short}: {name}", "", "success" if new_state else "info")

    def _on_toggle_fail(self, name: str, msg: str) -> None:
        self.status.emit(f"Error toggle {name}: {msg}")
        self.notify.emit(f"Error con {name}", msg, "error")
