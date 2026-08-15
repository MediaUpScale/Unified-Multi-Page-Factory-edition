# -*- coding: utf-8 -*-
"""Rebuild Ancient Knowledge sequence reels with stepped image holds."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

_LOG = logging.getLogger("rebuild_ak")

PAGE_ID = "ancient_knowledge"
NARRATION_SPEED = 1.0
VOICE_LEAD_IN_S = 0.25
PLANNER_DIR = _HERE / "outputs" / PAGE_ID / "postplanner"
PLANNER_XLSX = PLANNER_DIR / "postplan_20260814_073435.xlsx"
FALLBACK_LOGO = _HERE / "channels_config" / PAGE_ID / "logo" / "logo.png"
TEST_CLIP = "reel_they_built_flight__centuries_bef_v02.mp4"
TEST_OUTPUT_NAME = "test_engine_fix_v03.mp4"
HASHED_PREFIX = "saqqara_bird_that_flies_as"
MAX_UNIQUE_STILLS = 14
LOCAL_RENDER_DIR = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "ak_reel_renders"


@dataclass
class ProductionParams:
    """Per-run production knobs (CLI overrides these defaults)."""

    target_duration_min: float = 80.0
    target_duration_max: float = 90.0
    reuse_existing_images: bool = True
    pacing_sequence: list[float] = field(
        default_factory=lambda: [3.0, 3.0, 4.0, 4.0]
    )
    enable_subtitle_padding: bool = True
    encoding_preset: str = "ultrafast"
    regen_music: bool = False
    narration_min_words: int = 130
    narration_max_words: int = 145
    max_stills: int = MAX_UNIQUE_STILLS

REBUILD_CLIPS: tuple[str, ...] = (
    "reel_they_built_flight__centuries_bef_v02.mp4",
    "reel_egyptian_tomb__a_mere_bird__or_s_v01.mp4",
    "reel_ancient_egypt_s_glider__did_engi_v03.mp4",
    "reel_saqqara_bird__the_missing_tail_u_v04.mp4",
    "reel_egyptian_hieroglyphs__do_they_de_v06.mp4",
    "reel_what_if_egypt_s__toy__proves_imp_v05.mp4",
    "reel_this_artefact_rewrites_ancient_t_v09.mp4",
    "reel_was_egypt_s__glider__merely_a_sy_v07.mp4",
    "reel_master_carvers_shaped_wood_to_de_v08.mp4",
    "reel_saqqara_bird_s_forgotten_design__v10.mp4",
)

_CTA_TAIL_RE = re.compile(
    r"(?:share\s+your\s+theories[^.]*\.?\s*)?"
    r"(?:follow\s+ancient\s+knowledge[^.]*\.?\s*)+$",
    re.IGNORECASE,
)
_HASHTAG_RE = re.compile(r"[#＃][\w]+")
_STOP = {
    "reel", "the", "and", "for", "with", "that", "this", "from", "what", "they",
    "were", "was", "did", "does", "into", "over", "under", "just", "more",
    "your", "have", "been", "about", "when", "then", "than", "also", "only",
    "some", "their", "them", "modern", "researchers", "impossible",
    "ancient", "egypt", "egyptian", "hidden", "mystery", "lost",
}
_SUBJECT_BLOCKLIST = {
    "nazca", "pyramid", "atlantis", "antarctica", "baalbek", "yonaguni",
    "voynich", "anunnaki", "dwarka", "mohenjo", "sirius", "crystal",
    "baghdad", "gobekli", "stonehenge", "puma", "antikythera",
}
_SUBJECT_ANCHORS = {
    "saqqara": 10.0, "glider": 8.0, "gliders": 8.0, "cairo": 6.0,
    "sycamore": 6.0, "hieroglyph": 10.0, "hieroglyphs": 10.0,
    "dihedral": 5.0, "aerodynamic": 4.0, "carvers": 4.0, "tomb": 3.0,
    "flight": 2.0, "bird": 1.5,
}


def _ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")


def _path_is_file(p) -> bool:
    try:
        return p is not None and Path(p).is_file()
    except OSError:
        return False


def _token_weight(tok: str) -> float:
    if tok in _STOP or len(tok) < 4:
        return 0.0
    return 1.0 + max(0, len(tok) - 4) * 0.2


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _load_planner_captions() -> dict[str, str]:
    """Load captions from postplan_20260814_073435.xlsx only."""
    mapping: dict[str, str] = {}
    xlsx = PLANNER_XLSX if PLANNER_XLSX.is_file() else None
    if xlsx is None:
        _LOG.error("Missing planner workbook %s", PLANNER_XLSX)
        return mapping
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header = [str(c or "").strip().upper() for c in next(rows, ())]
    cap_i = next((i for i, h in enumerate(header) if "CAPTION" in h), 1)
    url_i = next((i for i, h in enumerate(header) if "MEDIA" in h or "URL" in h), 2)
    for row in rows:
        if not row or len(row) <= max(cap_i, url_i):
            continue
        url = str(row[url_i] or "")
        cap = str(row[cap_i] or "").strip()
        name = url.replace("\\", "/").rsplit("/", 1)[-1]
        if name.endswith(".mp4") and cap:
            mapping[name] = cap
    wb.close()
    _LOG.info("PostPlanner %s | %d caption(s)", xlsx.name, len(mapping))
    return mapping


def _narration_from_caption(caption: str, cta_text: str) -> str:
    text = _HASHTAG_RE.sub(" ", caption or "").strip()
    text = re.sub(r"\s+", " ", text)
    if cta_text:
        cta_pat = re.escape(cta_text.strip().rstrip("."))
        text = re.sub(cta_pat + r"\.?\s*$", "", text, flags=re.IGNORECASE).strip()
    return _CTA_TAIL_RE.sub("", text).strip()


def _file_fingerprint(path: Path) -> str:
    h = hashlib.md5()
    h.update(str(path.stat().st_size).encode())
    with path.open("rb") as fh:
        h.update(fh.read(65536))
    return h.hexdigest()


def _iter_pngs(folder: Path):
    for f in sorted(folder.glob("*.png")):
        if "_wm." not in f.name.lower():
            yield f
    for sub in sorted(folder.glob("seq_run_*")):
        if sub.is_dir():
            for f in sorted(sub.glob("*.png")):
                if "_wm." not in f.name.lower():
                    yield f


_ACT_NAME_RE = re.compile(r"_v(\d{2})_act(\d{2})_", re.IGNORECASE)


def _variant_tag(clip: Path) -> str:
    m = re.search(r"(_v\d{2})$", clip.stem, re.IGNORECASE)
    return (m.group(1) if m else "").lower()


def _variant_num(clip: Path) -> int:
    m = re.search(r"_v(\d{2})$", clip.stem, re.IGNORECASE)
    return int(m.group(1)) if m else 0


def _collect_unique_images(
    assets_dir: Path,
    clip: Path,
    caption: str,
    *,
    params: ProductionParams,
) -> list[Path]:
    """Load this variant's 14 B-roll stills (act01 cover + work act02–14)."""
    del caption
    if not assets_dir.is_dir():
        return []
    vnum = _variant_num(clip)
    vtag = f"_v{vnum:02d}"
    by_act: dict[int, Path] = {}

    def _maybe(act_i: int, path: Path) -> None:
        if act_i < 1 or not path.is_file():
            return
        prev = by_act.get(act_i)
        try:
            if prev is None or path.stat().st_mtime >= prev.stat().st_mtime:
                by_act[act_i] = path
        except OSError:
            by_act.setdefault(act_i, path)

    for folder in sorted(assets_dir.iterdir()):
        if not folder.is_dir():
            continue
        name = folder.name.lower()
        if _tokens(name) & _SUBJECT_BLOCKLIST:
            continue
        work = folder / "work"
        roots = [work, folder] if work.is_dir() else [folder]
        for root in roots:
            for png in root.glob("*.png"):
                m = _ACT_NAME_RE.search(png.name)
                if not m:
                    continue
                if int(m.group(1)) != vnum:
                    continue
                _maybe(int(m.group(2)), png)
        if name.startswith(HASHED_PREFIX):
            for png in folder.glob("*.png"):
                if vtag in png.stem.lower():
                    _maybe(1, png)

    if len(by_act) < 14:
        for folder in sorted(assets_dir.iterdir()):
            if not folder.name.lower().startswith("ep_"):
                continue
            work = folder / "work"
            owns = False
            if work.is_dir():
                owns = any(
                    f"_v{vnum:02d}_act" in p.name.lower()
                    for p in work.glob("*.png")
                )
            if not owns:
                continue
            for i in range(1, 15):
                scene = folder / f"scene_{i:02d}.png"
                _maybe(i, scene)

    ordered = [by_act[k] for k in sorted(by_act) if 1 <= k <= 14]
    cap = max(2, int(params.max_stills))
    ordered = ordered[:cap]
    _LOG.info(
        "Variant stills | %s v=%02d acts=%s → %d pngs",
        clip.name, vnum, ",".join(str(k) for k in sorted(by_act)[:14]), len(ordered),
    )
    return ordered


