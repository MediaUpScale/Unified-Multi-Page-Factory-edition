# -*- coding: utf-8 -*-
"""
reel_sequence_engine — 4-image 80-second sequence reel compiler.

Architecture
------------
A sequence reel differs from the single-image ECONOMIC_REEL in that the
80-second runtime is divided into N visual acts (default 4), each backed by
a *different* generated image.  The narration and word-level timestamps from
ElevenLabs are used to determine natural act boundaries, then one video clip
is rendered per act and the clips are concatenated into the final MP4.

Flow
----
1. Receive N pre-generated image paths (one per act).
2. Split ``word_timings`` into N equal-duration segments (or use natural
   sentence boundaries from ``act_boundaries`` when supplied).
3. For each act i:
     a. Extract the word timings that fall within [t_start_i, t_end_i].
     b. Offset those timings to be relative to t_start_i (t=0 for that clip).
     c. Build an act sub-clip using the frame pipeline described below.
4. Concatenate all act clips with HARD CUTS (instant scene transitions — no fades).
5. Attach the *full* audio (voice + ambient) to the concatenated clip.
6. Export as H.264 / AAC MP4.

Frame pipeline (per act, applied inside ``_make_frame``)
---------------------------------------------------------
1.  Continuous ease-motion zoom  (never zero velocity at clip end)
2.  Coupled diagonal pan         (3-D depth parallax, direction varies per act)
3.  Sinusoidal micro-drift       (handheld camera float)
4.  Dark overlay
5.  Dynamic vignette pulse       (0.20 – 0.40 opacity, ~6.7 s breath)
6.  Motion blur at scene entry   (box-blur ramp, first 0.10 s of non-first acts)
7.  Optional flicker exposure    (±5 % brightness at ~0.10 s random intervals)
8.  Optional volumetric light rays (animated Gaussian beam columns)
9.  Text / logo burn-in
10. Film grain (additive noise)
11. Hard cuts between acts       (no fade / cross-dissolve / dissolve-to-black)

LLM script generation
---------------------
``build_sequence_script_prompt(topic, niche, persona_voice, n_acts, duration_s)``
returns a prompt string that instructs the LLM to write an N-act script whose
total spoken length matches ``duration_s``.
"""
from __future__ import annotations

import gc
import logging
import math
import os
import re
import shutil
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)


def _ensure_parent_dir(filepath: str | Path) -> Path:
    """Create parent directories for *filepath* before any save/open side-effect."""
    path = Path(filepath)
    parent = path.parent
    if str(parent) not in ("", "."):
        os.makedirs(parent, exist_ok=True)
    return path


def _make_placeholder_act_image(dest: Path) -> Path:
    """Write a solid near-black 9:16 placeholder so MoviePy never crashes."""
    dest = Path(dest)
    _ensure_parent_dir(dest)
    Image.new("RGB", (1080, 1920), (10, 10, 14)).save(str(dest))
    logger.warning("Wrote blinded placeholder act image → %s", dest)
    return dest.resolve()


def _resolve_existing_image(
    image_path: str | Path,
    *,
    fallback: "Path | None" = None,
    copy_to: "Path | None" = None,
) -> Path:
    """
    Return a path that ``os.path.exists`` confirms is a readable image.

    If *image_path* is missing and *fallback* exists, copy the fallback into
    *copy_to* (or *image_path*) via ``shutil.copy2`` so MoviePy/PIL never see
    a missing file. Never raises ``FileNotFoundError`` — last resort is a
    solid-color placeholder so compilation always completes.
    """
    path = Path(image_path)
    if path.is_file() and os.path.exists(path):
        return path.resolve()

    src = Path(fallback) if fallback is not None else None
    if src is not None and src.is_file() and os.path.exists(src):
        dest = Path(copy_to) if copy_to is not None else path
        _ensure_parent_dir(dest)
        try:
            shutil.copy2(src, dest)
            logger.warning(
                "Missing act image %s — copied fallback %s → %s",
                path.name, src.name, dest,
            )
        except OSError as exc:
            logger.warning(
                "Could not copy fallback image (%s) — using source path %s",
                exc, src,
            )
            return src.resolve()

        if not (dest.is_file() and os.path.exists(dest)):
            return src.resolve()
        return dest.resolve()

    # Blinded last resort — never crash MoviePy on API depletion
    dest = Path(copy_to) if copy_to is not None else path
    if str(dest.parent) in ("", ".") or dest.suffix.lower() not in (
        ".png", ".jpg", ".jpeg", ".webp",
    ):
        dest = Path(tempfile.gettempdir()) / "seq_act_placeholder.png"
    return _make_placeholder_act_image(dest)


def sanitize_sequence_image_paths(
    image_paths: list,
    *,
    base_fallback: "str | Path | None" = None,
) -> list[Path]:
    """
    Validate every act image exists before MoviePy/PIL compilation.

    Missing files are backfilled by copying Act 1 / *base_fallback* / the
    previous valid frame. Never raises ``FileNotFoundError``.
    """
    if not image_paths:
        raise ValueError("sanitize_sequence_image_paths: empty image_paths.")

    resolved: list[Path] = []
    last_good: Path | None = None
    act1: Path | None = None
    base = Path(base_fallback) if base_fallback is not None else None
    if base is not None and not (base.is_file() and os.path.exists(base)):
        base = None

    for i, raw in enumerate(image_paths):
        candidate = Path(raw)
        preferred = act1 or last_good or base
        ok = _resolve_existing_image(
            candidate,
            fallback=preferred,
            copy_to=candidate if preferred is not None else None,
        )
        resolved.append(ok)
        last_good = ok
        if act1 is None:
            act1 = ok
        logger.debug("Sequence image act %d OK → %s", i + 1, ok.name)
    return resolved


# ---------------------------------------------------------------------------
# Dense scene-to-audio sync (~1 image / 4.0–5.0 s of speech)
# ---------------------------------------------------------------------------
_DENSE_SECONDS_PER_ACT: float = 4.5
_DENSE_MIN_ACTS: int = 12
_DENSE_MAX_ACTS: int = 18


def compute_dense_act_count(
    duration_s: float,
    *,
    seconds_per_act: float = _DENSE_SECONDS_PER_ACT,
    min_acts: int = _DENSE_MIN_ACTS,
    max_acts: int = _DENSE_MAX_ACTS,
) -> int:
    """
    Hardcoded math enforcement: ``Total Images = round(audio_seconds / seconds_per_act)``.

    ``seconds_per_act`` is honoured as configured (clamped only to a sane
    3.0–5.0 s band — NEVER silently forced to a fixed 4.0). The result is
    then clamped to ``[min_acts, max_acts]`` so a single act's screen time
    (``total_duration / n_acts`` at compile time) always lands inside the
    per-page pacing window (e.g. master_mei's strict 4–5 s/shot rule).

    Example: 90 s ÷ 4.5 = 20 images (master_mei default cadence).
    """
    dur = max(1.0, float(duration_s or _DEFAULT_DURATION))
    spa = float(seconds_per_act or _DENSE_SECONDS_PER_ACT)
    spa = max(3.0, min(5.0, spa))
    lo = max(2, int(min_acts))
    hi = max(lo, int(max_acts))
    n = int(round(dur / spa))
    return max(lo, min(hi, n))


