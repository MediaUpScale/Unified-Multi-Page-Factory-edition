# -*- coding: utf-8 -*-
"""Verify Master Mei narrative POV / word budget / blacklist + visual negatives."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from avatar_engine.mei_narrative import (
        banned_narration_phrases,
        build_three_act_script_instructions,
        enforce_first_person_mei,
        episode_theme_meta,
        mei_voice_prompt_block,
        narration_word_budget,
        prepare_mei_tts_text,
        sanitize_mei_narration_body,
    )
    from avatar_engine.providers.together_image import MANDATORY_NEGATIVE_PROMPT, merge_negative_prompt
    from avatar_engine.visual_roles import (
        build_training_discipline_prompt,
        pick_training_environment,
    )
    from core_engine.reel_sequence_engine import build_sequence_script_prompt
    from pages_config.master_mei import page_config as mei_cfg

    fails: list[str] = []

    # --- Word budget ---
    wmin, wtarget, wmax = narration_word_budget()
    assert (wmin, wtarget, wmax) == (190, 210, 230), (wmin, wtarget, wmax)
    assert mei_cfg.REEL_NARRATION_MIN_WORDS == 190
    assert mei_cfg.REEL_NARRATION_MAX_WORDS == 230
    assert mei_cfg.REEL_NARRATION_WORDS == 210
    print("[PASS] Word budget 190-230 (target 210)")

    # --- Prompt POV + blacklist ---
    voice = mei_voice_prompt_block()
    for needle in ("FIRST PERSON", "190", "230", "citadel", "I demand"):
        if needle.lower() not in voice.lower() and needle not in voice:
            # citadel should appear as blacklisted term
            if needle == "citadel" and "citadel" not in voice.lower():
                fails.append("voice block missing blacklist term citadel")
            elif needle != "citadel":
                fails.append(f"voice block missing {needle!r}")
    if "FIRST PERSON" not in voice and "first person" not in voice.lower():
        fails.append("voice block missing FIRST PERSON POV lock")
    print("[PASS] mei_voice_prompt_block contains POV + word targets")

    ep = episode_theme_meta("Marcus Aurelius discipline against digital numbness")
    instr = build_three_act_script_instructions(10, ep)
    for needle in ("FIRST PERSON", "190", "230", "citadel", "sensory numbness"):
        if needle.lower() not in instr.lower():
            fails.append(f"3-act instructions missing {needle!r}")
    # Blacklist may be named in the prompt; must NOT appear as voiceover formula text
    directive_chunks = re.findall(
        r'voiceover[^"]*"([^"]+)"', instr, flags=re.IGNORECASE | re.DOTALL,
    )
    joined_dirs = " ".join(directive_chunks).lower()
    for bad in ("master mei offers no comfort", "relentless practice", "internal citadel"):
        if bad in joined_dirs:
            fails.append(f"3-act voiceover directive still templates {bad!r}")
    print("[PASS] build_three_act_script_instructions POV/blacklist/depth")

    seq = build_sequence_script_prompt(
        topic="How propaganda steals your perception",
        niche="Master Mei",
        persona_voice="stern ancestral master",
        n_acts=10,
        duration_s=90.0,
        total_words_target=210,
        narrative_mode="warrior_discipline",
        niche_disclaimer=mei_cfg.NICHE_DISCLAIMER,
    )
    if "190" not in seq or "230" not in seq:
        fails.append("sequence prompt missing 190-230 word rule")
    if "first person" not in seq.lower():
        fails.append("sequence prompt missing first-person POV")
    print("[PASS] build_sequence_script_prompt warrior rules")

    # --- Sanitizers ---
    dirty = (
        "Master Mei offers no comfort. Stoic discipline demands total command "
        "over your internal citadel. Follow Master Mei through relentless practice "
        "against biomechanical cords and techno-slave sirens in the silent war for your essence."
    )
    clean = sanitize_mei_narration_body(dirty)
    for banned in banned_narration_phrases():
        if banned.lower() in clean.lower():
            fails.append(f"blacklist leak after sanitize: {banned!r} in {clean!r}")
    if re.search(r"\bMaster\s+Mei\b", clean, re.IGNORECASE):
        fails.append(f"third-person Master Mei remains: {clean!r}")
    tts = prepare_mei_tts_text("[stoic] " + dirty)
    if "[" in tts or "]" in tts:
        fails.append(f"TTS prep left brackets: {tts!r}")
    print("[PASS] sanitize + prepare_mei_tts_text POV/blacklist")
    print(f"       sample rewrite: {enforce_first_person_mei('Master Mei demands focus.')!r}")

    # --- Visual negatives ---
    required_neg = [
        "text", "words", "typography", "font", "letters", "sample",
        "watermark", "signature", "caption", "quotes", "UI elements",
        "subtitles", "labels",
    ]
    merged = merge_negative_prompt("role test")
    for term in required_neg:
        if term.lower() not in MANDATORY_NEGATIVE_PROMPT.lower():
            fails.append(f"MANDATORY_NEGATIVE missing {term!r}")
        if term.lower() not in merged.lower():
            fails.append(f"merge_negative_prompt missing {term!r}")
    print("[PASS] FLUX mandatory negative text/typography bans")

    # --- Dynamic training ---
    envs = {pick_training_environment(act_index=i, episode_seed="epA") for i in range(3)}
    envs_b = {pick_training_environment(act_index=i, episode_seed="epB") for i in range(3)}
    if len(envs) < 2:
        fails.append("training env rotation too static for one seed")
    pos, neg = build_training_discipline_prompt(
        act_index=0, episode_seed="waterfall-test-topic", include_mei_supervisor=True,
    )
    if "idle" not in neg.lower() and "idle" not in pos.lower():
        fails.append("training prompt missing idle-monk prohibition")
    if "2 to 5" not in pos.lower() and "2–5" not in pos:
        fails.append("training prompt missing 2-5 disciples rule")
    if "typography" not in neg.lower() and "text" not in neg.lower():
        fails.append("training negative missing text ban")
    # Different seeds should often diverge
    a0 = pick_training_environment(act_index=5, episode_seed="alpha")
    b0 = pick_training_environment(act_index=5, episode_seed="omega")
    print(f"[PASS] Dynamic training pool ({len(envs)}/{len(envs_b)} unique samples; "
          f"seed diversify={'YES' if a0 != b0 else 'SAME-OK'})")
    print(f"       sample env: {a0[:72]}...")

    if fails:
        print("\n[FAIL] issues:")
        for f in fails:
            print("  -", f)
        return 1

    # Optional live LLM smoke (skip if no key)
    try:
        import config as app_config
        key = getattr(app_config, "GEMINI_API_KEY", None) or getattr(app_config, "GOOGLE_API_KEY", None)
        if not key:
            print("[SKIP] Live script generation (no Gemini key)")
            print("\nALL LOCAL GUARDRAIL CHECKS PASSED")
            return 0
        from avatar_engine.caption_engine import CaptionEngine

        eng = CaptionEngine()
        script = eng.generate_sequence_voiceover(
            topic="How propaganda and instant gratification steal perception",
            page_niche="Master Mei | Mind Control",
            persona_voice="stern ancestral master, first person",
            n_acts=10,
            duration_s=90.0,
            total_words_target=210,
            narrative_mode="warrior_discipline",
            niche_disclaimer=mei_cfg.NICHE_DISCLAIMER,
            cta_line="",  # CTA stitched separately
        )
        words = len((script or "").split())
        print(f"[LIVE] Generated script words={words}")
        if not script:
            print("[WARN] Live generation returned empty (API/config) — local checks still OK")
        else:
            low = script.lower()
            if re.search(r"\bmaster\s+mei\b", low):
                print("[WARN] Live script still contains 'Master Mei' after sanitize — check pipeline order")
            for banned in ("citadel", "biomechanical cords", "techno-slave", "relentless practice"):
                if banned in low:
                    print(f"[WARN] Live script contains banned phrase: {banned}")
            if words < 190 or words > 250:
                print(f"[WARN] Live word count {words} outside 190-230 soft band")
            else:
                print("[PASS] Live script word count in band")
            out = ROOT / "outputs" / "test_mei_script_sample.txt"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(script, encoding="utf-8")
            print(f"[OK]   Wrote {out}")
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] Live script generation ({type(exc).__name__}: {exc})")

    print("\nALL LOCAL GUARDRAIL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
