from __future__ import annotations

import json
from pathlib import Path

import pytest

from PIL import Image, ImageDraw, ImageFont

from utils.quote_renderer import (
    ImageTextsAssetError,
    ImageTextsRenderEngine,
    generate_notebook_template,
    resolve_channel_assets,
    wrap_quote,
)


def _write_vault(path: Path, texts: list[str]) -> None:
    items = {
        f"quote_{index:02d}.jpg": {
            "file_path": f"C:/tmp/quote_{index:02d}.jpg",
            "extracted_text": text,
            "processed_at": "2026-09-07T00:00:00",
            "status": "success",
        }
        for index, text in enumerate(texts, start=1)
    }
    payload = {
        "ocr_momma_deploy": {
            "last_updated": "2026-09-07T00:00:00",
            "total_items": len(items),
            "items": items,
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_template(path: Path, size: tuple[int, int] = (720, 900)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = generate_notebook_template(size, variant=1)
    image.save(path)


def _channel_tree(tmp_path: Path) -> Path:
    channel_dir = tmp_path / "channels_config" / "ancient_knowledge"
    templates = channel_dir / "image_texts" / "image_texts_templates"
    templates.mkdir(parents=True)
    _write_template(templates / "notebook_01.png")
    _write_template(templates / "notebook_02.png")
    logo = Image.new("RGBA", (200, 48), (0, 0, 0, 0))
    ImageDraw.Draw(logo).text((8, 10), "LOGO", fill=(40, 40, 40, 255))
    logo_dir = channel_dir / "logo"
    logo_dir.mkdir()
    logo.save(logo_dir / "logo.png")
    return channel_dir


def test_wrap_quote_respects_newlines_and_width() -> None:
    font = ImageFont.load_default()
    lines = wrap_quote("Hello world\n\nA second paragraph here", font, max_width=40)
    assert lines[0]
    assert "" in lines


def test_generate_image_texts_writes_pngs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory_outputs = tmp_path / "factory_outputs"
    monkeypatch.setenv("OUTPUT_PATH", str(factory_outputs))
    _channel_tree(tmp_path)
    vault = tmp_path / "ocr_vault.json"
    _write_vault(
        vault,
        [
            "Guess what?\nyou are exactly\nthe mom that\nyour child needs.",
            "my child is my\nfavorite person\non Earth.",
        ],
    )
    engine = ImageTextsRenderEngine(
        vault_path=vault,
        channels_config_root=tmp_path / "channels_config",
        template_mode="cycle",
        rotate_text=False,
        seed=7,
    )
    paths = engine.generate_image_texts("ancient_knowledge", limit=2, font_size=28)
    assert len(paths) == 2
    for raw in paths:
        out = Path(raw)
        assert out.is_file()
        assert out.parent == factory_outputs / "ancient_knowledge" / "image_texts"
        with Image.open(out) as image:
            assert image.size == (1080, 1350)
            assert image.mode == "RGB"


def test_missing_dataset_raises(tmp_path: Path) -> None:
    _channel_tree(tmp_path)
    vault = tmp_path / "ocr_vault.json"
    _write_vault(vault, ["one line"])
    engine = ImageTextsRenderEngine(
        vault_path=vault,
        channels_config_root=tmp_path / "channels_config",
    )
    try:
        engine.generate_image_texts("ancient_knowledge", dataset_key="missing_key")
    except KeyError as exc:
        assert "missing_key" in str(exc)
    else:
        raise AssertionError("expected KeyError for unknown dataset")


def test_unknown_channel_raises(tmp_path: Path) -> None:
    vault = tmp_path / "ocr_vault.json"
    _write_vault(vault, ["hello"])
    engine = ImageTextsRenderEngine(
        vault_path=vault,
        channels_config_root=tmp_path / "channels_config",
    )
    try:
        engine.generate_image_texts("no_such_channel")
    except ImageTextsAssetError:
        return
    raise AssertionError("expected ImageTextsAssetError")


def test_discovers_sibling_image_texts_templates(tmp_path: Path) -> None:
    channel_dir = tmp_path / "channels_config" / "momma_circle"
    sibling = channel_dir / "image_texts_templates"
    sibling.mkdir(parents=True)
    _write_template(sibling / "notebook_01.png")
    assets = resolve_channel_assets(
        "momma_circle",
        channels_config_root=tmp_path / "channels_config",
    )
    assert assets.templates_dir == sibling
    assert len(assets.templates) == 1


def test_fallback_template_when_folder_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory_outputs = tmp_path / "factory_outputs"
    monkeypatch.setenv("OUTPUT_PATH", str(factory_outputs))
    channel_dir = tmp_path / "channels_config" / "momma_circle"
    (channel_dir / "image_texts" / "image_texts_templates").mkdir(parents=True)
    vault = tmp_path / "ocr_vault.json"
    _write_vault(vault, ["Be gentle with yourself."])
    engine = ImageTextsRenderEngine(
        vault_path=vault,
        channels_config_root=tmp_path / "channels_config",
        rotate_text=False,
        seed=1,
    )
    paths = engine.generate_image_texts("momma_circle", font_size=36)
    assert len(paths) == 1
    assert Path(paths[0]).parent == factory_outputs / "momma_circle" / "image_texts"
    with Image.open(paths[0]) as image:
        assert image.size == (1080, 1350)
