"""Cliente HTTP compartido para la API de DockerLabs.

Encapsula:
- Descarga del JSON público de /api con la colección info_maquinas.
- Login contra /api/auth/login (CSRF + cookie de sesión firmada de Flask).
- Persistencia del 'token' (cookie de sesión + CSRF) en un .env del CWD.

Usa `requests.Session` para reutilizar la conexión TLS (keep-alive): el login
encadena 3-4 peticiones y con `urllib` cada una abría una conexión nueva
(handshake TLS + Cloudflare), lo que hacía que a veces tardara mucho. Además se
usa un timeout de conexión corto con reintento, para que una ruta IPv6 rota o
un pico de red no bloqueen la UI durante 30 s por petición.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import re
import shlex
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://dockerlabs.es"
API_URL = f"{BASE_URL}/api"
LOGIN_PAGE = f"{BASE_URL}/login"
LOGIN_ENDPOINT = f"{BASE_URL}/api/auth/login"
DASHBOARD_URL = f"{BASE_URL}/dashboard"
USER_AGENT = "dockerlabs-gui/0.2 (+https://dockerlabs.es)"

CSRF_META_RE = re.compile(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', re.IGNORECASE)


class DockerLabsError(Exception):
    """Error funcional del cliente."""


@dataclass
class AuthResult:
    success: bool
    message: str
    redirect_url: Optional[str]
    username: str
    session_cookie: Optional[str]
    csrf_token: Optional[str]
    raw_response: dict


class DockerLabsClient:
    """Cliente sencillo basado en urllib + cookiejar."""

    # (conexión, lectura) en segundos. Conectar a dockerlabs.es (Cloudflare) tarda
    # <1 s; si no responde en 8 s casi seguro es una ruta rota y conviene reintentar.
    CONNECT_TIMEOUT = 8.0

    def __init__(self, base_url: str = BASE_URL, timeout: int = 30,
                 session: Optional[requests.Session] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/html;q=0.9, */*;q=0.5",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        })
        # Reintenta sólo errores de conexión (antes de enviar nada, seguro para POST).
        retry = Retry(total=2, connect=2, read=0, status=0, backoff_factor=0.3,
                      allowed_methods=None, raise_on_status=False)
        # Varios hilos (catálogo, imágenes, valoraciones, avatares…) comparten la
        # sesión; con un pool pequeño urllib3 avisaba "Connection pool is full,
        # discarding connection" y abría conexiones nuevas cada vez.
        adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=16,
                              pool_block=False)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._csrf_token: Optional[str] = None

    @property
    def cookies(self) -> http.cookiejar.CookieJar:
        return self.session.cookies

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:  # noqa: BLE001
            pass

    # ---------- Bajo nivel ----------

    def _request(self, url: str, *, method: str = "GET", data: Optional[bytes] = None,
                 headers: Optional[dict] = None) -> tuple[int, dict, bytes]:
        try:
            resp = self.session.request(
                method, url, data=data, headers=headers or None,
                timeout=(self.CONNECT_TIMEOUT, self.timeout), allow_redirects=True,
            )
        except requests.exceptions.Timeout as exc:
            raise DockerLabsError(f"Tiempo de espera agotado contactando {url}") from exc
        except requests.exceptions.RequestException as exc:
            reason = getattr(exc, "args", [exc])[0] if exc.args else exc
            raise DockerLabsError(f"Error de red contactando {url}: {reason}") from exc
        return resp.status_code, dict(resp.headers), resp.content

    # ---------- Sesión / CSRF ----------

    def fetch_csrf_token(self) -> str:
        """Obtiene el CSRF leyendo el meta de /login y guarda la cookie de sesión inicial."""
        status, _, body = self._request(f"{self.base_url}/login")
        if status != 200:
            raise DockerLabsError(f"No se pudo cargar /login (HTTP {status})")
        match = CSRF_META_RE.search(body.decode("utf-8", errors="ignore"))
        if not match:
            raise DockerLabsError("No se encontró el meta csrf-token en /login")
        self._csrf_token = match.group(1)
        return self._csrf_token

    @property
    def csrf_token(self) -> Optional[str]:
        return self._csrf_token

    def session_cookie_value(self) -> Optional[str]:
        for cookie in self.cookies:
            if cookie.name == "session":
                return cookie.value
        return None

    def inject_session_cookie(self, value: str) -> None:
        """Inyecta una cookie 'session' previamente guardada en el .env."""
        domain = urllib.parse.urlparse(self.base_url).hostname or "dockerlabs.es"
        cookie = http.cookiejar.Cookie(
            version=0, name="session", value=value,
            port=None, port_specified=False,
            domain=domain, domain_specified=True, domain_initial_dot=False,
            path="/", path_specified=True,
            secure=True, expires=None, discard=False,
            comment=None, comment_url=None, rest={"HttpOnly": None}, rfc2109=False,
        )
        self.cookies.set_cookie(cookie)

    # ---------- Operaciones ----------

    def login(self, username: str, password: str) -> AuthResult:
        if not username or not password:
            raise DockerLabsError("Usuario y contraseña son obligatorios")
        csrf = self.fetch_csrf_token()
        body = json.dumps({"username": username, "password": password}).encode("utf-8")
        status, _, raw = self._request(
            f"{self.base_url}/api/auth/login",
            method="POST",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-CSRFToken": csrf,
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/login",
                "Accept": "application/json",
            },
        )
        try:
            payload = json.loads(raw.decode("utf-8", errors="ignore") or "{}")
        except json.JSONDecodeError:
            payload = {"raw": raw[:200].decode("utf-8", errors="ignore")}

        success = bool(payload.get("success")) and status == 200
        return AuthResult(
            success=success,
            message=payload.get("message")
                    or ("Login correcto" if success else f"Error HTTP {status}"),
            redirect_url=payload.get("redirect_url"),
            username=username,
            session_cookie=self.session_cookie_value() if success else None,
            csrf_token=csrf,
            raw_response=payload,
        )

    def verify_session(self) -> bool:
        """Comprueba que la cookie de sesión actual da acceso al dashboard."""
        status, headers, _ = self._request(f"{self.base_url}/dashboard")
        if status != 200:
            return False
        # Si la sesión no es válida la app sirve el HTML de login en /dashboard,
        # así que comprobamos un marcador que solo aparece en la página de login.
        return True  # placeholder, se complementa con check_dashboard_html en el llamador

    def fetch_api_data(self) -> dict:
        status, _, raw = self._request(f"{self.base_url}/api")
        if status != 200:
            raise DockerLabsError(f"La API respondió HTTP {status}")
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise DockerLabsError("La respuesta de /api no es JSON") from exc
        if not isinstance(data, dict) or "info_maquinas" not in data:
            raise DockerLabsError("La API no contiene la colección 'info_maquinas'")
        return data


# ---------- Persistencia .env ----------

ENV_KEYS = (
    "DOCKERLABS_USERNAME",
    "DOCKERLABS_SESSION",
    "DOCKERLABS_CSRF",
    "DOCKERLABS_LOGIN_AT",
    "DOCKERLABS_BASE_URL",
)


def _escape_env_value(value: str) -> str:
    return shlex.quote(value)


def save_env(path: Path, values: dict) -> Path:
    """Escribe / actualiza un .env preservando claves no gestionadas por la app."""
    path = path.expanduser().resolve()
    existing: list[str] = []
    if path.exists():
        existing = path.read_text(encoding="utf-8").splitlines()

    managed_keys = set(values.keys())
    kept: list[str] = []
    for line in existing:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            kept.append(line)
            continue
        if "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in managed_keys:
                continue
        kept.append(line)

    header = "# DockerLabs CLI session — generado por dockerlabs-gui"
    new_lines = [f"{k}={_escape_env_value(str(v))}" for k, v in values.items() if v is not None]

    if any(line.strip() == header for line in kept):
        out_lines = kept + new_lines
    else:
        out_lines = [header] + kept + new_lines

    # normalizar dobles saltos
    text = "\n".join(line for line in out_lines if line is not None).rstrip() + "\n"
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def load_env(path: Path) -> dict:
    """Carga .env -> dict sin alterar os.environ."""
    path = path.expanduser().resolve()
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if (value.startswith("'") and value.endswith("'")) or (
            value.startswith('"') and value.endswith('"')
        ):
            value = value[1:-1]
        result[key] = value
    return result


def build_session_payload(result: AuthResult, base_url: str = BASE_URL) -> dict:
    return {
        "DOCKERLABS_USERNAME": result.username,
        "DOCKERLABS_SESSION": result.session_cookie or "",
        "DOCKERLABS_CSRF": result.csrf_token or "",
        "DOCKERLABS_LOGIN_AT": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "DOCKERLABS_BASE_URL": base_url,
    }
