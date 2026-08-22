# LOFI Schnell — module closed (2026-08-21)

Detail: `_agent_log/archive/20260820_text-legibility-investigation-full.md`. One-line verdicts only.

**Closing video:** `outputs/wonder_feed/clips/lofi_reel_distance_20260821_211202_v01.mp4` (27.0s locked beats, ~17.5 MB). Theme `distance` / `silence_that_speaks`. Scene 1 hand-into-chest: critic now fails that class (`deformed or anatomically incorrect hand` + torso crop). Replaced via `regenerate_scene` → `lofi_reel_distance_20260821_211202_v01_regen.mp4`.

## Current / next

ECONOMIC_REEL_LOFI (Schnell) **closed**. Hands-free bank shipped. No cup/mug/coffee/glass as `key_object`. 9/9 object stems unique. Residual holds accepted (scene 3 text-on-building, scene 4 uniq16 after INTRUDER ladder). Next unit: **ECONOMIC_REEL_LOFI_FLUXDEV** (real negative prompt, 20 steps, CFG 3.5–5.0, same QA), then Schnell vs Dev, then LoRA. Do not start LoRA first.

## Hands-free close (this round)

- Removed mug/cup/coffee/glass from writer sources: `DEFAULT_SETTING_OBJECT_PAIRS`, `_MEANING_CLUSTERS`, `_VARIETY_PAIRS`, seed + live theme banks, concrete-detail RAG, script_agent few-shots. `only_object_clause()` unchanged. Runtime filter `_is_banned_key_object` still strips leftovers.
- Bank is scenery / empty-handed figures only (silhouette, wide couple silhouette variant, portraits with empty hands, rain/window, platform, unmade bed, closed suitcase on the floor). No small object in a hand near the face.
- Stem cap `_MAX_OBJECT_STEM_HITS = 1` (no category repeat in 9 beats).
- Unlocked run **7/9** QA. Manual accept scenes 3+4 (known families: critic text; uniq16 after INTRUDER empty-frame). MP4 assembled via `--lofi-script` reuse. QA stack (critic, uniq16, INTRUDER, style) unchanged.

## Future pass (do not fix now)

- object_focus of large scenery (window / door / cushion / platform): INTRUDER often fires on attempt 1, then recovers on step 1. Scene 4 `wide empty cushion` then failed uniq16 (61) — known INTRUDER↔uniq16 tension, not a new >50% class.

## Fixes (committed earlier)

1. TEXT_LEGIBILITY_GUARD v1 (`366277f`) — partial.
2. Halftone backgrounds steps 1–2 (`26f9681`) — helps step 2 uniq16.
3. Camera-vocab removal (`6f7e6aa`) — photoreal 62%→0% on scene 4 probe.
4. unnamed `only_object_clause` (`24e6b59`) — INTRUDER 77%→56%.
5. TEXT_LEGIBILITY_GUARD v2 (`d4852a1`) — scene 8 6/6.
6. Portrait `_TEXT_SURFACE_CLAUSE` — 2/6, reverted.