def _gemini_text(response) -> str:
    text_attr = getattr(response, "text", None)
    line = text_attr() if callable(text_attr) else text_attr
    if line:
        return str(line).strip()
    parts_out: list[str] = []
    for cand in getattr(response, "candidates", []) or []:
        content = getattr(cand, "content", None)
        if not content:
            continue
        for part in getattr(content, "parts", []) or []:
            t = getattr(part, "text", None)
            if t:
                parts_out.append(str(t))
    return "\n".join(parts_out).strip()


def _strip_act_markers(text: str, cta_text: str) -> str:
    cleaned = _narration_from_caption(text, cta_text)
    cleaned = re.sub(r"\[ACT\s*\d+\]", " ", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip()


def _expand_narration(script: str, params: ProductionParams, cta_text: str) -> str:
    """Grow a PostPlanner caption to the 130–145 word band; never embed the CTA."""
    from main import _trim_script_to_word_limit

    cleaned = _strip_act_markers(script, cta_text)
    lo = max(20, int(params.narration_min_words))
    hi = max(lo, int(params.narration_max_words))
    n = len(cleaned.split())
    if lo <= n <= hi:
        _LOG.info("Narration already in band | %d words", n)
        return cleaned
    if n > hi:
        trimmed = _trim_script_to_word_limit(cleaned, max_words=hi)
        _LOG.info("Narration trimmed | %d → %d words", n, len(trimmed.split()))
        return trimmed

    import config as app_config
    from avatar_engine.providers.gemini_utils import generate_text_with_client_chain

    best = cleaned
    for attempt in range(1, 4):
        extra = ""
        if attempt > 1:
            extra = (
                f"PREVIOUS DRAFT WAS {len(best.split())} WORDS — TOO SHORT.\n"
                f"Write {lo} to {hi} words. Do not finish before {lo}. "
                "Add tomb context, sycamore wood, missing tail, dihedral wings, "
                "Cairo museum display, and aerodynamic questions.\n"
            )
        prompt = (
            "Expand this Ancient Knowledge documentary narration for a spoken reel.\n"
            f"HARD REQUIREMENT: output MUST be {lo}–{hi} words (aim {hi}). "
            "Count before you stop.\n"
            "Preserve the FIRST sentence as the hook.\n"
            "Stay on the Saqqara Bird / Egyptian wooden glider artefact. Investigative, "
            "neutral, immersive. Spoken prose only — no [ACT] markers, no hashtags, "
            "no titles, no follow/subscribe CTA.\n"
            f"{extra}\nSOURCE:\n{cleaned}\n"
        )
        try:
            resp = generate_text_with_client_chain(
                api_key=str(app_config.GEMINI_API_KEY),
                preferred_model=getattr(app_config, "GEMINI_RESEARCH_MODEL", None),
                contents=prompt,
            )
            expanded = _strip_act_markers(_gemini_text(resp), cta_text)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("Narration expand attempt %d failed (%s)", attempt, exc)
            continue
        wn = len(expanded.split())
        _LOG.info("Narration expand attempt %d | %d → %d words", attempt, n, wn)
        if lo <= wn <= hi:
            return expanded
        if wn > hi:
            capped = " ".join(expanded.split()[:hi])
            _LOG.info("Narration hard-capped | %d → %d words", wn, len(capped.split()))
            return capped
        if wn > len(best.split()):
            best = expanded

    try:
        from avatar_engine.caption_engine import CaptionEngine
        from core_engine.interfaces.factory import ChannelFactory

        ce = CaptionEngine(channel=ChannelFactory.from_env())
        vo = ce.generate_sequence_voiceover(
            topic=cleaned,
            page_niche="ancient mysteries, Saqqara Bird, Egyptian wooden glider",
            persona_voice="investigative, neutral, immersive",
            n_acts=15,
            duration_s=85.0,
            total_words_target=hi,
            cta_line="",
            batch_angle_block=cleaned,
        )
        expanded = _strip_act_markers(vo or "", cta_text)
        wn = len(expanded.split())
        _LOG.info("Narration CaptionEngine fallback | %d words", wn)
        if wn > hi:
            expanded = " ".join(expanded.split()[:hi])
            wn = len(expanded.split())
        if wn >= lo:
            return expanded
        if wn > len(best.split()):
            best = expanded
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("CaptionEngine fallback failed (%s)", exc)

    _LOG.warning("Narration still short | %d words (need %d–%d)", len(best.split()), lo, hi)
    return best


def _resolve_logo(page_ctx) -> Path:
    try:
        logo = getattr(page_ctx, "logo_png", None)
        if logo and Path(logo).is_file():
            return Path(logo)
    except OSError:
        pass
    if FALLBACK_LOGO.is_file():
        return FALLBACK_LOGO
    raise FileNotFoundError(f"Logo missing: {FALLBACK_LOGO}")


def _resolve_font(page_ctx) -> str | None:
    try:
        rel = (getattr(page_ctx, "font_path", None) or "").strip()
        if not rel:
            return None
        cand = Path(rel)
        if not cand.is_absolute():
            cand = _HERE / cand
        return str(cand) if cand.is_file() else None
    except OSError:
        return None


def _prepend_silence(src: Path, dst: Path, lead_in_s: float) -> Path:
    ms = max(0, int(round(lead_in_s * 1000)))
    if ms <= 0:
        return src
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(src), "-af", f"adelay={ms}|{ms}",
         "-c:a", "libmp3lame", "-q:a", "2", str(dst)],
        check=True, timeout=120,
    )
    return dst


