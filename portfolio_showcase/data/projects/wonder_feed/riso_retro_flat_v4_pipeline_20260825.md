# riso_retro_flat_v4 — end-to-end map

**Exported:** 2026-08-25 from Cursor canvas `riso-retro-flat-v4-pipeline.canvas.tsx`  
**Superseding gate/ship map (2026-08-26):** [`riso_retro_flat_v4_gates_20260826.md`](./riso_retro_flat_v4_gates_20260826.md) + canvas `riso-retro-flat-v4-gates.canvas.tsx`  
**Live path:** `core/economic_reel_lofi` + Flux Dev (`LOFI_FLUX_BACKEND=dev`)  
**Style tag:** `style-riso_painting_retro_vintage/dev/riso_retro_flat_v4`  
**Evidence run:** grief stills-only `lofi_stills_grief_20260825_151156_v01` (18 image calls)

No patches in this export. This is the machine as it ran grief 151156.

## Headline facts

| Metric | Meaning |
|--------|---------|
| CFG 5.5 | Negatives cannot beat a named room |
| 3 strings | Kitchen restated on every figure beat |
| Skip | object-gate off when `key_object` is mug |
| Shared | Same assembler for all 5 packs |

## Diagnosis

The loop is not a missing noun in the negative list.

Flux Dev is called with `guidance_scale=5.5` and a long negative body. That is classifier-free guidance:

`output ≈ uncond + 5.5 × (cond − uncond)`

The positive prompt is **cond**. On figure beats cond still says “in small kitchen at night, counter in the foreground” plus “Same place every beat: a small kitchen at night, one counter lamp”. A blanket “kettle, jar, window” list is the **uncond** side. At CFG 5.5 it cannot out-compete a room name that Flux already associates with kettle/jar/plant still-life. Patching the negative list without changing those positive strings is why each rerun grows a new leak.

## Pipeline (node graph)

Accent-outlined stages are where setting nouns enter pixels.

```
Pack JSON
    → Atmosphere          ← leak: zone.setting + world.place stamped
    → Stamp / motif
    → assemble_v2         ← leak: figure scene concatenates setting + place
    → Negative            (one list, licensed stems subtracted)
    → Flux Dev 5.5        ← leak: cond ≫ uncond at this CFG
    → Per-beat gates
    → Retry extras        ← leak: global “detailed interior objects” on attempt 2
    → Episode HOLD
```

Feeds-what:

1. **Pack JSON** supplies spoken lines + `force_episode_world_id`.
2. **Atmosphere** writes `setting`, `key_object`, `atmosphere_place`, `lighting_condition` from `_EPISODE_WORLDS`.
3. **Stamp / motif** remaps object-focus / anaphora `key_object`; does not rewrite zone `setting`.
4. **assemble_v2** concatenates those fields into the positive prompt.
5. **Negative** starts from static `LOFI_DEV_NEGATIVE_PROMPT`, minus licensed stems.
6. **Flux Dev 5.5** generates pixels (DeepInfra fallback: negative in body).
7. **Per-beat gates** accept/reject; failures append retry extras.
8. **Retry extras** rebuild prompt (focus_step + global guards) and generate again.
9. **Episode HOLD** runs lighting/cross-style after all 9 stills exist.

## 1. Prompt assembly — what lands in the positive string

`assemble_v2_prompt_dev` does **not** read `episode_anchor_stem`. That field is variety-scoring only. The setting sentence is built from stamped `setting` + `key_object` + `time_of_day` + `atmosphere_place`.

