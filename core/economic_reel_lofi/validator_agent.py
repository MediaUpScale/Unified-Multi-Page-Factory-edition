# -*- coding: utf-8 -*-
"""ValidatorAgent — separate from ScriptGeneratorAgent (no rubber-stamp incentive)."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from core.economic_reel_lofi import config as lofi_cfg
from core.economic_reel_lofi import lofi_collections as rag
from core.economic_reel_lofi.reference_guard import reference_overlap_hit
from core.economic_reel_lofi.visual_identity import (
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
    # Length is the writer's call on freeform scripts, but spoken duration is not:
    # N beats × beat_s must fit duration_requested_s.
    freeform = str(script.get("writer") or "").strip().startswith("freeform")
    if not isinstance(lines, list):
        reasons.append(f"expected a lines list, got {type(lines).__name__}")
    else:
        if freeform:
            if not lines:
                reasons.append("freeform script has no lines")
        elif thematic:
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
                continue
            slot_s = float(
                row.get("duration_s")
                or script.get("scene_duration_s")
                or lofi_cfg.beat_duration_s()
            )
            spoken_ceiling = lofi_cfg.beat_word_ceiling(slot_s)
            n_spoken = len(text.split())
            if n_spoken > spoken_ceiling:
                reasons.append(
                    f"scene {i} spoken line has {n_spoken} words "
                    f"(max {spoken_ceiling} for {slot_s:.1f}s at "
                    f"{lofi_cfg.narration_wpm():.0f} wpm) — rewrite that line; "
                    "do not let it proceed to image generation"
                )
            if freeform:
                caps = row.get("caption_beats")
                caps = [str(c).strip() for c in caps if str(c).strip()] if isinstance(caps, list) else []
                if not caps:
                    reasons.append(f"scene {i} has no caption_beats")
                for j, cap in enumerate(caps, start=1):
                    if len(cap.split()) > max_words or len(cap) > max_chars:
                        reasons.append(
                            f"scene {i} caption beat {j} exceeds {max_words}w/"
                            f"{max_chars}c ({len(cap.split())}w/{len(cap)}c): {cap!r}"
                        )
                if caps and " ".join(caps).split() != text.split():
                    reasons.append(
                        f"scene {i} caption_beats do not reconstruct the written line"
                    )
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
            visuals_ready = bool(
                str(row.get("setting") or "").strip()
                and str(row.get("subject_type") or "").strip()
            )
            if use_v2 and (not freeform or visuals_ready):
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
                if (
                    not str(row.get("key_object") or "").strip()
                    and not row.get("abstract_license")
                ):
                    reasons.append(f"scene {i} missing key_object")
                if not str(row.get("subject_expression") or "").strip():
                    reasons.append(f"scene {i} missing subject_expression")
                world = script.get("episode_atmosphere")
                if not isinstance(world, dict) or not world:
                    world = None
                    if isinstance(row, dict) and str(
                        row.get("episode_world_id") or ""
                    ).strip():
                        world = {
                            "id": row.get("episode_world_id"),
                            "place": row.get("atmosphere_place"),
                        }
                if thematic and not visual_tied_to_caption(
                    text,
                    str(row.get("setting") or ""),
                    str(row.get("key_object") or ""),
                    episode_world=world,
                ):
                    reasons.append(
                        f"scene {i} setting/object not tied to caption "
                        f"({text!r} vs {row.get('key_object')!r})"
                    )
            elif not use_v2:
                if not visual:
                    reasons.append(f"scene {i} missing visual_prompt")
                if visual and text and visual.strip().lower() == text.strip().lower():
                    reasons.append(f"scene {i} visual_prompt must differ from caption text")

        beat_s = float(script.get("scene_duration_s") or lofi_cfg.beat_duration_s())
        requested = float(
            script.get("duration_requested_s")
            or script.get("duration_s")
            or lofi_cfg.declared_duration_s(scene_count=len(lines))
        )
        max_beats = lofi_cfg.max_beats_for_duration(requested)
        if len(lines) > max_beats:
            needed = round(len(lines) * beat_s, 3)
            reasons.append(
                f"script has {len(lines)} beats × {beat_s:.1f}s = {needed:.1f}s "
                f"but duration_requested_s is {requested:.1f}s (max {max_beats} beats). "
                f"Request a longer format up front; do not overrun silently."
            )

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

    # Regex spine/stakes/close scoring. Recorded as a diagnostic note only — the
    # writing itself is now judged by the five-criterion LLM gate in
    # agents.writer.judge_gate, which scores what the piece does rather than
    # whether it matches a shape.
    from agents.writer.script_agent import assess_story_quality

    story = assess_story_quality(
        lines if isinstance(lines, list) else [],
        theme=str(script.get("theme") or ""),
    )
    story["blocking"] = False
    script["story_quality"] = story
    if story.get("fails"):
        _LOG.info("story_quality diagnostic (non-blocking): %s", story.get("fails"))
        print(f"[LOFI validator] story_quality note (non-blocking): {story.get('fails')}")

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
                "close_variant": script.get("close_variant"),
                "close_target": script.get("close_target"),
                "eye_close_context": script.get("eye_close_context"),
                "close_character": script.get("close_character"),
                "dominant_lighting": script.get("dominant_lighting"),
                "lighting_beats": script.get("lighting_beats"),
                "setting_archetypes": (
                    (script.get("setting_archetypes") or {}).get("unique")
                    if isinstance(script.get("setting_archetypes"), dict)
                    else script.get("setting_archetypes")
                ),
                "setting_archetype_beats": (
                    (script.get("setting_archetypes") or {}).get("beats")
                    if isinstance(script.get("setting_archetypes"), dict)
                    else None
                ),
                "connective_map": rag.extract_connective_map(
                    [r for r in (script.get("lines") or []) if isinstance(r, dict)]
                ),
                "rhetoric_pattern": script.get("rhetoric_pattern"),
                "rhetoric_hook_overlay": script.get("rhetoric_hook_overlay"),
            },
        )
        theme = str(script.get("theme") or "")
        sub = str(script.get("subtheme") or "")
        if theme:
            rag.mark_theme_used(module, theme, sub or None)
            rag.mark_core_rag_used(module, script)

    _LOG.info("ValidatorAgent PASS | hook=%s theme=%s scenes=%s", hook, script.get("theme"), scene_count)
    return ValidationResult(True, [], script=script)
