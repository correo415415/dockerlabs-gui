"""Extensiones al DockerLabsClient para los endpoints internos del frontend.

Reusa el cliente urllib + cookiejar que viene en dockerlabs_api.py y añade:
- /api/completed_machines/<nombre>
- /api/toggle_completed_machine
- /api/author_profile?nombre=<u>
- helpers para extraer CSRF de páginas autenticadas (necesario para POSTs).
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Optional

from dockerlabs_api import CSRF_META_RE, DockerLabsClient, DockerLabsError


class DockerLabsExtClient(DockerLabsClient):
    """Cliente extendido con endpoints internos descubiertos."""

    def fetch_root_csrf(self) -> str:
        """Pide la home autenticada y extrae el csrf-token de su meta."""
        status, _, body = self._request(f"{self.base_url}/")
        if status != 200:
            raise DockerLabsError(f"GET / devolvió HTTP {status}")
        match = CSRF_META_RE.search(body.decode("utf-8", errors="ignore"))
        if not match:
            raise DockerLabsError("No se encontró csrf-token en /")
        self._csrf_token = match.group(1)
        return self._csrf_token

    def get_completed_state(self, machine_name: str) -> bool:
        path = "/api/completed_machines/" + urllib.parse.quote(machine_name, safe="")
        status, _, body = self._request(self.base_url + path)
        if status == 401:
            raise DockerLabsError("Necesitas iniciar sesión")
        if status != 200:
            raise DockerLabsError(f"HTTP {status} consultando estado")
        try:
            data = json.loads(body.decode("utf-8", errors="ignore"))
        except json.JSONDecodeError:
            raise DockerLabsError("Respuesta no-JSON en completed_machines")
        return bool(data.get("completed"))

    def toggle_completed(self, machine_name: str) -> bool:
        """POST /api/toggle_completed_machine. Devuelve nuevo estado."""
        if not self._csrf_token:
            self.fetch_root_csrf()
        payload = json.dumps({"machine_name": machine_name}).encode("utf-8")
        status, _, raw = self._request(
            self.base_url + "/api/toggle_completed_machine",
            method="POST",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-CSRFToken": self._csrf_token or "",
                "Origin": self.base_url,
                "Referer": self.base_url + "/",
                "Accept": "application/json",
            },
        )
        if status == 403:
            # CSRF expirado: lo refrescamos y reintentamos una vez
            self.fetch_root_csrf()
            status, _, raw = self._request(
                self.base_url + "/api/toggle_completed_machine",
                method="POST",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-CSRFToken": self._csrf_token or "",
                    "Origin": self.base_url,
                    "Referer": self.base_url + "/",
                    "Accept": "application/json",
                },
            )
        if status != 200:
            raise DockerLabsError(f"HTTP {status} en toggle_completed_machine")
        try:
            data = json.loads(raw.decode("utf-8", errors="ignore"))
        except json.JSONDecodeError:
            raise DockerLabsError("Respuesta no-JSON en toggle_completed_machine")
        if not data.get("success"):
            raise DockerLabsError(data.get("error") or "Toggle rechazado")
        return bool(data.get("completed"))

    def author_profile(self, nombre: str) -> dict:
        url = self.base_url + "/api/author_profile?nombre=" + urllib.parse.quote(nombre, safe="")
        status, _, raw = self._request(url)
        if status != 200:
            raise DockerLabsError(f"HTTP {status} en author_profile")
        try:
            return json.loads(raw.decode("utf-8", errors="ignore"))
        except json.JSONDecodeError:
            raise DockerLabsError("author_profile devolvió no-JSON")

    def is_session_valid(self) -> bool:
        """Verifica que la cookie actual sigue válida pidiendo la home.

        Si la cookie expiró, DockerLabs sirve un HTML con el formulario de
        login (no devuelve 401/403). Detectamos eso buscando el marcador
        'var currentUser' que solo aparece cuando el usuario está logueado.
        """
        try:
            status, _, body = self._request(f"{self.base_url}/")
        except DockerLabsError:
            return False
        if status != 200:
            return False
        html = body.decode("utf-8", errors="ignore")
        return "var currentUser" in html and 'currentUser = ""' not in html

    def current_user_from_home(self) -> Optional[str]:
        """Extrae el nombre del usuario de la home autenticada."""
        try:
            status, _, body = self._request(f"{self.base_url}/")
        except DockerLabsError:
            return None
        if status != 200:
            return None
        html = body.decode("utf-8", errors="ignore")
        m = re.search(r'var\s+currentUser\s*=\s*"([^"]+)"', html)
        return m.group(1) if m else None

    def completed_machines_from_home(self) -> list[str]:
        """Saca la lista de máquinas completadas parseando la home autenticada."""
        status, _, body = self._request(self.base_url + "/")
        if status != 200:
            raise DockerLabsError(f"GET / devolvió HTTP {status}")
        html = body.decode("utf-8", errors="ignore")
        # Cada máquina aparece en un div con class="maquina-item ... completada"
        completed: list[str] = []
        pattern = re.compile(
            r'<div\s+onclick="presentacion\(&#34;([^&]+)&#34;[^"]*"\s+class="maquina-item[^"]*\bcompletada\b',
            re.S,
        )
        for m in pattern.finditer(html):
            completed.append(m.group(1))
        return completed