| Part | Source field | Figure beat (grief 1/5/6/8) | Object-focus (grief 2/4) |
|------|--------------|-----------------------------|---------------------------|
| open | profile.open (static) | “A risograph print close up illustration…” | same |
| scene | setting + key_object + tod + lighting_figure_framing | `{char} in {setting}, {tod}. {key_object} is part of the environment.` | `object_focus_scene_text` uses “a flat surface, blank wall behind” — setting noun dropped **here only** |
| world | atmosphere_place (world.place) | Same place every beat: a small kitchen at night, one counter lamp… | Same episode place, already established. |
| isolation | subject_type / close_variant | One clothed silhouette in the room. Blank wall behind. | The object fills the frame on a bare printed surface. |
| palette | lighting_condition → LIGHTING_CONDITIONS[id].atmos | Night interior print: cool indigo walls, one small warm lamp | same (locked indoor_lamp_glow for the whole episode) |
| anchor | resolve_visual_anchor(beat) | The image must visually embody: {hint or object} | mug still out on a flat surface alone, filling the frame |

Portrait_close injects `setting` as `ctx` and falls back to the literal string “window light” if setting is empty. Eye_close still says “Window-light atmosphere only.”

## 2. Where setting nouns are hardcoded

Not templated from the spoken line. `_EPISODE_WORLDS` in `visual_identity.py` is a frozen 9-zone table per world id. Pack JSON only supplies `force_episode_world_id`. Zones are applied as `setting = zones[i % 9].setting` in `apply_episode_atmosphere`.

| Pack | world id | Hardcoded setting leak (zone 0 / place) | Fixes grief automatically? |
|------|----------|------------------------------------------|----------------------------|
| grief | kitchen_night_counter | “small kitchen at night, counter in the foreground” + place “one counter lamp” | Only if kitchen zone/place strings change |
| self_respect | apartment_night_edge | “apartment doorway from inside at night” | No — different world table |
| communication | parked_car_night | “parked car at night, door ajar” + ambient “empty passenger seat” | No — car cabin prior |
| attachment | bedroom_night_hall | “bedroom at night, hall light as a thin crack” | No — bed/hall prior |
| trust | front_stoop_evening | “front stoop at evening, street as a dark plane” | No — street/stoop prior |

**“street corner” is a different bank.** `DEFAULT_SETTING_OBJECT_PAIRS` includes “street corner under a streetlamp”. That pool is used only when atmosphere is off (`episode_world_id` empty). All five packs force a world, so that pair is dormant on this path. Trust’s “street as a dark plane” is the live street-noun leak.

## Every positive-prompt leak site (independent of the negative list)

| Leak site | Stage | Example string | What Flux treats it as |
|-----------|-------|----------------|------------------------|
| zone.setting in scene | assemble → figure/silhouette/portrait | in small kitchen at night, counter in the foreground | Kitchen still-life prior (kettle, jar, plant, window) |
| world.place on every figure beat | assemble world clause | Same place every beat: a small kitchen at night, one counter lamp | Repeats the room name 7× per episode |
| key_object “is part of the environment” | assemble figure scene | mug is part of the environment | On figure beats, mug+kitchen = countertop clutter |
| lighting atmos | palette_sentence | one small warm lamp as a hard color plane | Lamp is drawn; spoken-prop may flag it unless caption has lit/light/dark |
| portrait ctx fallback | assemble portrait_close | window light (if setting empty) | Window pane |
| retry linework guard (151156 attempt 2) | retry extras (global) | detailed interior objects, not a flat graphic… | Explicitly asks for interior clutter on every retry |

## 3. Negative prompt — rebuilt per beat, still one list

`compose_dev_negative(licensed_object)` starts from the static `LOFI_DEV_NEGATIVE_PROMPT` and only subtracts stems of the licensed object (mug → mug/cup/coffee). It does **not** swap lists by subject_type. Object-focus and figure share the same kitchen-intruder nouns. There is no “figure negative” vs “object negative”.

**Per-beat mutation**

- Grief object beat: mug/cup stripped. Kettle/jar/window stay.
- Grief figure beat with blank wall: mug stays banned. Window stays banned. Kitchen is not in the negative at all.

**Model-call weighting**

- DeepInfra FLUX-1-dev POST: prompt + negative_prompt + guidance_scale=5.5, skip_mandatory_negative=1.
- CFG 5.5 is moderate. Positive tokens that name a room dominate negative tokens that name props of that room. Precedence: **cond ≫ uncond** at this scale.