def _find_cached_bed(clips_dir: Path, clip: Path, kind: str) -> Path | None:
    tokens = _tokens(clip.stem)
    skip = ("_realign_", "_slowed", "test_", "_rebuilt", "_rebuild_")
    best, best_score = None, 0.0
    for f in clips_dir.glob(f"*{kind}*.mp3"):
        if any(s in f.name.lower() for s in skip):
            continue
        score = sum(_token_weight(t) for t in (tokens & _tokens(f.stem)))
        if score > best_score:
            best, best_score = f, score
    return best if best_score >= 0.9 else None


def _resolve_beds(
    clips_dir: Path,
    clip: Path,
    stem: str,
    page_ctx,
    topic: str,
    duration_s: float = 90.0,
    *,
    regen_music: bool = False,
):
    music = None if regen_music else _find_cached_bed(clips_dir, clip, "music_v2")
    sfx = _find_cached_bed(clips_dir, clip, "atmosphere_sfx")
    if _path_is_file(music) and _path_is_file(sfx):
        _LOG.info("Reusing cached BGM %s + SFX %s", music.name, sfx.name)
        return music, sfx
    if regen_music:
        _LOG.info("Regenerating music_v2 bed (dark mystery template) for %s", stem)
    from avatar_engine.audio_engine import generate_ambient_track, generate_master_mei_soundscape
    if not _path_is_file(music) and getattr(page_ctx, "use_music_v2_bed", False):
        music, _imp = generate_master_mei_soundscape(
            clips_dir, stem=stem, duration_seconds=float(duration_s),
            include_impact_sfx=False, topic=topic or "",
            directive_path=getattr(page_ctx, "music_prompt_directive_path", None),
            style_profile=getattr(page_ctx, "ambient_music_style", None) or "mystery",
        )
    if not _path_is_file(sfx):
        import config as app_config
        if app_config.ELEVENLABS_API_KEY:
            sfx = generate_ambient_track(
                clips_dir / f"{stem}_atmosphere_sfx.mp3",
                duration_seconds=10.0,
                prompt=getattr(page_ctx, "ambient_sfx_prompt", None) or None,
            )
    return music, sfx


