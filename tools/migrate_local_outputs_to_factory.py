# -*- coding: utf-8 -*-
"""One-time move of a local ``outputs/<channel>/`` tree onto OUTPUT_PATH.

Copies first, rewrites embedded path references, verifies the destination,
then deletes the local source only after the destination is complete.

Usage
-----
    python tools/migrate_local_outputs_to_factory.py
    python tools/migrate_local_outputs_to_factory.py --channel ancient_knowledge
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

_FACTORY_ROOT = Path(__file__).resolve().parents[1]
if str(_FACTORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_FACTORY_ROOT))

from utils.pipeline_paths import outputs_root  # noqa: E402

_REWRITE_SUFFIXES = {".json", ".txt", ".csv", ".md"}
_XLSX_SUFFIXES = {".xlsx", ".xlsm"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _slash_variants(path: Path) -> list[str]:
    raw = str(path)
    posix = path.as_posix()
    variants = [raw, posix]
    if raw.endswith(("\\", "/")):
        variants.extend([raw.rstrip("\\/"), posix.rstrip("/")])
    else:
        variants.extend([raw + "\\", posix + "/"])
    # De-dupe while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for item in variants:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _rewrite_string(text: str, replacements: list[tuple[str, str]]) -> str:
    updated = text
    for old, new in replacements:
        if not old or old not in updated:
            continue
        # Never apply a relative ``outputs/<channel>`` remap to a string that
        # is already rooted at the destination (avoids doubling the prefix).
        if not Path(old).is_absolute() and (
            updated.startswith(new) or new in updated
        ):
            continue
        updated = updated.replace(old, new)
    return updated


def _rewrite_json_value(obj, replacements: list[tuple[str, str]]):
    if isinstance(obj, str):
        return _rewrite_string(obj, replacements)
    if isinstance(obj, list):
        return [_rewrite_json_value(item, replacements) for item in obj]
    if isinstance(obj, dict):
        return {key: _rewrite_json_value(val, replacements) for key, val in obj.items()}
    return obj


def _build_replacements(old_outputs: Path, new_outputs: Path, channel: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for old, new in zip(_slash_variants(old_outputs), _slash_variants(new_outputs)):
        pairs.append((old, new))
    # Relative factory paths written before the env root was honored.
    rel_posix = f"outputs/{channel}"
    rel_win = f"outputs\\{channel}"
    dest_page = new_outputs / channel
    pairs.append((rel_posix, dest_page.as_posix()))
    pairs.append((rel_win, str(dest_page)))
    # Longest old-string first so the more specific prefix wins.
    pairs.sort(key=lambda item: len(item[0]), reverse=True)
    return pairs


_MERGE_LIST_FILES = {
    "asset_library.json": ("assets", "local_path"),
    "content_library.json": ("items", "local_image_path"),
}


def _merge_catalog(src_file: Path, dest_file: Path, list_key: str, id_key: str) -> None:
    """Keep destination catalog entries and append any source rows it lacks."""
    try:
        src_payload = json.loads(src_file.read_text(encoding="utf-8"))
        dest_payload = json.loads(dest_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        shutil.copy2(src_file, dest_file)
        return
    src_rows = src_payload.get(list_key) if isinstance(src_payload, dict) else src_payload
    dest_rows = dest_payload.get(list_key) if isinstance(dest_payload, dict) else dest_payload
    if not isinstance(src_rows, list) or not isinstance(dest_rows, list):
        shutil.copy2(src_file, dest_file)
        return
    seen = {str(row.get(id_key) or "") for row in dest_rows if isinstance(row, dict)}
    extra = [
        row for row in src_rows
        if isinstance(row, dict) and str(row.get(id_key) or "") not in seen
    ]
    dest_rows.extend(extra)
    if isinstance(dest_payload, dict):
        dest_payload[list_key] = dest_rows
        dest_file.write_text(
            json.dumps(dest_payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    else:
        dest_file.write_text(
            json.dumps(dest_rows, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    print(f"  merged  {dest_file.name} (+{len(extra)} new rows)")


def _copy_tree(src: Path, dest: Path) -> list[Path]:
    copied: list[Path] = []
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        target = dest / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        merge_spec = _MERGE_LIST_FILES.get(item.name)
        if merge_spec and target.is_file():
            _merge_catalog(item, target, *merge_spec)
        else:
            shutil.copy2(item, target)
            print(f"  copied  {rel}")
        copied.append(target)
    return copied


def _rewrite_file(path: Path, replacements: list[tuple[str, str]]) -> bool:
    suffix = path.suffix.lower()
    if suffix in _XLSX_SUFFIXES:
        return _rewrite_xlsx(path, replacements)
    if suffix not in _REWRITE_SUFFIXES:
        return False
    if suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"  skip json parse  {path.name}: {exc}")
            return False
        rewritten = _rewrite_json_value(payload, replacements)
        if rewritten != payload:
            path.write_text(
                json.dumps(rewritten, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print(f"  rewrote {path.name}")
            return True
        return False
    original = path.read_text(encoding="utf-8")
    updated = _rewrite_string(original, replacements)
    if updated != original:
        path.write_text(updated, encoding="utf-8")
        print(f"  rewrote {path.name}")
        return True
    return False


def _rewrite_xlsx(path: Path, replacements: list[tuple[str, str]]) -> bool:
    try:
        import openpyxl
    except ImportError:
        print("  openpyxl missing — skipped xlsx rewrite for", path.name)
        return False
    workbook = openpyxl.load_workbook(path)
    changed = False
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    updated = _rewrite_string(cell.value, replacements)
                    if updated != cell.value:
                        cell.value = updated
                        changed = True
    if changed:
        workbook.save(path)
        print(f"  rewrote {path.name}")
    return changed


def _count_files(root: Path, suffix: str | None = None) -> int:
    if not root.exists():
        return 0
    files = [p for p in root.rglob("*") if p.is_file()]
    if suffix:
        files = [p for p in files if p.suffix.lower() == suffix]
    return len(files)


def _verify(src: Path, dest: Path, old_outputs: Path) -> list[str]:
    errors: list[str] = []
    src_files = [p for p in src.rglob("*") if p.is_file()]
    dest_files = [p for p in dest.rglob("*") if p.is_file()]
    src_rel = {p.relative_to(src).as_posix() for p in src_files}
    dest_rel = {p.relative_to(dest).as_posix() for p in dest_files}
    missing = sorted(src_rel - dest_rel)
    if missing:
        errors.append(f"destination missing {len(missing)} file(s): {missing[:8]}")

    src_images = _count_files(src / "assets", ".png") + _count_files(src / "assets", ".jpg")
    dest_images = _count_files(dest / "assets", ".png") + _count_files(dest / "assets", ".jpg")
    if dest_images < src_images:
        errors.append(f"image count dest={dest_images} < src={src_images}")
    if dest_images < 40:
        errors.append(f"expected at least 40 image variants, found {dest_images}")

    src_library = _count_files(src / "library", ".json")
    dest_library = _count_files(dest / "library", ".json")
    if dest_library < src_library:
        errors.append(f"library json dest={dest_library} < src={src_library}")

    for name in ("asset_library.json", "automated_bulk_posts_import.xlsx"):
        if (src / name).is_file() and not (dest / name).is_file():
            errors.append(f"missing {name} on destination")

    stale = str(old_outputs)
    stale_hits: list[str] = []
    for path in dest_files:
        if path.suffix.lower() not in (_REWRITE_SUFFIXES | _XLSX_SUFFIXES):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if stale in text or stale.replace("\\", "/") in text:
            stale_hits.append(path.name)
    if stale_hits:
        errors.append(f"old path still present in: {stale_hits}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", default="ancient_knowledge")
    parser.add_argument(
        "--source",
        default="",
        help="Local source tree (default: <repo>/outputs/<channel>)",
    )
    parser.add_argument(
        "--keep-source",
        action="store_true",
        help="Copy + rewrite only; do not delete the local tree.",
    )
    args = parser.parse_args()

    channel = args.channel.strip().lower()
    old_outputs = _FACTORY_ROOT / "outputs"
    new_outputs = outputs_root()
    src = Path(args.source) if args.source else old_outputs / channel
    dest = new_outputs / channel

    print(f"channel     : {channel}")
    print(f"source      : {src}")
    print(f"destination : {dest}")
    print(f"OUTPUT_PATH : {new_outputs}")

    if not src.is_dir():
        print(f"[ERROR] source not found: {src}")
        return 1
    if dest.resolve() == src.resolve():
        print("[ERROR] source and destination are the same path — nothing to migrate.")
        return 1
    if not str(new_outputs).strip() or new_outputs.resolve() == old_outputs.resolve():
        print("[ERROR] OUTPUT_PATH is unset or still points at the local repo outputs/.")
        return 1

    print("\n[1/4] Copying files…")
    copied = _copy_tree(src, dest)
    print(f"copied {len(copied)} files")

    print("\n[2/4] Rewriting path references in copied files…")
    replacements = _build_replacements(old_outputs, new_outputs, channel)
    rewritten = 0
    copied_rel = {path.relative_to(dest) for path in copied}
    for rel in copied_rel:
        path = dest / rel
        if path.is_file() and _rewrite_file(path, replacements):
            rewritten += 1
    print(f"rewrote {rewritten} files")

    print("\n[3/4] Verifying destination…")
    errors = _verify(src, dest, old_outputs)
    dest_images = _count_files(dest / "assets", ".png") + _count_files(dest / "assets", ".jpg")
    dest_library = _count_files(dest / "library", ".json")
    dest_total = _count_files(dest)
    print(f"destination images  : {dest_images}")
    print(f"destination library : {dest_library}")
    print(f"destination files   : {dest_total}")
    if errors:
        print("[ERROR] verification failed:")
        for err in errors:
            print(f"  - {err}")
        print("Local source was NOT deleted.")
        return 1
    print("verification OK")

    if args.keep_source:
        print("\n[4/4] --keep-source set; leaving local tree in place.")
        return 0

    print("\n[4/4] Removing local source…")
    shutil.rmtree(src)
    if src.exists():
        print(f"[ERROR] failed to remove {src}")
        return 1
    print(f"removed {src}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
