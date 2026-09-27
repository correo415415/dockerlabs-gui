"""Normalización y exportación a CSV de las máquinas de DockerLabs.

Soporta máquinas con varios autores separados por '&', 'y', 'and', ',' o ';'.
NO incluye el campo imagen_url (eliminado por requisito).
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# Campos del JSON que conservamos en el CSV principal (sin imagen_url).
PREFERRED_FIELDS = [
    "id",
    "nombre",
    "dificultad",
    "clase",
    "color",
    "autor",
    "enlace_autor",
    "fecha",
    "imagen",
    "descripcion",
    "link_descarga",
]

AUTHOR_SPLIT_RE = re.compile(r"\s(?:&|y|and|\||,|;)\s", re.IGNORECASE)


def split_authors(author_raw: str) -> list[str]:
    if not isinstance(author_raw, str):
        return []
    author_raw = author_raw.strip()
    if not author_raw:
        return []
    parts = [part.strip() for part in AUTHOR_SPLIT_RE.split(author_raw) if part.strip()]
    return parts or [author_raw]


def machine_headers(max_authors: int) -> list[str]:
    extras = [
        "autor_raw",
        "numero_autores",
        "autores_parseados",
        "enlace_autor_compartido",
        "fecha_exportacion_utc",
    ]
    extras.extend([f"autor_{i}" for i in range(1, max(1, max_authors) + 1)])
    return PREFERRED_FIELDS + extras


def exploded_headers() -> list[str]:
    return [
        "id",
        "nombre",
        "dificultad",
        "clase",
        "color",
        "fecha",
        "imagen",
        "descripcion",
        "link_descarga",
        "autor_raw",
        "numero_autores",
        "autor_individual",
        "indice_autor",
        "enlace_autor",
        "enlace_autor_compartido",
        "fecha_exportacion_utc",
    ]


def normalize_machine(machine: dict, export_time: str, max_authors: int) -> dict:
    row = {field: machine.get(field, "") for field in PREFERRED_FIELDS}
    authors = split_authors(str(machine.get("autor", "")))
    row["autor_raw"] = machine.get("autor", "")
    row["numero_autores"] = len(authors)
    row["autores_parseados"] = " | ".join(authors)
    row["enlace_autor_compartido"] = "sí" if len(authors) > 1 else "no"
    row["fecha_exportacion_utc"] = export_time
    for idx in range(max(1, max_authors)):
        row[f"autor_{idx + 1}"] = authors[idx] if idx < len(authors) else ""
    return row


def explode_machine(machine: dict, export_time: str) -> list[dict]:
    authors = split_authors(str(machine.get("autor", ""))) or [""]
    rows = []
    for idx, author in enumerate(authors, start=1):
        rows.append({
            "id": machine.get("id", ""),
            "nombre": machine.get("nombre", ""),
            "dificultad": machine.get("dificultad", ""),
            "clase": machine.get("clase", ""),
            "color": machine.get("color", ""),
            "fecha": machine.get("fecha", ""),
            "imagen": machine.get("imagen", ""),
            "descripcion": machine.get("descripcion", ""),
            "link_descarga": machine.get("link_descarga", ""),
            "autor_raw": machine.get("autor", ""),
            "numero_autores": len(authors),
            "autor_individual": author,
            "indice_autor": idx,
            "enlace_autor": machine.get("enlace_autor", ""),
            "enlace_autor_compartido": "sí" if len(authors) > 1 else "no",
            "fecha_exportacion_utc": export_time,
        })
    return rows


def export_machines(machines: Iterable[dict], output_dir: Path,
                    prefix: str = "dockerlabs",
                    api_url: str = "") -> dict:
    machines = list(machines)
    if not machines:
        raise ValueError("No hay máquinas para exportar.")

    output_dir.mkdir(parents=True, exist_ok=True)
    export_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    author_counts = [len(split_authors(str(m.get("autor", "")))) for m in machines]
    max_authors = max(author_counts) if author_counts else 1

    main_csv = output_dir / f"{prefix}_maquinas.csv"
    exploded_csv = output_dir / f"{prefix}_maquinas_por_autor.csv"
    summary_json = output_dir / f"{prefix}_resumen.json"

    normalized = [normalize_machine(m, export_time, max_authors) for m in machines]
    exploded = [r for m in machines for r in explode_machine(m, export_time)]

    with main_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=machine_headers(max_authors))
        writer.writeheader()
        writer.writerows(normalized)

    with exploded_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=exploded_headers())
        writer.writeheader()
        writer.writerows(exploded)

    summary = {
        "api_url": api_url,
        "exported_at_utc": export_time,
        "machine_count": len(machines),
        "multi_author_machine_count": sum(1 for c in author_counts if c > 1),
        "max_authors_in_single_machine": max_authors,
        "files": {
            "machines_csv": str(main_csv),
            "machines_by_author_csv": str(exploded_csv),
        },
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
