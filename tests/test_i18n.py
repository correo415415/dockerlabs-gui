"""i18n: detección de idioma, catálogo y tr()."""
from __future__ import annotations

import json

import i18n
from settings_store import UserSettings


def test_detect_from_env_variables():
    assert i18n.detect_system_language({"LANG": "es_ES.UTF-8"}, qt=False) == "es"
    assert i18n.detect_system_language({"LANGUAGE": "es:en"}, qt=False) == "es"
    assert i18n.detect_system_language({"LC_ALL": "en_US.UTF-8"}, qt=False) == "en"
    # idiomas sin traducción → inglés
    assert i18n.detect_system_language({"LANG": "fr_FR.UTF-8"}, qt=False) == "en"
    assert i18n.detect_system_language({"LANG": "de"}, qt=False) == "en"


def test_detect_ignores_c_locale(monkeypatch):
    monkeypatch.setattr(i18n.locale, "getlocale", lambda: (None, None))
    assert i18n.detect_system_language({"LANG": "C.UTF-8"}, qt=False) == "en"
    assert i18n.detect_system_language({}, qt=False) == "en"


def test_set_language_and_tr(tmp_path, monkeypatch):
    (tmp_path / "en.json").write_text(json.dumps({"Máquinas": "Machines", "Vacío": ""}), encoding="utf-8")
    monkeypatch.setattr(i18n, "_locales_dir", lambda: tmp_path)
    try:
        assert i18n.set_language("es") == "es"
        assert i18n.tr("Máquinas") == "Máquinas"
        assert i18n.set_language("en") == "en"
        assert i18n.tr("Máquinas") == "Machines"
        assert i18n.tr("Vacío") == "Vacío"                 # traducción vacía → texto fuente
        assert i18n.tr("Sin traducir") == "Sin traducir"   # clave ausente → texto fuente
        assert i18n.tr("") == ""
        # 'auto' resuelve con el entorno
        monkeypatch.setattr(i18n, "detect_system_language", lambda: "es")
        assert i18n.set_language("auto") == "es"
        assert i18n.current_language() == "es"
    finally:
        i18n.set_language("es")


def test_broken_catalog_falls_back(tmp_path, monkeypatch):
    (tmp_path / "en.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(i18n, "_locales_dir", lambda: tmp_path)
    try:
        assert i18n.set_language("en") == "en"
        assert i18n.tr("Máquinas") == "Máquinas"
    finally:
        i18n.set_language("es")


def test_settings_language_validation():
    assert UserSettings.from_dict({}).language == "auto"
    assert UserSettings.from_dict({"language": "en"}).language == "en"
    assert UserSettings.from_dict({"language": "klingon"}).language == "auto"


def test_real_en_catalog_is_valid_json_and_has_no_empty_values():
    data = i18n.load_catalog("en")
    raw = json.loads((i18n._locales_dir() / "en.json").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert all(v for v in raw.values()), "hay traducciones vacías en locales/en.json"
    assert len(data) == len(raw)
