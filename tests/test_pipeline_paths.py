# -*- coding: utf-8 -*-
"""Guard: production writers must honor OUTPUT_PATH / ASSETS_PATH."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from utils.pipeline_paths import (
    assets_root,
    coerce_outputs_path,
    outputs_root,
    page_assets_dir,
    page_outputs_dir,
)

_REPO = Path(__file__).resolve().parents[1]
_FORBIDDEN = re.compile(
    r"""(?:ENGINE_ROOT|_ENGINE_ROOT|_FACTORY_ROOT|factory_root\(\)|PROJECT_ROOT)\s*/\s*[\"']outputs[\"']"""
)
_SCAN_SKIP = {
    "utils/pipeline_paths.py",
    "tools/migrate_local_outputs_to_factory.py",
    "tools/repair_doubled_output_paths.py",
    "tests/test_pipeline_paths.py",
}


def test_outputs_root_honors_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "factory-outputs"
    monkeypatch.setenv("OUTPUT_PATH", str(target))
    assert outputs_root() == target
    assert page_outputs_dir("ancient_knowledge") == target / "ancient_knowledge"
    assert page_assets_dir("ancient_knowledge") == target / "ancient_knowledge" / "assets"


def test_assets_root_honors_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "factory-assets"
    monkeypatch.setenv("ASSETS_PATH", str(target))
    assert assets_root() == target


def test_coerce_pack_relative_outputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "factory-outputs"
    monkeypatch.setenv("OUTPUT_PATH", str(target))
    assert coerce_outputs_path("outputs/ancient_knowledge") == target / "ancient_knowledge"


def test_live_env_is_not_repo_outputs() -> None:
    """When .env sets OUTPUT_PATH, the live resolver must not return <repo>/outputs."""
    raw = (os.getenv("OUTPUT_PATH") or "").strip()
    if not raw:
        pytest.skip("OUTPUT_PATH unset in this process")
    resolved = outputs_root()
    assert resolved == Path(raw)
    assert resolved.resolve() != (_REPO / "outputs").resolve()


def test_no_repo_outputs_literals_in_production() -> None:
    hits: list[str] = []
    for path in _REPO.rglob("*.py"):
        rel = path.relative_to(_REPO).as_posix()
        if rel in _SCAN_SKIP or "/__pycache__/" in rel:
            continue
        if rel.startswith(("outputs/", ".venv/", "venv/")):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if _FORBIDDEN.search(text):
            hits.append(rel)
    assert hits == [], f"hardcoded repo outputs/ still present in: {hits}"
