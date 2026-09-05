# -*- coding: utf-8 -*-
"""Strip doubled OUTPUT_PATH prefixes left by an overly broad path rewrite."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_FACTORY_ROOT = Path(__file__).resolve().parents[1]
if str(_FACTORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_FACTORY_ROOT))

from utils.pipeline_paths import page_outputs_dir

_TEXT_SUFFIXES = {".json", ".txt", ".csv", ".md"}
_XLSX_SUFFIXES = {".xlsx", ".xlsm"}
_MARKERS = ("Unified Multi-Page Factory\\G:", "Unified Multi-Page Factory/G:")


def _fix_string(value: str) -> str:
    updated = value
    while any(marker in updated for marker in _MARKERS):
        idx = updated.find("\\G:")
        if idx == -1:
            idx = updated.find("/G:")
        if idx == -1:
            break
        updated = updated[idx + 1 :]
    return updated


def _fix_value(obj):
    if isinstance(obj, str):
        return _fix_string(obj)
    if isinstance(obj, list):
        return [_fix_value(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _fix_value(val) for key, val in obj.items()}
    return obj


def _fix_xlsx(path: Path) -> bool:
    import openpyxl

    workbook = openpyxl.load_workbook(path)
    changed = False
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    updated = _fix_string(cell.value)
                    if updated != cell.value:
                        cell.value = updated
                        changed = True
    if changed:
        workbook.save(path)
    return changed


def main() -> int:
    dest = page_outputs_dir("ancient_knowledge")
    if not dest.is_dir():
        print(f"[ERROR] destination not found: {dest}")
        return 1
    fixed = 0
    scanned = 0
    for path in dest.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in (_TEXT_SUFFIXES | _XLSX_SUFFIXES):
            continue
        scanned += 1
        try:
            if suffix in _XLSX_SUFFIXES:
                if _fix_xlsx(path):
                    fixed += 1
                    print(f"  fixed {path.name}")
                continue
            if suffix == ".json":
                payload = json.loads(path.read_text(encoding="utf-8"))
                updated = _fix_value(payload)
                if updated != payload:
                    path.write_text(
                        json.dumps(updated, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                    fixed += 1
                    print(f"  fixed {path.name}")
                continue
            original = path.read_text(encoding="utf-8")
            updated = _fix_string(original)
            if updated != original:
                path.write_text(updated, encoding="utf-8")
                fixed += 1
                print(f"  fixed {path.name}")
        except (OSError, ValueError) as exc:
            print(f"  skip {path.name}: {exc}")
    print(f"scanned {scanned} files, fixed {fixed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