def segment_script_into_act_snippets(script: str, n_acts: int) -> list[str]:
    """
    Split narration into *n_acts* sequential text snippets (~4 s of speech each).

    Prefer sentence boundaries while preserving chronological order so each
    Gemini image prompt visualises the exact spoken beat for that act.
    """
    import re as _re

    n = max(1, int(n_acts))
    clean = _re.sub(r"\[ACT\s*\d+\]", " ", script or "", flags=_re.IGNORECASE)
    clean = _re.sub(
        r"\[(?:cackles?|chuckles?|dry\s*laugh|laughs?|sighs?|whispers?)\]",
        " ",
        clean,
        flags=_re.IGNORECASE,
    )
    clean = _re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return [""] * n

    sentences = [s.strip() for s in _re.split(r"(?<=[.!?])\s+", clean) if s.strip()]
    if len(sentences) >= n:
        # Pack sentences sequentially into n roughly equal word buckets
        total_words = sum(len(s.split()) for s in sentences) or 1
        target = max(1, total_words // n)
        chunks: list[str] = []
        buf: list[str] = []
        buf_w = 0
        for sent in sentences:
            sw = max(1, len(sent.split()))
            # Flush when adding this sentence would overshoot the per-act target
            if buf and len(chunks) < n - 1 and (buf_w + sw) > target:
                chunks.append(" ".join(buf))
                buf, buf_w = [sent], sw
            else:
                buf.append(sent)
                buf_w += sw
        if buf:
            chunks.append(" ".join(buf))
        while len(chunks) < n:
            chunks.append(chunks[-1] if chunks else clean)
        if len(chunks) > n:
            head, tail = chunks[: n - 1], chunks[n - 1 :]
            chunks = head + [" ".join(tail)]
        return chunks

    words = clean.split()
    if len(words) <= n:
        out = list(words) + [words[-1] if words else ""] * (n - len(words))
        return out[:n]

    size = max(1, len(words) // n)
    snippets: list[str] = []
    for i in range(n):
        start = i * size
        end = (i + 1) * size if i < n - 1 else len(words)
        snippets.append(" ".join(words[start:end]))
    return snippets


def _page_clips_dir(page_id: str | None = None) -> Path:
    """Return ``outputs/{page}/clips`` and ensure it exists."""
    try:
        import config as app_config

        page = (page_id or getattr(app_config, "ACTIVE_PAGE", "") or "default").strip()
        page = page or "default"
        clips = Path(app_config.OUTPUTS_DIR) / page / "clips"
    except Exception:
        page = (page_id or "default").strip() or "default"
        clips = Path("outputs") / page / "clips"
    os.makedirs(clips, exist_ok=True)
    return clips


def _resolve_sequence_mp4_path(
    output_path: "Path | None",
    *,
    page_id: str | None = None,
) -> Path:
    """
    Force final MP4s into ``outputs/{page}/clips/{sanitized_filename}.mp4``.
    Never write sequence reels to the engine root or a random temp folder.
    """
    clips_dir = _page_clips_dir(page_id)
    if output_path is None:
        slug = "".join(
            ch if ch.isalnum() else "_" for ch in (page_id or "sequence")
        ).strip("_") or "sequence"
        try:
            from avatar_engine.text_utils import safe_output_stem

            name = f"{safe_output_stem(slug)}_sequence_reel.mp4"
        except Exception:
            name = f"{slug[:35]}_sequence_reel.mp4"
        dest = clips_dir / name
    else:
        dest = Path(output_path)
        if dest.suffix.lower() != ".mp4":
            dest = dest.with_suffix(".mp4")
        # Relocate into page clips/ if caller pointed elsewhere
        if dest.parent.resolve() != clips_dir.resolve():
            dest = clips_dir / dest.name
    os.makedirs(os.path.dirname(str(dest)) or str(clips_dir), exist_ok=True)
    return dest


# ---------------------------------------------------------------------------
# Canvas constants
# ---------------------------------------------------------------------------
_REEL_WIDTH: int  = 1080
_REEL_HEIGHT: int = 1920
_DEFAULT_FPS: int = 30
_AMBIENT_VOLUME: float = 0.32   # music bed ~0.32; clear VO separation
_VOICE_VOLUME_GAIN: float = 1.15  # +15% VO mix gain (Master Mei default)
_IMPACT_SFX_VOLUME_DEFAULT: float = 0.50  # cinematic braam at t=0
_AMBIENT_GAIN_MUL_DEFAULT: float = 1.0  # no compounding SFX boost (distortion fix)
_AMBIENT_DUCK_RATIO_DEFAULT: float = 0.70  # × bed while voice plays (still audible)
_MASTER_AUDIO_GAIN_DEFAULT: float = 1.15   # +15% overall master after mix
_AUDIO_SAMPLE_RATE: int = 48000   # force all mix tracks / AAC encode to 48 kHz

_DEFAULT_N_ACTS: int   = 4
_DEFAULT_DURATION: float = 80.0

# ── Video duration safety guardrails ─────────────────────────────────────────
# Hard floor prevents <60 s clips even if TTS narration runs short.
# Hard cap enforces the 80 s platform limit unless the caller passes an
# explicit duration_override > 80 to compile_sequence_reel().
_DURATION_FLOOR_S: float = 60.0   # minimum reel length
_DURATION_CAP_S:   float = 80.0   # maximum reel length (hard cap)


# ---------------------------------------------------------------------------
# Procedural ambient drone — guaranteed fallback (ancient_knowledge mute fix)
# ---------------------------------------------------------------------------

def _synthesize_ambient_drone(
    total_duration: float,
    sample_rate: int = _AUDIO_SAMPLE_RATE,
    volume: float = 0.08,
    profile: str = "mystery",
) -> "object | None":
    """
    Build a dark cinematic pad of ``total_duration`` seconds using numpy.

    NO white-noise / rain hiss — pure sub-bass + atmospheric sine pads only.

    Profiles
    --------
    mystery  — dark A1/E2/A2 drone (ancient_knowledge)
    warrior  — deep sub-bass pad + subtle inspirational low synth (master_mei)
    """
    try:
        from moviepy.audio.AudioClip import AudioArrayClip  # type: ignore[import]
        sr = int(sample_rate) if sample_rate and int(sample_rate) > 0 else _AUDIO_SAMPLE_RATE
        n = int(max(0.5, total_duration) * sr)
        t = np.linspace(0.0, total_duration, n, dtype=np.float32)
        # Slow amplitude envelope — soft swell, never harsh
        swell = 0.85 + 0.15 * np.sin(2 * np.pi * 0.05 * t)
        if (profile or "").lower() == "warrior":
            # Dark & inspiring cinematic: sub-bass + low synth, NO noise/hiss
            drone = (
                0.42 * np.sin(2 * np.pi * 40.0 * t)          # deep sub-bass
                + 0.28 * np.sin(2 * np.pi * 55.0 * t)         # dark pad
                + 0.16 * np.sin(2 * np.pi * 82.5 * t)         # atmospheric drone
                + 0.08 * np.sin(2 * np.pi * 110.0 * t) * swell  # subtle inspirational synth
            )
        else:
            drone = (
                0.40 * np.sin(2 * np.pi * 55.0 * t)
                + 0.22 * np.sin(2 * np.pi * 82.5 * t)
                + 0.14 * np.sin(2 * np.pi * 110.0 * t)
            )
        combined = drone * swell
        peak = float(np.max(np.abs(combined)))
        vol = (
            max(0.28, min(0.38, float(volume)))
            if (profile or "").lower() == "warrior"
            else max(0.08, float(volume))
        )
        if peak > 0:
            combined = combined / peak * vol
        stereo = np.column_stack([combined, combined])
        clip = AudioArrayClip(stereo, fps=sr)
        logger.info(
            "Procedural cinematic pad synthesized | profile=%s dur=%.1fs vol=%.2f sr=%d (no noise)",
            profile, total_duration, vol, sr,
        )
        return clip
    except Exception as _exc:  # noqa: BLE001
        logger.warning("Procedural ambient synthesis failed: %s", _exc)
        return None


# ── Continuous-motion zoom ────────────────────────────────────────────────────
# Range increased to 1.0 → 1.30 for maximum immersive depth sensation.
# The easing function (_ease_motion_continuous) blends 70 % cubic ease-out with
# 30 % linear so the camera velocity floor at t=1 is 30 % of peak — never freezes.
_ZOOM_PER_ACT_START: float = 1.0
_ZOOM_PER_ACT_END: float   = 1.30

# ── Per-act motion profiles ───────────────────────────────────────────────────
# Each act cycles through 4 distinct camera-movement styles so successive
# scenes feel spatially independent (push-in → reveal → sweep → overhead).
#   zoom_start / zoom_end : scale range for this act
#   pan_mul               : multiplier on _PAN_AMP_X/Y for this profile
#   reverse_dir           : flip pan direction for pull-out feels
_MOTION_PROFILES: "list[dict]" = [
    # 0 — RAPID PUSH-IN  (+65 % zoom range, strong forward momentum)
    {"zoom_start": 1.00, "zoom_end": 1.42, "pan_mul": 1.65, "reverse_dir": False},
    # 1 — WIDE PULL-OUT REVEAL  (start close, dynamically reveal full subject)
    {"zoom_start": 1.48, "zoom_end": 1.02, "pan_mul": 1.00, "reverse_dir": True},
    # 2 — SWEEPING LATERAL PARALLAX  (aggressive horizontal sweep, moderate zoom)
    {"zoom_start": 1.10, "zoom_end": 1.32, "pan_mul": 4.60, "reverse_dir": False},
    # 3 — OVERHEAD CRANE SWEEP  (Z-push + strong vertical travel)
    {"zoom_start": 1.18, "zoom_end": 1.40, "pan_mul": 2.30, "reverse_dir": True},
]

# ── Coupled diagonal pan (3-D depth parallax) ────────────────────────────────
# Each act pans toward a different corner so successive scenes feel spatially
# independent.  Amplitude is in pixels (well within the 1.25× zoom headroom).
# The pan uses the SAME motion curve as the zoom so scale and translation are
# physically coupled, simulating a real camera tracking a point of interest.
_PAN_DIRS: "list[tuple[int, int]]" = [
    ( 1,  1), (-1,  1), ( 1, -1), (-1, -1),   # diagonal corners
    ( 1,  0), (-1,  0), ( 0,  1), ( 0, -1),   # axis-aligned fallback
]
_PAN_AMP_X: float = 60.0   # pixels of total horizontal travel per act (+50 % vs subtle preset)
_PAN_AMP_Y: float = 42.0   # pixels of total vertical travel per act   (+50 % vs subtle preset)

# ── Sinusoidal micro-drift ────────────────────────────────────────────────────
_DRIFT_AMP_X: float  = 8.0
_DRIFT_AMP_Y: float  = 5.0
_DRIFT_FREQ_X: float = 0.11   # Hz
_DRIFT_FREQ_Y: float = 0.07   # Hz

# ── Dynamic vignette pulse ────────────────────────────────────────────────────
_VIGNETTE_PULSE_MIN: float  = 0.20
_VIGNETTE_PULSE_MAX: float  = 0.40
_VIGNETTE_PULSE_FREQ: float = 0.15   # Hz (~6.7 s per breath)

# ── Motion blur at scene entry ────────────────────────────────────────────────
_MB_DUR: float      = 0.10   # s of ramp-out after each cut
_MB_MAX_RADIUS: int = 6      # max box-blur radius at t=0

# ── Flicker exposure ──────────────────────────────────────────────────────────
# ±FLICKER_AMP brightness modulation, changing state every FLICKER_INTERVAL s.
# Interpolated (not stepped) so transitions are smooth, not jarring.
_FLICKER_INTERVAL: float = 0.10   # seconds between random keyframes
_FLICKER_AMP: float      = 0.05   # ±5 % brightness oscillation

# ── Volumetric light rays ─────────────────────────────────────────────────────
# A warm-gold Gaussian beam column sweeps slowly across the canvas.  All heavy
# arrays (X-profile, Y-fade) are precomputed in the closure; per-frame work is
# a single outer-product + additive blend.
_RAY_SIGMA: float   = 0.09    # beam half-width as fraction of canvas width
_RAY_OPACITY: float = 0.07    # additive blend intensity (0 = off, 0.1 = strong)
_RAY_SWEEP: float   = 0.45    # beam sweeps this fraction of canvas width per act
_RAY_COLOR: "np.ndarray" = np.array([235, 195, 110], dtype=np.float32)  # warm gold

# ── Scene transitions (Shorts / Reels) ────────────────────────────────────────
# HARD CUTS only — no fadein / fadeout / crossfade / dissolve between acts.
# Optional global open/close edge is disabled (0.0); keep ≤0.1 s if re-enabled.
_DISSOLVE_DUR: float = 0.0    # seconds — 0 = instant hard cut between scenes
_EDGE_FADE_DUR: float = 0.0   # opening/closing visual fade (disabled for Reels)
_AUDIO_EDGE_FADE_S: float = 0.1  # tiny mix out at absolute end only (not scene cuts)


# ---------------------------------------------------------------------------
# Motion curve helpers
# ---------------------------------------------------------------------------

def _ease_motion_continuous(t_norm: float) -> float:
    """
    70 % cubic ease-out  +  30 % linear blend.

    Unlike pure cubic ease-out (``1-(1-t)³``) whose derivative reaches zero
    at ``t=1``, this blend guarantees a velocity floor of 0.30 at clip end —
    the camera NEVER freezes before the hard cut.

    ``t_norm`` must be clamped to [0, 1] by the caller.
    """
    cubic = 1.0 - (1.0 - t_norm) ** 3
    return 0.70 * cubic + 0.30 * t_norm


# ---------------------------------------------------------------------------
# Visual identity helpers
# ---------------------------------------------------------------------------

def _make_vignette(
    width: int,
    height: int,
    strength: float = 1.0,
) -> "np.ndarray":
    """
    Pre-compute a float32 vignette mask shaped (height, width) in [0, strength].

    Pass ``strength=1.0`` for a normalised mask; the caller scales it per-frame.
    """
    y = np.linspace(-1.0, 1.0, height, dtype=np.float32)
    x = np.linspace(-1.0, 1.0, width, dtype=np.float32)
    xx, yy = np.meshgrid(x, y)
    r = np.sqrt(xx ** 2 + yy ** 2)
    raw = np.clip((r - 0.4) / 0.9, 0.0, 1.0) ** 2
    return (raw * strength).astype(np.float32)


# ---------------------------------------------------------------------------
# LLM script prompt builder
# ---------------------------------------------------------------------------

def build_sequence_script_prompt(
    topic: str,
    niche: str,
    persona_voice: str = "investigative, neutral, immersive",
    n_acts: int = _DEFAULT_N_ACTS,
    duration_s: float = _DEFAULT_DURATION,
    *,
    total_words_target: "int | None" = None,
    previously_generated_hooks: "list[str] | None" = None,
    narrative_mode: str = "",
    niche_disclaimer: str = "",
    batch_angle_block: str = "",
    uniqueness_rejection: str = "",
) -> str:
    """
    Build an LLM prompt that produces an N-act spoken script.

    Parameters
    ----------
    total_words_target:
        Override the computed word count.  When provided the prompt instructs
        the LLM to hit exactly this many words total (spread evenly across acts).
        Use 130-140 for slow 80-second documentary narration (~100 WPM pace).
        Defaults to computing from ``duration_s`` at 130 WPM.
    narrative_mode:
        ``investigative`` (default for mystery channels) or ``warrior_discipline``
        (Master Mei SUPER channel).
    """
    _mode = (narrative_mode or "").strip().lower()
    if not _mode:
        _mode = "investigative"
    _word_cap = 180 if _mode == "warrior_discipline" else 240
    _word_cap_pad = 10 if _mode == "warrior_discipline" else 20

    if total_words_target is not None:
        words_per_act = total_words_target // n_acts
        total_words   = words_per_act * n_acts
    else:
        words_per_act = int((duration_s / n_acts) * (130 / 60))
        total_words   = words_per_act * n_acts

    anti_repeat_block = ""
    if previously_generated_hooks:
        recent = previously_generated_hooks[-10:]
        lines  = "\n".join(f"  - {h}" for h in recent)
        anti_repeat_block = (
            f"\n\nPREVIOUSLY USED OPENING LINES (DO NOT REPEAT OR PARAPHRASE):\n{lines}\n"
        )

    _max_seconds_target = 90 if _mode == "warrior_discipline" else 100
    pacing_note = (
        f"Write for a SLOW, deliberate, solemn delivery (~100–110 spoken WPM before 0.80× TTS). "
        f"Each act must be approximately {words_per_act} words "
        f"(total EXACTLY {total_words}–{min(_word_cap, total_words + _word_cap_pad)} words across all {n_acts} acts; "
        f"HARD CAP {_word_cap}). "
        f"The spoken narration MUST naturally fill "
        f"~{max(80, int(duration_s) - 10)}–{min(_max_seconds_target, int(duration_s))} "
        f"seconds of airtime. Do NOT write a short viral caption — write a FULL 4-Act script."
        if total_words_target is not None else
        f"Each act must be approximately {words_per_act} words "
        f"(total ~{total_words} words across all acts; Master Mei target "
        f"{150 if _mode == 'warrior_discipline' else 200}–{_word_cap})."
    )

    _directive_block = f"\nCHANNEL DIRECTIVE:\n{niche_disclaimer}\n" if niche_disclaimer else ""
    _batch_block = f"\n{batch_angle_block}\n" if batch_angle_block else ""
    _reject_block = (
        f"\nUNIQUENESS REJECTION (REGENERATE):\n{uniqueness_rejection}\n"
        if uniqueness_rejection else ""
    )

    if _mode == "warrior_discipline":
        from avatar_engine.mei_narrative import (
            build_four_act_script_instructions,
            episode_theme_meta,
        )

        _ep = episode_theme_meta(topic)
        _four_act = build_four_act_script_instructions(n_acts, _ep)
        return f"""You are writing a compelling {duration_s:.0f}-second 4-Act philosophical voiceover for Master Mei.

TOPIC: {topic}
CHANNEL NICHE: {niche}
NARRATOR VOICE: {persona_voice}
EPISODE THEME (LOCKED — SINGLE TOPIC): {_ep['label']}
TONE: Deeply authoritative, solemn, uncompromising — ancient wisdom meeting brutal dystopian reality.
AUDIENCE: Western / US men focused on self-mastery, discipline, tech-critique, and financial sovereignty.
{_directive_block}
{_batch_block}
{_reject_block}
STRICT RULES:
1. Divide the script into exactly {n_acts} acts using markers: [ACT 1], [ACT 2], ... [ACT {n_acts}].
2. {pacing_note}
3. Tone: deeply authoritative, solemn, uncompromising. Implacable. Brutally direct. DO NOT rush; heavy pauses between hard truths.
4. VOICE PACING: Pure spoken prose only. Insert deliberate ellipses (`...`) between heavy statements. Prefer short sentences and strategic commas. Example: "Look around you... What do you truly see?"
5. FORBIDDEN: ALL emotion/expression tags and SSML — never write `[cold chuckle]`, `[arrogant scoff]`, `[deep subtle laugh]`, `[cackles]`, or `<break …/>`.
6. {_four_act}
7. Core purpose (immutable): teach resilience of body, mind, spirit, and financial sovereignty — escape digital slavery, cheap dopamine, and trap beauty through uncompromising self-mastery.
8. NEVER use hustle-bro clichés, therapy-speak, wellness fluff, or ancient-mystery conspiracy content. Never dump disconnected quotes.
9. Do NOT write "Follow Master Mei", "Subscribe", or any channel follow CTA in the script — that CTA is stitched separately AFTER narration.
10. NO headers, NO bullet points, NO markdown — pure spoken prose only (ellipsis pauses ARE required; bracketed sound tags are FORBIDDEN).
11. Target 150–180 words total (≈80–90 s at 0.80× deliberate delivery). HARD CAP 180.
12. Output ONLY the script with [ACT N] markers. No preamble, no labels, no meta commentary.
{anti_repeat_block}
Write the complete {n_acts}-act script now:"""

    return f"""You are writing a compelling {duration_s:.0f}-second documentary-style voiceover script.

TOPIC: {topic}
CHANNEL NICHE: {niche}
NARRATOR VOICE: {persona_voice}
{_directive_block}
{_batch_block}
{_reject_block}
STRICT RULES:
1. Divide the script into exactly {n_acts} acts using markers: [ACT 1], [ACT 2], ... [ACT {n_acts}].
2. {pacing_note}
3. NEVER claim any conspiracy or theory is factual. Use language like: "some researchers believe", "ancient records suggest", "according to legend", "one theory proposes".
4. Each act must feel visually distinct — the narrator should describe a different aspect, location, or era.
5. Begin ACT 1 with a REAL, RECOGNISABLE WORLD ANCHOR (e.g., "The Great Pyramid of Giza", "The stone blocks of Baalbek", "Easter Island's moai") and then immediately introduce the HIGH-CONCEPT IMPOSSIBLE element that cannot be explained by mainstream history. This anchor → impossibility structure drives maximum curiosity and engagement.
6. Maintain an investigative, immersive, documentary tone throughout.
7. NO headers, NO bullet points, NO markdown — pure spoken prose only.
8. Output ONLY the script with [ACT N] markers. No preamble, no labels, no meta commentary.
{anti_repeat_block}
Write the complete {n_acts}-act script now:"""


# ---------------------------------------------------------------------------
# Act boundary splitter
# ---------------------------------------------------------------------------

def _split_word_timings_into_acts(
    word_timings: "list[tuple[str, float, float]]",
    n_acts: int,
    total_duration: float,
) -> "list[tuple[float, float, list[tuple[str, float, float]]]]":
    """Divide word_timings into n_acts equal-duration segments.

    Words that overlap an act window are included, then clamped to the local
    act timeline so no phrase can bleed past the act end (prevents final-frame
    script stacking).
    """
    act_dur  = total_duration / max(1, n_acts)
    segments: list[tuple[float, float, list]] = []

    for i in range(n_acts):
        t_start = i * act_dur
        t_end   = (i + 1) * act_dur if i < n_acts - 1 else total_duration
        act_len = max(0.05, t_end - t_start)
        act_words: list[tuple[str, float, float]] = []
        for w, ws, we in word_timings:
            # Overlap test (inclusive) — capture words straddling boundaries
            if we <= t_start or ws >= t_end:
                continue
            local_s = max(0.0, ws - t_start)
            local_e = min(act_len - 0.02, max(0.0, we - t_start))
            if local_s < local_e:
                act_words.append((w, local_s, local_e))
        act_words = _sanitize_subtitle_timings(act_words)
        segments.append((t_start, t_end, act_words))

    return segments


# ---------------------------------------------------------------------------
# Phrase-chunking helper
# ---------------------------------------------------------------------------

def _sanitize_subtitle_token(raw: str, max_chars: int = 28) -> str:
    """Collapse a timing entry to a single short token (anti script-dump)."""
    parts = (raw or "").strip().split()
    if not parts:
        return ""
    # Corrupt alignments sometimes stuff the whole script into one "word"
    tok = parts[0]
    if len(tok) > max_chars:
        tok = tok[:max_chars]
    return tok


def _chunk_words_into_phrases(
    word_timings: "list[tuple[str, float, float]]",
    words_per_phrase: int = 4,
) -> "list[tuple[str, float, float]]":
    """
    Collapse word_timings into short natural phrases (3-5 words per block).

    Hard-caps each phrase at ``words_per_phrase`` tokens so a corrupt timing
    blob can never dump the entire script onto one frame.
    """
    if not word_timings:
        return []
    wpp = max(1, min(6, int(words_per_phrase or 4)))
    # Flatten / sanitize: one timing entry may illegally contain many words
    flat: list[tuple[str, float, float]] = []
    for raw, ws, we in word_timings:
        parts = (raw or "").strip().split()
        if not parts:
            continue
        if len(parts) == 1:
            tok = _sanitize_subtitle_token(parts[0])
            if tok:
                flat.append((tok, float(ws), float(we)))
            continue
        # Split multi-word dump across the timing window evenly
        span = max(0.05, float(we) - float(ws))
        step = span / len(parts)
        for j, p in enumerate(parts):
            tok = _sanitize_subtitle_token(p)
            if not tok:
                continue
            flat.append((tok, float(ws) + j * step, float(ws) + (j + 1) * step))
    if not flat:
        return []
    if wpp <= 1:
        return _sanitize_subtitle_timings(flat)
    phrases: list[tuple[str, float, float]] = []
    for i in range(0, len(flat), wpp):
        chunk = flat[i : i + wpp]
        tokens = [w for w, _, _ in chunk][:wpp]
        phrase_start = float(chunk[0][1])
        phrase_end = float(chunk[-1][2])
        if phrase_end <= phrase_start:
            phrase_end = phrase_start + 0.12
        phrases.append((" ".join(tokens), phrase_start, phrase_end))
    return _sanitize_subtitle_timings(phrases)


# ---------------------------------------------------------------------------
# Subtitle timestamp sanitizer
# ---------------------------------------------------------------------------

def _sanitize_subtitle_timings(
    timings: "list[tuple[str, float, float]]",
    min_gap: float = 0.05,
    min_duration: float = 0.10,
) -> "list[tuple[str, float, float]]":
    """Build a strictly exclusive, non-overlapping subtitle timeline.

    * Sort by start time.
    * Clamp each end before the next start (``min_gap``).
    * Merge identical consecutive phrases (prevents shadowed double lines).
    * Drop degenerate intervals.
    """
    if not timings:
        return timings
    result = sorted(
        ((str(w).strip(), float(ws), float(we)) for w, ws, we in timings if str(w).strip()),
        key=lambda x: (x[1], x[2]),
    )
    if not result:
        return []
    # Merge identical adjacent text into one interval
    merged: list[tuple[str, float, float]] = [result[0]]
    for word, ws, we in result[1:]:
        prev_w, prev_s, prev_e = merged[-1]
        if word == prev_w and ws <= prev_e + min_gap:
            merged[-1] = (prev_w, prev_s, max(prev_e, we))
        else:
            merged.append((word, ws, we))
    # Exclusive non-overlap pass
    exclusive: list[tuple[str, float, float]] = []
    for i, (word, ws, we) in enumerate(merged):
        end = we
        if i + 1 < len(merged):
            next_s = merged[i + 1][1]
            if end >= next_s:
                end = max(ws + min_duration, next_s - min_gap)
        if ws < end:
            exclusive.append((word, ws, end))
    return exclusive


# ---------------------------------------------------------------------------
# Per-act clip builder
# ---------------------------------------------------------------------------

def _build_act_clip(
    image_path: Path,
    act_duration: float,
    word_timings: "list[tuple[str, float, float]]",
    *,
    hook_text: str = "",
    enable_hook_text: bool = True,
    overlay_opacity: float = 0.35,
    font_path: str | None = None,
    subtitle_fontsize: int = 46,
    subtitle_y_position: "int | None" = None,
    hook_y_frac: float = 0.55,
    logo_static_array: "np.ndarray | None" = None,
    # Normalised (0..1) vignette mask; per-frame strength is applied via pulse.
    vignette_mask: "np.ndarray | None" = None,
    grain_intensity: float = 18.0,
    fps: int = _DEFAULT_FPS,
    zoom_start: float = _ZOOM_PER_ACT_START,
    zoom_end: float   = _ZOOM_PER_ACT_END,
    act_index: int = 0,
    words_per_phrase: int = 4,
    subtitle_fill: tuple = (255, 230, 0),
    subtitle_stroke_width: int = 0,
    subtitle_stroke_fill: "tuple | None" = None,
    # Scene transitions: keep at 0.0 for hard cuts (no fade/dissolve).
    dissolve_in_dur: float  = 0.0,
    dissolve_out_dur: float = 0.0,
    # Flicker: ±5 % brightness oscillation at ~0.10 s intervals.
    enable_flicker: bool = False,
    # Volumetric light rays: slow-moving Gaussian beam column.
    enable_light_rays: bool = False,
    # Floating dust particles drifting upward (opt-in for ruin/cave scenes).
    enable_dust_particles: bool = False,
    # Subtle prismatic light refraction (opt-in for glass/crystal subjects).
    enable_light_refraction: bool = False,
):
    """
    Build one MoviePy VideoClip for a single act.

    All cinematic motion features are computed per-frame inside ``_make_frame``
    with no external state — the function is pure (given ``t``).
    """
    from moviepy import VideoClip  # type: ignore[import]

    # ── Pre-load and fit image to canvas ─────────────────────────────────────
    image_path = _resolve_existing_image(image_path)
    img = Image.open(image_path).convert("RGBA")
    canvas_ratio = _REEL_WIDTH / _REEL_HEIGHT
    img_ratio    = img.width / img.height
    if img_ratio > canvas_ratio:
        new_h = _REEL_HEIGHT
        new_w = int(new_h * img_ratio)
    else:
        new_w = _REEL_WIDTH
        new_h = int(new_w / img_ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)

    # ── Grain noise (seeded per act for determinism) ──────────────────────────
    _grain: "np.ndarray | None" = None
    if grain_intensity > 0:
        rng_grain = np.random.default_rng(seed=act_index + 42)
        _grain = (
            rng_grain.random((_REEL_HEIGHT, _REEL_WIDTH)) * (grain_intensity * 2) - grain_intensity
        ).astype(np.float32)

    # ── Flicker schedule (precomputed random keyframes) ───────────────────────
    # State changes every _FLICKER_INTERVAL seconds; linearly interpolated so
    # transitions are smooth rather than hard-stepped.
    _flicker_targets: "np.ndarray | None" = None
    _n_flicker_states: int = 0
    if enable_flicker:
        _n_flicker_states = max(2, int(act_duration / _FLICKER_INTERVAL) + 2)
        rng_flicker = np.random.default_rng(seed=act_index * 7 + 123)
        _flicker_targets = rng_flicker.uniform(
            -_FLICKER_AMP, _FLICKER_AMP, _n_flicker_states
        ).astype(np.float32)

    # ── Volumetric light-ray precomputation ───────────────────────────────────
    # X-profile and Y-fade are computed once; only the beam center shifts per frame.
    _ray_x_coords: "np.ndarray | None" = None
    _ray_y_fade:   "np.ndarray | None" = None
    _ray_beam_start_x: float = 0.0
    if enable_light_rays:
        _ray_x_coords = np.linspace(0.0, 1.0, _REEL_WIDTH, dtype=np.float32)
        # Beam fades toward bottom (light absorbed by atmosphere)
        _ray_y_fade = (np.linspace(1.0, 0.0, _REEL_HEIGHT, dtype=np.float32) ** 0.6).astype(
            np.float32
        )
        # Start position cycles across acts to avoid repetition
        _ray_beam_start_x = 0.10 + (act_index * 0.22) % 0.65

    # ── Dust particles precompute ─────────────────────────────────────────────
    # A 50-particle tile is rendered once (blurred for soft appearance) then
    # shifted upward per-frame with np.roll — near-zero per-frame cost.
    _dust_tile: "np.ndarray | None" = None
    if enable_dust_particles:
        _rng_dust = np.random.default_rng(seed=act_index * 97 + 11)
        _dt = np.zeros((_REEL_HEIGHT, _REEL_WIDTH), dtype=np.float32)
        for _ in range(55):
            _px = int(_rng_dust.integers(10, _REEL_WIDTH - 10))
            _py = int(_rng_dust.integers(10, _REEL_HEIGHT - 10))
            _pr = int(_rng_dust.integers(1, 4))
            _pa = float(_rng_dust.uniform(0.05, 0.14))
            _y0, _y1 = max(0, _py - _pr), min(_REEL_HEIGHT, _py + _pr + 1)
            _x0, _x1 = max(0, _px - _pr), min(_REEL_WIDTH,  _px + _pr + 1)
            _dt[_y0:_y1, _x0:_x1] = np.clip(_dt[_y0:_y1, _x0:_x1] + _pa, 0.0, 1.0)
        # Soft Gaussian blur for natural appearance
        _dt_pil = Image.fromarray((_dt * 255).astype(np.uint8), mode="L")
        _dt_pil = _dt_pil.filter(ImageFilter.GaussianBlur(radius=2))
        _dust_tile = np.array(_dt_pil, dtype=np.float32) / 255.0

    # ── Light refraction precompute ───────────────────────────────────────────
    # Very soft prismatic diagonal streaks at ~4 % opacity — simulates light
    # passing through quartz/glass.  Precomputed once; blended per frame with
    # a slow sinusoidal breath so it feels alive without being distracting.
    _prism_overlay: "np.ndarray | None" = None
    if enable_light_refraction:
        _po = np.zeros((_REEL_HEIGHT, _REEL_WIDTH, 3), dtype=np.float32)
        _bands = [
            ((220, 80,  80),  0),
            ((80,  220, 80),  35),
            ((80,  80,  220), 70),
            ((220, 200, 80),  105),
            ((160, 80,  220), 140),
        ]
        _cx = _REEL_WIDTH // 3
        _cy = _REEL_HEIGHT // 5
        for _col, _off in _bands:
            for _bx in range(0, _REEL_WIDTH, 2):
                _by = int(_cy + (_bx - _cx) * 0.18 + _off)
                if 0 <= _by < _REEL_HEIGHT:
                    _by0, _by1 = max(0, _by - 3), min(_REEL_HEIGHT, _by + 4)
                    _bx0, _bx1 = max(0, _bx - 1), min(_REEL_WIDTH, _bx + 2)
                    _po[_by0:_by1, _bx0:_bx1] += np.array(_col, dtype=np.float32) * (0.028 / 255.0)
        # Heavy blur so streaks become gentle glows
        for _ch in range(3):
            _cp = Image.fromarray(np.clip(_po[:, :, _ch] * 255, 0, 255).astype(np.uint8))
            _cp = _cp.filter(ImageFilter.GaussianBlur(radius=45))
            _po[:, :, _ch] = np.array(_cp, dtype=np.float32) / 255.0
        _prism_overlay = np.clip(_po, 0.0, 1.0)

    # ── Font resolution ───────────────────────────────────────────────────────
    _font_subtitle: "ImageFont.FreeTypeFont | ImageFont.ImageFont"
    _font_hook: "ImageFont.FreeTypeFont | ImageFont.ImageFont"
    try:
        if font_path:
            _font_subtitle = ImageFont.truetype(font_path, subtitle_fontsize)
            _font_hook     = ImageFont.truetype(font_path, max(28, int(subtitle_fontsize * 1.35)))
        else:
            _font_subtitle = ImageFont.load_default()
            _font_hook     = ImageFont.load_default()
    except Exception:
        _font_subtitle = ImageFont.load_default()
        _font_hook     = ImageFont.load_default()

    _subtitle_y = subtitle_y_position if subtitle_y_position is not None else int(_REEL_HEIGHT * 0.82)

    # ── Word-to-phrase grouping (SINGLE exclusive timeline) ───────────────────
    # Clamp every phrase end to the act duration so subtitles NEVER bleed past
    # the clip (root cause of "entire script stacked on final frame").
    _raw_phrases = _chunk_words_into_phrases(word_timings, words_per_phrase)
    _display_timings: list[tuple[str, float, float]] = []
    _act_cut = max(0.05, float(act_duration) - 0.04)
    for _ph, _ps, _pe in _sanitize_subtitle_timings(_raw_phrases):
        _ps2 = max(0.0, float(_ps))
        _pe2 = min(_act_cut, float(_pe))
        if _ps2 < _pe2 and _ph.strip():
            # Hard cap visible words (anti buffer-leak)
            _toks = _ph.split()
            if len(_toks) > max(1, int(words_per_phrase or 4)):
                _ph = " ".join(_toks[: max(1, int(words_per_phrase or 4))])
            _display_timings.append((_ph, _ps2, _pe2))
    # Re-sanitize after act clamp (exclusive single-active intervals only)
    _display_timings = _sanitize_subtitle_timings(_display_timings)

    # Pre-render each unique phrase ONCE as an RGBA layer — single blit per frame
    # (eliminates overlapping/shadowed double TextClip/PIL draws).
    _phrase_layer_cache: dict[str, "np.ndarray"] = {}
    for _ph, _, _ in _display_timings:
        if _ph in _phrase_layer_cache:
            continue
        _layer = Image.new("RGBA", (_REEL_WIDTH, _REEL_HEIGHT), (0, 0, 0, 0))
        _ldraw = ImageDraw.Draw(_layer)
        _draw_wrapped_text(
            _ldraw, _ph, _font_subtitle, _subtitle_y, _REEL_WIDTH,
            fill=subtitle_fill,
            stroke_width=subtitle_stroke_width,
            stroke_fill=subtitle_stroke_fill,
        )
        _phrase_layer_cache[_ph] = np.array(_layer)

    def _current_phrase(t: float) -> str:
        """Return the single active phrase for local time t; empty = flushed."""
        if t < 0 or t >= _act_cut:
            return ""
        # Exclusive half-open intervals — at most one match after sanitize
        for phrase, ws, we in _display_timings:
            if ws <= t < we:
                return phrase
        return ""

    # ── Diagonal pan direction (acts rotate through 8 compass directions) ─────
    # ── Motion profile (cycles across acts for varied camera dynamics) ────────
    _mp = _MOTION_PROFILES[act_index % len(_MOTION_PROFILES)]
    _mp_zoom_start = _mp["zoom_start"]
    _mp_zoom_end   = _mp["zoom_end"]
    _mp_pan_mul    = _mp["pan_mul"]
    _mp_rev        = -1 if _mp["reverse_dir"] else 1
    _pan_dir = _PAN_DIRS[act_index % len(_PAN_DIRS)]

    # ── Frame renderer ────────────────────────────────────────────────────────
    def _make_frame(t: float) -> np.ndarray:
        # ── 1. Continuous ease-motion zoom + coupled diagonal pan ─────────────
        # t_norm ∈ [0,1]; eased value never reaches zero derivative at t=1.
        t_norm  = min(1.0, t / max(act_duration, 0.001))
        motion  = _ease_motion_continuous(t_norm)

        _scale  = _mp_zoom_start + (_mp_zoom_end - _mp_zoom_start) * motion
        # Legacy dissolve micro-zoom disabled when dissolve_out_dur == 0 (hard cuts).
        if dissolve_out_dur > 0 and t > act_duration - dissolve_out_dur:
            _bump  = (t - (act_duration - dissolve_out_dur)) / max(dissolve_out_dur, 0.001)
            _scale += 0.02 * _bump

        # Diagonal pan: physically coupled to zoom (parallax scales with headroom)
        pan_x_total = int(_pan_dir[0] * _mp_rev * _PAN_AMP_X * _mp_pan_mul * motion)
        pan_y_total = int(_pan_dir[1] * _mp_rev * _PAN_AMP_Y * _mp_pan_mul * motion)

        # ── 2. Sinusoidal micro-drift (handheld float, independent of pan) ────
        drift_x = int(_DRIFT_AMP_X * math.sin(2 * math.pi * _DRIFT_FREQ_X * t + act_index * 1.3))
        drift_y = int(_DRIFT_AMP_Y * math.cos(2 * math.pi * _DRIFT_FREQ_Y * t + act_index * 0.9))

        # ── 3. Scale, crop with compound offset ──────────────────────────────
        scaled_w = int(img.width  * _scale)
        scaled_h = int(img.height * _scale)
        scaled   = img.resize((scaled_w, scaled_h), Image.LANCZOS)
        cx = max(0, min(
            (scaled_w - _REEL_WIDTH)  // 2 + pan_x_total + drift_x,
            scaled_w - _REEL_WIDTH,
        ))
        cy = max(0, min(
            (scaled_h - _REEL_HEIGHT) // 2 + pan_y_total + drift_y,
            scaled_h - _REEL_HEIGHT,
        ))
        cropped = scaled.crop((cx, cy, cx + _REEL_WIDTH, cy + _REEL_HEIGHT))

        arr = np.array(cropped.convert("RGB"), dtype=np.float32)

        # ── 4. Dark overlay ───────────────────────────────────────────────────
        arr *= (1.0 - overlay_opacity)

        # ── 5. Dynamic vignette pulse ─────────────────────────────────────────
        if vignette_mask is not None:
            v_t = _VIGNETTE_PULSE_MIN + (
                (_VIGNETTE_PULSE_MAX - _VIGNETTE_PULSE_MIN) * 0.5
                * (1.0 + math.sin(2 * math.pi * _VIGNETTE_PULSE_FREQ * t + act_index * 2.1))
            )
            arr *= (1.0 - vignette_mask[:, :, np.newaxis] * v_t)

        # ── 6. Motion blur at scene entry ─────────────────────────────────────
        if act_index > 0 and t < _MB_DUR:
            _mb_fade   = 1.0 - t / max(_MB_DUR, 0.001)
            _mb_radius = max(0, int(_MB_MAX_RADIUS * _mb_fade))
            if _mb_radius >= 1:
                _arr_u8  = np.clip(arr, 0, 255).astype(np.uint8)
                _pil_tmp = Image.fromarray(_arr_u8, mode="RGB")
                _pil_tmp = _pil_tmp.filter(ImageFilter.BoxBlur(_mb_radius))
                arr      = np.array(_pil_tmp, dtype=np.float32)

        # ── 7. Flicker exposure ───────────────────────────────────────────────
        # Linearly interpolates between adjacent keyframes for smooth variation.
        if enable_flicker and _flicker_targets is not None:
            idx_f   = t / _FLICKER_INTERVAL
            idx0    = min(int(idx_f), _n_flicker_states - 1)
            idx1    = min(idx0 + 1,  _n_flicker_states - 1)
            frac    = idx_f - int(idx_f)
            flicker = float(
                _flicker_targets[idx0] * (1.0 - frac)
                + _flicker_targets[idx1] * frac
            )
            arr *= (1.0 + flicker)

        arr = np.clip(arr, 0, 255).astype(np.uint8)
        frame = Image.fromarray(arr, mode="RGB").convert("RGBA")
        draw  = ImageDraw.Draw(frame)

        # ── 8. Hook headline — Act 1 only (never duplicates subtitle stream) ─
        if enable_hook_text and hook_text and act_index == 0:
            hook_y = int(_REEL_HEIGHT * hook_y_frac)
            _draw_centered_text(draw, hook_text, _font_hook, hook_y, _REEL_WIDTH)

        # ── 9. Lower-third phrase subtitle — SINGLE pre-rendered blit ────────
        phrase = _current_phrase(t)
        frame_arr = np.array(frame)
        if phrase and phrase in _phrase_layer_cache:
            _alpha_composite_numpy(frame_arr, _phrase_layer_cache[phrase], 0, 0)

        # ── 10. Logo layer ────────────────────────────────────────────────────
        if logo_static_array is not None:
            lh, lw   = logo_static_array.shape[:2]
            lx       = (_REEL_WIDTH  - lw) // 2
            ly       = _REEL_HEIGHT  - lh - 100
            _alpha_composite_numpy(frame_arr, logo_static_array, lx, ly)
        frame = Image.fromarray(frame_arr)

        # ── 11. Film grain ────────────────────────────────────────────────────
        rgb_arr = np.array(frame.convert("RGB"), dtype=np.float32)
        if _grain is not None:
            rgb_arr += _grain[:, :, np.newaxis]

        # ── 11a. Dust particles ───────────────────────────────────────────────
        # Pre-rendered tile rolled upward at 15 px/s; adds organic atmosphere
        # to underground/ruin environments without per-frame allocation cost.
        if enable_dust_particles and _dust_tile is not None:
            _shift = int(t * 15) % _REEL_HEIGHT
            _shifted = np.roll(_dust_tile, -_shift, axis=0)
            rgb_arr = np.clip(
                rgb_arr + _shifted[:, :, np.newaxis] * 180.0,
                0.0, 255.0,
            )

        # ── 11b. Light refraction (prismatic glass/quartz glow) ───────────────
        # Very soft prismatic streaks that slowly breathe (0.2 Hz sine) — only
        # meaningful on glass or crystal subjects; invisible at low opacity.
        if enable_light_refraction and _prism_overlay is not None:
            _refract_alpha = 0.045 * (1.0 + 0.35 * math.sin(2.0 * math.pi * 0.2 * t + act_index))
            rgb_arr = np.clip(
                rgb_arr + _prism_overlay * (_refract_alpha * 255.0),
                0.0, 255.0,
            )

        # ── 12. Volumetric light rays ─────────────────────────────────────────
        # Beam column sweeps _RAY_SWEEP × canvas_width across the act duration.
        # Precomputed x-profile and y-fade arrays keep per-frame cost minimal.
        if enable_light_rays and _ray_x_coords is not None and _ray_y_fade is not None:
            _beam_x = _ray_beam_start_x + _RAY_SWEEP * motion
            _sigma2  = 2.0 * _RAY_SIGMA ** 2
            _x_profile = np.exp(-((_ray_x_coords - _beam_x) ** 2) / _sigma2)  # (W,)
            # Outer product → (H, W) beam mask; multiply by fade and opacity
            _beam_mask = _ray_y_fade[:, np.newaxis] * _x_profile[np.newaxis, :] * _RAY_OPACITY
            # Additive warm-gold blend
            rgb_arr = np.clip(
                rgb_arr + _beam_mask[:, :, np.newaxis] * _RAY_COLOR[np.newaxis, np.newaxis, :],
                0, 255,
            )

        rgb_arr = np.clip(rgb_arr, 0, 255).astype(np.uint8)

        # ── 13. Scene edge blend (DISABLED for hard cuts) ─────────────────────
        # When dissolve_*_dur > 0 this would fade to/from black. For Shorts/Reels
        # both durations are forced to 0.0 → instantaneous hard cuts.
        if dissolve_in_dur > 0 or dissolve_out_dur > 0:
            _blend = 1.0
            if dissolve_in_dur > 0 and t < dissolve_in_dur:
                _blend = t / dissolve_in_dur
            if dissolve_out_dur > 0 and t > act_duration - dissolve_out_dur:
                _out_blend = (act_duration - t) / dissolve_out_dur
                _blend     = min(_blend, _out_blend)
            if _blend < 0.999:
                rgb_arr = (rgb_arr.astype(np.float32) * max(0.0, _blend)).astype(np.uint8)

        return rgb_arr

    clip = VideoClip(frame_function=_make_frame, duration=act_duration)
    clip = clip.with_fps(fps)
    return clip


# ---------------------------------------------------------------------------
# Text rendering helpers
# ---------------------------------------------------------------------------

def _draw_centered_text(
    draw: "ImageDraw.Draw",
    text: str,
    font,
    y_center: int,
    canvas_width: int,
    fill: tuple = (255, 255, 255),
    stroke_width: int = 0,
    stroke_fill: "tuple | None" = None,
) -> None:
    """Draw horizontally centered text at y_center (optional neon/metallic stroke)."""
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw   = bbox[2] - bbox[0]
        th   = bbox[3] - bbox[1]
    except AttributeError:
        tw, th = draw.textsize(text, font=font)  # type: ignore[attr-defined]
    x = (canvas_width - tw) // 2
    y = y_center - th // 2
    kwargs: dict = {"font": font, "fill": fill}
    if stroke_width and stroke_width > 0:
        kwargs["stroke_width"] = int(stroke_width)
        kwargs["stroke_fill"] = stroke_fill or (0, 0, 0)
    draw.text((x, y), text, **kwargs)


def _draw_wrapped_text(
    draw: "ImageDraw.Draw",
    text: str,
    font,
    y_center: int,
    canvas_width: int,
    fill: tuple = (255, 255, 255),
    max_width_frac: float = 0.85,
    line_spacing: int = 12,
    stroke_width: int = 0,
    stroke_fill: "tuple | None" = None,
) -> None:
    """
    Draw word-wrapped text centered on the canvas within max_width_frac of width.
    Lines are stacked vertically and centred around y_center.
    """
    max_w = int(canvas_width * max_width_frac)
    words: list[str] = text.split()
    lines: list[str] = []
    current: list[str] = []

    for word in words:
        candidate = " ".join(current + [word])
        try:
            bbox = draw.textbbox((0, 0), candidate, font=font)
            tw   = bbox[2] - bbox[0]
        except AttributeError:
            tw, _ = draw.textsize(candidate, font=font)  # type: ignore[attr-defined]
        if tw <= max_w or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    if not lines:
        return

    line_heights: list[int] = []
    for line in lines:
        try:
            bbox = draw.textbbox((0, 0), line, font=font)
            th   = bbox[3] - bbox[1]
        except AttributeError:
            _, th = draw.textsize(line, font=font)  # type: ignore[attr-defined]
        line_heights.append(max(1, th))

    total_h = sum(line_heights) + line_spacing * max(0, len(lines) - 1)
    y = y_center - total_h // 2
    for line, lh in zip(lines, line_heights):
        _draw_centered_text(
            draw, line, font, y + lh // 2, canvas_width,
            fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill,
        )
        y += lh + line_spacing


def _alpha_composite_numpy(
    base: np.ndarray,
    overlay: np.ndarray,
    x: int,
    y: int,
) -> None:
    """Alpha-composite overlay (RGBA uint8) onto base (RGB/RGBA uint8) in-place."""
    oh, ow = overlay.shape[:2]
    bh, bw = base.shape[:2]
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(bw, x + ow), min(bh, y + oh)
    if x2 <= x1 or y2 <= y1:
        return
    ov    = overlay[y1 - y: y1 - y + (y2 - y1), x1 - x: x1 - x + (x2 - x1)]
    alpha = ov[:, :, 3:4].astype(np.float32) / 255.0
    base[y1:y2, x1:x2, :3] = (
        ov[:, :, :3].astype(np.float32) * alpha
        + base[y1:y2, x1:x2, :3].astype(np.float32) * (1.0 - alpha)
    ).clip(0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Logo pre-renderer
# ---------------------------------------------------------------------------

def _prerender_logo(
    logo_image_path: "Path | None",
    logo_width_px: int,
    logo_opacity: float,
    logo_max_height_px: "int | None",
) -> "np.ndarray | None":
    """Load, scale, and alpha-premultiply the logo PNG; returns RGBA ndarray or None."""
    if logo_image_path is None or not logo_image_path.is_file():
        return None
    try:
        logo  = Image.open(logo_image_path).convert("RGBA")
        scale = logo_width_px / logo.width
        new_h = int(logo.height * scale)
        if logo_max_height_px and new_h > logo_max_height_px:
            scale = logo_max_height_px / logo.height
            new_w = int(logo.width  * scale)
            new_h = logo_max_height_px
        else:
            new_w = logo_width_px
        logo       = logo.resize((new_w, new_h), Image.LANCZOS)
        arr        = np.array(logo).astype(np.float32)
        arr[:, :, 3] = arr[:, :, 3] * logo_opacity
        return np.clip(arr, 0, 255).astype(np.uint8)
    except Exception as exc:
        logger.warning("Logo prerender failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Main public entry point
# ---------------------------------------------------------------------------

def compile_sequence_reel(
    image_paths: "list[Path]",
    hook_text: str,
    *,
    voice_audio: "Path | None" = None,
    ambient_audio: "Path | None" = None,
    impact_sfx_audio: "Path | None" = None,
    impact_sfx_volume: "float | None" = None,
    output_path: "Path | None" = None,
    target_duration: float = _DEFAULT_DURATION,
    act_duration_s: "float | None" = None,
    word_timings: "list[tuple[str, float, float]] | None" = None,
    font_path: "str | None" = None,
    overlay_opacity: float = 0.35,
    enable_hook_text: bool = True,
    vignette_strength: float = 0.0,
    grain_intensity: float = 18.0,
    logo_image_path: "Path | None" = None,
    logo_width_px: int = 200,
    logo_y_offset_px: int = 100,
    logo_opacity: float = 0.85,
    logo_max_height_px: "int | None" = None,
    subtitle_fontsize: int = 56,
    subtitle_y_position: "int | None" = None,
    hook_y_frac: float = 0.50,
    page_id: str = "",
    fps: int = _DEFAULT_FPS,
    words_per_phrase: int = 4,
    subtitle_fill: tuple = (255, 230, 0),
    subtitle_stroke_width: int = 0,
    subtitle_stroke_fill: "tuple | None" = None,
    # Cinematic lighting toggles (opt-in; False by default to keep other pages unchanged)
    enable_flicker: bool = False,
    enable_light_rays: bool = False,
    # Atmosphere post-FX (opt-in)
    enable_dust_particles: bool = False,    # floating dust for ruin/cave environments
    enable_light_refraction: bool = False,  # prismatic glow for glass/crystal subjects
    # Override hard cap: set to a value > 80 to allow longer reels
    duration_override: "float | None" = None,
    # Guaranteed CTA text overlay — rendered as a fixed subtitle for the entire
    # CTA audio block so subtitles never go dark at the end of the video.
    cta_text: str = "",
    cta_start_s: float = -1.0,   # negative → derive automatically from word_timings
    # Ambient bed volume (0–1). Master Mei target 0.14–0.18.
    ambient_volume: "float | None" = None,
    # Linear VO gain (1.0 = unity). Master Mei V4 uses ~1.334 (+2.5 dB).
    voice_volume_gain: "float | None" = None,
    # Extra ambient/SFX multiplier after ambient_volume (default 1.40).
    ambient_gain_mul: "float | None" = None,
    # Duck ambient under voice (0.70 = bed×70% while VO plays).
    ambient_duck_ratio: "float | None" = None,
    # Seconds of voice to duck under; default = full voice clip duration.
    ambient_duck_until_s: "float | None" = None,
    # Overall master mix gain after loudnorm (+15% = 1.15).
    master_audio_gain: "float | None" = None,
    # Procedural drone profile when no ambient file: "mystery" | "warrior"
    ambient_profile: str = "mystery",
    # Extra seconds after final audio so CTA sentence/subtitles never clip
    tail_pad_s: float = 1.0,
    # When True, floor (and prefer) exact_duration_s so reels never undershoot
    # (master_mei / ancient_knowledge 80 s mandate).
    force_exact_duration: bool = False,
    exact_duration_s: float = 80.0,
) -> Path:
    """
    Compile an N-image sequence reel from a list of background images.

    Parameters
    ----------
    image_paths:
        Ordered list of image paths — one per act.  Must be non-empty.
    hook_text:
        Static headline.  Burned into Act 1 when ``enable_hook_text=True``.
    voice_audio:
        Path to the full-length voiceover MP3/WAV.
    ambient_audio:
        Path to the ambient soundscape MP3/WAV (optional).
    output_path:
        Destination MP4 path.  Auto-generated in a temp dir if None.
    target_duration:
        Target total reel duration in seconds (fallback when no audio file).
    act_duration_s:
        Explicit per-act clip length.  Ignored when audio file drives timeline.
    word_timings:
        List of ``(word, start_s, end_s)`` from ElevenLabs timestamps.
    vignette_strength:
        When > 0, enables the dynamic vignette pulse (0.20 – 0.40 opacity).
    enable_flicker:
        Subtle ±5 % brightness oscillation at ~0.10 s random intervals.
        Simulates live flame / torch reflection.  Default: False.
    enable_light_rays:
        Slow-moving warm-gold Gaussian light beam column sweeping across the
        canvas.  Default: False.
    """
    from moviepy import AudioFileClip, concatenate_videoclips  # type: ignore[import]

    if not image_paths:
        raise ValueError("compile_sequence_reel: image_paths must not be empty.")

    # Harden against Windows MAX_PATH / failed act saves — every path must exist.
    # Blinded fallback: Act 1 / first readable path — never FileNotFoundError.
    image_paths = sanitize_sequence_image_paths(
        list(image_paths),
        base_fallback=image_paths[0] if image_paths else None,
    )
    n_acts = len(image_paths)

    # ── Canonical timeline ────────────────────────────────────────────────────
    _configured_dur = (
        float(act_duration_s) * n_acts
        if act_duration_s is not None and act_duration_s > 0
        else float(target_duration)
    )
    _voice_actual_dur: float = 0.0
    if voice_audio and voice_audio.is_file():
        try:
            _tmp_ac = AudioFileClip(str(voice_audio))
            _voice_actual_dur = _tmp_ac.duration
            _tmp_ac.close()
            total_duration = _voice_actual_dur
        except Exception as _ae:
            logger.warning("Could not read voice audio duration: %s — using floor", _ae)
            total_duration = _configured_dur
    else:
        total_duration = _configured_dur

    # ── Duration guardrails ───────────────────────────────────────────────────
    # Timeline is AUDIO-DRIVEN: video_render_duration = tts_audio_duration + tail_pad.
    # NEVER pad with silent B-roll to hit an 80 s floor — if VO is short, the
    # container trims to match (master_mei mandate).  force_exact_duration is
    # retained only as a soft *target hint* for logging; it must NOT inflate
    # the timeline past the spoken track.
    _DURATION_BUFFER_S = max(1.0, float(tail_pad_s) if tail_pad_s is not None else 1.0)
    _exact_floor = float(exact_duration_s) if exact_duration_s and exact_duration_s > 0 else 80.0
    if duration_override is not None:
        total_duration = float(duration_override)
    elif _voice_actual_dur > 0:
        total_duration = _voice_actual_dur + _DURATION_BUFFER_S   # audio-driven trim
        if force_exact_duration and total_duration < _exact_floor:
            # Legacy flag: log only — do NOT pad silent frames (causes dead air).
            logger.warning(
                "force_exact_duration ignored for padding | audio+pad=%.1fs < target=%.1fs "
                "— trimming to audio (no silent B-roll)",
                total_duration, _exact_floor,
            )
    else:
        # No voice track — fall back to configured duration with floor safety net
        total_duration = max(_configured_dur, _DURATION_FLOOR_S)
        if force_exact_duration:
            total_duration = max(total_duration, _exact_floor)

    act_duration_locked = total_duration / n_acts

    logger.info(
        "compile_sequence_reel | page=%s n_acts=%d total=%.1fs act=%.1fs "
        "voice=%.1fs vignette=%.2f grain=%.1f flicker=%s rays=%s transition=hard_cut",
        page_id, n_acts, total_duration, act_duration_locked,
        _voice_actual_dur, vignette_strength, grain_intensity,
        enable_flicker, enable_light_rays,
    )

    wt = list(word_timings or [])
    # Resolve CTA start early so narration subtitles never leak into the CTA
    # window (ghost text / full-script stack on the final frame).
    _resolved_cta_t0 = -1.0
    if cta_text:
        if cta_start_s >= 0:
            _resolved_cta_t0 = float(cta_start_s)
        else:
            _narr_end = (
                _voice_actual_dur * 0.95 if _voice_actual_dur > 0 else total_duration * 0.80
            )
            _cta_hit = [ws for w, ws, we in wt if ws >= _narr_end]
            _resolved_cta_t0 = _cta_hit[0] if _cta_hit else max(0.0, total_duration - 6.0)
        # Drop ANY word that starts at/after CTA — and clamp ends that spill in
        _cut = _resolved_cta_t0 - 0.05
        wt = [
            (w, ws, min(we, _cut))
            for w, ws, we in wt
            if ws < _cut and min(we, _cut) > ws
        ]
    act_segments = _split_word_timings_into_acts(wt, n_acts, total_duration)
    logo_arr     = _prerender_logo(logo_image_path, logo_width_px, logo_opacity, logo_max_height_px)

    # Normalised (0..1) vignette mask; per-frame pulse scales the strength.
    vignette_arr: "np.ndarray | None" = None
    if vignette_strength > 0:
        vignette_arr = _make_vignette(_REEL_WIDTH, _REEL_HEIGHT, strength=1.0)
        logger.debug(
            "Vignette (normalised) | pulse=[%.2f, %.2f] @ %.3f Hz",
            _VIGNETTE_PULSE_MIN, _VIGNETTE_PULSE_MAX, _VIGNETTE_PULSE_FREQ,
        )

    # ── Build per-act clips ───────────────────────────────────────────────────
    clips: list = []
    for i, (img_path, (_t_start, _t_end, act_wt)) in enumerate(
        zip(image_paths, act_segments)
    ):
        logger.info(
            "Rendering act %d/%d | %s | zoom=%.2f→%.2f | pan_dir=%s | transition=hard_cut",
            i + 1, n_acts, img_path.name,
            _ZOOM_PER_ACT_START, _ZOOM_PER_ACT_END,
            _PAN_DIRS[i % len(_PAN_DIRS)],
        )
        clip = _build_act_clip(
            img_path,
            act_duration      = act_duration_locked,
            word_timings      = act_wt,
            hook_text         = hook_text,
            enable_hook_text  = enable_hook_text,
            overlay_opacity   = overlay_opacity,
            font_path         = font_path,
            subtitle_fontsize = subtitle_fontsize,
            subtitle_y_position = subtitle_y_position,
            hook_y_frac       = hook_y_frac,
            logo_static_array = logo_arr,
            vignette_mask     = vignette_arr,
            grain_intensity   = grain_intensity,
            fps               = fps,
            zoom_start        = _ZOOM_PER_ACT_START,
            zoom_end          = _ZOOM_PER_ACT_END,
            act_index         = i,
            words_per_phrase  = words_per_phrase,
            subtitle_fill     = subtitle_fill,
            subtitle_stroke_width = subtitle_stroke_width,
            subtitle_stroke_fill  = subtitle_stroke_fill,
            # Hard cuts: never apply fade/dissolve between B-roll scenes.
            dissolve_in_dur   = 0.0,
            dissolve_out_dur  = 0.0,
            enable_flicker    = enable_flicker,
            enable_light_rays = enable_light_rays,
            enable_dust_particles   = enable_dust_particles,
            enable_light_refraction = enable_light_refraction,
        )
        clips.append(clip)

    # Hard-cut concatenate (chain = back-to-back; no padding / fade overlays)
    logger.info("Concatenating %d clip(s) with hard cuts → %.1fs total …", len(clips), total_duration)
    try:
        final_video = concatenate_videoclips(clips, method="chain")
    except TypeError:
        # Older MoviePy builds may not expose method=; compose still hard-cuts
        # when dissolve durations are 0 and clips share the same size.
        final_video = concatenate_videoclips(clips, method="compose")

    # Always route final MP4 into outputs/{page}/clips/
    output_path = _resolve_sequence_mp4_path(output_path, page_id=page_id)
    os.makedirs(os.path.dirname(str(output_path)), exist_ok=True)
    logger.info("Sequence reel output path → %s", output_path)

    final_video = final_video.with_duration(total_duration)

    # ── Guaranteed CTA text overlay (ISOLATED — no ghost narration behind) ───
    # Narration word timings were already stripped above. CTA TextClip is the
    # ONLY text drawn in the CTA window.
    if cta_text and _resolved_cta_t0 >= 0:
        _cta_t0 = _resolved_cta_t0
        _cta_dur = max(0.5, total_duration - _cta_t0)
        try:
            from moviepy import TextClip, CompositeVideoClip, ColorClip  # type: ignore[import]
            # Isolate CTA string only — strip tags + fix sovereignty typos
            _cta_safe = re.sub(
                r"\[(?:cackles|chuckles|dry\s*laugh)\]\s*",
                "",
                (cta_text or "").strip(),
                flags=re.IGNORECASE,
            ).strip()
            _cta_safe = re.sub(r"\bsovereianty\b", "sovereignty", _cta_safe, flags=re.IGNORECASE)
            _cta_safe = re.sub(r"\bsoverignty\b", "sovereignty", _cta_safe, flags=re.IGNORECASE)
            _cta_safe = re.sub(r"\bsovereignity\b", "sovereignty", _cta_safe, flags=re.IGNORECASE)
            # Hard isolation: if somehow a long script leaked into cta_text, keep
            # only the first sentence / ~12 words.
            if len(_cta_safe.split()) > 16 or "\n\n" in _cta_safe:
                _cta_safe = " ".join(_cta_safe.split()[:12])
            # Flush: narration subtitles already stripped above — CTA is sole text
            _max_chars = 28
            _words = _cta_safe.split()
            _lines: list[str] = []
            _cur: list[str] = []
            for _w in _words:
                _cand = " ".join(_cur + [_w])
                if len(_cand) <= _max_chars or not _cur:
                    _cur.append(_w)
                else:
                    _lines.append(" ".join(_cur))
                    _cur = [_w]
            if _cur:
                _lines.append(" ".join(_cur))
            _cta_wrapped = "\n".join(_lines) if _lines else _cta_safe
            _cta_y = subtitle_y_position or int(_REEL_HEIGHT * 0.72)
            # Opaque black bar behind CTA so no ghost phrase can show through
            _bar_h = max(120, int(subtitle_fontsize * 2.8))
            _bar = (
                ColorClip(size=(_REEL_WIDTH, _bar_h), color=(0, 0, 0))
                .with_opacity(0.55)
                .with_start(_cta_t0)
                .with_duration(_cta_dur)
                .with_position(("center", max(0, _cta_y - _bar_h // 2)))
            )
            _cta_tc = (
                TextClip(
                    text=_cta_wrapped,
                    font_size=subtitle_fontsize,
                    color="white",
                    font=font_path or "Arial",
                    stroke_color="black",
                    stroke_width=2,
                    method="label",
                    text_align="center",
                )
                .with_start(_cta_t0)
                .with_duration(_cta_dur)
                .with_position(("center", _cta_y))
            )
            final_video = CompositeVideoClip([final_video, _bar, _cta_tc])
            final_video = final_video.with_duration(total_duration)
            logger.info(
                "CTA overlay ISOLATED | text='%s' start=%.1fs dur=%.1fs (no ghost narr)",
                _cta_safe[:60], _cta_t0, _cta_dur,
            )
        except Exception as _cta_ov_exc:
            logger.warning(
                "CTA overlay skipped (%s) — falling back to word-timing subtitles.",
                _cta_ov_exc,
            )
    audio_clips: list = []

    # ── Cinematic impact SFX at exact t=0 (hook braam) ────────────────────────
    _impact_vol = (
        float(impact_sfx_volume)
        if impact_sfx_volume is not None and float(impact_sfx_volume) > 0
        else _IMPACT_SFX_VOLUME_DEFAULT
    )
    _impact_vol = max(0.0, min(1.0, _impact_vol))
    if impact_sfx_audio and Path(impact_sfx_audio).is_file() and _impact_vol > 0.01:
        try:
            _imp = AudioFileClip(str(impact_sfx_audio))
            if abs(_impact_vol - 1.0) > 0.01:
                try:
                    _imp = _imp.with_volume_scaled(_impact_vol)
                except Exception:
                    pass
            _imp = _imp.with_start(0.0)
            audio_clips.append(_imp)
            logger.info(
                "Impact SFX layered at t=0 | dur=%.2fs vol=%.2f | %s",
                float(getattr(_imp, "duration", 0) or 0),
                _impact_vol,
                Path(impact_sfx_audio).name,
            )
        except Exception as _imp_exc:
            logger.warning("Impact SFX load failed (%s) — skipping hook hit.", _imp_exc)

    # ── Sub-bass transition booms ─────────────────────────────────────────────
    # A short (400 ms) decaying sine-wave boom at ~65 Hz is placed 50 ms before
    # each scene cut.  This adds cinematic weight to transitions with no
    # additional API calls or external assets.
    try:
        from moviepy.audio.AudioClip import AudioArrayClip  # type: ignore[import]
        _BOOM_SR   = _AUDIO_SAMPLE_RATE
        _BOOM_DUR  = 0.40          # seconds
        _BOOM_FREQ = 65.0          # Hz (sub-bass)
        _BOOM_AMP  = 0.12          # subtle under VO — avoid hiss/clip with 48 kHz mix
        _bt = np.linspace(0.0, _BOOM_DUR, int(_BOOM_SR * _BOOM_DUR), dtype=np.float32)
        _boom_wave = (
            np.sin(2.0 * math.pi * _BOOM_FREQ * _bt)
            * np.exp(-_bt * 18.0)
            * _BOOM_AMP
        )
        _boom_stereo = np.column_stack([_boom_wave, _boom_wave])  # (N, 2) float32
        for _bi in range(1, n_acts):
            _t_cut = act_duration_locked * _bi - 0.05   # 50 ms before cut
            if _t_cut < 0:
                _t_cut = 0.0
            _boom_clip = AudioArrayClip(_boom_stereo, fps=_BOOM_SR).with_start(_t_cut)
            audio_clips.append(_boom_clip)
        logger.debug("Transition booms injected: %d cuts", n_acts - 1)
    except Exception as _boom_exc:
        logger.debug("Bass-boom generation skipped: %s", _boom_exc)

    _voice_gain = (
        float(voice_volume_gain)
        if voice_volume_gain is not None and float(voice_volume_gain) > 0
        else _VOICE_VOLUME_GAIN
    )
    if voice_audio and voice_audio.is_file():
        try:
            vc = AudioFileClip(str(voice_audio))
            # Only scale when explicitly ≠ 1.0 — raw gain was causing distortion
            if abs(_voice_gain - 1.0) > 0.01:
                try:
                    vc = vc.with_volume_scaled(_voice_gain)
                except Exception:
                    pass
            audio_clips.append(vc)
            logger.info(
                "Voice audio loaded | dur=%.1fs | gain=%.3fx (loudnorm upstream)",
                vc.duration, _voice_gain,
            )
        except Exception as _ae:
            logger.warning("Voice audio load failed: %s", _ae)

    if ambient_audio and not ambient_audio.is_file():
        logger.warning(
            "Ambient track path supplied but MISSING: %s — will try procedural fallback",
            ambient_audio,
        )
    # Bed volume: page override → engine default (0.14–0.18 for warrior/Master Mei).
    _AMBIENT_GAIN_MUL = (
        float(ambient_gain_mul)
        if ambient_gain_mul is not None and float(ambient_gain_mul) > 0
        else _AMBIENT_GAIN_MUL_DEFAULT
    )
    _amb_vol = (
        float(ambient_volume)
        if ambient_volume is not None
        else _AMBIENT_VOLUME
    )
    _amb_profile = (ambient_profile or "mystery").strip().lower() or "mystery"
    if _amb_profile == "warrior":
        _amb_vol = max(0.28, min(0.38, float(_amb_vol) * _AMBIENT_GAIN_MUL))
    else:
        _amb_vol = max(0.08, min(1.0, float(_amb_vol) * _AMBIENT_GAIN_MUL))
    _duck = (
        float(ambient_duck_ratio)
        if ambient_duck_ratio is not None and float(ambient_duck_ratio) > 0
        else _AMBIENT_DUCK_RATIO_DEFAULT
    )
    _duck = max(0.30, min(1.0, _duck))
    _master_gain = (
        float(master_audio_gain)
        if master_audio_gain is not None and float(master_audio_gain) > 0
        else _MASTER_AUDIO_GAIN_DEFAULT
    )
    _master_gain = max(1.0, min(1.50, _master_gain))
    _duck_until = (
        float(ambient_duck_until_s)
        if ambient_duck_until_s is not None and float(ambient_duck_until_s) > 0
        else float(_voice_actual_dur or 0.0)
    )

    def _apply_voice_duck(clip, *, bed_vol: float, duck_ratio: float, until_s: float):
        """
        Scale ambient to bed_vol; duck to bed_vol×duck_ratio while VO plays,
        then restore full bed for CTA / tail (audible + impactful).
        """
        from moviepy import concatenate_audioclips as _cat_a  # type: ignore[import]

        dur = float(getattr(clip, "duration", 0) or 0)
        if dur <= 0.05:
            return clip
        until = max(0.0, min(float(until_s), dur - 0.05))
        ducked_vol = max(0.05, float(bed_vol) * float(duck_ratio))
        full_vol = max(0.05, float(bed_vol))
        if until <= 0.05 or duck_ratio >= 0.99:
            try:
                return clip.with_volume_scaled(full_vol)
            except Exception:
                return clip
        try:
            head = clip.subclipped(0, until).with_volume_scaled(ducked_vol)
            tail = clip.subclipped(until, dur).with_volume_scaled(full_vol)
            return _cat_a([head, tail])
        except Exception:
            try:
                return clip.with_volume_scaled(ducked_vol)
            except Exception:
                return clip

    if ambient_audio and ambient_audio.is_file():
        try:
            import math as _math
            _amb_probe = AudioFileClip(str(ambient_audio))
            _amb_dur   = _amb_probe.duration
            _amb_probe.close()
            logger.info(
                "Ambient track FOUND (%.1f KB) → %s | looping to %.1fs vol=%.2f duck=%.2f until=%.1fs",
                ambient_audio.stat().st_size / 1024, ambient_audio.name,
                total_duration, _amb_vol, _duck, _duck_until,
            )
            if _amb_dur < total_duration:
                from moviepy import concatenate_audioclips  # type: ignore[import]
                n_loops       = _math.ceil(total_duration / _amb_dur)
                looped_parts  = [AudioFileClip(str(ambient_audio)) for _ in range(n_loops)]
                background_music = (
                    concatenate_audioclips(looped_parts)
                    .subclipped(0, total_duration)
                )
            else:
                background_music = (
                    AudioFileClip(str(ambient_audio))
                    .subclipped(0, total_duration)
                )
            background_music = _apply_voice_duck(
                background_music, bed_vol=_amb_vol, duck_ratio=_duck, until_s=_duck_until
            )
            audio_clips.append(background_music)
        except Exception as _ae:
            logger.warning("Ambient audio load failed: %s — synthesizing drone", _ae)
            _synth = _synthesize_ambient_drone(
                total_duration, volume=_amb_vol, profile=_amb_profile
            )
            if _synth is not None:
                audio_clips.append(
                    _apply_voice_duck(
                        _synth, bed_vol=1.0, duck_ratio=_duck, until_s=_duck_until
                    )
                )
    else:
        # No ambient file — synthesize a procedural drone so there is ALWAYS
        # a background atmosphere layer (ancient_knowledge mute-bug fix).
        logger.info(
            "No ambient file — falling back to procedural drone | profile=%s vol=%.2f duck=%.2f",
            _amb_profile, _amb_vol, _duck,
        )
        _synth = _synthesize_ambient_drone(
            total_duration, volume=_amb_vol, profile=_amb_profile
        )
        if _synth is not None:
            audio_clips.append(
                _apply_voice_duck(
                    _synth, bed_vol=1.0, duck_ratio=_duck, until_s=_duck_until
                )
            )
        else:
            logger.warning(
                "AMBIENT MISSING — procedural drone also failed. "
                "Voice-only mix; drop a loop into assets/*/audio/."
            )

    if audio_clips:
        from moviepy import CompositeAudioClip  # type: ignore[import]
        mixed = CompositeAudioClip(audio_clips) if len(audio_clips) > 1 else audio_clips[0]
        # CRITICAL (ancient_knowledge mute fix): pin composite duration to the
        # full video container. Without this, MoviePy may infer duration from
        # the shortest clip (voice) and silence ambient after narration ends.
        try:
            mixed = mixed.with_duration(total_duration)
        except Exception:
            pass
        try:
            # Tiny absolute-end audio edge only — not a scene transition.
            _af = float(_AUDIO_EDGE_FADE_S)
            if _af > 0:
                try:
                    mixed = mixed.audio_fadeout(_af)
                except AttributeError:
                    from moviepy.audio.fx import AudioFadeOut  # type: ignore[import]
                    mixed = mixed.with_effects([AudioFadeOut(_af)])
        except Exception:
            pass
        final_video = final_video.with_audio(mixed)

    logger.info("Writing sequence reel → %s (audio_fps=%d)", output_path, _AUDIO_SAMPLE_RATE)
    os.makedirs(os.path.dirname(str(output_path)), exist_ok=True)
    try:
        final_video.write_videofile(
            str(output_path),
            fps         = fps,
            codec       = "libx264",
            audio_codec = "aac",
            audio_fps   = _AUDIO_SAMPLE_RATE,
            preset      = "medium",
            threads     = 4,
            ffmpeg_params=["-ar", str(_AUDIO_SAMPLE_RATE)],
            logger      = None,
        )
    finally:
        for c in clips:
            try:
                c.close()
            except Exception:
                pass
        try:
            final_video.close()
        except Exception:
            pass
        for ac in audio_clips:
            try:
                ac.close()
            except Exception:
                pass
        gc.collect()

    # Post-encode: 48 kHz + loudnorm + optional master gain (+15%)
    _normalize_sequence_audio_48k(Path(output_path), master_gain=_master_gain)

    logger.info("Sequence reel complete: %s", output_path)
    return output_path


def _normalize_sequence_audio_48k(
    mp4_path: Path,
    *,
    master_gain: float = 1.15,
) -> None:
    """Re-mux MP4 audio through loudnorm + 48 kHz + master gain (video copy)."""
    import shutil
    import subprocess

    path = Path(mp4_path)
    if not path.is_file():
        return
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return
    gain = max(1.0, min(1.50, float(master_gain or 1.0)))
    af = f"loudnorm=I=-16:TP=-1.5:LRA=11,volume={gain:.3f}"
    tmp = path.with_suffix(".loudnorm48k_tmp.mp4")
    try:
        subprocess.run(
            [
                ffmpeg, "-y",
                "-i", str(path),
                "-c:v", "copy",
                "-af", af,
                "-ar", str(_AUDIO_SAMPLE_RATE),
                "-c:a", "aac", "-b:a", "192k",
                str(tmp),
            ],
            check=True,
            capture_output=True,
            timeout=600,
        )
        tmp.replace(path)
        logger.info(
            "Final mix loudnorm+48kHz+master_gain=%.2f applied → %s",
            gain, path.name,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Final mix loudnorm skipped: %s", exc)
        if tmp.is_file():
            try:
                tmp.unlink()
            except OSError:
                pass