## 4. Gates — who actually runs

Coverage is **not** object isolation. It is garment coverage: Gemini asks whether a human figure shows skin below the face. It is skipped on object_focus. Grief 151156 coverage FAILs were beats 6–8 (figures), not beat 2.

| Gate | Runs on | Skips | Fail means |
|------|---------|-------|------------|
| VisualQA (lofi_economic) | Every beat | Fail-open if Gemini down | Generic critic: garbled text, “unspoken objects”, photoreal. Does not know pronoun licensing. Beat 5 named mug and still got “unspoken: lamp, mug, table”. |
| linework / complexity | Every beat | Read errors fail-open | Near-flat graphic (uniq16 / lap_var / edge). |
| default_object_gate (INTRUDER blobs) | object_focus + silhouette only | woman/man/couple; **also skips if key_object matches mug/cup/coffee/laptop** | Compact dark blob = mug-class intruder. Grief object beats are mugs → this gate is SKIPPED. Window/jar on a mug still-life are never blob-gated. |
| style_gate (photoreal stats) | object_focus only | All figure beats | Photographic smoothness: std≥80 and edge<12 and lap_var<900. Not garment coverage. |
| spoken_prop | Every beat | Fail-open on API error | Gemini lists kettle/mug/plant/window unless caption (or it/this/that→motif) licenses them. Window-only rejects are copied to `*_spoken_ok.png` and restored if the beat never fully passes. |
| garment_coverage | woman/man/couple/silhouette + portrait_close | object_focus, eye_close | Human in frame with bare back/shoulders/chest, or coverage_ok=false. Log: `COVERAGE: incomplete garment / exposed skin`. |
| hook | Scene 1 only | Scenes 2–9 | No character, empty still-life, or not high-contrast. |
| portrait_style | close_variant portrait_close or eye_close | Everyone else | Photoreal skin/eyes, or portrait cropped to an eye macro. |
| eye_close | close_variant == eye_close | Everyone else (grief unused) | Nose/mouth/chin leaked into an eyes-only crop. |
| bed_pose | Couple + bed in setting/object | Grief kitchen (not a bed world) | Figures standing on the mattress. |
| anchor identity / painterly_lock | anchor_beat introduce\|callback and not eye_close | Normal grief beats (no anchor_beat stamped) | Wrong object drawn, or flat woodcut instead of printed room. |

## 5. Retry guard — global, not scene-type-aware

`_retry_prompt_extras` appends the same strings on every attempt>1, any subject_type. Spoken-prop fix_instructions that mention window or `INVENTED_STILL_LIFE_STEMS` are stripped (so “remove kettle” never reaches the positive prompt). VisualQA / coverage fixes still append.

| Injection | Grief 151156 attempt 2 (what ran) | Code now (post-run, not retested) |
|-----------|-----------------------------------|-----------------------------------|
| retry_variation_clause(1) | Same licensed subject. Closer crop. Light from the left. | unchanged |
| LOFI_PROMPT_LINEWORK_GUARD | fine black ink outlines, **detailed interior objects**, not a flat graphic, not a solid color field, not an empty silhouette poster | fine black ink outlines, paper grain, hard-edged color planes |
| LOFI_PROMPT_EXPOSURE_GUARD (via last_fix / coverage) | even midtone exposure, no blown highlights, no white bloom… | even midtone exposure, printed color planes |
| spoken_prop fix (if not window/kettle-named) | Redraw with only the licensed subject. Empty floor… | same, then `_strip_positive_negation` |

**Why attempt-2 can regress a clean attempt-1:** the linework guard that actually ran on 151156 asked for “detailed interior objects” on every retry, including object-focus and empty-wall figure beats. That is a global clutter instruction, not a kitchen-only one. A beat that was window-only on attempt 1 can pick up jar/kettle on attempt 2 from that phrase.

