"""Catálogo de máquinas: descarga de `/api`, caché JSON local y modelo normalizado.

Sustituye al flujo CSV (que pasa a ser una exportación opcional). Sin Qt.

    catalog = CatalogStore(APP_DIR / "catalog.json")
    data = catalog.load_cached()              # None si no hay caché
    data = catalog.refresh(client)            # descarga + guarda
    data.machines -> list[Machine]
    data.writeups_for("Psycho") -> list[Writeup]
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://dockerlabs.es"

# Orden y etiquetas canónicas de dificultad (la API mezcla "Fácil"/"Facil")
DIFFICULTY_ORDER = ["Muy Fácil", "Fácil", "Medio", "Difícil"]
_DIFF_CANON = {
    "muy facil": "Muy Fácil",
    "facil": "Fácil",
    "medio": "Medio",
    "dificil": "Difícil",
}
DIFFICULTY_COLORS = {
    "Muy Fácil": "#4caf50",
    "Fácil": "#8bc34a",
    "Medio": "#ffb300",
    "Difícil": "#ef4444",
}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def canonical_difficulty(raw: str) -> str:
    key = _strip_accents((raw or "").strip().lower())
    return _DIFF_CANON.get(key, (raw or "").strip() or "—")


def difficulty_rank(diff: str) -> int:
    try:
        return DIFFICULTY_ORDER.index(canonical_difficulty(diff))
    except ValueError:
        return len(DIFFICULTY_ORDER)


def split_authors(raw: str) -> List[str]:
    parts = re.split(r"\s*(?:,|&|\by\b|/|\+)\s*", raw or "")
    return [p.strip() for p in parts if p and p.strip()]


def parse_date_ddmmyyyy(s: str) -> str:
    """'10/08/2024' → '2024-08-10' (para ordenar); si no cuadra devuelve ''."""
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$", s or "")
    if not m:
        return ""
    d, mo, y = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


@dataclass
class Writeup:
    id: int
    machine: str
    author: str
    url: str
    kind: str          # 'texto' | 'video'
    created_at: str


@dataclass
class Machine:
    id: int
    name: str
    difficulty: str          # canónica
    difficulty_raw: str
    css_class: str
    color: str
    author: str
    author_url: str
    date: str                # dd/mm/yyyy tal cual
    date_iso: str            # yyyy-mm-dd para ordenar
    description: str
    download_url: str
    image_url: str           # absoluta
    authors: List[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        from lab_manager import slug_from_name
        return slug_from_name(self.name)

    @property
    def machine_page_url(self) -> str:
        return f"{BASE_URL}/#{self.name}"

    def matches(self, query: str) -> bool:
        q = _strip_accents(query.lower())
        blob = _strip_accents(" ".join([self.name, self.author, self.difficulty,
                                        self.css_class, self.description]).lower())
        return all(tok in blob for tok in q.split())


@dataclass
class Catalog:
    machines: List[Machine]
    writeups: List[Writeup]
    ranking_creators: List[dict]
    ranking_writeups: List[dict]
    metadata: dict
    fetched_at: float = 0.0

    # ---- índices ----
    def by_name(self) -> Dict[str, Machine]:
        return {m.name: m for m in self.machines}

    def names(self) -> List[str]:
        return [m.name for m in self.machines]

    def writeups_for(self, name: str) -> List[Writeup]:
        key = _strip_accents(name.lower())
        return [w for w in self.writeups if _strip_accents(w.machine.lower()) == key]

    def counts_by_difficulty(self, names: Optional[Iterable[str]] = None) -> Dict[str, int]:
        sel = set(names) if names is not None else None
        out = {d: 0 for d in DIFFICULTY_ORDER}
        for m in self.machines:
            if sel is not None and m.name not in sel:
                continue
            out[m.difficulty] = out.get(m.difficulty, 0) + 1
        return out

    def latest(self, n: int = 5) -> List[Machine]:
        return sorted(self.machines, key=lambda m: (m.date_iso, m.id), reverse=True)[:n]

    @property
    def age_seconds(self) -> float:
        return time.time() - self.fetched_at if self.fetched_at else float("inf")


# ---------------------------------------------------------------------------
# Parseo del JSON de /api
# ---------------------------------------------------------------------------

def _abs_url(path: str) -> str:
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path
    return f"{BASE_URL}/{path.lstrip('/')}"


def parse_machine(raw: dict) -> Machine:
    author_raw = str(raw.get("autor", "") or "")
    diff_raw = str(raw.get("dificultad", "") or "")
    mid = int(raw.get("id") or 0)
    # La API devuelve en `imagen` un logo genérico que suele dar 404; el endpoint
    # /img/maquina/<id> (webp) es el que usa la propia web y es fiable.
    image = f"/img/maquina/{mid}" if mid else (raw.get("imagen_url") or raw.get("imagen") or "")
    return Machine(
        id=mid,
        name=str(raw.get("nombre", "") or "").strip(),
        difficulty=canonical_difficulty(diff_raw),
        difficulty_raw=diff_raw,
        css_class=str(raw.get("clase", "") or ""),
        color=str(raw.get("color", "") or "") or DIFFICULTY_COLORS.get(canonical_difficulty(diff_raw), ""),
        author=author_raw,
        author_url=str(raw.get("enlace_autor", "") or ""),
        date=str(raw.get("fecha", "") or ""),
        date_iso=parse_date_ddmmyyyy(str(raw.get("fecha", "") or "")),
        description=str(raw.get("descripcion", "") or "").strip(),
        download_url=str(raw.get("link_descarga", "") or ""),
        image_url=_abs_url(image),
        authors=split_authors(author_raw),
    )


def parse_catalog(data: dict, fetched_at: Optional[float] = None) -> Catalog:
    machines = [parse_machine(m) for m in data.get("info_maquinas", []) or []]
    machines = [m for m in machines if m.name]
    wu_raw = data.get("writeups") or {}
    writeups: List[Writeup] = []
    if isinstance(wu_raw, dict):
        items = list(wu_raw.get("textos", []) or []) + list(wu_raw.get("videos", []) or [])
    else:
        items = list(wu_raw or [])
    for w in items:
        try:
            writeups.append(Writeup(
                id=int(w.get("id") or 0), machine=str(w.get("maquina", "") or ""),
                author=str(w.get("autor", "") or ""), url=str(w.get("url", "") or ""),
                kind=str(w.get("tipo", "") or ("video" if "youtu" in str(w.get("url", "")) else "texto")),
                created_at=str(w.get("created_at", "") or ""),
            ))
        except (TypeError, ValueError):
            continue
    return Catalog(
        machines=machines,
        writeups=writeups,
        ranking_creators=list(data.get("ranking_creadores", []) or []),
        ranking_writeups=list(data.get("ranking_writeups", []) or []),
        metadata=dict(data.get("metadata", {}) or {}),
        fetched_at=fetched_at if fetched_at is not None else (data.get("_fetched_at") or 0.0),
    )


# ---------------------------------------------------------------------------
# Caché en disco
# ---------------------------------------------------------------------------

class CatalogStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load_cached(self) -> Optional[Catalog]:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return parse_catalog(data, fetched_at=float(data.get("_fetched_at") or self.path.stat().st_mtime))
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("catálogo cacheado ilegible (%s): %s", self.path, exc)
            return None

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(data)
        payload["_fetched_at"] = time.time()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def refresh(self, fetch: Callable[[], dict], retries: int = 2,
                sleep: Callable[[float], None] = time.sleep) -> Catalog:
        """Descarga `/api` (con reintentos), valida, guarda y devuelve el catálogo."""
        last: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                data = fetch()
                if not isinstance(data, dict) or not data.get("info_maquinas"):
                    raise ValueError("Respuesta de /api sin 'info_maquinas'")
                self.save(data)
                return parse_catalog(data, fetched_at=time.time())
            except Exception as exc:  # noqa: BLE001
                last = exc
                logger.warning("refresh catálogo intento %d: %s", attempt + 1, exc)
                if attempt < retries:
                    sleep(1.5 * (attempt + 1))
        assert last is not None
        raise last