def _render_tts(script: str, stem: str, reel_dir: Path, page_ctx) -> tuple[Path, list, float, float]:
    from avatar_engine.audio_engine import (
        _audio_file_duration_s, approximate_word_timings, generate_voiceover_with_timestamps,
    )
    from main import _filter_audio_tag_timings, _stitch_audio_sequential

    voice_id = page_ctx.elevenlabs_voice_id if page_ctx else None
    model_id = page_ctx.elevenlabs_model if page_ctx else "eleven_multilingual_v2"
    vs = dict(page_ctx.elevenlabs_voice_settings if page_ctx else {})
    vs["speed"] = NARRATION_SPEED
    raw_narr = reel_dir / f"{stem}_rebuild_narration_raw.mp3"
    voice_path, wts = generate_voiceover_with_timestamps(
        script, raw_narr, voice_id=voice_id or None, model_id=model_id,
        speed=NARRATION_SPEED, voice_settings=vs, enable_ssml=False,
        expressive_mode=False, force_elevenlabs=True,
    )
    wts = _filter_audio_tag_timings(wts or [])
    padded = _prepend_silence(Path(voice_path), reel_dir / f"{stem}_rebuild_narration.mp3", VOICE_LEAD_IN_S)
    wts = [(w, s + VOICE_LEAD_IN_S, e + VOICE_LEAD_IN_S) for w, s, e in wts]

    cta_text = (page_ctx.reel_cta_text if page_ctx else "").strip()
    cta_path = None
    cta_wts: list = []
    if cta_text:
        try:
            cta_path, cta_wts = generate_voiceover_with_timestamps(
                cta_text, reel_dir / f"{stem}_rebuild_cta.mp3",
                voice_id=voice_id or None, model_id=model_id,
                speed=NARRATION_SPEED, voice_settings=vs, enable_ssml=False,
                expressive_mode=False, force_elevenlabs=True,
            )
            cta_wts = _filter_audio_tag_timings(cta_wts or [])
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("CTA TTS failed: %s", exc)
            cta_path = None
    narr_dur = float(_audio_file_duration_s(Path(padded)))
    if cta_path is None or not Path(cta_path).is_file():
        return Path(padded), wts, narr_dur, narr_dur
    stitched = _stitch_audio_sequential(
        Path(padded), Path(cta_path), reel_dir / f"{stem}_rebuild_voice.mp3", silence_s=1.0
    )
    if not cta_wts:
        try:
            cta_dur = float(_audio_file_duration_s(Path(cta_path))) or 4.0
            cta_wts = approximate_word_timings(cta_text, cta_dur)
        except Exception:  # noqa: BLE001
            cta_wts = []
    offset = narr_dur + 1.0
    combined = list(wts) + [(w, s + offset, e + offset) for w, s, e in cta_wts]
    total = narr_dur + 1.0 + float(_audio_file_duration_s(Path(cta_path)))
    return (stitched.resolve() if stitched else Path(padded)), combined, total, narr_dur


