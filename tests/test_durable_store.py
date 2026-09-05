# -*- coding: utf-8 -*-
"""C: primary / G: mirror durable store + cleanup protection."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.durable_store import (
    classify_state_path,
    hydrate_state_file,
    peer_paths,
    reset_restore_cache,
    restore_channel_state,
    sync_state_file,
    write_state_json,
)
from modules.metadata_auditor import already_cataloged, build_generic_record, title_from_filename
from utils.pipeline_paths import (
    ProtectedStateError,
    channel_store_dir,
    contains_protected_store,
    discover_channel_ids,
    is_ephemeral_artifact_dir,
    is_protected_state_path,
    safe_rmtree,
)


@pytest.fixture
def dual_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    store_root = tmp_path / "channels_config"
    outputs = tmp_path / "gdrive_outputs"
    monkeypatch.setenv("CHANNEL_STORE_ROOT", str(store_root))
    monkeypatch.setenv("OUTPUT_PATH", str(outputs))
    reset_restore_cache()
    return {"store_root": store_root, "outputs": outputs, "tmp": tmp_path}


def test_channel_store_dir_is_under_channels_config(dual_roots: dict[str, Path]) -> None:
    path = channel_store_dir("ancient_knowledge")
    assert path == dual_roots["store_root"] / "ancient_knowledge" / "store"
    assert is_protected_state_path(path)
    assert contains_protected_store(dual_roots["store_root"] / "ancient_knowledge")


def test_safe_rmtree_refuses_store(dual_roots: dict[str, Path]) -> None:
    store = channel_store_dir("aiwake", create=True)
    (store / "content_library.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ProtectedStateError):
        safe_rmtree(store)
    with pytest.raises(ProtectedStateError):
        safe_rmtree(dual_roots["store_root"] / "aiwake")
    assert (store / "content_library.json").is_file()


def test_safe_rmtree_allows_ephemeral(dual_roots: dict[str, Path]) -> None:
    ephemeral = dual_roots["outputs"] / "ancient_knowledge" / "tmp"
    ephemeral.mkdir(parents=True)
    (ephemeral / "scratch.bin").write_bytes(b"x")
    assert is_ephemeral_artifact_dir(ephemeral)
    safe_rmtree(ephemeral)
    assert not ephemeral.exists()


def test_dual_write_and_restore(dual_roots: dict[str, Path]) -> None:
    path = write_state_json("wonder_feed", "content_library.json", [{"topic": "hello"}])
    assert path == channel_store_dir("wonder_feed") / "content_library.json"
    mirror_store = dual_roots["outputs"] / "wonder_feed" / "store" / "content_library.json"
    legacy = dual_roots["outputs"] / "wonder_feed" / "content_library.json"
    assert json.loads(mirror_store.read_text(encoding="utf-8"))[0]["topic"] == "hello"
    assert json.loads(legacy.read_text(encoding="utf-8"))[0]["topic"] == "hello"

    path.unlink()
    reset_restore_cache()
    counts = restore_channel_state("wonder_feed", force=True)
    assert counts["restored"] == 1
    assert json.loads(path.read_text(encoding="utf-8"))[0]["topic"] == "hello"


def test_hydrate_from_legacy_root(dual_roots: dict[str, Path]) -> None:
    legacy = dual_roots["outputs"] / "momma_circle" / "facebook_history.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"posted": ["a.mp4"]}', encoding="utf-8")
    local = channel_store_dir("momma_circle") / "facebook_history.json"
    hydrate_state_file(local)
    assert json.loads(local.read_text(encoding="utf-8"))["posted"] == ["a.mp4"]


def test_sync_from_mirror_root_updates_local(dual_roots: dict[str, Path]) -> None:
    legacy = dual_roots["outputs"] / "master_mei" / "asset_library.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"assets": [1]}', encoding="utf-8")
    assert classify_state_path(legacy)[1] == "mirror_root"  # type: ignore[index]
    sync_state_file(legacy)
    local = channel_store_dir("master_mei") / "asset_library.json"
    assert json.loads(local.read_text(encoding="utf-8"))["assets"] == [1]


def test_unclassified_tmp_path_has_no_peers(tmp_path: Path) -> None:
    orphan = tmp_path / "content_library.json"
    orphan.write_text("[]", encoding="utf-8")
    assert classify_state_path(orphan) is None
    assert peer_paths(orphan) == []


def test_discover_channel_ids_skips_pycache(dual_roots: dict[str, Path]) -> None:
    (dual_roots["store_root"] / "ancient_knowledge").mkdir(parents=True)
    (dual_roots["store_root"] / "__pycache__").mkdir()
    (dual_roots["store_root"] / ".hidden").mkdir()
    assert discover_channel_ids() == ["ancient_knowledge"]


def test_generic_record_and_catalog_match(tmp_path: Path) -> None:
    video = tmp_path / "clips" / "hidden_temple_reveal.mp4"
    assert title_from_filename(video) == "hidden temple reveal"
    record = build_generic_record("ancient_knowledge", video)
    assert record.channel_id == "ancient_knowledge"
    assert record.post_type == "SEQUENCE_REEL"
    rows = [record.to_library_row()]
    assert already_cataloged(rows, video)
    assert not already_cataloged(rows, tmp_path / "other.mp4")
