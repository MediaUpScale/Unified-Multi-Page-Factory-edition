# -*- coding: utf-8 -*-
"""
Audio-aligned scene prompt generator.

``generate_scene_prompts(script, audio_chunks, channel_dna)`` returns one
JSON object per 3–5 s spoken window. The image must illustrate the words
spoken in that window — not the channel's generic tropes.
"""
from __future__ import annotations

import json
import logging
import math
import re
from typing import Any, Iterable

from core.audio_chunker import AudioChunk

_LOG = logging.getLogger(__name__)

_DEDUPE_THRESHOLD = 0.75

# Channel-style extras only (not historical landmark bans). Landmark bans
# come from ``_DOMAIN_PROFILES`` so they stay topic-specific.
_GENERIC_TROPES: dict[str, tuple[str, ...]] = {
    "master_mei": (
        "neon billboard", "cybernetic visor", "matrix pod",
    ),
}

_NEG_BASE = (
    "text, watermark, logo, caption, subtitles, frame, border, "
    "modern clothing, smartphone, CGI, cartoon, illustration"
)

# Topic-aware visual domains. First-pass Flux stills collapse to cliché
# landmarks unless the prompt is geographically anchored up front.
_DOMAIN_PROFILES: dict[str, dict[str, Any]] = {
    "hellenistic_greece": {
        "match": (
            "antikythera", "hellenistic", "archimedes", "ancient greece",
            "greece", "greek", "mediterranean", "analogue computer",
            "analog computer", "clockwork", "bronze gear",
        ),
        "anchors": (
            "Ancient Greece, Hellenistic aesthetics, Mediterranean setting, "
            "marble architecture, bronze patina, clockwork gears"
        ),
        "banned": (
            "pyramid", "pyramids", "egyptian", "egypt", "pharaoh", "pharaohs",
        ),
    },
    "ancient_egypt": {
        "match": (
            "giza", "great pyramid", "khufu", "sphinx", "pharaoh",
            "nile", "karnak", "luxor", "saqqara", "egyptian",
        ),
        "anchors": (
            "Ancient Egypt, Nile valley, limestone masonry, "
            "period-accurate Egyptian monuments named in the spoken beat"
        ),
        "banned": (),
    },
}

def _extract_json_payload(text: str) -> Any:
    raw = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if fence:
        raw = fence.group(1).strip()
    start_obj, end_obj = raw.find("{"), raw.rfind("}")
    start_arr, end_arr = raw.find("["), raw.rfind("]")
    if start_arr >= 0 and (start_obj < 0 or start_arr < start_obj) and end_arr > start_arr:
        raw = raw[start_arr : end_arr + 1]
    elif start_obj >= 0 and end_obj > start_obj:
        raw = raw[start_obj : end_obj + 1]
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    return json.loads(raw)


def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9']+", (text or "").lower()) if len(t) >= 3]


