# -*- coding: utf-8 -*-
"""
ECONOMIC_REEL_LOFI orchestrator.

ThemeSelector → ScriptGenerator → Validator → ImageGen → VisualQA → Assembler
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from core_engine.economic_reel_lofi import config as lofi_cfg
from core_engine.economic_reel_lofi import lofi_collections as rag
from core_engine.economic_reel_lofi.assembler import (
    assemble_lofi_reel,
    compute_caption_scene_duration_s,
)
from core_engine.economic_reel_lofi.image_gen import (
    LOFI_IMAGE_HEIGHT,
    LOFI_IMAGE_STEPS,
    LOFI_IMAGE_WIDTH,
    generate_scene_image,
)
from core_engine.economic_reel_lofi.riso_prompt_bank import (
    assign_riso_prompts_for_scenes,
    export_active_library_diff,
)
from core_engine.economic_reel_lofi.script_agent import (
    _sanitize_caption_typos,
    generate_script,
    get_script_llm_call_log,
    note_batch_structure_id,
    repair_script_captions,
    reset_batch_structure_ids,
    reset_script_llm_call_log,
)
from core_engine.economic_reel_lofi.validator_agent import validate_script

_LOG = logging.getLogger(__name__)


def _tts_text_with_breaks(caption: str) -> str:
    """Insert ElevenLabs SSML pauses at commas / dashes. Caption on-screen stays clean."""
    text = (caption or "").strip()
    if not text:
        return text
    text = re.sub(r"\s*[—–]\s*", ' <break time="0.40s" /> ', text)
    text = re.sub(r",\s+", ', <break time="0.30s" /> ', text)
    text = re.sub(r";\s+", '; <break time="0.32s" /> ', text)
    return " ".join(text.split())


@dataclass
class LofiItemResult:
    ok: bool
    video_path: str | None = None
    meta_path: str | None = None
    module: str = ""
    theme: str = ""
    hook_type: str = ""
    scene_count: int = 0
    duration_s: float = 0.0
    manual_review: bool = False
    errors: list[str] = field(default_factory=list)
    script: dict[str, Any] | None = None
    work_dir: str | None = None
    stills_only: bool = False


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _engine_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_lofi_thumb_gray(
    image_path: Path, size: int = 512
) -> tuple[np.ndarray, np.ndarray]:
    from PIL import Image as PILImage

    im = PILImage.open(image_path).convert("RGB")
    im.thumbnail((size, size))
    arr = np.array(im, dtype=np.float32)
    gray = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    return arr, gray


def _linework_stats(image_path: Path, size: int = 512) -> dict[str, float]:
    """Color/edge stats already used by the linework guard (512 thumb)."""
    arr, gray = _load_lofi_thumb_gray(image_path, size=size)
    lap = (
        gray[1:-1, 1:-1] * 4
        - gray[:-2, 1:-1]
        - gray[2:, 1:-1]
        - gray[1:-1, :-2]
        - gray[1:-1, 2:]
    )
    q = (arr.astype(np.uint8) // 16).reshape(-1, 3)
    return {
        "uniq16": float(np.unique(q, axis=0).shape[0]),
        "lap_var": float(lap.var()),
        "edge": float(
            np.abs(gray[:, 1:] - gray[:, :-1]).mean()
            + np.abs(gray[1:, :] - gray[:-1, :]).mean()
        ),
        "std": float(gray.std()),
        "hard": float((np.abs(lap) > 40).mean()),
    }


def _assess_linework_complexity(image_path: Path) -> tuple[bool, list[str]]:
    """Reject near-flat graphics with no inked structure (grief scene-3 failure)."""
    try:
        st = _linework_stats(image_path, size=512)
        uniq = int(st["uniq16"])
        lap_var = float(st["lap_var"])
        edge = float(st["edge"])
        std = float(st["std"])
        flaws: list[str] = []
        if uniq < 90:
            flaws.append(f"low color structure uniq16={uniq}")
        if lap_var < 80 and edge < 3.0:
            flaws.append(f"flat/no linework lap_var={lap_var:.1f} edge={edge:.2f}")
        if std < 22 and uniq < 140:
            flaws.append(f"near-flat luminance std={std:.1f}")
        if flaws:
            print(
                f"[LOFI image QA] REJECT {image_path.name} "
                f"uniq16={uniq} lap_var={lap_var:.1f} edge={edge:.2f} std={std:.1f} "
                f"| {'; '.join(flaws)}"
            )
        return (len(flaws) == 0), flaws
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("linework QA failed for %s (%s)", image_path.name, exc)
        return True, []


_INTRUDER_SUBJECTS = frozenset({"object_focus", "silhouette"})
_INTRUDER_SELF = re.compile(
    r"\b(mugs?|cups?|coffee|glasses?|laptops?|computers?|keyboards?)\b",
    re.I,
)
# Compact requested objects: a larger architectural dark plane (door/wall)
# is often the biggest blob; the object itself then looks like a "mug".
_COMPACT_INTENDED_RE = re.compile(
    r"\b(suitcase|bag|kettle|chair|pillow|blanket|coat|lamp|radio|plate|"
    r"journal|notebook|letter|envelope|stone|stones|rock|rocks|pebble|"
    r"pebbles|boulder|box|boxes|paper|newspaper)\b",
    re.I,
)


def _uniform_gray(gray: np.ndarray, k: int) -> np.ndarray:
    try:
        from scipy.ndimage import uniform_filter

        return uniform_filter(gray, size=k, mode="nearest")
    except Exception:  # noqa: BLE001
        kernel = np.ones((k,), dtype=np.float32) / k
        tmp = np.apply_along_axis(lambda m: np.convolve(m, kernel, mode="same"), 1, gray)
        return np.apply_along_axis(lambda m: np.convolve(m, kernel, mode="same"), 0, tmp)


def _connected_components(mask: np.ndarray, min_area: int) -> list[dict[str, float]]:
    h, w = mask.shape
    n = h * w
    seen = np.zeros((h, w), dtype=np.uint8)
    out: list[dict[str, float]] = []
    for y in range(h):
        for x in range(w):
            if not mask[y, x] or seen[y, x]:
                continue
            stack = [(y, x)]
            seen[y, x] = 1
            ys: list[int] = []
            xs: list[int] = []
            while stack:
                cy, cx = stack.pop()
                ys.append(cy)
                xs.append(cx)
                for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = 1
                        stack.append((ny, nx))
            area = len(ys)
            if area < min_area:
                continue
            y0, y1 = min(ys), max(ys)
            x0, x1 = min(xs), max(xs)
            bw, bh = x1 - x0 + 1, y1 - y0 + 1
            out.append(
                {
                    "frac": area / n,
                    "bbox_frac": (bw * bh) / n,
                    "fill": area / max(bw * bh, 1),
                    "aspect": bh / max(bw, 1),
                    "cy": float(np.mean(ys)) / h,
                    "cx": float(np.mean(xs)) / w,
                    "x0": x0 / w,
                    "y0": y0 / h,
                    "x1": (x1 + 1) / w,
                    "y1": (y1 + 1) / h,
                }
            )
    out.sort(key=lambda d: d["bbox_frac"], reverse=True)
    return out


def assess_default_object_intrusion(
    image_path: Path,
    *,
    subject_type: str = "",
    key_object: str = "",
) -> tuple[bool, list[str], dict[str, Any]]:
    """
    Cheap non-LLM gate for mug/laptop desk-still-life substitution.

    Uses the same PIL+numpy gray thumb as linework. Finds locally-darker
    compact blobs (mug/laptop silhouettes) and compares their bbox footprint
    to the rest of the frame. Scoped to object_focus / silhouette when those
    defaults are not the requested key_object. Does not judge photoreal/style.
    """
    st = (subject_type or "").strip().lower().replace(" ", "_")
    obj = (key_object or "").strip()
    meta: dict[str, Any] = {
        "subject_type": st,
        "key_object": obj,
        "passed": True,
        "skipped": False,
        "intruder_bbox_frac": 0.0,
        "intruder_area_frac": 0.0,
        "intended_bbox_frac": 0.0,
        "ratio": 0.0,
        "n_intruders": 0,
    }
    if st not in _INTRUDER_SUBJECTS:
        meta["skipped"] = True
        meta["skip_reason"] = "not_object_focus_or_silhouette"
        return True, [], meta
    if obj and _INTRUDER_SELF.search(obj):
        meta["skipped"] = True
        meta["skip_reason"] = "key_object_is_default_intruder"
        return True, [], meta
    try:
        _, gray = _load_lofi_thumb_gray(image_path, size=192)
        local = _uniform_gray(gray, 21)
        mask = gray < (local - 16.0)
        blobs = _connected_components(mask, min_area=50)
        intruders = [
            b
            for b in blobs
            if b["cy"] >= 0.48
            and 0.35 <= b["aspect"] <= 1.65
            and b["fill"] >= 0.22
            and b["bbox_frac"] >= 0.03
            and b["frac"] >= 0.012
        ]
        # Door knobs / latches: small compact dark blobs, not mug-class props.
        hardware = [b for b in intruders if b["bbox_frac"] < 0.06]
        compact = [b for b in intruders if b["bbox_frac"] >= 0.06]
        meta["n_hardware"] = len(hardware)
        if obj and _COMPACT_INTENDED_RE.search(obj) and compact:
            # First compact mid-frame blob is the requested object; only a
            # second compact blob is a real extra prop.
            intended = compact[0]
            extras = compact[1:]
            top = extras[0] if extras else None
            meta["compact_as_intended"] = True
        else:
            intended = blobs[0] if blobs else None
            top = compact[0] if compact else None
            extras = compact
        intended_bbox = float((intended or {}).get("bbox_frac") or 0.0)
        intr_bbox = float((top or {}).get("bbox_frac") or 0.0)
        intr_frac = float((top or {}).get("frac") or 0.0)
        ratio = intr_bbox / max(intended_bbox, 1e-6) if intended_bbox else 0.0
        meta.update(
            {
                "n_intruders": len(extras) if top is not None else 0,
                "intruder_bbox_frac": round(intr_bbox, 4),
                "intruder_area_frac": round(intr_frac, 4),
                "intended_bbox_frac": round(intended_bbox, 4),
                "ratio": round(ratio, 3),
            }
        )
        # Fail when a compact lower/mid dark object has a large footprint of
        # its own, or when it is a large fraction of the biggest dark region.
        fail = bool(top) and (
            intr_bbox >= 0.08 or intr_frac >= 0.04 or (ratio >= 0.45 and intr_bbox >= 0.03)
        )
        # Same blob counted as both intended and intruder (ratio≈1): the
        # requested compact object (stone, bag, box, …) is not a mug.
        if (
            fail
            and intended is not None
            and top is not None
            and obj
            and _COMPACT_INTENDED_RE.search(obj)
            and abs(intr_bbox - intended_bbox) < 1e-6
        ):
            fail = False
            top = None
            extras = []
            meta["n_intruders"] = 0
            meta["compact_as_intended"] = True
        if fail and top is not None:
            meta["passed"] = False
            flaw = (
                "INTRUDER: compact dark object (mug/laptop-class) bbox_frac="
                f"{intr_bbox:.3f} area_frac={intr_frac:.3f} ratio={ratio:.2f} "
                f"vs requested {obj or 'key_object'!r}"
            )
            fix = (
                f"Draw only {obj or 'the requested object'} filling the frame. "
                "Empty surface: no mug, no cup, no coffee cup, no glass, no laptop."
            )
            meta["fix_instructions"] = fix
            print(
                f"[LOFI object-gate] REJECT {image_path.name} {flaw}"
            )
            return False, [flaw], meta
        print(
            f"[LOFI object-gate] PASS {image_path.name} "
            f"n_intruders={meta.get('n_intruders')} bbox={intr_bbox:.3f} "
            f"ratio={ratio:.2f} compact_intended={int(bool(meta.get('compact_as_intended')))}"
        )
        return True, [], meta
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("object-gate failed for %s (%s)", image_path.name, exc)
        meta["skipped"] = True
        meta["skip_reason"] = f"error:{exc}"
        return True, [], meta


def debug_intruder_overlay(
    image_path: Path,
    out_path: Path | None = None,
    *,
    subject_type: str = "object_focus",
    key_object: str = "",
) -> Path:
    """Draw dark-blob bboxes for INTRUDER debug. Does not change gate logic."""
    from PIL import Image as PILImage
    from PIL import ImageDraw

    src = Path(image_path)
    dest = Path(out_path) if out_path else src.with_name(f"{src.stem}_intruder_debug.png")
    im = PILImage.open(src).convert("RGB")
    w, h = im.size
    _, gray = _load_lofi_thumb_gray(src, size=192)
    local = _uniform_gray(gray, 21)
    mask = gray < (local - 16.0)
    blobs = _connected_components(mask, min_area=50)
    draw = ImageDraw.Draw(im)
    for i, b in enumerate(blobs[:12]):
        color = (255, 40, 40) if i == 0 else (40, 200, 80)
        x0 = int(float(b["x0"]) * w)
        y0 = int(float(b["y0"]) * h)
        x1 = int(float(b["x1"]) * w)
        y1 = int(float(b["y1"]) * h)
        draw.rectangle([x0, y0, x1, y1], outline=color, width=4)
        draw.text((x0 + 4, y0 + 4), f"{i} bf={b['bbox_frac']:.3f}", fill=color)
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest)
    ok, flaws, meta = assess_default_object_intrusion(
        src, subject_type=subject_type, key_object=key_object
    )
    print(
        f"[LOFI intruder-debug] {src.name} → {dest.name} "
        f"blobs={len(blobs)} gate_ok={int(ok)} meta={meta} flaws={flaws}"
    )
    return dest


def assess_photoreal_style(
    image_path: Path,
    *,
    subject_type: str = "",
) -> tuple[bool, list[str], dict[str, Any]]:
    """
    Cheap non-LLM photoreal gate for macro object_focus stills.

    Independent of Gemini. Uses the same uniq16/lap_var/edge/std thumb as
    the linework guard. Photographic bokeh has high luminance contrast
    (std) with weak ink edges (edge, lap_var). Illustration-safe stills
    from the DoF A/B sit well above the ink-edge floor.

    Scoped to object_focus. Does not change INTRUDER thresholds or escalate
    the framing ladder. Fail-open on read errors (same as INTRUDER).
    """
    st = (subject_type or "").strip().lower().replace(" ", "_")
    meta: dict[str, Any] = {
        "subject_type": st,
        "passed": True,
        "skipped": False,
        "uniq16": 0,
        "lap_var": 0.0,
        "edge": 0.0,
        "std": 0.0,
        "hard": 0.0,
    }
    if st != "object_focus":
        meta["skipped"] = True
        meta["skip_reason"] = "not_object_focus"
        return True, [], meta
    try:
        stats = _linework_stats(image_path, size=512)
        uniq = int(stats["uniq16"])
        lap_var = float(stats["lap_var"])
        edge = float(stats["edge"])
        std = float(stats["std"])
        hard = float(stats["hard"])
        meta.update(
            {
                "uniq16": uniq,
                "lap_var": round(lap_var, 1),
                "edge": round(edge, 2),
                "std": round(std, 1),
                "hard": round(hard, 4),
            }
        )
        # Calibrated on locked-attachment beat 4:
        # photoreal+DoF (225455 s4): std=86.4 edge=9.78 lap_var=459.7 → FAIL
        # no-DoF illustration (3/3 probe): std 51–75, edge 15–21, lap_var 2174–3889 → PASS
        photographic = std >= 80.0 and edge < 12.0 and lap_var < 900.0
        if photographic:
            meta["passed"] = False
            flaw = (
                "PHOTOREAL: photographic smoothness "
                f"std={std:.1f} edge={edge:.2f} lap_var={lap_var:.1f} "
                f"uniq16={uniq} (want ink-hard edges, not camera bokeh)"
            )
            meta["fix_instructions"] = (
                "Redraw as a risograph illustration: hard-edged color blocks, "
                "halftone grain, no camera bokeh, no lens blur, no photography."
            )
            print(f"[LOFI style-gate] REJECT {image_path.name} {flaw}")
            return False, [flaw], meta
        print(
            f"[LOFI style-gate] PASS {image_path.name} "
            f"std={std:.1f} edge={edge:.2f} lap_var={lap_var:.1f} uniq16={uniq}"
        )
        return True, [], meta
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("style-gate failed for %s (%s)", image_path.name, exc)
        meta["skipped"] = True
        meta["skip_reason"] = f"error:{exc}"
        return True, [], meta


def _qa_scene_image(
    image_path: Path,
    visual_prompt: str,
    *,
    subject_type: str = "",
    key_object: str = "",
    prior_fail_criteria: list[str] | None = None,
    style_profile: str | None = None,
) -> tuple[bool, list[str], str]:
    """
    Run VisualQA lofi_economic profile when available.
    Soft-fail open if Gemini/critic unavailable (log + accept).
    Always run local linework/complexity check (rejects flat graphics).
    Returns (passed, flaws, critic_fix_instructions).
    """
    del visual_prompt
    struct_ok, struct_flaws = _assess_linework_complexity(image_path)
    fix = ""
    try:
        from VisualQA_Agent.channel_rag import get_channel_rules, set_channel_context
        from VisualQA_Agent.visual_critic import evaluate_image

        set_channel_context("lofi_economic")
        rules = get_channel_rules("lofi_economic")
        verdict = evaluate_image(
            image_path,
            channel_name="lofi_economic",
            rules=rules,
            quality_threshold=6.0,
            requested_subject=subject_type or None,
            requested_object=key_object or None,
            prior_fail_criteria=prior_fail_criteria,
            style_profile=style_profile,
        )
        critic_ok = bool(verdict.passed)
        critic_flaws = list(verdict.flaws or [])
        fix = str(verdict.fix_instructions or "").strip()
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("VisualQA skipped for %s (%s)", image_path.name, exc)
        critic_ok = True
        critic_flaws = []
        try:
            from PIL import Image as PILImage

            im = PILImage.open(image_path)
            w, h = im.size
            if h < w:
                critic_flaws.append("aspect ratio not vertical")
                critic_ok = False
            if w < 512 or h < 512:
                critic_flaws.append("resolution too low for reels")
                critic_ok = False
        except Exception:  # noqa: BLE001
            pass
    flaws = list(struct_flaws)
    if not critic_ok:
        flaws.extend(critic_flaws)
    return (struct_ok and critic_ok and not flaws), flaws, fix


def apply_ad_hoc_guidance(visual_prompt: str, reason: str) -> str:
    """Append one-off prompt guidance. Not persisted to the RAG / object bank."""
    extra = " ".join((reason or "").split())
    base = " ".join((visual_prompt or "").split())
    if not extra:
        return base
    if extra.lower() in base.lower():
        return base
    return f"{base} {extra}"


def generate_and_qa_scene(
    row: dict[str, Any],
    out_img: Path,
    *,
    attempt_budget: int | None = None,
    extra_prompt: str = "",
    mood: dict[str, Any] | None = None,
    generate_fn: Any | None = None,
    assemble_fn: Any | None = None,
) -> tuple[bool, dict[str, Any], int, int]:
    """
    Generate one beat still and run critic + uniq16 style_gate + INTRUDER.

    Writes pixels to ``out_img`` (last attempt kept on failure). Returns
    ``(ok, last_gate, n_image_calls, n_critic_calls)``.

    ``generate_fn`` / ``assemble_fn`` default to the Schnell live path.
    Flux Dev callers pass generate_scene_image_dev + assemble_v2_prompt_dev.
    """
    gen_image = generate_fn or generate_scene_image
    scene_i = int(row.get("scene") or 1)
    visual = str(row.get("visual_prompt") or "")
    extra = " ".join((extra_prompt or "").split())
    subject_type = str(row.get("subject_type") or "").strip()
    key_object = str(row.get("key_object") or "").strip()
    st_l = subject_type.strip().lower().replace(" ", "_")
    mood_meta = dict(mood or {
        "id": str(row.get("riso_id") or f"scene_{scene_i}"),
        "lighting": "from_riso_prompt",
        "palette": row.get("riso_palette"),
        "shadow": lofi_cfg.DUOTONE_SHADOW,
        "highlight": lofi_cfg.DUOTONE_HIGHLIGHT,
    })
    if attempt_budget is None:
        n_attempts = int(getattr(lofi_cfg, "IMAGE_ATTEMPTS_PER_SCENE", 0) or 0)
        if n_attempts < 1:
            n_attempts = int(lofi_cfg.IMAGE_MAX_RETRIES_PER_SCENE) + 1
    else:
        n_attempts = max(1, int(attempt_budget))
    n_attempts = max(1, min(n_attempts, 2))

    ok_img = False
    last_flaws: list[str] = []
    last_fix = ""
    last_gate: dict[str, Any] = {
        "scene": scene_i,
        "subject_type": subject_type,
        "key_object": key_object,
        "passed": True,
        "skipped": True,
    }
    last_intruder = False
    focus_step = 0
    scene_attempts: list[dict[str, Any]] = []
    n_image_calls = 0
    n_critic_calls = 0

    for attempt in range(1, n_attempts + 1):
        prompt_i = apply_ad_hoc_guidance(visual, extra)
        if st_l in {"object_focus", "silhouette"}:
            if last_intruder:
                focus_step = min(focus_step + 1, 2)
            from core_engine.economic_reel_lofi.visual_identity import (
                assemble_v2_prompt,
            )

            assembler = assemble_fn or assemble_v2_prompt
            rebuilt = assembler(row, focus_step=focus_step)
            row["visual_prompt"] = rebuilt
            prompt_i = apply_ad_hoc_guidance(rebuilt, extra)
            print(
                f"[LOFI framing] scene={scene_i} attempt={attempt} "
                f"step={focus_step} kind={row.get('object_focus_framing')} "
                f"escalate_intruder={int(last_intruder)}"
            )
            if attempt > 1 and last_fix:
                guard = str(getattr(lofi_cfg, "LOFI_PROMPT_LINEWORK_GUARD", "") or "")
                extras = [p for p in (guard, last_fix) if p]
                if extras:
                    prompt_i = f"{prompt_i} {' '.join(extras)}"
        elif attempt > 1:
            guard = str(getattr(lofi_cfg, "LOFI_PROMPT_LINEWORK_GUARD", "") or "")
            extras = [p for p in (guard, last_fix) if p]
            if extras:
                prompt_i = f"{apply_ad_hoc_guidance(visual, extra)} {' '.join(extras)}"
        try:
            _, mood_meta = gen_image(
                prompt_i,
                out_img,
                mood=mood_meta,
                verbatim=True,
            )
            n_image_calls += 1
            lofi_cfg.write_still_style_sidecar(
                out_img, run_id=out_img.parent.name, reused=False
            )
        except Exception as exc:  # noqa: BLE001
            last_flaws = [f"image gen failed: {exc}"]
            _LOG.warning("scene %s gen attempt %s failed: %s", scene_i, attempt, exc)
            continue
        n_critic_calls += 1
        passed, flaws, last_fix = _qa_scene_image(
            out_img, visual,
            subject_type=subject_type,
            key_object=key_object,
            prior_fail_criteria=[
                f for f in last_flaws
                if f and not str(f).startswith("image gen failed")
            ] if attempt > 1 and last_flaws else None,
            style_profile=str(row.get("visual_identity_profile") or "") or None,
        )
        if attempt > 1 and last_flaws:
            hold = [
                f for f in last_flaws
                if f and not str(f).startswith("image gen failed")
            ]
            print(
                f"[LOFI retry-hold] scene={scene_i} attempt={attempt} "
                f"same_criteria={hold}"
            )
        gate_ok, gate_flaws, gate_meta = assess_default_object_intrusion(
            out_img,
            subject_type=subject_type,
            key_object=key_object,
        )
        style_ok, style_flaws, style_meta = assess_photoreal_style(
            out_img,
            subject_type=subject_type,
        )
        del gate_ok, style_ok
        gate_meta = dict(gate_meta)
        gate_meta["scene"] = scene_i
        gate_meta["attempt"] = attempt
        gate_meta["focus_step"] = focus_step
        gate_meta["framing"] = row.get("object_focus_framing") or ""
        gate_meta["style_gate"] = style_meta
        last_gate = gate_meta
        last_intruder = bool(gate_flaws)
        if gate_flaws:
            flaws = list(flaws) + gate_flaws
            passed = False
            extra_fix = str(gate_meta.get("fix_instructions") or "").strip()
            if extra_fix:
                last_fix = f"{last_fix} {extra_fix}".strip() if last_fix else extra_fix
        if style_flaws:
            flaws = list(flaws) + style_flaws
            passed = False
            extra_fix = str(style_meta.get("fix_instructions") or "").strip()
            if extra_fix:
                last_fix = f"{last_fix} {extra_fix}".strip() if last_fix else extra_fix
        gate_meta["qa_passed"] = bool(passed)
        gate_meta["qa_flaws"] = list(flaws)
        if passed:
            ok_img = True
            scene_attempts.append(dict(last_gate))
            break
        last_flaws = flaws
        scene_attempts.append(dict(last_gate))
        if attempt >= 2:
            print(
                f"[LOFI retry-stop] scene={scene_i} attempt={attempt} "
                f"same named fail — not spending another call. flaws={flaws}"
            )
            break
        _LOG.info(
            "VisualQA reject scene %s attempt %s subject=%s: %s",
            scene_i,
            attempt,
            subject_type or "?",
            "; ".join(flaws),
        )

    last_gate["image_ok"] = ok_img
    last_gate["attempts"] = scene_attempts
    last_gate["attempts_used"] = n_image_calls
    last_gate["focus_step"] = focus_step
    last_gate["qa_flaws"] = list(last_gate.get("qa_flaws") or last_flaws)
    last_gate["qa_passed"] = bool(ok_img)
    last_gate["mood"] = mood_meta
    return ok_img, last_gate, n_image_calls, n_critic_calls


def _caption_cap_reasons_only(reasons: list[str]) -> bool:
    """True when every validator reason is a per-beat word/char cap miss."""
    if not reasons:
        return False
    pats = (
        re.compile(r"has \d+ words \(max \d+\)"),
        re.compile(r"caption exceeds \d+ chars"),
    )
    return all(any(p.search(r) for p in pats) for r in reasons)


def _generate_validated_script(
    *,
    module: str,
    theme_row: dict[str, Any],
    scene_count: int,
    persist_on_pass: bool = True,
) -> tuple[dict[str, Any] | None, list[str], bool]:
    """Returns (script|None, errors, needs_manual_review)."""
    feedback: str | None = None
    last_errors: list[str] = []
    last_script: dict[str, Any] | None = None
    for attempt in range(1, lofi_cfg.SCRIPT_MAX_RETRIES + 1):
        script = generate_script(
            module=module,
            theme=str(theme_row.get("theme") or "connection"),
            subtheme=str(theme_row.get("subtheme") or ""),
            scene_count=scene_count,
            feedback=feedback,
            theme_row=theme_row,
        )
        script["subtheme"] = theme_row.get("subtheme")
        last_script = script
        result = validate_script(
            script,
            module=module,
            scene_count=scene_count,
            persist_on_pass=persist_on_pass,
        )
        if (not result.ok) and _caption_cap_reasons_only(result.reasons):
            repair_script_captions(script)
            result = validate_script(
                script,
                module=module,
                scene_count=scene_count,
                persist_on_pass=persist_on_pass,
            )
            if result.ok and result.script:
                print(
                    "[LOFI script] recovered over-cap beat(s) by trim — "
                    "not retrying the whole episode"
                )
        if result.ok and result.script:
            note_batch_structure_id(str(result.script.get("structure_id") or ""))
            return result.script, [], False
        last_errors = list(result.reasons)
        feedback = result.feedback()
        print(f"[LOFI script] attempt {attempt}/{lofi_cfg.SCRIPT_MAX_RETRIES} rejected: {feedback}")
        _LOG.info(
            "Script attempt %d/%d rejected: %s",
            attempt,
            lofi_cfg.SCRIPT_MAX_RETRIES,
            feedback,
        )
    # Last resort: deterministic short-line fallback so a test still renders
    from core_engine.economic_reel_lofi.script_agent import _fallback_script

    last_details = list((last_script or {}).get("retrieved_details") or [])
    if not last_details:
        last_details = rag.select_concrete_details(theme_row, module=module)
    fallback = _fallback_script(
        module=module,
        theme=str(theme_row.get("theme") or "connection"),
        scene_count=scene_count,
        hook_type="definition",
        quote=None,
        setting_object_pairs=theme_row.get("setting_object_pairs")
        if isinstance(theme_row.get("setting_object_pairs"), list)
        else None,
        retrieved_details=last_details,
        arc_template=str((last_script or {}).get("arc_template") or ""),
    )
    fallback["subtheme"] = theme_row.get("subtheme")
    repair_script_captions(fallback)
    result = validate_script(
        fallback,
        module=module,
        scene_count=scene_count,
        persist_on_pass=persist_on_pass,
    )
    if result.ok and result.script:
        print("[LOFI script] using fallback after validator retries")
        return result.script, [], False
    fallback_reasons = list(result.reasons)
    print(f"[LOFI validator] last-resort REJECT: {result.feedback()}")
    return None, fallback_reasons or last_errors or ["script validation failed"], True


def _resolve_page_dirs(page_id: str, outputs_dir: Path | str | None) -> tuple[Path, Path, Path]:
    """
    Return ``(page_outputs, clips_dir, assets_dir)`` using the existing
    per-page layout: ``outputs/<page>/{clips,assets}/``.
    """
    engine_root = _engine_root()
    if outputs_dir is None:
        page_outputs = engine_root / "outputs" / page_id
    else:
        page_outputs = Path(outputs_dir)
        # Callers may pass .../clips by mistake — normalize to page root.
        if page_outputs.name == "clips":
            page_outputs = page_outputs.parent
        elif page_outputs.name == "economic_reel_lofi":
            page_outputs = page_outputs.parent
    clips_dir = page_outputs / "clips"
    assets_dir = page_outputs / "assets"
    clips_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)
    return page_outputs, clips_dir, assets_dir


def _load_locked_script(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    envelope: dict[str, Any] = raw if isinstance(raw, dict) else {}
    if isinstance(raw, dict) and isinstance(raw.get("script"), dict):
        script = dict(raw["script"])
    elif isinstance(raw, dict):
        script = dict(raw)
    else:
        raise ValueError(f"locked script is not a JSON object: {p}")
    if not (script.get("lines") or script.get("monologue")):
        raise ValueError(f"locked script has no lines/monologue: {p}")
    extras = {
        "scene_images": list(envelope.get("scene_images") or []),
        "work_dir": envelope.get("work_dir"),
        "manual_accept_scenes": [
            int(x) for x in (envelope.get("manual_accept_scenes") or [])
        ],
        "source": str(p),
    }
    script["_locked_sidecar_assets"] = extras
    print(f"[LOFI pipeline] loaded locked script → {p}")
    return script


def _print_script_report(script: dict[str, Any], *, index: int, qty: int) -> None:
    theme = str(script.get("theme") or "")
    sub = str(script.get("subtheme") or "")
    hook = str(script.get("hook_type") or "")
    arc = str(script.get("arc_template") or "")
    lines = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
    print("")
    print("=" * 72)
    print(f"SCRIPT {index}/{qty}  theme={theme}  subtheme={sub}")
    print(f"hook={hook}  arc={arc}  beats={len(lines)}")
    print("-" * 72)
    print("MONOLOGUE:")
    print(str(script.get("monologue") or " ".join(str(r.get("text") or "") for r in lines)))
    print("-" * 72)
    print(f"{'#':<3} {'text':<56} {'setting':<28} {'object'}")
    for row in lines:
        n = int(row.get("scene") or 0)
        text = str(row.get("text") or "")[:54]
        setting = str(row.get("setting") or "")[:26]
        obj = str(row.get("key_object") or "")[:22]
        print(f"{n:<3} {text:<56} {setting:<28} {obj}")
    print("=" * 72)


def _produce_one(
    *,
    page_id: str,
    module: str,
    duration_s: int,
    clips_dir: Path,
    assets_dir: Path,
    force_theme: str | None = None,
    force_subtheme: str | None = None,
    index: int,
    script_only: bool = False,
    stills_only: bool = False,
    batch_qty: int = 1,
    locked_script: dict[str, Any] | None = None,
) -> LofiItemResult:
    scene_count = lofi_cfg.scene_count_for_duration(duration_s, thematic=True)
    stamp = _utc_stamp()
    persist_on_pass = not script_only and not stills_only

    reset_script_llm_call_log()
    if isinstance(locked_script, dict) and (locked_script.get("lines") or locked_script.get("monologue")):
        script = dict(locked_script)
        repair_script_captions(script, keep_extra_scenes=True)
        theme_row = rag.select_theme(
            module,
            theme=str(script.get("theme") or force_theme or ""),
            subtheme=str(script.get("subtheme") or force_subtheme or ""),
        )
        print(
            "[LOFI pipeline] LOCKED script — skipping writer/validator "
            f"theme={script.get('theme')} subtheme={script.get('subtheme')} "
            f"beats={len(script.get('lines') or [])}"
        )
        _print_script_report(script, index=index, qty=batch_qty)
        errs: list[str] = []
    else:
        theme_row = rag.select_theme(module, theme=force_theme, subtheme=force_subtheme)
        script, errs, manual = _generate_validated_script(
            module=module,
            theme_row=theme_row,
            scene_count=scene_count,
            persist_on_pass=persist_on_pass,
        )
    if script is None:
        review_path = clips_dir / f"lofi_manual_review_{stamp}_{index:02d}.json"
        review_path.write_text(
            json.dumps(
                {"errors": errs, "theme": theme_row, "module": module, "script_only": script_only},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return LofiItemResult(
            ok=False,
            module=module,
            theme=str(theme_row.get("theme") or ""),
            scene_count=scene_count,
            duration_s=float(duration_s),
            manual_review=True,
            errors=errs,
            meta_path=str(review_path),
        )

    script_llm_log = get_script_llm_call_log()
    script_llm_calls = len(script_llm_log)
    script_llm_cost_usd = round(sum(c.get("cost_usd_est", 0.0) for c in script_llm_log), 6)

    if script_only:
        theme = str(script.get("theme") or theme_row.get("theme") or "")
        sub = str(script.get("subtheme") or theme_row.get("subtheme") or "")
        if theme:
            rag.mark_theme_used(module, theme, sub or None)
        _print_script_report(script, index=index, qty=batch_qty)
        theme_slug = "".join(
            c if c.isalnum() else "_"
            for c in theme.lower()
        ).strip("_")[:32] or "lofi"
        meta_path = clips_dir / f"lofi_script_{theme_slug}_{stamp}_v{index:02d}.json"
        meta = {
            "post_type": "ECONOMIC_REEL_LOFI",
            "mode": "script_only",
            "page": page_id,
            "module": module,
            "duration_requested_s": duration_s,
            "scene_count": len(script.get("lines") or []),
            "scene_duration_s": lofi_cfg.beat_duration_s(),
            "duration_expected_s": lofi_cfg.duration_for_beat_count(
                len(script.get("lines") or [])
            ),
            "subtheme": script.get("subtheme"),
            "hook_type": script.get("hook_type"),
            "arc_template": script.get("arc_template"),
            "retrieved_details": script.get("retrieved_details"),
            "script": script,
            "script_llm_calls": script_llm_calls,
            "script_llm_cost_usd": script_llm_cost_usd,
            "est_cost_usd": round(script_llm_cost_usd, 5),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        lines = list(script.get("lines") or [])
        return LofiItemResult(
            ok=True,
            video_path=None,
            meta_path=str(meta_path),
            module=module,
            theme=theme,
            hook_type=str(script.get("hook_type") or ""),
            scene_count=len(lines),
            duration_s=lofi_cfg.duration_for_beat_count(len(lines)),
            manual_review=False,
            errors=[],
            script=script,
        )

    # Working stills live under assets/; final deliverables go to clips/.
    run_dir = assets_dir / f"lofi_run_{stamp}_{index:02d}"
    run_dir.mkdir(parents=True, exist_ok=True)

    lines = list(script.get("lines") or [])
    episode_variety: dict[str, Any] = {}
    use_dev = bool(lofi_cfg.uses_flux_dev())
    # V2 identity bank assembles prompts from beat fields (live riso JSON untouched).
    if bool(getattr(lofi_cfg, "USE_VISUAL_IDENTITY_V2", False)):
        if use_dev:
            from core_engine.economic_reel_lofi.visual_identity import (
                apply_v2_prompts_to_lines_dev,
            )

            print(
                "[LOFI backend] flux=dev | "
                f"profile={lofi_cfg.DEFAULT_VISUAL_IDENTITY_PROFILE} | "
                "assemble=assemble_v2_prompt_dev | "
                f"gen={lofi_cfg.LOFI_IMAGE_WIDTH}x{lofi_cfg.LOFI_IMAGE_HEIGHT} "
                f"delivery={lofi_cfg.REEL_WIDTH}x{lofi_cfg.REEL_HEIGHT} "
                f"aspect={lofi_cfg.LOFI_IMAGE_WIDTH / lofi_cfg.LOFI_IMAGE_HEIGHT:.6f}"
            )
            apply_v2_prompts_to_lines_dev(
                lines,
                theme_row=theme_row,
                vary_imagery=lofi_cfg.is_thematic_arc(
                    str(script.get("arc_template") or "")
                ),
                lock_visuals=bool(locked_script),
            )
        else:
            from core_engine.economic_reel_lofi.visual_identity import (
                apply_v2_prompts_to_lines,
            )

            apply_v2_prompts_to_lines(
                lines,
                theme_row=theme_row,
                vary_imagery=lofi_cfg.is_thematic_arc(
                    str(script.get("arc_template") or "")
                ),
                lock_visuals=bool(locked_script),
            )
        if lines and isinstance(lines[0], dict):
            raw_var = lines[0].pop("episode_variety", None)
            if isinstance(raw_var, dict):
                episode_variety = raw_var
        if episode_variety:
            script["episode_variety"] = episode_variety
        for row in lines:
            if not isinstance(row, dict):
                continue
            print(
                f"[LOFI identity v2] scene={row.get('scene')} "
                f"type={row.get('subject_type')} expr={row.get('subject_expression')!r} "
                f"setting={row.get('setting')!r} object={row.get('key_object')!r} "
                f"tod={row.get('time_of_day')} pal={row.get('palette_key')} "
                f"act={row.get('arc_position')}"
            )
            print(f"[LOFI identity v2] prompt={row.get('visual_prompt')!r}")
    elif bool(getattr(lofi_cfg, "USE_RISO_PROMPT_LIBRARY", True)):
        lib_diff = export_active_library_diff()
        try:
            from core_engine.economic_reel_lofi.riso_prompt_bank import load_riso_library

            live_dump = clips_dir / f"riso_library_live_{stamp}.json"
            live_dump.write_text(
                json.dumps(load_riso_library(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"[LOFI riso] full live library exported -> {live_dump}")
            print(f"[LOFI riso] diff added={lib_diff.get('added')} removed={lib_diff.get('removed')}")
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("library export failed: %s", exc)
        emotions = [str(r.get("emotion") or r.get("mood") or "") for r in lines]
        arcs = [str(r.get("arc_position") or "") for r in lines]
        riso_rows = assign_riso_prompts_for_scenes(
            len(lines) if lines else scene_count,
            theme=str(script.get("theme") or ""),
            seed=None,
            scene_emotions=emotions,
            scene_arcs=arcs,
        )
        for i, row in enumerate(lines):
            if i >= len(riso_rows):
                break
            r = riso_rows[i]
            row["visual_prompt"] = str(r.get("prompt") or "")
            row["riso_id"] = r.get("id")
            row["riso_palette"] = r.get("palette")
            row["riso_mood"] = r.get("mood")
            row["riso_scene_type"] = r.get("scene_type")
            row["riso_emotion_tags"] = r.get("emotion_tags")
            row["riso_arc_position"] = r.get("arc_position")
            row.pop("lighting_mood", None)
        ids = [str(r.get("id")) for r in riso_rows[: len(lines)]]
        print("[LOFI pipeline] riso prompts assigned: " + ", ".join(ids))
        if len(ids) != len(set(ids)):
            raise RuntimeError(f"duplicate riso ids in one video: {ids}")
        print(
            "[LOFI pipeline] riso_013/014 distinct scenes: "
            f"013={'riso_013' in ids} 014={'riso_014' in ids}"
        )

    locked_assets = script.pop("_locked_sidecar_assets", None) or {}
    accept_scenes = {int(x) for x in (locked_assets.get("manual_accept_scenes") or [])}
    reuse_images = [Path(p) for p in (locked_assets.get("scene_images") or []) if str(p).strip()]
    if accept_scenes:
        print(f"[LOFI pipeline] manual_accept_scenes={sorted(accept_scenes)}")

    scene_paths: list[Path] = []
    captions: list[str] = []
    scene_moods: list[dict] = []
    qa_flags: list[str] = []
    voice_paths: list[Path | None] = []
    word_timings_per_scene: list[list[tuple[str, float, float]] | None] = []
    voice_settings_result: dict[str, Any] | None = None
    n_image_calls = 0
    n_critic_calls = 0
    object_gate_by_scene: list[dict[str, Any]] = []
    n_beats_planned = max(1, len(lines))
    call_budget = lofi_cfg.image_call_budget(n_beats_planned)
    print(
        f"[LOFI budget] image_call_cap={call_budget} "
        f"({n_beats_planned} beats x {lofi_cfg.IMAGE_CALL_BUDGET_MULT:g}) "
        f"attempts_per_scene={lofi_cfg.IMAGE_ATTEMPTS_PER_SCENE}"
    )

    if (not stills_only) and bool(getattr(lofi_cfg, "ENABLE_VOICEOVER", True)):
        try:
            from avatar_engine.audio_engine import apply_elevenlabs_voice_settings

            voice_id_pre = str(getattr(lofi_cfg, "LOFI_VOICE_ID", "") or "")
            speed_pre = float(getattr(lofi_cfg, "LOFI_VOICE_SPEED", 0.8))
            voice_settings_result = apply_elevenlabs_voice_settings(
                voice_id_pre,
                speed=speed_pre,
                stability=1.0,
                similarity_boost=1.0,
                style=0.0,
                use_speaker_boost=True,
            )
        except Exception as exc:  # noqa: BLE001
            msg = f"ElevenLabs voice settings/edit failed: {exc}"
            _LOG.warning(msg)
            print(f"[LOFI VO] WARN {msg}")
            # Best-effort persist of voice defaults. Do not hold the episode:
            # TTS still sends per-call voice_settings, and a shadowed `config`
            # module must not block a reel whose images already passed QA.

    for row in lines:
        scene_i = int(row.get("scene") or len(scene_paths) + 1)
        out_img = run_dir / f"scene_{scene_i:02d}.png"
        caption = _sanitize_caption_typos(str(row.get("text") or ""))
        mood_meta = {
            "id": str(row.get("riso_id") or f"scene_{scene_i}"),
            "lighting": "from_riso_prompt",
            "palette": row.get("riso_palette"),
            "shadow": lofi_cfg.DUOTONE_SHADOW,
            "highlight": lofi_cfg.DUOTONE_HIGHLIGHT,
        }
        ok_img = False
        last_flaws: list[str] = []
        subject_type = str(row.get("subject_type") or "").strip()
        key_object = str(row.get("key_object") or "").strip()
        last_gate: dict[str, Any] = {
            "scene": scene_i,
            "subject_type": subject_type,
            "key_object": key_object,
            "passed": True,
            "skipped": True,
        }
        reuse_src = reuse_images[scene_i - 1] if 0 < scene_i <= len(reuse_images) else None
        prev_ok = bool((row.get("default_object_gate") or {}).get("image_ok"))
        want_tag = lofi_cfg.current_still_style_tag()
        src_tag = lofi_cfg.still_style_tag_of(reuse_src) if reuse_src else None
        allow_mixed = lofi_cfg.allow_mixed_era_assemble()
        reuse_ok = bool(
            reuse_src
            and reuse_src.is_file()
            and (prev_ok or scene_i in accept_scenes)
        )
        if reuse_ok and src_tag != want_tag:
            if allow_mixed:
                print(
                    f"[LOFI reuse] mixed-era APPROVED scene={scene_i} "
                    f"src={src_tag or 'UNTAGGED'} want={want_tag}"
                )
            else:
                print(
                    f"[LOFI reuse] REJECT stale scene={scene_i} "
                    f"src={src_tag or 'UNTAGGED'} want={want_tag} "
                    f"— generating fresh"
                )
                reuse_ok = False
        if reuse_ok:
            if Path(reuse_src) != out_img:
                shutil.copy2(reuse_src, out_img)
            keep_tag = src_tag if (src_tag and src_tag != want_tag) else want_tag
            lofi_cfg.write_still_style_sidecar(
                out_img,
                run_id=run_dir.name,
                reused=True,
                style_tag=keep_tag,
            )
            ok_img = True
            last_gate = dict(row.get("default_object_gate") or last_gate)
            last_gate["image_ok"] = True
            last_gate["reused"] = True
            last_gate["manual_accept"] = scene_i in accept_scenes
            print(
                f"[LOFI reuse] scene={scene_i} "
                f"{'manual_accept' if scene_i in accept_scenes else 'prior_pass'} "
                f"← {reuse_src.name}"
            )
        else:
            remaining = call_budget - n_image_calls
            if remaining <= 0:
                qa_flags.append(
                    f"scene_{scene_i}: skipped — episode image-call budget "
                    f"{call_budget} exhausted"
                )
                print(
                    f"[LOFI budget] STOP scene={scene_i} calls={n_image_calls}/"
                    f"{call_budget} — unresolved"
                )
                if not out_img.is_file():
                    from PIL import Image as PILImage

                    PILImage.new("RGB", (LOFI_IMAGE_WIDTH, LOFI_IMAGE_HEIGHT), (30, 40, 55)).save(out_img)
                last_gate = {
                    "scene": scene_i,
                    "subject_type": subject_type,
                    "key_object": key_object,
                    "passed": False,
                    "qa_passed": False,
                    "qa_flaws": ["episode image-call budget exhausted"],
                    "image_ok": False,
                    "skipped": True,
                    "skip_reason": "image_call_budget",
                }
                object_gate_by_scene.append(last_gate)
                row["default_object_gate"] = last_gate
                row["riso_id"] = mood_meta.get("id")
                scene_paths.append(out_img)
                captions.append(caption)
                scene_moods.append(mood_meta)
                voice_paths.append(None)
                word_timings_per_scene.append(None)
                continue
            gen_kw: dict[str, Any] = {
                "mood": mood_meta,
                "attempt_budget": min(
                    int(getattr(lofi_cfg, "IMAGE_ATTEMPTS_PER_SCENE", 2) or 2),
                    remaining,
                ),
            }
            if use_dev:
                from core_engine.economic_reel_lofi.image_gen import (
                    generate_scene_image_dev,
                )
                from core_engine.economic_reel_lofi.visual_identity import (
                    assemble_v2_prompt_dev,
                )

                gen_kw["generate_fn"] = generate_scene_image_dev
                gen_kw["assemble_fn"] = assemble_v2_prompt_dev
            ok_img, last_gate, n_i, n_c = generate_and_qa_scene(
                row, out_img, **gen_kw,
            )
            n_image_calls += n_i
            n_critic_calls += n_c
            last_flaws = list(last_gate.get("qa_flaws") or [])
            if isinstance(last_gate.get("mood"), dict):
                mood_meta = last_gate["mood"]
        object_gate_by_scene.append(last_gate)
        row["default_object_gate"] = last_gate
        if not ok_img:
            qa_flags.append(f"scene_{scene_i}: {'; '.join(last_flaws) or 'qa failed'}")
            if not out_img.is_file():
                from PIL import Image as PILImage

                PILImage.new("RGB", (LOFI_IMAGE_WIDTH, LOFI_IMAGE_HEIGHT), (30, 40, 55)).save(out_img)
        row["riso_id"] = mood_meta.get("id")
        scene_paths.append(out_img)
        captions.append(caption)
        scene_moods.append(mood_meta)

        # Per-scene VO + word timestamps (skipped in stills-only preview)
        vo_path: Path | None = None
        timings: list[tuple[str, float, float]] | None = None
        if (
            (not stills_only)
            and bool(getattr(lofi_cfg, "ENABLE_VOICEOVER", True))
            and caption.strip()
            and not qa_flags
        ):
            try:
                from avatar_engine.audio_engine import generate_voiceover_with_timestamps

                vo_path = run_dir / f"vo_scene_{scene_i:02d}.mp3"
                voice_id = str(getattr(lofi_cfg, "LOFI_VOICE_ID", "") or "")
                tts_text = _tts_text_with_breaks(caption)
                use_ssml = "<break" in tts_text
                speed = float(getattr(lofi_cfg, "LOFI_VOICE_SPEED", 0.8))
                print(
                    f"[LOFI VO] scene={scene_i} voice={voice_id} "
                    f"speed={speed} ssml={use_ssml} text={caption!r} tts={tts_text!r}"
                )
                vo_path, raw_timings = generate_voiceover_with_timestamps(
                    tts_text,
                    vo_path,
                    voice_id=voice_id or None,
                    force_elevenlabs=True,
                    expressive_mode=False,
                    enable_ssml=use_ssml,
                    speed=speed,
                    voice_settings={
                        "stability": 1.0,
                        "similarity_boost": 1.0,
                        "style": 0.0,
                        "use_speaker_boost": True,
                        "speed": speed,
                    },
                )
                timings = [
                    (str(w), float(s), float(e))
                    for w, s, e in (raw_timings or [])
                    if str(w).strip()
                    and not str(w).startswith("<")
                    and str(w).lower() not in {"break", "time"}
                ]
            except Exception as exc:  # noqa: BLE001
                msg = f"scene_{scene_i} VO failed: {exc}"
                _LOG.warning(msg)
                if bool(getattr(lofi_cfg, "REQUIRE_VOICEOVER", True)):
                    qa_flags.append(msg)
                vo_path = None
                timings = None
        voice_paths.append(vo_path)
        word_timings_per_scene.append(timings)

    theme_slug = "".join(
        c if c.isalnum() else "_"
        for c in str(script.get("theme") or "lofi").lower()
    ).strip("_")[:32] or "lofi"

    print("[LOFI object-gate] per-beat")
    for g in object_gate_by_scene:
        attempts = list(g.get("attempts") or [g])
        status = "SKIP" if g.get("skipped") else ("PASS" if g.get("passed") else "FAIL")
        print(
            f"[LOFI object-gate] scene={g.get('scene')} {status} "
            f"type={g.get('subject_type')} object={g.get('key_object')!r} "
            f"bbox={g.get('intruder_bbox_frac')} ratio={g.get('ratio')} "
            f"n={g.get('n_intruders')} step={g.get('focus_step')}"
        )
        if len(attempts) > 1 or (attempts and attempts[0].get("attempt")):
            for a in attempts:
                a_st = "SKIP" if a.get("skipped") else ("PASS" if a.get("passed") else "FAIL")
                print(
                    f"[LOFI object-gate]   attempt={a.get('attempt')} {a_st} "
                    f"step={a.get('focus_step')} framing={a.get('framing')!r} "
                    f"bbox={a.get('intruder_bbox_frac')} ratio={a.get('ratio')}"
                )
                sg = a.get("style_gate") if isinstance(a.get("style_gate"), dict) else {}
                if sg and not sg.get("skipped"):
                    sg_st = "PASS" if sg.get("passed") else "FAIL"
                    print(
                        f"[LOFI style-gate]   attempt={a.get('attempt')} {sg_st} "
                        f"std={sg.get('std')} edge={sg.get('edge')} "
                        f"lap_var={sg.get('lap_var')} uniq16={sg.get('uniq16')}"
                    )

    # Per-image rate follows the backend this run actually POSTed
    # (DeepInfra Dev formula vs DeepInfra Schnell formula).
    from VisualQA_Agent.config import COST_GEMINI_FLASH_USD

    img_cost_per_call, cost_meta = lofi_cfg.lofi_image_cost_per_call_usd(
        LOFI_IMAGE_WIDTH, LOFI_IMAGE_HEIGHT, schnell_steps=LOFI_IMAGE_STEPS
    )
    img_cost = img_cost_per_call * n_image_calls
    critic_cost = COST_GEMINI_FLASH_USD * n_critic_calls
    media_cost = img_cost + critic_cost
    total_est_cost = round(media_cost + script_llm_cost_usd, 5)
    print(
        f"[LOFI cost] backend={cost_meta.get('backend')} "
        f"provider={cost_meta.get('provider')} "
        f"rate=${img_cost_per_call:.6f}/image "
        f"({cost_meta.get('formula')}) | "
        f"images={n_image_calls}/{call_budget} "
        f"image_cost=${img_cost:.4f} | critic={n_critic_calls} "
        f"critic_cost=${critic_cost:.4f} | script_cost=${script_llm_cost_usd:.4f} "
        f"| total_est_cost=${total_est_cost:.4f}"
    )
    if cost_meta.get("note"):
        print(f"[LOFI cost] {cost_meta['note']}")
    cost_delta = lofi_cfg.lofi_image_cost_delta()
    print(
        f"[LOFI cost] delta old={cost_delta['old_size']} "
        f"${cost_delta['old_usd_per_image']:.6f}/image "
        f"({cost_delta['old_mp']} MP) → new={cost_delta['new_size']} "
        f"${cost_delta['new_usd_per_image']:.6f}/image "
        f"({cost_delta['new_mp']} MP) usd_delta=${cost_delta['usd_delta']:.6f} "
        f"pixel_ratio={cost_delta['pixel_ratio']} "
        f"cost_ratio={cost_delta['cost_ratio']} "
        f"scaling={cost_delta['scaling']} "
        f"time_est_s={cost_delta['time_est_s_linear']} "
        f"(baseline {cost_delta['time_baseline_s']}s @ {cost_delta['old_size']})"
    )

    if stills_only:
        n_beats = max(1, len(captions) or len(lines))
        stills_cost = media_cost
        # Full render pays the same image+critic, plus 9 TTS calls and MoviePy encode.
        # ElevenLabs eleven_v3 is ~$0.12–0.30 / 1k chars; 9 short captions ≈ $0.03–0.08.
        # Encode on the last full reel was ~8 min after stills were done.
        print(
            f"[LOFI stills-only] beats={n_beats} images={n_image_calls} "
            f"critic={n_critic_calls} est_cost=${stills_cost:.4f} "
            f"(image ${img_cost:.4f} + critic ${critic_cost:.4f}) | "
            f"skipped TTS + video assemble"
        )
        print(
            "[LOFI stills-only] vs full render: same image+critic cost; "
            "saves ~9 ElevenLabs TTS calls (~$0.03–0.08) and ~5–8 min MoviePy encode. "
            "Typical full 9-beat wall time ~12 min; stills-only is image+QA only."
        )
        mix: dict[str, int] = {}
        for row in lines:
            st = str(row.get("subject_type") or "?").strip().lower() or "?"
            mix[st] = mix.get(st, 0) + 1
        print(f"[LOFI stills-only] subject_type mix={mix}")
        for row in lines:
            print(
                f"[LOFI stills-only] scene={row.get('scene')} "
                f"type={row.get('subject_type')} object={row.get('key_object')!r} "
                f"text={str(row.get('text') or '')[:56]!r}"
            )
        meta = {
            "post_type": "ECONOMIC_REEL_LOFI",
            "mode": "stills_only",
            "page": page_id,
            "module": module,
            "duration_requested_s": duration_s,
            "scene_count": n_beats,
            "scene_duration_s": lofi_cfg.beat_duration_s(),
            "duration_expected_s": lofi_cfg.duration_for_beat_count(n_beats),
            "subtheme": script.get("subtheme"),
            "hook_type": script.get("hook_type"),
            "quote_id": script.get("quote_id"),
            "anchor_object": script.get("anchor_object"),
            "arc_template": script.get("arc_template"),
            "retrieved_details": script.get("retrieved_details"),
            "script": script,
            "riso_ids": [str(r.get("riso_id") or "") for r in lines],
            "visual_identity": "v2"
            if bool(getattr(lofi_cfg, "USE_VISUAL_IDENTITY_V2", False))
            else "riso_library",
            "flux_backend": "dev" if use_dev else "schnell",
            "visual_identity_profile": (
                lofi_cfg.DEFAULT_VISUAL_IDENTITY_PROFILE if use_dev else None
            ),
            "scene_images": [str(p) for p in scene_paths],
            "work_dir": str(run_dir),
            "visual_qa_flags": qa_flags,
            "manual_review": bool(qa_flags),
            "episode_variety": episode_variety,
            "object_gate_by_scene": object_gate_by_scene,
            "image_calls": n_image_calls,
            "image_call_budget": call_budget,
            "image_cost_per_call": img_cost_per_call,
            "image_cost_meta": cost_meta,
            "critic_calls": n_critic_calls,
            "script_llm_calls": script_llm_calls,
            "script_llm_cost_usd": script_llm_cost_usd,
            "est_cost_usd": total_est_cost,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        meta_path = clips_dir / f"lofi_stills_{theme_slug}_{stamp}_v{index:02d}.json"
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[LOFI stills-only] wrote {meta_path}")
        print(f"[LOFI stills-only] stills {run_dir}")
        if qa_flags:
            print(
                "[LOFI HOLD] QA exhausted — stills kept for review, episode NOT "
                f"cleared for render/post. flags={qa_flags}"
            )
        return LofiItemResult(
            ok=not bool(qa_flags),
            video_path=None,
            meta_path=str(meta_path),
            module=module,
            theme=str(script.get("theme") or ""),
            hook_type=str(script.get("hook_type") or ""),
            scene_count=n_beats,
            duration_s=lofi_cfg.duration_for_beat_count(n_beats),
            manual_review=bool(qa_flags),
            errors=qa_flags,
            script=script,
            work_dir=str(run_dir),
            stills_only=True,
        )

    if qa_flags:
        n_beats = max(1, len(captions) or len(lines))
        print(
            "[LOFI HOLD] QA exhausted — NOT assembling MP4, not postable. "
            f"flags={qa_flags}"
        )
        print(f"[LOFI HOLD] stills kept at {run_dir}")
        hold_meta = {
            "post_type": "ECONOMIC_REEL_LOFI",
            "mode": "qa_hold",
            "page": page_id,
            "module": module,
            "duration_requested_s": duration_s,
            "scene_count": n_beats,
            "subtheme": script.get("subtheme"),
            "hook_type": script.get("hook_type"),
            "arc_template": script.get("arc_template"),
            "script": script,
            "scene_images": [str(p) for p in scene_paths],
            "work_dir": str(run_dir),
            "visual_qa_flags": qa_flags,
            "manual_review": True,
            "episode_variety": episode_variety,
            "object_gate_by_scene": object_gate_by_scene,
            "video_path": None,
            "flux_backend": "dev" if use_dev else "schnell",
            "visual_identity_profile": (
                lofi_cfg.DEFAULT_VISUAL_IDENTITY_PROFILE if use_dev else None
            ),
            "image_calls": n_image_calls,
            "image_call_budget": call_budget,
            "image_cost_per_call": img_cost_per_call,
            "image_cost_meta": cost_meta,
            "critic_calls": n_critic_calls,
            "script_llm_calls": script_llm_calls,
            "script_llm_cost_usd": script_llm_cost_usd,
            "est_cost_usd": total_est_cost,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        hold_path = clips_dir / f"lofi_hold_{theme_slug}_{stamp}_v{index:02d}.json"
        hold_path.write_text(
            json.dumps(hold_meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"[LOFI HOLD] meta {hold_path}")
        return LofiItemResult(
            ok=False,
            video_path=None,
            meta_path=str(hold_path),
            module=module,
            theme=str(script.get("theme") or ""),
            hook_type=str(script.get("hook_type") or ""),
            scene_count=n_beats,
            duration_s=0.0,
            manual_review=True,
            errors=qa_flags,
            script=script,
            work_dir=str(run_dir),
        )

    out_mp4 = clips_dir / f"lofi_reel_{theme_slug}_{stamp}_v{index:02d}.mp4"
    n_beats = max(1, len(captions) or len(lines))
    lock_beat = bool(getattr(lofi_cfg, "LOCK_FIXED_BEAT_DURATION", True))
    beat_s = float(lofi_cfg.beat_duration_s())
    scene_durations: list[float] = []
    scene_timing_flags: list[dict[str, Any]] = []
    for i, cap in enumerate(captions):
        timings_i = word_timings_per_scene[i] if i < len(word_timings_per_scene) else None
        vp_i = voice_paths[i] if i < len(voice_paths) else None
        if lock_beat:
            vo_dur = 0.0
            if vp_i and Path(vp_i).is_file():
                try:
                    from avatar_engine.audio_engine import _audio_file_duration_s

                    vo_dur = float(_audio_file_duration_s(Path(vp_i)))
                except Exception:  # noqa: BLE001
                    vo_dur = 0.0
            dur_i, extended_i = lofi_cfg.slot_duration_for_vo(vo_dur, base_s=beat_s)
            dur_meta = {
                "base_s": beat_s,
                "vo_dur": round(vo_dur, 3),
                "needed_s": dur_i,
                "duration_s": dur_i,
                "locked": True,
                "vo_trimmed": False,
            }
            if extended_i:
                print(
                    f"[LOFI caption-timing] scene {i + 1} VO {vo_dur:.2f}s > "
                    f"{beat_s:.1f}s base — extending slot to {dur_i:.2f}s "
                    f"(never trim VO) | text={cap!r}"
                )
        else:
            dur_i, extended_i, dur_meta = compute_caption_scene_duration_s(
                timings_i,
                Path(vp_i) if vp_i else None,
            )
        scene_durations.append(dur_i)
        scene_timing_flags.append(
            {
                "scene": i + 1,
                "text": cap,
                "extended": extended_i,
                **dur_meta,
            }
        )
        if extended_i:
            print(
                f"[LOFI caption-timing] FLAG scene {i + 1} extended to {dur_i:.2f}s "
                f"(base={lofi_cfg.SCENE_DURATION_S:.1f}s) so the line can fully "
                f"display + hold | text={cap!r}"
            )
    actual_dur = float(sum(scene_durations))
    print(
        f"[LOFI timing] beats={n_beats} beat_s={beat_s:.1f} "
        f"total={actual_dur:.1f}s locked={int(lock_beat)} "
        f"(requested_writer_target={duration_s}s)"
    )
    want_tag = lofi_cfg.current_still_style_tag()
    by_tag: dict[str, list[int]] = {}
    for i, p in enumerate(scene_paths, start=1):
        tag = lofi_cfg.still_style_tag_of(p) or "UNTAGGED"
        by_tag.setdefault(tag, []).append(i)
    style_ok = set(by_tag.keys()) == {want_tag}
    if not style_ok:
        bits = []
        for tag, idxs in sorted(by_tag.items()):
            kind = "fresh" if tag == want_tag else "stale"
            bits.append(
                f"{len(idxs)} of {len(scene_paths)} beats are {kind} "
                f"(style {tag}) scenes={idxs}"
            )
        mix_msg = " — ".join(bits) + (
            " — cannot produce a consistent video automatically."
        )
        if not lofi_cfg.allow_mixed_era_assemble():
            print(f"[LOFI assemble] REFUSE mixed-era: {mix_msg}")
            hold_path = clips_dir / f"lofi_hold_mixed_era_{stamp}_v{index:02d}.json"
            hold_path.write_text(
                json.dumps(
                    {
                        "post_type": "ECONOMIC_REEL_LOFI",
                        "mode": "mixed_era_hold",
                        "error": mix_msg,
                        "want_style_tag": want_tag,
                        "style_tags": by_tag,
                        "scene_images": [str(p) for p in scene_paths],
                        "work_dir": str(run_dir),
                        "script": script,
                    },
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
            return LofiItemResult(
                ok=False,
                module=module,
                theme=str(script.get("theme") or ""),
                hook_type=str(script.get("hook_type") or ""),
                scene_count=scene_count,
                duration_s=actual_dur,
                manual_review=True,
                errors=[mix_msg],
                script=script,
                meta_path=str(hold_path),
            )
        print(f"[LOFI assemble] mixed-era APPROVED: {mix_msg}")
    else:
        print(
            f"[LOFI assemble] style_ok=1 tag={want_tag} "
            f"beats={len(scene_paths)}"
        )
    try:
        assemble_audit: dict[str, Any] = {}
        assemble_lofi_reel(
            scene_paths,
            captions,
            out_mp4,
            engine_root=_engine_root(),
            page_id=page_id,
            scene_duration_s=lofi_cfg.SCENE_DURATION_S,
            scene_durations=scene_durations,
            moods=scene_moods,
            caption_style=lofi_cfg.DEFAULT_CAPTION_STYLE,
            voice_paths=voice_paths,
            word_timings_per_scene=word_timings_per_scene,
            audit_out=assemble_audit,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.error("assemble failed: %s", exc, exc_info=True)
        return LofiItemResult(
            ok=False,
            module=module,
            theme=str(script.get("theme") or ""),
            hook_type=str(script.get("hook_type") or ""),
            scene_count=scene_count,
            duration_s=actual_dur,
            manual_review=True,
            errors=[f"assemble failed: {exc}"],
            script=script,
        )

    meta = {
        "post_type": "ECONOMIC_REEL_LOFI",
        "page": page_id,
        "module": module,
        "duration_requested_s": duration_s,
        "duration_actual_s": actual_dur,
        "duration_expected_s": lofi_cfg.duration_for_beat_count(n_beats),
        "scene_count": n_beats,
        "scene_duration_s": beat_s,
        "pacing": "locked_3s_per_beat" if lock_beat else "vo_extend",
        "scene_durations": scene_durations,
        "caption_timing": scene_timing_flags,
        "voice_settings_api": voice_settings_result,
        "voice_speed": getattr(lofi_cfg, "LOFI_VOICE_SPEED", None),
        "logo_path": str(
            lofi_cfg.resolve_logo_path(page_id, _engine_root()) or ""
        ),
        "caption_line_height_frac": lofi_cfg.CAPTION_LINE_HEIGHT_FRAC,
        "subtheme": script.get("subtheme"),
        "hook_type": script.get("hook_type"),
        "quote_id": script.get("quote_id"),
        "anchor_object": script.get("anchor_object"),
        "arc_template": script.get("arc_template"),
        "retrieved_details": script.get("retrieved_details"),
        "script": script,
        "riso_ids": [str(r.get("riso_id") or "") for r in lines],
        "visual_identity": "v2"
        if bool(getattr(lofi_cfg, "USE_VISUAL_IDENTITY_V2", False))
        else "riso_library",
        "flux_backend": "dev" if use_dev else "schnell",
        "visual_identity_profile": (
            lofi_cfg.DEFAULT_VISUAL_IDENTITY_PROFILE if use_dev else None
        ),
        "still_style_tag": lofi_cfg.current_still_style_tag(),
        "gen_width": lofi_cfg.LOFI_IMAGE_WIDTH,
        "gen_height": lofi_cfg.LOFI_IMAGE_HEIGHT,
        "delivery_width": lofi_cfg.REEL_WIDTH,
        "delivery_height": lofi_cfg.REEL_HEIGHT,
        "aspect": round(lofi_cfg.LOFI_IMAGE_WIDTH / lofi_cfg.LOFI_IMAGE_HEIGHT, 6),
        "image_cost_delta": cost_delta,
        "watermark_native_size": int(assemble_audit.get("watermark_native_size") or 0),
        "watermark_audit": assemble_audit,
        "voice_id": getattr(lofi_cfg, "LOFI_VOICE_ID", None),
        "caption_style": lofi_cfg.DEFAULT_CAPTION_STYLE,
        "grading_applied": bool(getattr(lofi_cfg, "LOFI_APPLY_GRADING", False)),
        "video_path": str(out_mp4),
        "scene_images": [str(p) for p in scene_paths],
        "voice_paths": [str(p) if p else None for p in voice_paths],
        "work_dir": str(run_dir),
        "visual_qa_flags": qa_flags,
        "manual_review": False,
        "episode_variety": episode_variety,
        "object_gate_by_scene": object_gate_by_scene,
        "image_calls": n_image_calls,
        "image_call_budget": call_budget,
        "image_cost_per_call": img_cost_per_call,
        "image_cost_meta": cost_meta,
        "critic_calls": n_critic_calls,
        "script_llm_calls": script_llm_calls,
        "script_llm_cost_usd": script_llm_cost_usd,
        "est_cost_usd": total_est_cost,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    meta_path = clips_dir / f"{out_mp4.stem}.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    ao = script.get("anchor_object") if isinstance(script.get("anchor_object"), dict) else {}
    rag.record_video_performance(
        module,
        theme=str(script.get("theme") or ""),
        subtheme=str(script.get("subtheme") or ""),
        views=None,
        completion_rate=None,
        anchor_object=str(ao.get("name") or "") or None,
        video_path=str(out_mp4),
        retrieved_details=list(script.get("retrieved_details") or []),
    )

    return LofiItemResult(
        ok=True,
        video_path=str(out_mp4),
        meta_path=str(meta_path),
        module=module,
        theme=str(script.get("theme") or ""),
        hook_type=str(script.get("hook_type") or ""),
        scene_count=n_beats,
        duration_s=actual_dur,
        manual_review=False,
        errors=qa_flags,
        script=script,
    )


def run_economic_reel_lofi(
    *,
    page_id: str,
    quantity: int = 1,
    duration: int | None = None,
    module: str | None = None,
    outputs_dir: Path | str | None = None,
    theme: str | None = None,
    subtheme: str | None = None,
    script_only: bool = False,
    stills_only: bool = False,
    locked_scripts: list[str] | None = None,
    **_: Any,
) -> dict[str, Any]:
    """
    Registry entrypoint for ECONOMIC_REEL_LOFI.

    Final MP4 + metadata → ``outputs/<page>/clips/``
    Working scene stills → ``outputs/<page>/assets/lofi_run_*/``
    """
    page = (page_id or "").strip().lower()
    mod = lofi_cfg.validate_module_for_page(module or "relationship", page)
    dur = lofi_cfg.validate_duration(duration if duration is not None else lofi_cfg.DEFAULT_DURATION_S)
    qty = max(1, int(quantity))

    rag.ensure_seeded()
    reset_batch_structure_ids()
    force_theme = str(theme or os.getenv("LOFI_FORCE_THEME") or "").strip() or None
    force_subtheme = str(subtheme or os.getenv("LOFI_FORCE_SUBTHEME") or "").strip() or None
    locked_rows: list[dict[str, Any]] = []
    for raw_path in locked_scripts or []:
        if str(raw_path).strip():
            locked_rows.append(_load_locked_script(raw_path))
    if locked_rows:
        qty = len(locked_rows)

    page_outputs, clips_dir, assets_dir = _resolve_page_dirs(page, outputs_dir)

    items: list[dict[str, Any]] = []
    ok_n = 0
    for i in range(1, qty + 1):
        print(
            f"[ECONOMIC_REEL_LOFI] ({i}/{qty}) page={page} module={mod} "
            f"duration={dur}s scenes={lofi_cfg.scene_count_for_duration(dur)} "
            f"theme={force_theme or 'auto'} "
            f"{'STILLS-ONLY ' if stills_only else ''}"
            f"{'SCRIPT-ONLY ' if script_only and not stills_only else ''}"
            f"→ {clips_dir}"
        )
        result = _produce_one(
            page_id=page,
            module=mod,
            duration_s=dur,
            clips_dir=clips_dir,
            assets_dir=assets_dir,
            force_theme=force_theme,
            force_subtheme=force_subtheme,
            index=i,
            script_only=script_only and not stills_only,
            stills_only=stills_only,
            batch_qty=qty,
            locked_script=locked_rows[i - 1] if i <= len(locked_rows) else None,
        )
        item = asdict(result)
        items.append(item)
        if result.ok:
            ok_n += 1
            if result.video_path:
                print(f"  Video : {result.video_path}")
            if result.work_dir:
                print(f"  Stills: {result.work_dir}")
            print(f"  Meta  : {result.meta_path}")
            if result.manual_review:
                print(f"  WARN  : flagged for manual review ({'; '.join(result.errors)})")
        else:
            print(f"  FAIL  : {'; '.join(result.errors)}")
            if result.meta_path:
                print(f"  Review: {result.meta_path}")

    envelope = {
        "post_type": "ECONOMIC_REEL_LOFI",
        "page": page,
        "module": mod,
        "duration_s": dur,
        "quantity": qty,
        "successful": ok_n,
        "script_only": bool(script_only) and not stills_only,
        "stills_only": bool(stills_only),
        "items": items,
        "outputs_dir": str(page_outputs),
        "clips_dir": str(clips_dir),
    }
    summary_path = clips_dir / f"lofi_batch_{_utc_stamp()}.json"
    summary_path.write_text(json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")
    envelope["batch_meta"] = str(summary_path)
    return envelope
