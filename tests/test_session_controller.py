"""Tests de SessionController con un cliente DockerLabs falso (sin red)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from dockerlabs_api import AuthResult, load_env  # noqa: E402
from session_controller import SessionController  # noqa: E402

_app = QApplication.instance() or QApplication([])  # noqa: F841


class FakeClient:
    base_url = "https://dockerlabs.es"

    def __init__(self) -> None:
        self.server_completed: set[str] = {"Trust"}
        self.accept = {"neo": "pw"}
        self.toggles: list[str] = []
        self._csrf_token = "tok"

    # auth
    def login(self, user, pwd):
        ok = self.accept.get(user) == pwd
        return AuthResult(success=ok, message="" if ok else "Credenciales inválidas",
                          redirect_url=None, username=user, session_cookie="c",
                          csrf_token="tok", raw_response={})

    def author_profile(self, user):
        return {"profile_image_url": f"/img/{user}.png"}

    def session_cookie_value(self):
        return "cookie123"

    @property
    def csrf_token(self):
        return self._csrf_token

    # completadas
    def completed_machines_from_home(self):
        return sorted(self.server_completed)

    def toggle_completed(self, name):
        self.toggles.append(name)
        if name in self.server_completed:
            self.server_completed.discard(name); return False
        self.server_completed.add(name); return True


def _pump(ctrl: SessionController, ms: int = 3000) -> None:
    """Espera a que terminen los hilos del controlador y procesa la cola de eventos."""
    import time
    deadline = time.time() + ms / 1000
    while time.time() < deadline:
        QCoreApplication.processEvents()
        if not ctrl._workers.any_running():  # noqa: SLF001
            break
        time.sleep(0.01)
    for _ in range(20):
        QCoreApplication.processEvents()


def _make(tmp_path, fake=None):
    fake = fake or FakeClient()
    return SessionController(tmp_path / ".env", tmp_path / "completed.json",
                             client_factory=lambda: fake), fake


def test_toggle_offline_persists_locally(tmp_path):
    ctrl, fake = _make(tmp_path)
    seen = []
    ctrl.completed_changed.connect(lambda s: seen.append(set(s)))
    ctrl.toggle("Asturias")
    assert ctrl.completed == {"Asturias"}
    assert seen[-1] == {"Asturias"}
    assert fake.toggles == []            # sin sesión no se toca el servidor
    assert ctrl.store.all_for_user(None) == {"Asturias"}
    ctrl.toggle("Asturias")
    assert ctrl.completed == set()


def test_login_merges_anonymous_and_syncs(tmp_path):
    ctrl, fake = _make(tmp_path)
    ctrl.toggle("Asturias")              # marcada offline
    logged = []
    ctrl.logged_in.connect(lambda u, p: logged.append((u, p)))
    ctrl.login("neo", "pw")
    _pump(ctrl)
    assert logged == [("neo", "/img/neo.png")]
    assert ctrl.username == "neo"
    # la anónima se subió al servidor y se unió a las del servidor
    assert "Asturias" in fake.server_completed
    assert ctrl.completed == {"Asturias", "Trust"}
    assert ctrl.store.all_for_user("neo") == {"Asturias", "Trust"}
    assert ctrl.store.all_for_user(None) == set()
    env = load_env(tmp_path / ".env")
    assert env["DOCKERLABS_SESSION"] == "cookie123"
    assert env["DOCKERLABS_USERNAME"] == "neo"


def test_login_failure_emits_signal(tmp_path):
    ctrl, _ = _make(tmp_path)
    fails = []
    ctrl.login_failed.connect(fails.append)
    ctrl.login("neo", "bad")
    _pump(ctrl)
    assert fails == ["Credenciales inválidas"]
    assert ctrl.username is None


def test_login_without_credentials_notifies(tmp_path):
    ctrl, _ = _make(tmp_path)
    notes = []
    ctrl.notify.connect(lambda t, b, k: notes.append((t, k)))
    ctrl.login("", "")
    assert notes == [("Faltan datos", "warning")]


def test_toggle_online_hits_server(tmp_path):
    ctrl, fake = _make(tmp_path)
    ctrl.login("neo", "pw"); _pump(ctrl)
    ctrl.toggle("Grandma"); _pump(ctrl)
    assert "Grandma" in fake.toggles
    assert "Grandma" in ctrl.completed
    assert "Grandma" in ctrl.store.all_for_user("neo")


def test_logout_clears_env_and_restores_anonymous(tmp_path):
    ctrl, fake = _make(tmp_path)
    ctrl.login("neo", "pw"); _pump(ctrl)
    out = []
    ctrl.logged_out.connect(lambda: out.append(True))
    ctrl.logout()
    assert out == [True]
    assert ctrl.username is None
    assert ctrl.completed == set()       # sin anónimas pendientes
    env = load_env(tmp_path / ".env")
    assert env["DOCKERLABS_SESSION"] == ""


def test_manual_sync_without_session_warns(tmp_path):
    ctrl, _ = _make(tmp_path)
    notes = []
    ctrl.notify.connect(lambda t, b, k: notes.append(t))
    ctrl.sync(manual=True)
    assert notes == ["Inicia sesión"]
    ctrl.sync()
    assert notes == ["Inicia sesión"]