def _embed(text: str) -> list[float]:
    tokens = _tokenize(text) or ["empty"]
    dim = 64
    vec = [0.0] * dim
    for tok in tokens:
        h = abs(hash(tok))
        vec[h % dim] += 1.0
        vec[(h // dim) % dim] += 0.35
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine_similarity(a: str, b: str) -> float:
    va, vb = _embed(a), _embed(b)
    return float(sum(x * y for x, y in zip(va, vb)))


def _tropes_for(channel_id: str, dna: dict[str, Any] | None) -> list[str]:
    tropes = list(_GENERIC_TROPES.get((channel_id or "").lower(), ()))
    extra = (dna or {}).get("generic_tropes") or (dna or {}).get("forbidden_tokens") or []
    if isinstance(extra, (list, tuple)):
        tropes.extend(str(x) for x in extra if str(x).strip())
    # Dedup, keep short visual tropes only
    seen: set[str] = set()
    out: list[str] = []
    for t in tropes:
        key = t.strip().lower()
        if 2 < len(key) < 40 and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def infer_visual_domain(script: str, topic: str = "") -> str:
    """Return a domain profile id, or '' when no geographic lock applies."""
    blob = f"{script or ''} {topic or ''}".lower()
    best_id = ""
    best_hits = 0
    for domain_id, spec in _DOMAIN_PROFILES.items():
        hits = sum(1 for kw in spec["match"] if kw in blob)
        if hits > best_hits:
            best_id = domain_id
            best_hits = hits
    return best_id if best_hits else ""


def domain_anchors(domain_id: str) -> str:
    spec = _DOMAIN_PROFILES.get(domain_id) or {}
    return str(spec.get("anchors") or "").strip()


def domain_banned_subjects(
    domain_id: str,
    *,
    channel_id: str = "",
    dna: dict[str, Any] | None = None,
    spoken_text: str = "",
) -> list[str]:
    """Tropes forbidden in this frame unless the spoken window names them."""
    spoken = (spoken_text or "").lower()
    banned: list[str] = []
    spec = _DOMAIN_PROFILES.get(domain_id) or {}
    raw = list(spec.get("banned") or ())
    seen: set[str] = set()
    for token in raw:
        key = str(token).strip().lower()
        if not key or key in seen or key in spoken:
            continue
        seen.add(key)
        banned.append(key)
    return banned


def sanitize_channel_style(
    style: str,
    spoken_text: str,
    banned: Iterable[str],
) -> str:
    """Strip out-of-scope landmark names from inherited channel style text."""
    text = style or ""
    spoken = (spoken_text or "").lower()
    for token in banned:
        tok = str(token).strip()
        if not tok or tok in spoken:
            continue
        text = re.sub(rf"(?i)\b{re.escape(tok)}s?\b", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"(?:\s*[,;:]){2,}", ",", text)
    text = re.sub(r"\(\s*[,; ]+", "(", text)
    text = re.sub(r"[,; ]+\)", ")", text)
    text = re.sub(r"\(\s*\)", "", text)
    return text.strip(" ,.;")


def front_load_domain_anchors(prompt: str, anchors: str) -> str:
    prompt = (prompt or "").strip()
    anchors = (anchors or "").strip()
    if not anchors:
        return prompt
    if anchors.lower() in prompt.lower():
        return prompt
    return f"{anchors}. {prompt}".strip()


def _anti_slop_negative(chunk_text: str, tropes: Iterable[str]) -> str:
    spoken = (chunk_text or "").lower()
    banned = [t for t in tropes if t not in spoken]
    extra = ", ".join(banned[:16])
    return f"{_NEG_BASE}{', ' + extra if extra else ''}"


def apply_topic_visual_lock(
    prompt: str,
    *,
    topic: str = "",
    caption: str = "",
    style: str = "",
) -> dict[str, Any]:
    """
    Topic-specific anchors + negatives for stills (LONG_CAPTION, carousel).

    Same lock as ECONOMIC_REEL scene prompts: infer domain from the topic,
    strip inherited slop (e.g. Pyramid in AK style) unless the topic names it,
    front-load geographic anchors, and return a VLM banned-subject list.
    """
    blob = f"{topic or ''} {caption or ''} {prompt or ''}"
    domain_id = infer_visual_domain(blob, topic)
    anchors = domain_anchors(domain_id)
    allow = f"{topic or ''} {caption or ''}"
    banned = domain_banned_subjects(domain_id, spoken_text=allow)
    locked = sanitize_channel_style(prompt or "", allow, banned)
    if style:
        locked = sanitize_channel_style(
            f"{locked} {style}".strip(), allow, banned,
        )
    locked = front_load_domain_anchors(locked, anchors)
    return {
        "image_generation_prompt": locked,
        "negative_prompt": _anti_slop_negative(allow, banned),
        "banned_subjects": banned,
        "domain_id": domain_id,
        "domain_anchors": anchors,
    }


def _heuristic_scene(
    chunk: AudioChunk,
    *,
    script: str,
    channel_id: str,
    dna: dict[str, Any] | None,
    atmosphere: str,
    domain_id: str = "",
) -> dict[str, Any]:
    spoken = (chunk.text or "").strip()
    banned = domain_banned_subjects(
        domain_id, channel_id=channel_id, dna=dna, spoken_text=spoken,
    )
    anchors = domain_anchors(domain_id)
    lighting = str((dna or {}).get("lighting_style") or "").strip()
    style = sanitize_channel_style(
        atmosphere or lighting or "cinematic documentary photograph",
        spoken,
        banned,
    )
    subject = spoken[:120] or "the concrete object named in the spoken beat"
    prompt = front_load_domain_anchors(
        f"{style}. Visualise ONLY this spoken beat: {spoken}. "
        f"Primary subject: {subject}. "
        "Ultra-realistic photography, 35mm film grain. "
        "Invent no landmarks outside the spoken beat.",
        anchors,
    )
    return {
        "scene_index": int(chunk.index),
        "start_time": float(chunk.start_s),
        "end_time": float(chunk.end_s),
        "spoken_text": spoken,
        "visual_subject": subject,
        "image_generation_prompt": prompt,
        "negative_prompt": _anti_slop_negative(spoken, banned),
        "domain_id": domain_id,
        "domain_anchors": anchors,
        "banned_subjects": banned,
    }


def _rewrite_for_variety(scene: dict[str, Any], *, reason: str) -> dict[str, Any]:
    cameras = (
        "low-angle close-up on the named object",
        "overhead documentary insert of the artefact / action",
        "three-quarter side view with shallow depth of field",
        "handheld observer POV through a foreground element",
    )
    idx = int(scene.get("scene_index") or 0)
    cam = cameras[idx % len(cameras)]
    spoken = str(scene.get("spoken_text") or "")
    scene = dict(scene)
    scene["image_generation_prompt"] = (
        f"{scene.get('image_generation_prompt', '')} "
        f"CAMERA REWRITE ({reason}): {cam}. Change the featured entity focus "
        f"to a different noun from: \"{spoken}\"."
    )
    return scene


def _call_llm_scenes(
    script: str,
    chunks: list[AudioChunk],
    channel_dna: dict[str, Any] | None,
    channel_id: str,
) -> list[dict[str, Any]] | None:
    domain_id = infer_visual_domain(script, str((channel_dna or {}).get("topic") or ""))
    anchors = domain_anchors(domain_id)
    payload = {
        "full_script": script,
        "channel_id": channel_id,
        "visual_domain": domain_id,
        "required_anchors": anchors,
        "atmosphere": str((channel_dna or {}).get("lighting_style") or ""),
        "chunks": [
            {
                "scene_index": c.index,
                "start_time": c.start_s,
                "end_time": c.end_s,
                "current_chunk_text": c.text,
            }
            for c in chunks
        ],
    }
    prompt = (
        "You are a visual prompt director. Return STRICT JSON only — an array "
        "named \"scenes\" (or a bare array). Each item must match:\n"
        '{"scene_index":int,"start_time":float,"end_time":float,"spoken_text":string,'
        '"visual_subject":string,"image_generation_prompt":string,"negative_prompt":string}\n'
        "Rules:\n"
        "1. Analyse current_chunk_text inside full_script context.\n"
        "2. The image MUST depict the subject/object/action spoken in THIS 3–5s window.\n"
        "3. ANTI-SLOP: do NOT default to channel generic tropes (pyramids, desert, "
        "neon cyberpunk, etc.) unless those words appear in current_chunk_text.\n"
        "4. FIRST-PASS ACCURACY: start image_generation_prompt with required_anchors "
        "when visual_domain is set. Keep the setting historically specific.\n"
        "5. negative_prompt MUST list the topic-specific banned tropes for "
        "visual_domain (empty when the topic allows those landmarks).\n"
        "6. image_generation_prompt is a single cinematic photography prompt, no captions.\n"
        f"INPUT:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        from agents.mcp.text_model import complete_script

        raw = complete_script(prompt, provider="gemini").text
        data = _extract_json_payload(raw)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("generate_scene_prompts LLM failed (%s) — heuristic fallback", exc)
        return None

    rows: list[Any]
    if isinstance(data, dict):
        rows = data.get("scenes") or data.get("prompts") or []
        if not rows and "image_generation_prompt" in data:
            rows = [data]
    elif isinstance(data, list):
        rows = data
    else:
        return None
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict) and row.get("image_generation_prompt"):
            out.append(row)
    return out or None


def generate_scene_prompts(
    script: str,
    audio_chunks: list[AudioChunk] | list[dict[str, Any]],
    channel_dna: dict[str, Any] | None = None,
    *,
    channel_id: str = "",
    atmosphere: str = "",
    use_llm: bool = True,
) -> list[dict[str, Any]]:
    """
    Build one visual prompt per audio chunk.

    Dedupes consecutive near-identical prompts (cosine > 0.75) when the
    spoken text changed — forces a camera / entity rewrite.
    """
    chunks: list[AudioChunk] = []
    for i, raw in enumerate(audio_chunks or []):
        if isinstance(raw, AudioChunk):
            chunks.append(raw)
            continue
        chunks.append(
            AudioChunk(
                index=int(raw.get("scene_index", raw.get("index", i))),
                start_s=float(raw.get("start_time", raw.get("start_s", 0.0))),
                end_s=float(raw.get("end_time", raw.get("end_s", 0.0))),
                text=str(raw.get("spoken_text", raw.get("text", ""))),
            )
        )

    dna = dict(channel_dna or {})
    cid = (channel_id or str(dna.get("channel_id") or "")).strip().lower()
    domain_id = infer_visual_domain(script, str(dna.get("topic") or ""))
    anchors = domain_anchors(domain_id)
    llm_rows = (
        _call_llm_scenes(script, chunks, dna, cid) if use_llm and chunks else None
    )
    scenes: list[dict[str, Any]] = []
    by_idx = {
        int(r.get("scene_index", -1)): r
        for r in (llm_rows or [])
        if isinstance(r, dict)
    }

    for chunk in chunks:
        banned = domain_banned_subjects(
            domain_id, channel_id=cid, dna=dna, spoken_text=chunk.text,
        )
        row = by_idx.get(chunk.index)
        if row:
            scene = {
                "scene_index": chunk.index,
                "start_time": chunk.start_s,
                "end_time": chunk.end_s,
                "spoken_text": chunk.text,
                "visual_subject": str(row.get("visual_subject") or chunk.text)[:180],
                "image_generation_prompt": front_load_domain_anchors(
                    str(row.get("image_generation_prompt") or "").strip(),
                    anchors,
                ),
                "negative_prompt": str(row.get("negative_prompt") or "")
                or _anti_slop_negative(chunk.text, banned),
                "domain_id": domain_id,
                "domain_anchors": anchors,
                "banned_subjects": banned,
            }
            if not scene["image_generation_prompt"]:
                scene = _heuristic_scene(
                    chunk, script=script, channel_id=cid, dna=dna,
                    atmosphere=atmosphere, domain_id=domain_id,
                )
        else:
            scene = _heuristic_scene(
                chunk, script=script, channel_id=cid, dna=dna,
                atmosphere=atmosphere, domain_id=domain_id,
            )
        # Anti-slop: if prompt names a generic trope the chunk never said, rewrite.
        prompt_l = scene["image_generation_prompt"].lower()
        spoken_l = chunk.text.lower()
        leaked = [t for t in banned if t in prompt_l and t not in spoken_l]
        if leaked:
            scene = _heuristic_scene(
                chunk, script=script, channel_id=cid, dna=dna,
                atmosphere=atmosphere, domain_id=domain_id,
            )
        scene["image_generation_prompt"] = front_load_domain_anchors(
            scene["image_generation_prompt"], anchors,
        )
        scene["domain_id"] = domain_id
        scene["domain_anchors"] = anchors
        scene["banned_subjects"] = banned
        if banned:
            scene["negative_prompt"] = _anti_slop_negative(
                chunk.text, banned,
            )
        scenes.append(scene)

    # Dedup guardrail
    prev_prompt = ""
    prev_spoken = ""
    for i, scene in enumerate(scenes):
        prompt = str(scene.get("image_generation_prompt") or "")
        spoken = str(scene.get("spoken_text") or "")
        if (
            prev_prompt
            and spoken
            and spoken != prev_spoken
            and cosine_similarity(prompt, prev_prompt) > _DEDUPE_THRESHOLD
        ):
            scenes[i] = _rewrite_for_variety(scene, reason="cosine>0.75")
            prompt = str(scenes[i].get("image_generation_prompt") or "")
        prev_prompt = prompt
        prev_spoken = spoken

    return scenes
