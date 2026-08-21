# -*- coding: utf-8 -*-
"""ValidatorAgent — separate from ScriptGeneratorAgent (no rubber-stamp incentive)."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from core_engine.economic_reel_lofi import config as lofi_cfg
from core_engine.economic_reel_lofi import lofi_collections as rag
from core_engine.economic_reel_lofi.reference_guard import reference_overlap_hit
from core_engine.economic_reel_lofi.visual_identity import (
    beat_lacks_noun_and_action,
    visual_tied_to_caption,
)

_LOG = logging.getLogger(__name__)

_EXPLICIT_RE = re.compile(
    r"\b(nsfw|nude|naked|porn|sex\b|erotic|genital|xxx)\b",
    re.IGNORECASE,
)


@dataclass
class ValidationResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    script: dict[str, Any] | None = None

    def feedback(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "rejected"


def _script_flat_text(script: dict[str, Any]) -> str:
    lines = script.get("lines") or []
    parts = []
    for row in lines:
        if isinstance(row, dict):
            parts.append(str(row.get("text") or ""))
        else:
            parts.append(str(row))
    return "\n".join(parts)


def validate_script(
    script: dict[str, Any],
    *,
    module: str,
    scene_count: int,
    persist_on_pass: bool = True,
) -> ValidationResult:
    """
    Ordered checks:
      1) structural  2) quote integrity  3) reference-copy  4) dedup  5) content safety
    """
    reasons: list[str] = []

    # 1) Structural
    if not isinstance(script, dict):
        return ValidationResult(False, ["script is not a JSON object"])

    thematic = lofi_cfg.is_thematic_arc(str(script.get("arc_template") or ""))
    max_words, max_chars = lofi_cfg.caption_limits(
        str(script.get("arc_template") or "")
    )
    object_hooks = {"definition", "rhetorical_question", "authority_quote"}
    allowed_hooks = set(object_hooks)
    if thematic:
        allowed_hooks |= set(getattr(lofi_cfg, "THEMATIC_HOOK_TYPES", ()))

    hook = str(script.get("hook_type") or "").strip()
    if hook not in allowed_hooks:
        reasons.append(f"invalid or missing hook_type ({hook!r})")

    lines = script.get("lines")
    min_scenes = int(getattr(lofi_cfg, "MIN_SCENES", 8))
    max_scenes = (
        int(lofi_cfg.thematic_max_scenes())
        if thematic
        else int(getattr(lofi_cfg, "MAX_SCENES", 12))
    )
    if not isinstance(lines, list):
        reasons.append(f"expected a lines list, got {type(lines).__name__}")
    else:
        if thematic:
            if not (min_scenes <= len(lines) <= max_scenes):
                reasons.append(
                    f"expected {min_scenes}–{max_scenes} lines for thematic_arc, "
                    f"got {len(lines)}"
                )
        elif len(lines) != scene_count:
            reasons.append(
                f"expected {scene_count} lines, got {len(lines)}"
            )
        for i, row in enumerate(lines, start=1):
            if not isinstance(row, dict):
                reasons.append(f"scene {i} is not an object")
                continue
            text = str(row.get("text") or row.get("beat_text") or "").strip()
            visual = str(row.get("visual_prompt") or "").strip()
            use_v2 = bool(getattr(lofi_cfg, "USE_VISUAL_IDENTITY_V2", False))
            if not text:
                reasons.append(f"scene {i} has empty text")
            else:
                n_words = len(text.split())
                if n_words > max_words:
                    if thematic and len(lines) >= max_scenes:
                        reasons.append(
                            f"scene {i} has {n_words} words (max {max_words}) — "
                            "shorten the line; do not add another beat"
                        )
                    else:
                        reasons.append(
                            f"scene {i} has {n_words} words (max {max_words}) — "
                            "split the thought across two beats instead of compressing"
                        )
                if len(text) > max_chars:
                    reasons.append(
                        f"scene {i} caption exceeds {max_chars} chars "
                        f"({len(text)})"
                    )
                if (
                    not thematic
                    and bool(getattr(lofi_cfg, "REQUIRE_BEAT_CONCRETENESS", True))
                ):
                    if beat_lacks_noun_and_action(text):
                        reasons.append(
                            f"scene {i} has no concrete noun+action pairing "
                            f"({text!r}) — rewrite around an object doing something"
                        )
                if not thematic:
                    low = text.lower()
                    if re.search(
                        r"it wasn['’]?t .{1,40} it was\b|"
                        r"\bis not .{1,30},?\s*it['’]?s\b|"
                        r"wasn't .{1,30}, it was\b",
                        low,
                    ):
                        reasons.append(
                            f"scene {i} uses banned 'X is not Y, it's Z' template"
                        )
            if use_v2:
                st = str(row.get("subject_type") or "").strip().lower()
                if st not in {
                    "woman",
                    "man",
                    "couple",
                    "silhouette",
                    "object_focus",
                }:
                    reasons.append(f"scene {i} missing/invalid subject_type ({st!r})")
                if not str(row.get("setting") or "").strip():
                    reasons.append(f"scene {i} missing setting")
                if not str(row.get("key_object") or "").strip():
                    reasons.append(f"scene {i} missing key_object")
                if not str(row.get("subject_expression") or "").strip():
                    reasons.append(f"scene {i} missing subject_expression")
                if thematic and not visual_tied_to_caption(
                    text,
                    str(row.get("setting") or ""),
                    str(row.get("key_object") or ""),
                ):
                    reasons.append(
                        f"scene {i} setting/object not tied to caption "
                        f"({text!r} vs {row.get('key_object')!r})"
                    )
            else:
                if not visual:
                    reasons.append(f"scene {i} missing visual_prompt")
                if visual and text and visual.strip().lower() == text.strip().lower():
                    reasons.append(f"scene {i} visual_prompt must differ from caption text")

        # Split-across-cut version of the same template (object arcs only)
        if not thematic:
            for i in range(len(lines) - 1):
                a = str((lines[i].get("text") if isinstance(lines[i], dict) else "") or "").lower()
                b = str(
                    (lines[i + 1].get("text") if isinstance(lines[i + 1], dict) else "") or ""
                ).lower()
                if re.search(r"\b(isn['’]t|is not|wasn['’]t)\b", a) and re.search(
                    r"^(it['’]s|it is)\b", b.strip()
                ):
                    reasons.append(
                        f"scenes {i + 1}-{i + 2} split banned 'X isn't Y / it's Z' across the cut"
                    )

    if (not thematic) and bool(getattr(lofi_cfg, "REQUIRE_ANCHOR_OBJECT", True)):
        ao = script.get("anchor_object")
        if not isinstance(ao, dict):
            reasons.append("missing anchor_object (need name, initial_state, final_state)")
        else:
            for key in ("name", "initial_state", "final_state"):
                if not str(ao.get(key) or "").strip():
                    reasons.append(f"anchor_object missing {key}")

    # 2) Quote integrity
    if hook == "authority_quote" and not reasons:
        quote_id = str(script.get("quote_id") or "").strip()
        q = rag.get_quote_by_id(quote_id) if quote_id else None
        if not q:
            # Try exact match on scene-1 text
            first = ""
            if isinstance(lines, list) and lines and isinstance(lines[0], dict):
                first = str(lines[0].get("text") or "")
            q = rag.find_quote_exact(first)
            if q:
                script["quote_id"] = q["id"]
            else:
                reasons.append(
                    "authority_quote hook but quote not found in lofi_verified_quotes"
                )
        if q and isinstance(lines, list) and lines and isinstance(lines[0], dict):
            first = str(lines[0].get("text") or "")
            body = str(q.get("quote_text") or "")
            if body and body.lower() not in first.lower():
                reasons.append(
                    "authority quote paraphrased — must match verified quote_text verbatim"
                )

    # 3) Dedup / similarity
    flat = _script_flat_text(script)
    mono = str(script.get("monologue") or "").strip()
    overlap_blob = "\n".join(p for p in (mono, flat) if p)
    if overlap_blob:
        hit, ref_reason = reference_overlap_hit(overlap_blob)
        if hit:
            reasons.append(ref_reason)
            print(f"[LOFI validator] reference-copy REJECT")

    if flat and not reasons:
        sim = rag.max_similarity_to_history(module, flat)
        if sim >= lofi_cfg.DEDUP_SIMILARITY_THRESHOLD:
            reasons.append(
                f"dedup similarity {sim:.3f} >= {lofi_cfg.DEDUP_SIMILARITY_THRESHOLD}"
            )

    # 4) Content safety
    blob = flat + " " + " ".join(
        str((row or {}).get("visual_prompt", ""))
        for row in (lines if isinstance(lines, list) else [])
        if isinstance(row, dict)
    )
    if _EXPLICIT_RE.search(blob):
        reasons.append("content safety: explicit language detected")

    # Very light PII / private individual heuristic — block "my husband John" style
    if re.search(r"\b(my|our)\s+(husband|wife|ex|boyfriend|girlfriend)\s+[A-Z][a-z]{2,}\b", blob):
        reasons.append("content safety: named private individual detected")

    if reasons:
        msg = "; ".join(reasons)
        _LOG.info("ValidatorAgent REJECT: %s", msg)
        print(f"[LOFI validator] REJECT: {msg}")
        return ValidationResult(False, reasons, script=script)

    if persist_on_pass:
        rag.append_history(
            module,
            flat,
            meta={
                "theme": script.get("theme"),
                "hook_type": hook,
                "quote_id": script.get("quote_id"),
                "anchor_object": script.get("anchor_object"),
                "arc_template": script.get("arc_template"),
                "retrieved_details": script.get("retrieved_details"),
            },
        )
        theme = str(script.get("theme") or "")
        sub = str(script.get("subtheme") or "")
        if theme:
            rag.mark_theme_used(module, theme, sub or None)
            rag.mark_core_rag_used(module, script)

    _LOG.info("ValidatorAgent PASS | hook=%s theme=%s scenes=%s", hook, script.get("theme"), scene_count)
    return ValidationResult(True, [], script=script)
