# LOFI four-stage pipeline — 2026-08-25

**Canvas:** `lofi-four-stage-pipeline.canvas.tsx`  
**Live path:** `core/economic_reel_lofi`  
**Style module:** `core/economic_reel_lofi/style_modules/riso_retro_flat_v4.py`  
**Default:** `review_required=true` — no Flux or TTS before Gate 2.

This map replaces the `_EPISODE_WORLDS` audit (`riso_retro_flat_v4_pipeline_20260825.md`). It documents the rebuild, not another patch on the kitchen zone table.

## Headline

| | |
|--|--|
| 4 stages | Script → concept → prompt → image+VO |
| 2 gates | Human hold; `--lofi-no-review` auto-passes both |
| Retired | `_EPISODE_WORLDS` is no longer the source of setting/object |
| One module | Swapping riso / model / LoRA = zero Stage 1–2 or QA-logic edits |

## Pipeline (what feeds what)

```
Stage 1  Script generation          (LLM writer; text + timing only)
    → Gate 1  human approval        (locked --lofi-script auto-clears)
Stage 2  Narrative → visual concept (LLM per beat; place locked after beat 1)
Stage 3  Prompt assembly            (style module + episode_anchor_stem; no Flux/TTS)
    → Gate 2  human approval        (assembled positive + negative)
Stage 4  Image generation + QA + VO (VO only here, after Gate 2)
```

1. **Stage 1** accepts `duration_s`. Default 27 → 9 × 3s. Larger values add beats at 3s (45s → 15, 90s → 30). Output has visual fields stripped.
2. **Gate 1** writes `lofi_pipeline_*.json` and returns if `review_required`. Resume: `--lofi-resume-from PATH --lofi-approve-gate 1`. Locked `--lofi-script` auto-clears Gate 1 only.
3. **Stage 2** asks an LLM what each beat looks like from the spoken line. Character RAG supplies appearance only. Episode place from beat 1 is carried into beats 2–9. `_EPISODE_WORLDS` is not read.
4. **Stage 3** builds one scene paragraph from the concept, injects `episode_anchor_stem`, and derives the negative from style + `not_in_frame`. No Flux or TTS.
5. **Gate 2** holds until `--lofi-approve-gate 2`. The reviewed artifact is the assembled `visual_prompt` + `negative_prompt`, not Stage 2 concept text. No image or TTS cost before this clears.
6. **Stage 4** generates stills, runs QA, then ElevenLabs (skipped in `--stills-only`). Attempt 1 uses the Gate-2-approved prompt.

## Audit finding → change

| Audit finding | Change | Where | Why |
|---------------|--------|-------|-----|
| Kitchen restated 3× on figure beats | One `scene_description`; no world clause; no “object is part of the environment” | `assemble_v2_prompt_dev` | CFG 5.5: cond named the room, so kettle/window negatives could not win |
| `_EPISODE_WORLDS` hardcoded zones per pack | Stage 2 LLM decides place from the line; locks it for the episode | `visual_concept.py` | Pack JSON only sent a world id; kitchen/car/bed/stoop strings were frozen |
| Static negative + stem subtraction | `style_negative` + per-beat `not_in_frame` minus licensed stems | `compose_beat_negative` | Blanket kettle/jar/window list was uncond; it could not out-compete a named room |
| `episode_anchor_stem` variety-only | Copied onto every beat and injected into the positive prompt | `apply_v2_prompts_to_lines_dev` | Motif never reached Flux |
| object-gate skipped when `key_object` is mug | Mug blob = intended; extra compact blobs still fail | `assess_default_object_intrusion` | Window/jar/plant on a mug still-life were never blob-gated |
| Retry “detailed interior objects” | Line-quality guard only (outlines / grain / planes) | `_retry_prompt_extras` + `StyleConfig.linework_guard` | Attempt 2 re-injected clutter on every subject_type |
| warm_frac mug macro → sunset_doorway / sunset_disc | `macro_no_setting` / `object_focus` never take sunset labels | `pixel_lighting_label` / `_episode_style_class` | Beat 4 warm mug disc was not a doorway |
| VisualQA and spoken_prop independently decided unspoken | Shared `licensed_objects` list | `licensed_objects.py` | Beat 5 named mug and still got “unspoken: lamp, mug, table” |
| VO / voice-settings before concept review | ElevenLabs only in Stage 4 after Gate 2 | `_produce_one` | Audit: no image or TTS cost before Gate 2 |

## Style module (cuts across Stage 3/4)

Everything riso-specific lives in `style_modules/riso_retro_flat_v4.py`:

- open / technique / mood / format / palettes / characters
- model, steps, CFG 5.5, LoRA id/trigger (empty today)
- linework + exposure guards
- `style_negative` (photo / nsfw / text / anatomy — not kitchen clutter)
- warm_frac thresholds (`warm_sunset_doorway=0.10`, `warm_sunset_disc=0.12`, macros ineligible)

Swap later with `LOFI_STYLE_MODULE`. QA *logic* stays in `pipeline.py`; it reads thresholds from the style object.

## Stage 3 assembly (after)

| Part | Source | Notes |
|------|--------|-------|
| open + technique + mood | style module | Not restated from the room |
| scene | Stage 2 `scene_description` | One paragraph; place named once |
| isolation / coverage | subject_type | No kitchen noun |
| episode_anchor_stem | beat 0 stem, copied to all beats | Now in the prompt |
| negative | style_negative + `not_in_frame` − licensed | Per-beat “what this concept is NOT” |

## Stage 4 gate behavior (after)

| Gate | After |
|------|--------|
| default_object_gate | Runs on mug object_focus. Licensed blob excluded; second compact blob fails. |
| retry extras | Linework guard cannot contain “interior objects”. |
| pixel lighting | Macros → indoor_lamp_glow / overcast, never sunset_doorway. |
| VisualQA + spoken_prop | Same `licensed_objects` list. |

## CLI

```
# Hold at gates (default)
python main.py --page wonder_feed --post-type ECONOMIC_REEL_LOFI --stills-only --module relationship

# Locked script: Gate 1 auto-clears, Gate 2 still holds
python main.py --page wonder_feed --post-type ECONOMIC_REEL_LOFI --stills-only --lofi-script PATH.json

# Approve Gate 2 and generate
python main.py --page wonder_feed --post-type ECONOMIC_REEL_LOFI --lofi-resume-from PATH.json --lofi-approve-gate 2

# Auto-pass both gates (not the trusted default yet)
python main.py --page wonder_feed --post-type ECONOMIC_REEL_LOFI --stills-only --lofi-no-review
```

## Future work (stubbed, not blocking)

`niche_config.py` — `get_active_niche()`. Live: `relationship_reflective`. Parenting is an empty stub. Stage 1 logs the niche id. A swappable niche module mirroring the style module is out of scope this round.
