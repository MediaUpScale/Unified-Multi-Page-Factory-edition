# -*- coding: utf-8 -*-
"""Cursor History Recovery Utility."""

import argparse
import datetime
import json
import os
import shutil
from pathlib import Path
from typing import List, Optional
from urllib.parse import unquote, urlparse

CURSOR_HISTORY_DIR = Path(os.path.expandvars(r"%APPDATA%\Cursor\User\History"))


def parse_timestamp_ms(timestamp_ms: int) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(timestamp_ms / 1000.0)


def recover_cursor_snapshots(
    project_dir: Optional[str] = None,
    target_date: Optional[str] = None,
    time_start: Optional[str] = None,
    time_end: Optional[str] = None,
    target_files: Optional[List[str]] = None,
    history_dir: Path = CURSOR_HISTORY_DIR,
    undo_only: bool = True,
    dry_run: bool = False,
) -> List[str]:
    if not history_dir.exists():
        print(f"[!] Diretório de histórico não encontrado: {history_dir}")
        return []

    parsed_date = None
    if target_date:
        parsed_date = datetime.datetime.strptime(target_date, "%Y-%m-%d").date()

    t_start = datetime.datetime.strptime(time_start, "%H:%M").time() if time_start else None
    t_end = datetime.datetime.strptime(time_end, "%H:%M").time() if time_end else None

    norm_project_dir = str(Path(project_dir)).lower() if project_dir else None
    restored_files: List[str] = []

    print(f"[*] Varredura no Histórico do Cursor em: {history_dir}")
    if target_date:
        print(f"[*] Data Alvo: {target_date} | Janela: {time_start or '00:00'} -> {time_end or '23:59'}")
    if project_dir:
        print(f"[*] Projeto Alvo: {project_dir}")

    for folder in history_dir.iterdir():
        if not folder.is_dir():
            continue

        entries_file = folder / "entries.json"
        if not entries_file.exists():
            continue

        try:
            with open(entries_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            raw_url = data.get("resource", "")
            file_path_str = unquote(urlparse(raw_url).path).lstrip("/")

            if ":" in file_path_str and not file_path_str.startswith("/"):
                file_path_str = file_path_str[0].upper() + file_path_str[1:]

            dest_path = Path(file_path_str)
            dest_path_lower = str(dest_path).lower()

            if norm_project_dir and norm_project_dir not in dest_path_lower:
                continue

            if target_files and dest_path.name not in target_files:
                continue

            entries = data.get("entries", [])

            for entry in reversed(entries):
                entry_id = entry.get("id")
                source = entry.get("source", "")
                ts_ms = entry.get("timestamp")

                if not entry_id or not ts_ms:
                    continue

                entry_dt = parse_timestamp_ms(ts_ms)
                entry_date = entry_dt.date()
                entry_time = entry_dt.time()

                if parsed_date and entry_date != parsed_date:
                    continue

                if t_start and entry_time < t_start:
                    continue
                if t_end and entry_time > t_end:
                    continue

                is_undo_event = any(kw in source for kw in ["Undo", "Reject", "Diff", "Discard"])
                if undo_only and not is_undo_event:
                    continue

                snapshot_file = folder / entry_id
                if snapshot_file.exists():
                    time_str = entry_dt.strftime("%Y-%m-%d %H:%M:%S")
                    if dry_run:
                        print(f"[DRY RUN] [{time_str}] [{source or 'Save'}] {dest_path.name} -> {snapshot_file}")
                    else:
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(snapshot_file, dest_path)
                        print(f"[SUCCESS] [{time_str}] Restaurado: {dest_path.name} <-- {snapshot_file.name}")
                        restored_files.append(str(dest_path))
                    break
        except Exception:
            continue

    print(f"\n[SUMMARY] {len(restored_files)} arquivo(s) processado(s).")
    return restored_files


def main():
    parser = argparse.ArgumentParser(description="Cursor History Recovery Utility")
    parser.add_argument("--project-dir", type=str, help="Caminho do canal/projeto alvo")
    parser.add_argument("--date", type=str, help="Data exata YYYY-MM-DD")
    parser.add_argument("--time-start", type=str, help="Hora inicial HH:MM")
    parser.add_argument("--time-end", type=str, help="Hora final HH:MM")
    parser.add_argument("--files", nargs="+", help="Lista de arquivos específicos")
    parser.add_argument("--all-events", action="store_true", help="Ignora a trava de Undo")
    parser.add_argument("--dry-run", action="store_true", help="Apenas simula")

    args = parser.parse_args()

    recover_cursor_snapshots(
        project_dir=args.project_dir,
        target_date=args.date,
        time_start=args.time_start,
        time_end=args.time_end,
        target_files=args.files,
        undo_only=not args.all_events,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()