def rebuild_one(
    *,
    clip: Path,
    assets_dir: Path,
    clips_dir: Path,
    page_ctx,
    output_path: Path,
    captions: dict[str, str],
    params: ProductionParams,
) -> bool:
    caption = (captions.get(clip.name) or "").strip()
    if not caption:
        _LOG.error("No PostPlanner caption for %s", clip.name)
        return False
    cta_text = str(getattr(page_ctx, "reel_cta_text", None) or "")
    script = _expand_narration(caption, params, cta_text)
    if len(script.split()) < 20:
        _LOG.error("Narration too short for %s", clip.name)
        return False
    images = _collect_unique_images(assets_dir, clip, caption, params=params)
    if len(images) < 2:
        _LOG.error("Need ≥2 unique stills for %s — found %d", clip.name, len(images))
        return False
    _LOG.info("Scene list (%d unique):\n  %s", len(images), "\n  ".join(p.name for p in images))

    stem = _slugify(clip.stem)
    voice_path, word_timings, total_audio_s, narr_dur = _render_tts(script, stem, clips_dir, page_ctx)
    cta_visual_s = float(narr_dur) + 0.3
    (clips_dir / f"{stem}_rebuild_timings.json").write_text(
        json.dumps({"clip": clip.name, "script": script, "audio_s": total_audio_s,
                    "narr_s": narr_dur, "cta_visual_s": cta_visual_s,
                    "images": [str(p) for p in images],
                    "params": {
                        "target_duration_min": params.target_duration_min,
                        "target_duration_max": params.target_duration_max,
                        "pacing_sequence": params.pacing_sequence,
                        "encoding_preset": params.encoding_preset,
                    }}, indent=2),
        encoding="utf-8",
    )
    topic = re.sub(r"[\-_]+", " ", clip.stem.replace("reel_", "", 1)).strip()
    music_bed, sfx_loop = _resolve_beds(
        clips_dir, clip, stem, page_ctx, topic, duration_s=params.target_duration_max,
        regen_music=bool(params.regen_music),
    )
    logo = _resolve_logo(page_ctx)

    from core_engine.reel_sequence_engine import compile_sequence_reel
    LOCAL_RENDER_DIR.mkdir(parents=True, exist_ok=True)
    local_out = LOCAL_RENDER_DIR / output_path.name
    _LOG.info(
        "Compile | audio=%.1fs narr=%.1fs cta_visual=%.1fs | %d stills | logo=%s | local=%s",
        total_audio_s, narr_dur, cta_visual_s, len(images), logo.name, local_out,
    )
    compile_sequence_reel(
        images, hook_text="", voice_audio=voice_path, ambient_audio=music_bed,
        output_path=local_out, target_duration=float(total_audio_s),
        word_timings=word_timings or None, font_path=_resolve_font(page_ctx),
        overlay_opacity=float(getattr(page_ctx, "reel_overlay_opacity", None) or 0.30),
        enable_hook_text=False,
        vignette_strength=float(getattr(page_ctx, "vignette_strength", None) or 0.0),
        grain_intensity=float(getattr(page_ctx, "grain_intensity", None) or 18.0),
        logo_image_path=logo,
        logo_width_px=int(getattr(page_ctx, "logo_width_px", None) or 420),
        logo_max_height_px=int(getattr(page_ctx, "logo_max_height_px", None) or 115),
        logo_y_offset_px=int(getattr(page_ctx, "logo_y_offset_px", None) or 90),
        logo_opacity=float(getattr(page_ctx, "logo_opacity", None) or 0.75),
        subtitle_fontsize=int(getattr(page_ctx, "subtitle_fontsize", None) or 56),
        subtitle_y_position=getattr(page_ctx, "subtitle_y_position", None),
        page_id=PAGE_ID, words_per_phrase=4, subtitle_fill=(255, 230, 0),
        enable_flicker=bool(getattr(page_ctx, "enable_flicker", None) or False),
        enable_light_rays=bool(getattr(page_ctx, "enable_light_rays", None) or False),
        enable_dust_particles=bool(getattr(page_ctx, "enable_dust_particles", None) or False),
        enable_light_refraction=False, cta_text=cta_text,
        cta_start_s=cta_visual_s,
        narration_duration_s=float(narr_dur),
        cta_visual_gap_s=0.3,
        ambient_volume=float(getattr(page_ctx, "ambient_volume", None) or 0.126),
        sfx_loop_audio=sfx_loop,
        sfx_loop_volume=float(getattr(page_ctx, "atmosphere_sfx_volume", None) or 0.12),
        sfx_fade_in_s=float(getattr(page_ctx, "atmosphere_sfx_fade_in", None) or 0.2),
        ambient_duck_ratio=float(getattr(page_ctx, "ambient_duck_ratio", None) or 0.80),
        ambient_duck_until_s=float(narr_dur) or None,
        ambient_gain_mul=float(getattr(page_ctx, "ambient_sfx_gain_mul", None) or 1.0),
        ambient_profile=str(getattr(page_ctx, "ambient_music_style", None) or "mystery"),
        bgm_start_s=float(getattr(page_ctx, "bgm_start_time", None) or 0.5),
        bgm_fade_in_s=float(getattr(page_ctx, "bgm_fade_in_duration", None) or 0.35),
        tail_pad_s=0.0,
        ffmpeg_preset=params.encoding_preset,
        pacing_sequence=params.pacing_sequence,
        target_duration_min=params.target_duration_min,
        target_duration_max=params.target_duration_max,
        enable_subtitle_padding=params.enable_subtitle_padding,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(local_out, output_path)
    except OSError as exc:
        _LOG.warning("Drive copy retry (%s) — waiting 3s", exc)
        import time as _time
        _time.sleep(3)
        shutil.copy2(local_out, output_path)
    _LOG.info("WROTE %s (from local %s)", output_path, local_out.name)
    return True


def _parse_pacing(raw: str) -> list[float]:
    seq = [float(x.strip()) for x in (raw or "").split(",") if x.strip()]
    if not seq:
        raise argparse.ArgumentTypeError("PACING_SEQUENCE must be a comma-separated list of seconds")
    return seq


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help=f"Video #1 → {TEST_OUTPUT_NAME}")
    parser.add_argument("--batch", action="store_true", help="Remaining 9 after Test #1 (80–90s).")
    parser.add_argument("--all", action="store_true", help="All 10 clips as *_enginefix.mp4")
    parser.add_argument("--skip-existing", action="store_true", help="Skip clips that already have enginefix output")
    parser.add_argument("--target-duration-min", type=float, default=80.0)
    parser.add_argument("--target-duration-max", type=float, default=90.0)
    parser.add_argument("--reuse-existing-images", dest="reuse_existing_images", action="store_true", default=True)
    parser.add_argument("--no-reuse-existing-images", dest="reuse_existing_images", action="store_false")
    parser.add_argument("--pacing-sequence", type=_parse_pacing, default=[3, 3, 4, 4])
    parser.add_argument("--enable-subtitle-padding", dest="enable_subtitle_padding", action="store_true", default=True)
    parser.add_argument("--no-subtitle-padding", dest="enable_subtitle_padding", action="store_false")
    parser.add_argument("--encoding-preset", default="ultrafast")
    parser.add_argument(
        "--regen-music",
        action="store_true",
        help="Force new ElevenLabs music_v2 beds (dark mystery template; ~$0.03/clip)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not args.test and not args.batch and not args.all:
        parser.error("Pass --test, --batch (remaining 9), or --all (10 clips).")

    params = ProductionParams(
        target_duration_min=float(args.target_duration_min),
        target_duration_max=float(args.target_duration_max),
        reuse_existing_images=bool(args.reuse_existing_images),
        pacing_sequence=list(args.pacing_sequence),
        enable_subtitle_padding=bool(args.enable_subtitle_padding),
        encoding_preset=str(args.encoding_preset or "ultrafast"),
        regen_music=bool(args.regen_music),
    )
    _LOG.info(
        "ProductionParams | duration=%.0f–%.0fs reuse=%s pacing=%s preset=%s pad=%s regen_music=%s",
        params.target_duration_min, params.target_duration_max,
        params.reuse_existing_images, params.pacing_sequence,
        params.encoding_preset, params.enable_subtitle_padding,
        params.regen_music,
    )

    import config as app_config
    from page_loader import load_page_context
    if not getattr(app_config, "ELEVENLABS_API_KEY", None):
        _LOG.error("ELEVENLABS_API_KEY missing.")
        return 1
    page_ctx = load_page_context(PAGE_ID, avatar_mode="OFF", post_format="IMAGE_BACKGROUND")
    outputs_dir = getattr(page_ctx, "outputs_dir", None) or (
        Path(getattr(app_config, "OUTPUTS_DIR", "outputs")) / PAGE_ID
    )
    clips_dir = Path(outputs_dir) / "clips"
    assets_dir = Path(outputs_dir) / "assets"
    captions = _load_planner_captions()

    if args.test:
        src = clips_dir / TEST_CLIP
        if not src.is_file():
            _LOG.error("Missing %s", src)
            return 1
        dst = clips_dir / TEST_OUTPUT_NAME
        ok = rebuild_one(
            clip=src, assets_dir=assets_dir, clips_dir=clips_dir,
            page_ctx=page_ctx, output_path=dst, captions=captions, params=params,
        )
        return 0 if ok else 1

    names = list(REBUILD_CLIPS if args.all else REBUILD_CLIPS[1:])
    ok = 0
    for name in names:
        src = clips_dir / name
        if not src.is_file():
            _LOG.warning("Missing %s", name)
            continue
        dst = clips_dir / f"{src.stem}_enginefix.mp4"
        if args.skip_existing and dst.is_file() and dst.stat().st_size > 1_000_000:
            _LOG.info("Skip existing %s", dst.name)
            ok += 1
            continue
        try:
            if rebuild_one(
                clip=src, assets_dir=assets_dir, clips_dir=clips_dir,
                page_ctx=page_ctx, output_path=dst, captions=captions, params=params,
            ):
                ok += 1
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("Failed %s: %s", name, exc)
    _LOG.info("Batch complete: %d/%d", ok, len(names))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
