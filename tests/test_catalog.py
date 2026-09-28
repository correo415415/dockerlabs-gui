from __future__ import annotations

import json

import pytest

import catalog as cat


def sample():
    return {
        "info_maquinas": [
            {"id": 1, "nombre": "Psycho", "dificultad": "Fácil", "clase": "facil", "color": "#8bc34a",
             "autor": "Luisillo_o", "enlace_autor": "https://yt/x", "fecha": "10/08/2024",
             "imagen": "dockerlabs/images/logos/logo.png", "descripcion": "LFI y sudo.",
             "link_descarga": "https://gestion-maquinas.dockerlabs.es/dl/psycho.zip"},
            {"id": 2, "nombre": "BreakMySSH", "dificultad": "Facil", "clase": "facil", "color": "",
             "autor": "A & B", "fecha": "01/01/2025", "descripcion": "", "link_descarga": ""},
            {"id": 3, "nombre": "Hard", "dificultad": "Difícil", "autor": "Z", "fecha": "31/12/2024"},
            {"id": 4, "nombre": "Baby", "dificultad": "Muy Fácil", "autor": "Z", "fecha": "bad"},
        ],
        "maquinas": ["Psycho"],
        "metadata": {"total_creadores": 2},
        "ranking_creadores": [{"id": 9, "nombre": "Z", "maquinas": 2}],
        "ranking_writeups": [],
        "writeups": {
            "textos": [{"id": 10, "maquina": "psycho", "autor": "w", "url": "https://gh/x.md",
                        "tipo": "texto", "created_at": "2026-01-01"}],
            "videos": [{"id": 11, "maquina": "Psycho", "autor": "v", "url": "https://youtube.com/w",
                        "tipo": "video", "created_at": "2026-01-02"}],
        },
    }


def test_parse_catalog_normalizes():
    c = cat.parse_catalog(sample())
    by = c.by_name()
    assert by["Psycho"].difficulty == "Fácil" and by["BreakMySSH"].difficulty == "Fácil"
    assert by["Hard"].difficulty == "Difícil" and by["Baby"].difficulty == "Muy Fácil"
    assert by["Psycho"].image_url == "https://dockerlabs.es/dockerlabs/images/logos/logo.png"
    assert by["Hard"].image_url == "https://dockerlabs.es/img/maquina/3"
    assert by["BreakMySSH"].authors == ["A", "B"]
    assert by["BreakMySSH"].color == "#8bc34a"   # color por dificultad si falta
    assert by["Psycho"].date_iso == "2024-08-10" and by["Baby"].date_iso == ""
    assert by["Psycho"].slug == "psycho"


def test_catalog_queries():
    c = cat.parse_catalog(sample())
    assert [w.kind for w in c.writeups_for("Psycho")] == ["texto", "video"]
    assert c.counts_by_difficulty() == {"Muy Fácil": 1, "Fácil": 2, "Medio": 0, "Difícil": 1}
    assert c.counts_by_difficulty(["Hard"])["Difícil"] == 1
    assert [m.name for m in c.latest(2)] == ["BreakMySSH", "Hard"]
    assert c.by_name()["Psycho"].matches("lfi luisillo")
    assert not c.by_name()["Psycho"].matches("windows")
    assert c.by_name()["Hard"].matches("dificil")   # sin acentos


def test_difficulty_helpers():
    assert cat.canonical_difficulty("FACIL") == "Fácil"
    assert cat.canonical_difficulty("") == "—"
    assert cat.difficulty_rank("Muy Fácil") < cat.difficulty_rank("Difícil") < cat.difficulty_rank("???")


def test_store_roundtrip(tmp_path):
    store = cat.CatalogStore(tmp_path / "catalog.json")
    assert store.load_cached() is None
    c = store.refresh(lambda: sample(), sleep=lambda s: None)
    assert len(c.machines) == 4 and c.fetched_at > 0
    c2 = store.load_cached()
    assert c2 is not None and c2.names() == c.names() and c2.age_seconds < 60
    assert "_fetched_at" in json.loads((tmp_path / "catalog.json").read_text())


def test_store_refresh_retries_then_fails(tmp_path):
    store = cat.CatalogStore(tmp_path / "catalog.json")
    calls = []

    def fetch():
        calls.append(1)
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        store.refresh(fetch, retries=2, sleep=lambda s: None)
    assert len(calls) == 3


def test_store_refresh_rejects_empty(tmp_path):
    store = cat.CatalogStore(tmp_path / "catalog.json")
    with pytest.raises(ValueError):
        store.refresh(lambda: {"info_maquinas": []}, retries=0, sleep=lambda s: None)
    assert not (tmp_path / "catalog.json").exists()


def test_store_ignores_corrupt_cache(tmp_path):
    p = tmp_path / "catalog.json"; p.write_text("{not json")
    assert cat.CatalogStore(p).load_cached() is None