## 6. Warm score / sunset_disc — one metric, two thresholds, no crop-type split

`pixel_lighting_label` and `_episode_style_class` share the same warm mask on a 256px thumb, **upper 65%** of the frame:

- R > 165
- G > 75
- B < 140
- R − B > 45

`warm_frac` = fraction of those pixels. No separate path for object macros vs full-scene shots.

| Check | Threshold | Label | Object macro implication |
|-------|-----------|-------|--------------------------|
| pixel_lighting_label | warm_frac ≥ 0.10 | sunset_doorway (episode lighting HOLD) | A warm mug filling the upper frame trips this. Beat 4 = 0.1353 → indoor_lamp_glow declared vs sunset_doorway pixel. |
| pixel_lighting_label | warm_frac ≥ 0.04 (and not ≥ 0.10) | indoor_lamp_glow | Intended grief look. |
| _episode_style_class | warm_frac ≥ 0.12 | sunset_disc (cross-style cluster) | Same mug disc. Beat 4 clustered alone. Cross-style HOLD is 3 classes, not a doorway. |

Palette-arc WARM→COLD→CONTRAST is exempt when `force_episode_world_id` or `episode_world_id` is set. That exemption does **not** touch this pixel classifier. Lighting HOLD is post-episode, not a per-beat retry.

## 7. Cross-pack: shared machine, private room names

**Shared (one copy):** all five packs call `apply_v2_prompts_to_lines_dev` → `assemble_v2_prompt_dev` → `generate_scene_image_dev` → `generate_and_qa_scene`. Gates, CFG, negative composer, retry extras, warm_frac math: one copy.

**Per world (not shared):** fixing grief’s kitchen zone/place strings does not rewrite apartment doorway, parked car, bedroom, or stoop. Those worlds have their own hardcoded `zones[].setting`. The leak **mechanism** is shared; the **nouns** are not.

## Failure → owning stage

| Observed fail (grief 151156) | Owning stage | Why the last patch missed it |
|------------------------------|--------------|------------------------------|
| Window on 2,3,4,5,6,8,9 | Positive setting/place (“kitchen…”) at assemble + Flux prior; caught only by spoken_prop (and VisualQA). object-gate skipped on mug. | Negatives listed window; cond still named the room. Window-only fails are restored as spoken_ok. |
| Jar / kettle / plant on figure beats | zone.setting + world.place on figure scene string (assemble). Retry “detailed interior objects” on attempt 2. | Object-focus neutralized kitchen; figure beats still say kitchen/counter/lamp. |
| sunset_disc beat 4 (warm=0.14) | Episode HOLD: pixel classifier, shared threshold, no object-vs-scene split | Not a doorway. Warm object macro vs indoor_lamp_glow declaration. |
| COVERAGE on 6–8 | garment_coverage Gemini on human figures | Unrelated to object isolation. object_focus beat 2 never runs this gate. |
| VisualQA “unspoken mug” on beat 5 | `_qa_scene_image` / lofi_economic critic | Separate from spoken_prop pronoun fix. Critic does not resolve “mug” as licensed. |
| 2 attempts / beat still | IMAGE_ATTEMPTS_PER_SCENE=2 + global retry extras | Retry can add clutter the first pass did not have. |

## If the next change is one change

The map says the **cond string that names the room** is the bottleneck, not another negative noun. Object-focus already dropped the kitchen noun; figure beats and `atmosphere_place` did not. Gates that skip mug object_focus cannot catch window/jar on those stills. Warm_frac=0.10 is a lighting HOLD, not proof of a sunset doorway.

## Code pointers

`assemble_v2_prompt_dev`, `_EPISODE_WORLDS`, `compose_dev_negative`, `generate_scene_image_dev`, `generate_and_qa_scene`, `pixel_lighting_label`, `_episode_style_class`, `assess_default_object_intrusion` (`_INTRUDER_SELF`), `assess_garment_coverage`, `_retry_prompt_extras`.
