# -*- coding: utf-8 -*-
"""
Master Mei Visual Inspector Agent (VLM validator).

Runs AFTER sequence image generation, BEFORE reel compile:
  1. Frame 1 + Frame 10 must contain Master Mei (elderly Asian master, dark robes).
  2. Reject if >2 frames share near-identical framing (duplicate prevention).
  3. Diversity: mix of wide / medium-action / high-concept biomechanical shots.

Uses Gemini Vision when available; falls back to cheap perceptual hashing for
duplicate detection so the pipeline never hard-fails offline.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

_LOG = logging.getLogger(__name__)


@dataclass
class FrameVerdict:
    index: int  # 0-based
    ok: bool = True
    reasons: list[str] = field(default_factory=list)
    framing_tag: str = ""  # wide | medium | close | biomech | unknown
    has_master_mei: bool | None = None


@dataclass
class SequenceInspectResult:
    passed: bool
    frames: list[FrameVerdict] = field(default_factory=list)
    regenerate_indices: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _phash_similarity(a: Path, b: Path) -> float:
    """Return 0..1 similarity via average-hash Hamming (1 = identical)."""
    try:
        from PIL import Image
        import numpy as np

        def _ahash(p: Path) -> np.ndarray:
            img = Image.open(p).convert("L").resize((16, 16))
            arr = np.asarray(img, dtype=np.float32)
            return (arr > arr.mean()).astype(np.uint8).flatten()

        ha, hb = _ahash(a), _ahash(b)
        dist = float(np.count_nonzero(ha != hb))
        return 1.0 - dist / float(ha.size)
    except Exception:  # noqa: BLE001
        return 0.0


def _duplicate_indices(paths: Sequence[Path], *, max_same: int = 2, thresh: float = 0.92) -> list[int]:
    """
    Flag frames that belong to clusters larger than ``max_same`` near-duplicates.
    Returns 0-based indices recommended for regeneration (extras beyond max_same).
    """
    n = len(paths)
    if n < 3:
        return []
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(n):
        if not paths[i].is_file():
            continue
        for j in range(i + 1, n):
            if not paths[j].is_file():
                continue
            if _phash_similarity(paths[i], paths[j]) >= thresh:
                union(i, j)

    clusters: dict[int, list[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)

    regen: list[int] = []
    for members in clusters.values():
        if len(members) > max_same:
            # Keep first max_same; regenerate the rest
            regen.extend(sorted(members)[max_same:])
    return sorted(set(regen))


def _gemini_inspect_sequence(paths: Sequence[Path]) -> SequenceInspectResult | None:
    """Full VLM pass — returns None if Gemini unavailable."""
    try:
        import config as app_config
        from google import genai
        from google.genai import types
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("Visual inspector Gemini import failed: %s", exc)
        return None

    api_key = getattr(app_config, "GEMINI_API_KEY", None) or ""
    if not api_key:
        return None

    valid = [(i, Path(p)) for i, p in enumerate(paths) if Path(p).is_file()]
    if not valid:
        return SequenceInspectResult(passed=False, notes=["no images"])

    parts: list = [
        types.Part.from_text(
            text=(
                "You are the Visual Control Agent for Master Mei Shorts.\n"
                "Inspect this ordered sequence of frames (Frame 1 = first image).\n\n"
                "RULES:\n"
                "1) Frame 1 AND the LAST frame MUST show Master Mei: elderly Asian "
                "master, long white hair/beard, dark traditional robes. "
                "If absent → fail that frame.\n"
                "2) Duplicate prevention: if more than 2 frames share nearly identical "
                "framing (e.g. repeating VR-goggle close-ups), list extras to regenerate.\n"
                "3) Diversity: sequence should mix wide landscape, medium action, and "
                "high-concept biomechanical/cyberpunk imagery.\n\n"
                "Return STRICT JSON only:\n"
                "{\n"
                '  "frames": [{"index": 1, "has_master_mei": true/false, '
                '"framing": "wide|medium|close|biomech", "ok": true/false, '
                '"reason": "..."}],\n'
                '  "regenerate": [2, 5],\n'
                '  "diversity_ok": true/false,\n'
                '  "notes": ["..."]\n'
                "}\n"
                "index is 1-based matching Frame numbers."
            )
        )
    ]
    for i, p in valid:
        try:
            mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
            parts.append(types.Part.from_text(text=f"FRAME {i + 1}:"))
            parts.append(types.Part.from_bytes(data=p.read_bytes(), mime_type=mime))
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("Inspector skip unreadable %s: %s", p.name, exc)

    client = genai.Client(api_key=api_key)
    model_ids = [
        getattr(app_config, "GEMINI_FLASH_MODEL", None) or "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
    ]
    text = ""
    for mid in model_ids:
        try:
            resp = client.models.generate_content(
                model=mid,
                contents=parts,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                ),
            )
            text = (getattr(resp, "text", None) or "").strip()
            if text:
                break
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("Inspector model %s failed: %s", mid, exc)

    if not text:
        return None

    try:
        # Strip fences if present
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("Inspector JSON parse failed: %s | raw=%.200s", exc, text)
        return None

    n = len(paths)
    frames: list[FrameVerdict] = [
        FrameVerdict(index=i) for i in range(n)
    ]
    for fr in data.get("frames") or []:
        try:
            idx0 = int(fr.get("index", 0)) - 1
        except (TypeError, ValueError):
            continue
        if idx0 < 0 or idx0 >= n:
            continue
        frames[idx0].has_master_mei = fr.get("has_master_mei")
        frames[idx0].framing_tag = str(fr.get("framing") or "")
        frames[idx0].ok = bool(fr.get("ok", True))
        reason = str(fr.get("reason") or "").strip()
        if reason:
            frames[idx0].reasons.append(reason)

    regen: set[int] = set()
    for r in data.get("regenerate") or []:
        try:
            ri = int(r) - 1
            if 0 <= ri < n:
                regen.add(ri)
        except (TypeError, ValueError):
            pass

    # Hard rules: Frame 1 + last must have Master Mei
    if frames[0].has_master_mei is False:
        frames[0].ok = False
        frames[0].reasons.append("Frame 1 missing Master Mei")
        regen.add(0)
    if n > 1 and frames[-1].has_master_mei is False:
        frames[-1].ok = False
        frames[-1].reasons.append("Frame 10/outro missing Master Mei")
        regen.add(n - 1)

    if data.get("diversity_ok") is False:
        # Soft: pick middle close-ups for regen if not already flagged
        closes = [f.index for f in frames if f.framing_tag == "close" and f.index not in regen]
        for ci in closes[2:]:  # allow max 2 closes
            regen.add(ci)
            frames[ci].ok = False
            frames[ci].reasons.append("diversity: excess close-ups")

    notes = [str(x) for x in (data.get("notes") or [])]
    passed = len(regen) == 0
    return SequenceInspectResult(
        passed=passed,
        frames=frames,
        regenerate_indices=sorted(regen),
        notes=notes,
    )


def inspect_sequence_images(
    image_paths: Sequence[Path | str],
    *,
    use_vlm: bool = True,
) -> SequenceInspectResult:
    """
    Validate a Master Mei sequence. Prefer Gemini Vision; always run phash
    duplicate guard as a safety net.
    """
    paths = [Path(p) for p in image_paths]
    result = SequenceInspectResult(passed=True, frames=[FrameVerdict(index=i) for i in range(len(paths))])

    # 1) Cheap duplicate guard (always)
    dupes = _duplicate_indices(paths, max_same=2, thresh=0.92)
    if dupes:
        result.passed = False
        result.regenerate_indices = sorted(set(result.regenerate_indices) | set(dupes))
        result.notes.append(
            f"phash: >2 near-identical framings → regen acts {[i + 1 for i in dupes]}"
        )
        for i in dupes:
            result.frames[i].ok = False
            result.frames[i].reasons.append("near-duplicate framing")

    # 2) VLM inspector
    if use_vlm:
        vlm = _gemini_inspect_sequence(paths)
        if vlm is not None:
            # Merge regenerate sets
            result.regenerate_indices = sorted(
                set(result.regenerate_indices) | set(vlm.regenerate_indices)
            )
            result.notes.extend(vlm.notes)
            for fv in vlm.frames:
                if 0 <= fv.index < len(result.frames):
                    result.frames[fv.index] = fv
            result.passed = len(result.regenerate_indices) == 0
            _LOG.info(
                "VISUAL_INSPECTOR VLM | passed=%s regen=%s notes=%s",
                result.passed,
                [i + 1 for i in result.regenerate_indices],
                result.notes[:3],
            )
        else:
            result.notes.append("VLM unavailable — phash-only inspection")
            _LOG.warning("VISUAL_INSPECTOR | Gemini VLM unavailable — phash only")

    # 3) Heuristic: if Frame 1/last are byte-identical to a middle slave frame, regen
    if len(paths) >= 3 and paths[0].is_file():
        for mid in range(1, len(paths) - 1):
            if paths[mid].is_file() and _phash_similarity(paths[0], paths[mid]) >= 0.95:
                if mid not in result.regenerate_indices:
                    result.regenerate_indices.append(mid)
                    result.frames[mid].ok = False
                    result.frames[mid].reasons.append("duplicate of Frame 1")
                    result.passed = False

    result.regenerate_indices = sorted(set(result.regenerate_indices))
    result.passed = len(result.regenerate_indices) == 0
    return result
