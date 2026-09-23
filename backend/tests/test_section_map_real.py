"""Regression checks against the supplied registry/export, never invented locations."""
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_export_uses_tree_ids_and_preserves_ambiguous_section():
    sections = json.loads((ROOT / "frontend/public/data/sections.json").read_text())
    assert len(sections) == 3173
    assert len({s["section_id"] for s in sections}) == len(sections)
    first = next(s for s in sections if s["smvu_key"] == "15:0")
    assert first["collector"] != 15
    assert first["collector"] == first["collector_ids"][0]
    ambiguous = [s for s in sections if s["mapping_status"] == "ambiguous"]
    assert [(s["section_id"], s["smvu_key"]) for s in ambiguous] == [(1490, "798:0")]
    assert ambiguous[0]["collector"] is None
    assert len(ambiguous[0]["collector_ids"]) == 2
    for section in sections:
        if section["mapping_status"] == "resolved":
            assert section["collector_ids"] == [section["collector"]]
            assert section["collector_name"]
        else:
            assert section["collector"] is None


def test_export_matches_supplied_customer_registries():
    candidates = [Path(os.environ.get("CUSTOMER_DATA_ROOT", ROOT / "data/01_raw"))]
    candidates += [parent / "data/01_raw" for parent in ROOT.parents]
    raw = next((p for p in candidates if (p / "dataset_update_20260916/справочник_каналов_датчиков.csv").exists()), None)
    if raw is None:
        pytest.skip("supplied customer registries are not installed; set CUSTOMER_DATA_ROOT")
    spec = importlib.util.spec_from_file_location("export_sections", ROOT / "frontend/scripts/export_sections.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    saved = json.loads(module.OUT_PATH.read_text())
    rebuilt = module.from_registries(
        raw / "dataset_update_20260916/справочник_каналов_датчиков.csv",
        raw / "dataset_20260915/справочник_объектов_диспетчер.csv", saved,
    )
    assert rebuilt == saved
