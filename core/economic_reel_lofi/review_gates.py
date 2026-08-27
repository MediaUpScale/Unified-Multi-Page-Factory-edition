# -*- coding: utf-8 -*-
"""Human review gates: Gate 1 after Stage 1 (script), Gate 2 after Stage 3 (assembled prompts).

When review_required=true, the pipeline writes a hold artifact and returns
without image or TTS calls. Resume with --lofi-resume-from PATH
--lofi-approve-gate 1|2. Locked --lofi-script auto-clears Gate 1 only.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PIPELINE_VERSION = "four_stage_v1"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_state(path: Path, state: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = dict(state)
    state["pipeline_version"] = PIPELINE_VERSION
    state["updated_at"] = _utc()
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_state(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    if p.is_dir():
        for name in (
            "pipeline_state.json",
            "gate2_hold.json",
            "gate1_hold.json",
        ):
            cand = p / name
            if cand.is_file():
                p = cand
                break
        else:
            jsons = sorted(p.glob("lofi_hold_gate*.json")) + sorted(p.glob("lofi_pipeline_*.json"))
            if not jsons:
                raise FileNotFoundError(f"no pipeline state in {path}")
            p = jsons[-1]
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"pipeline state is not an object: {p}")
    if isinstance(raw.get("script"), dict) and "gate1" not in raw:
        # Locked script JSON / stills meta — wrap.
        return {
            "pipeline_version": PIPELINE_VERSION,
            "source": str(p),
            "script": raw.get("script") if isinstance(raw.get("script"), dict) else raw,
            "gate1": {"status": "approved", "reason": "loaded_script_json"},
            "gate2": {"status": "pending"},
            "stage_completed": 1,
        }
    raw["source"] = str(p)
    return raw


def gate_status(state: dict[str, Any], gate: int) -> str:
    rec = state.get(f"gate{gate}") or {}
    return str(rec.get("status") or "pending").strip().lower()


def mark_approved(state: dict[str, Any], gate: int, *, reason: str = "cli") -> dict[str, Any]:
    state[f"gate{gate}"] = {
        "status": "approved",
        "approved_at": _utc(),
        "reason": reason,
    }
    return state


def auto_pass_gates(state: dict[str, Any]) -> dict[str, Any]:
    for g in (1, 2):
        state[f"gate{g}"] = {
            "status": "auto_pass",
            "approved_at": _utc(),
            "reason": "review_required=false",
        }
    return state


def hold_result_payload(
    *,
    gate: int,
    state_path: Path,
    script: dict[str, Any] | None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "hold_gate": gate,
        "state_path": str(state_path),
        "resume_cmd": (
            f"--lofi-resume-from {state_path} --lofi-approve-gate {gate}"
        ),
        "errors": list(errors or []),
        "theme": str((script or {}).get("theme") or ""),
        "scene_count": len((script or {}).get("lines") or []),
    }
