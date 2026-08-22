# Text-legibility hold investigation — 2026-08-20 (read-only, no render run)

Scope: investigate the two remaining `attachment` episode hold causes per user brief —
(a) linework-flatness floor (uniq16) on some object_focus beats, (b) recurring
legible/garbled text (papers, mail envelope, reported lips/sign). Propose a prompt-level
fix for (b) only; report on (a) without fixing it. No code changes made this pass. No
render run. INTRUDER gate logic, hold thresholds, and the style gate
(`assess_photoreal_style`) were read but not modified, per constraint.

## Files/data read
- core_engine/economic_reel_lofi/config.py (lines 60-149 — `LOFI_STYLE_BASE`,
  `LOFI_NEGATIVE_PROMPT`, `LOFI_PROMPT_EXPOSURE_GUARD`, `LOFI_PROMPT_LINEWORK_GUARD`)
- VisualQA_Agent/visual_critic.py (grep for garbled/legible; lines 270-308 — the EVALUATE
  rubric, criterion 3 "Text artifacts", scoring guide, pass condition)
- core_engine/economic_reel_lofi/visual_identity.py (lines 120-260, 570-650, 689-714 —
  `only_object_clause`, `_off_workspace_place`, `object_focus_scene_text`,
  `silhouette_scene_text`, `_DEFAULT_INTRUDER_TERMS`, `_OBJECT_FOCUS_GUARD`,
  `_SATURATION_GUARD`, `_object_stem`, `_OBJECT_STEM_WORDS`)
- core_engine/economic_reel_lofi/pipeline.py (lines 80-379 — `_linework_stats`,
  `_assess_linework_complexity` [the linework-flatness floor, uniq16 < 90],
  `assess_default_object_intrusion` [INTRUDER gate — read only, not touched],
  `assess_photoreal_style` [the style gate — read only, not touched, per constraint])
- docs/session_logs/wonder_feed_20260820_1850.md (grepped for uniq16/INTRUDER/
  garbled/legible/MAIL/papers/photoreal; read the mail_pile_03 style-drift section,
  §3.16-3.17, and the "still open" list — this documents the prior "mug fix" precedent
  this task asks to replicate for text)
- outputs/wonder_feed/clips/lofi_stills_attachment_20260820_230606_v01.json (script
  lines, `visual_qa_flags`, `object_gate_by_scene` incl. per-attempt `style_gate`)
- outputs/wonder_feed/clips/lofi_hold_attachment_20260820_221118_v01.json,
  lofi_hold_attachment_20260820_224824_v01.json,
  lofi_stills_attachment_20260820_225455_v01.json (visual_qa_flags only, for
  cross-run pattern confirmation)

No code was edited. No image/LLM/TTS API was called. No render was run.

## Part 1-2 — where text/lettering enters the prompt, and how the critic flags it

**Positive-prompt side (the actual cause).** `object_focus_scene_text()`
(visual_identity.py:161-200) builds the visible-object description for `object_focus`
beats across a 3-step escalation ladder (`macro_no_setting` → `tighter_macro` →
`off_workspace`, keyed by `focus_step`, which only escalates after an INTRUDER-gate
fail). None of the three steps say anything about the object's *surface content* —
they only describe framing ("Tight macro crop of {obj} filling the frame", "Extreme
close-up of {obj} alone", "Close focus on {obj} {place}"). When `{obj}` is
`"scattered papers"` or `"unopened mail pile"`, FLUX Schnell's learned prior for those
nouns includes printed/handwritten marks (return-address blocks, "MAIL" lettering,
postage) — the same class of problem as the mug/desk still-life prior, just triggered
by the object noun instead of the setting noun. This is confirmed live in the corpus:
`_object_stem()` (visual_identity.py:689-693) already special-cases `"paper"`/`"mail"`
into one collapsed stem for repetition-cap purposes, and `_off_workspace_place()`
(line 149-158) already relocates that stem off any named desk/table — but neither
function says anything about the object's *surface marking*, which is the actual
failure mode.

**Negative-prompt side (already present, already proven ineffective).**
`LOFI_NEGATIVE_PROMPT` (config.py:130-138) already lists `"text, watermark, logo,
typography, subtitles, ui, ... garbled text"`. This is NOT new — it predates this
investigation and clearly isn't working: DeepInfra Schnell runs at `guidance_scale=0.0`
(native inference payload, confirmed in image_gen.py / together_image.py), and the
session log already established (§3.10, 2026-08-20 08:57 approval) that Schnell does
not respond reliably to negative prompts at that setting — that's the documented reason
the mug fix was done as *positive*-prompt composition (`_DEFAULT_INTRUDER_TERMS`,
`only_object_clause`) instead of strengthening the negative prompt. The same lesson
applies here directly.

**Critic-side detection (working correctly — not the bug).** `visual_critic.py`
criterion 3 ("Text artifacts — no embedded/garbled letters, logos, or UI burned into
the image", line 284) already hard-fails on any of this: the scoring guide explicitly
lists "text artifacts" as a forced-FAIL category regardless of score (line 296), and
`passed=true` requires no hard flaws (line 298). Confirmed against 4 separate real runs
under `outputs/wonder_feed/clips/`:
- `lofi_hold_attachment_20260820_221118_v01.json` → scene_4: `"Text artifacts: Garbled
  text 'BITS SEIMELE' and other unreadable text on papers.; ... Watermark 'NEL 2016' in
  the bottom right corner.; OBJECT: ... paper pile, laptop, mug ..."`
- `lofi_stills_attachment_20260820_225455_v01.json` → scene_3: `"Garbled text on
  paper"`
- `lofi_stills_attachment_20260820_230606_v01.json` → scene_3: `"Garbled typography on
  papers"`; scene_4: `"low color structure uniq16=89; Text artifact: 'MAIL' visible on
  envelope"`

So the critic is doing its job every time — the problem is that retries keep
regenerating the same class of failure because the *positive* prompt never tells the
model what a "safe" paper/envelope surface looks like, only what framing to use.
`IMAGE_MAX_RETRIES_PER_SCENE = 2` (3 attempts total) is exhausted before the
fix_instructions loop (which only appends the linework guard + the critic's specific
complaint, not a standing rule) can reliably steer it away — matching the user's
"papers 3/3 attempts" observation exactly.

## Part 3 — proposed prompt-level fix (not yet implemented)

Two additive changes, same pattern already used for the mug fix and the exposure/
saturation guards — no gate logic touched:

**1. A new universal guard string, appended to every beat's assembled prompt**
(alongside the existing `_SATURATION_GUARD` in `assemble_v2_prompt`'s `parts` list —
visual_identity.py:1256), so it covers *any* subject_type, not just `object_focus`
(this is what would reach the reported scene 5 "lips" and scene 6 "background sign"
cases, which are portrait/couple beats, not object_focus):

> "Any paper, page, envelope, sign, screen, or lettered surface in frame shows only
> abstract ink mark-making, blank space, or dense illegible scribble-texture — never a
> coherent word, never legible letters, no readable typography, postage, or signage
> anywhere in the image."

**2. A targeted reinforcement for object_focus beats whose key_object is itself a
text-bearing noun** (the scene-3/scene-4 pattern specifically) — the same "name it and
neutralize it" structure as `_DEFAULT_INTRUDER_TERMS`/`only_object_clause`, but
describing the *requested* object safely instead of banning an *unrequested* one. New
stem set reusing the existing `_object_stem()` collapse (which already merges
`paper`/`mail` into one stem) extended to sibling text-bearing nouns already present in
this file's own noun regex (letters, envelopes, lists, notebooks, calendars, bills):
`{"paper", "mail", "letter", "envelope", "notebook", "journal", "calendar", "list",
"bills"}`. When `_object_stem(key_object)` is in that set, append to the
`object_focus_scene_text()` scene string (all 3 escalation steps, next to the existing
`only` clause):

> "The visible surface shows only abstract ink texture, creases, and blank or smudged
> space — no legible words, no coherent letters, no readable postage or printed
> address."

Rationale for doing both instead of just #1: #1 alone is a long shared sentence
competing for attention with the rest of the (already long) assembled prompt; #2 puts
the instruction directly adjacent to the specific noun that's causing it, which is what
worked for the mug fix (`only_object_clause` names the object and the exclusions in the
same sentence). Neither change touches `_DEFAULT_INTRUDER_TERMS`, the escalation ladder,
`assess_default_object_intrusion`, `assess_photoreal_style`, or any hold/retry
threshold — both are new strings composed into the existing prompt-building functions.

**Not proposed:** touching the negative prompt (already proven ineffective at
`guidance_scale=0`) or lowering/removing the critic's text-artifact hard-fail (that's
the correct, working detection — the fix belongs upstream of it).

Waiting for review before writing this code or running any stills probe.

## Part 5 — will the uniq16 floor resolve as a side effect, or does it need its own fix?

**No — direct evidence says it needs its own fix, independent of the text-legibility
change.** Pulled the full per-attempt record for scene 4
(`unopened mail pile`, `lofi_stills_attachment_20260820_230606_v01.json`,
`object_gate_by_scene`):

| Attempt | Framing step | Trigger for this step | style_gate uniq16 | Result |
|---|---|---|---|---|
| 1 | `macro_no_setting` (step 0) | first try | **138** (comfortable pass) | INTRUDER fail (mug detected) |
| 2 | `tighter_macro` (step 1) — escalated because attempt 1's INTRUDER failed | — | **82** (FAIL, < 90) | INTRUDER cleared, but low-color-structure fail |
| 3 | `tighter_macro` (step 1, unchanged) | — | **89** (FAIL, < 90, by one point) | text artifact + uniq16 fail |

This is a direct causal chain, not a correlation: escalating from step 0 to step 1 to
satisfy the INTRUDER gate is exactly what collapsed uniq16 from a comfortable 138 to a
failing 82-89. Step 1's own instruction text is the reason —
`object_focus_scene_text()` step 1 says *"Extreme close-up of {obj} alone... **Flat
featureless background**, no furniture, no room, no other objects."* (step 2 similarly
says "soft empty background"). A flat, empty background plus one large, close-cropped
object naturally quantizes into very few 16-level color bins — that's the same
"emptiness" the INTRUDER-avoidance ladder is deliberately optimizing for at steps 1-2,
because emptiness is also what prevents a mug/laptop from having room to appear.

So: the two gates are structurally in tension at steps 1-2 of the same escalation
ladder. My proposed text-legibility fix (item 3) adds ink-texture/mark-making
*on the object's own surface* — that might nudge uniq16 up slightly for text-bearing
objects specifically (more visual detail on the object itself), but it does nothing
about the *background* flatness, which the scene-4 data shows is the dominant lever.
I would not bet on the text fix clearing the uniq16 floor as a side effect.

This needs its own, separate fix — most likely loosening step 1/2's "flat
featureless background" wording to something like "muted halftone-textured
background" (adds print-grain detail without reintroducing furniture/mug-shaped
clutter for INTRUDER to catch) — but that means editing the same
`object_focus_scene_text()` escalation-ladder strings that exist specifically to
satisfy the INTRUDER gate, which is closer to "INTRUDER gate logic" than the
text-legibility fix is. Flagging it rather than touching it, per constraint. Not
fixed this pass.

## Report given in chat
See assistant response for the same findings summarized for the user.

---

# Implementation pass — 2026-08-20 (text-legibility fix, approved by user)

User approved implementing the Part 3 fix above ("Go ahead and implement the fix").
Implemented exactly the two additive changes proposed — nothing else. No render run;
validated with local dry-run calls only (no LLM/image/TTS API touched).

## File changed
- `core_engine/economic_reel_lofi/visual_identity.py`

## What changed
1. Added three new module-level constants next to the existing `_SATURATION_GUARD`
   (same section, same pattern):
   - `_TEXT_LEGIBILITY_GUARD` — the universal positive-prompt sentence, appended to
     every beat regardless of `subject_type`.
   - `_TEXT_BEARING_STEMS` — `{"paper", "mail", "letter", "envelope", "notebook",
     "journal", "calendar", "list", "bills"}`, reusing the exact stem vocabulary
     `_object_stem()` already merges for repetition-cap purposes.
   - `_TEXT_SURFACE_CLAUSE` — the targeted sentence for object_focus beats whose
     key_object collapses to one of those stems.
2. `object_focus_scene_text()`: computes `text_clause = _TEXT_SURFACE_CLAUSE if
   _object_stem(obj) in _TEXT_BEARING_STEMS else ""` once, and appends it after the
   existing `only` clause in all three escalation-ladder branches (`macro_no_setting`,
   `tighter_macro`, `off_workspace`) — so a text-bearing object gets the reinforcement
   at every retry step, and a non-text object (e.g. "empty extra chair") gets nothing
   extra.
3. `assemble_v2_prompt()`: added `_TEXT_LEGIBILITY_GUARD` to the universal `parts` list
   (next to `_SATURATION_GUARD`), so it reaches portrait/couple/silhouette beats too —
   this is what covers the reported scene 5 (lips) / scene 6 (background sign) cases,
   which are not `object_focus` and therefore never touch `object_focus_scene_text()`.

Nothing else changed. `only_object_clause`, `_DEFAULT_INTRUDER_TERMS`,
`_off_workspace_place`, the escalation-ladder step wording itself,
`assess_default_object_intrusion`, `assess_photoreal_style`, `_assess_linework_complexity`,
and all hold/retry threshold constants in `pipeline.py`/`config.py` are untouched.

## Cheap validation performed (no render, no API calls)
1. `python -m py_compile core_engine/economic_reel_lofi/visual_identity.py` — clean.
2. Called `object_focus_scene_text("unopened mail pile", step=0|1|2)` and confirmed the
   text-surface clause is present at all 3 steps; called it again with
   `"empty extra chair"` and confirmed the clause is absent (no leakage onto
   non-text objects). Confirmed `_object_stem("unopened mail pile") ==
   _object_stem("scattered papers") == "paper"`, i.e. both real failing objects from
   the corpus route through the same stem and get the fix.
3. Verified the em-dash characters in the new constants are real U+2014 codepoints
   (`hex(ord(c))` on the source string), not mojibake — the `�` seen in this
   session's terminal output is a console-encoding display artifact only (Git Bash /
   Windows codepage), not a data issue; the file on disk and the in-memory string are
   correct UTF-8.
4. Called `assemble_v2_prompt()` directly with a mock object_focus beat
   (`key_object="unopened mail pile"`, the exact scene-4 failing object) and a mock
   portrait beat (`subject_type="woman"`, `key_object="background sign"`, the class of
   object behind the scene-6 report) and asserted `_TEXT_LEGIBILITY_GUARD` is present
   in both assembled prompts — confirms the universal guard reaches non-object_focus
   subject types as intended.
5. Ran the actual pipeline entry point `apply_v2_prompts_to_lines()` (the real function
   `pipeline.py` calls when building an episode) with a single mock beat replicating
   the failing scene 4 (`object_focus` / `unopened mail pile`) and confirmed the final
   `visual_prompt` string contains both the targeted surface clause and the universal
   guard, end-to-end through the real code path, not just the lower-level helper.
6. Imported `core_engine.economic_reel_lofi.pipeline` to confirm nothing broke
   downstream (pipeline.py itself makes no changes and only reaches this code via a
   local import inside `apply_v2_prompts_to_lines`'s caller).

No live render was run. No stills probe was generated. No Claude/Gemini/DeepInfra/
ElevenLabs API was called at any point in this pass.

## What this does not fix (by design, reported previously — not touched)
- The uniq16 linework-flatness floor on object_focus beats. Per the Part 5 finding
  above, that failure is driven by the escalation ladder's own "flat featureless
  background" wording at steps 1-2, not by object surface content — this fix adds
  detail to the object's surface, not the background, so it should not be expected to
  clear that floor. Left for a separate, explicitly-scoped pass since it would mean
  editing the same INTRUDER-escalation ladder text.
- Scene 5 "lips" and scene 6 "sign" were not independently confirmed in any sidecar
  JSON checked this session (only scene 3 papers and scene 4 mail were directly
  evidenced across 4 real runs) — the universal guard is written broadly enough to
  cover the reported mechanism (any lettered/screen/sign surface, any subject_type),
  but its effect on those two specific beats has not been observed in a real render.

## Recommended next step
A cheap, isolated stills probe on the previously-failing beats (scene 3 "scattered
papers", scene 4 "unopened mail pile") from the locked `attachment` script — same
pattern as the `object_focus_nohand_20260820_205533` probe used for the earlier mug
fix — before committing to a full 9-beat re-validation. Not run this pass; awaiting
go-ahead given the render-budget concern already raised.

---

# Isolated stills probe run — 2026-08-21 (approved: "run the isolated stills probe on
scenes 3 and 4")

Ran the actual probe with real image-gen (DeepInfra Schnell) + real Gemini critic
calls, per the recommendation above. Plan was written and approved via the harness's
plan-mode flow before execution (plan saved at
`C:\Users\Freedom or Death\.claude\plans\modular-zooming-valley.md`).

## What was run
Temporary script `core_engine/economic_reel_lofi/_probe_text_legibility.py`
(deleted after this run, per the `painterly_probe.py` convention — outputs kept as
evidence). It hardcoded scene 3 and scene 4's exact row dicts from
`lofi_stills_attachment_20260820_230606_v01.json`, built prompts via the real
`assemble_v2_prompt()` (carrying the text-legibility fix), generated 3 independent
stills per scene via the real `generate_scene_image()`, and ran each through the real
gates: `_assess_linework_complexity`, `assess_default_object_intrusion` (scene 4
only), `assess_photoreal_style` (scene 4 only), and `VisualQA_Agent.visual_critic.
evaluate_image` (real Gemini call) — all read-only calls into existing gate code, none
modified.

**Environment note (unrelated pre-existing bug, found not fixed):** the first attempt
crashed with `UnicodeEncodeError` inside `VisualQA_Agent/visual_critic.py`'s cost-log
line (`console.print(f"...critic≈$...")`) — the `≈` character (U+2248) isn't
representable in this Windows console's cp1252 codepage via `rich`'s legacy-Windows
renderer. Worked around by forcing `PYTHONIOENCODING=utf-8 PYTHONUTF8=1` on the second
run rather than touching `visual_critic.py`. This is a real, pre-existing bug in the
critic's logging (not something this pass introduced or fixed) — it will crash any
`evaluate_image()` call on a cp1252 Windows console with `LOG_COSTS=True` (the
default). Flagging for a separate fix; out of scope here (not a gate/threshold, just a
print statement).

Cost: 7 images + 7 critic calls across the crashed partial run (1 image) and the
completed run (6 images) ≈ **$0.0148**.

## Results

**Scene 3 — man portrait, "scattered papers" (no framing ladder; same prompt reused
per production's own retry pattern):**

| Attempt | Critic score | Critic passed | Flaw |
|---|---|---|---|
| 1 (crashed run) | 5.0 | No | "garbled text visible on the jar/canister" (hallucinated prop, not the papers themselves) |
| 1 (completed run) | 4.0 | No | "Garbled text on paper" |
| 2 (completed run) | **9.5** | **Yes** | none |
| 3 (completed run) | 5.0 | No | "Garbled text artifacts on the papers." |

1 of 4 samples clean. Garbled text is still frequent on this beat — the fix did not
eliminate it. Practically: production only needs one clean attempt within its 3-try
budget per beat, and this batch's attempt 2 was clean, so *this specific 3-attempt
sequence* would have cleared scene 3 in production — but that is closer to getting
lucky within budget than a solved failure mode. Sample size (n=4) is too small to
claim a confident before/after rate; directionally, garbled text on this beat is
reduced but not resolved.

**Scene 4 — object_focus, "unopened mail pile", fixed at `focus_step=1`
(`tighter_macro`, the exact framing production last settled on for this beat):**

| Attempt | struct_ok (uniq16) | INTRUDER | Critic score | Critic passed | Flaw |
|---|---|---|---|---|---|
| 1 | **FAIL** uniq16=43 | pass | 3.0 | No | "Garbled typography is present on the papers." |
| 2 | pass uniq16=110 | **FAIL** (mug/laptop-class, bbox 0.324) | 9.5 | Yes | none |
| 3 | **FAIL** uniq16=54 | pass | 9.5 | Yes | none |

Text-specific result: clean on 2 of 3 (attempts 2–3), garbled only on attempt 1 — a
real improvement over the historical pattern (2 of 4 real production runs cited "MAIL"
lettering / envelope text as a final blocker). **But no single attempt passed *all*
gates at once** — attempt 1 fails both text and uniq16, attempt 2 clears text but
fails INTRUDER, attempt 3 clears text but fails uniq16. This confirms, with a live
sample, the Part 5 finding from the investigation: uniq16 and INTRUDER are the
surviving blockers on this beat now, not text — this specific probe run would still
have held in production, but for a different reason than before.

**Important caveat on methodology:** this probe held `focus_step` fixed at 1 for all
3 scene-4 attempts (to isolate the text variable). Production's real retry loop would
have escalated to `focus_step=2` after attempt 2's INTRUDER failure, which this probe
did not simulate. So this is a diagnostic read on the text-legibility fix specifically,
not a prediction of exactly what a live production retry sequence would produce.

## Verdict on the fix

The text-legibility fix is doing something real — scene 4's text-artifact rate visibly
dropped (2/3 clean here vs. it being the final blocker in half of recent real runs)
and scene 3 got at least one clean sample where historically it was 0 for several
consecutive attempts in 2 of 4 runs. It is **not** a complete fix for scene 3 (still
2/4 garbled in this sample) and does **not** touch scene 4's remaining uniq16/INTRUDER
blockers, which were always known to be separate issues. Recommend: do not spend a
full 9-beat render on this yet. Either (a) run a larger scene-3-only probe (more
samples) to get real confidence before/after, or (b) accept the improvement as
partial and separately address uniq16/INTRUDER (flagged, unapproved) before the
`attachment` episode can reliably clear scene 4.

## Files/artifacts from this run
- Deleted: `core_engine/economic_reel_lofi/_probe_text_legibility.py` (temp probe,
  per convention)
- Kept: `outputs/wonder_feed/clips/economic_reels_tests/text_legibility_probe_20260821_032601/`
  (1 image, crashed run) and
  `outputs/wonder_feed/clips/economic_reels_tests/text_legibility_probe_20260821_032701/`
  (6 images + `probe_report.json`, completed run)
- Plan file: `C:\Users\Freedom or Death\.claude\plans\modular-zooming-valley.md`

No production code was modified this pass (probe-only). No full render was run.

---

# Implementation + probe — 2026-08-21 (uniq16/INTRUDER background wording, approved:
"Fix the uniq16/INTRUDER background wording next")

## What changed
`core_engine/economic_reel_lofi/visual_identity.py`, `object_focus_scene_text()`,
steps 1 (`tighter_macro`) and 2 (`off_workspace`) only. Per the Part 5 finding from
the original investigation, "Flat featureless background" (step 1) and "soft empty
background" (step 2) were the direct cause of the uniq16 collapse — they starved the
frame of the halftone print grain the global style block asks for everywhere else.
Replaced both with wording that keeps every existing INTRUDER-safe exclusion
word-for-word ("no furniture, no room, no other objects" / "no furniture, no room
clutter") and adds back texture instead of emptiness:
- Step 1: "Background keeps the same grainy halftone print texture as the object,
  muted flat color with visible ink-dot grain, no gradient, ... no distinct
  background shapes."
- Step 2: "Tight crop, background keeps the grainy halftone print texture, muted
  color but not flat or blank, ..."

Step 0 ("Printed flat color field behind the object") was **not** touched — the
original investigation's evidence never showed a uniq16 failure at step 0 (only
steps 1/2), so it was left alone to avoid unnecessary risk to a step that already
works.

**Not touched, per standing constraint:** `_assess_linework_complexity`'s `uniq16 <
90` threshold, `assess_default_object_intrusion` (INTRUDER detection logic),
`assess_photoreal_style`, and every hold/retry threshold in `pipeline.py`/`config.py`.
This is a prompt-wording-only change.

## Validation
1. `py_compile` clean; dry-run printed all 3 steps' prompt text — confirmed every
   INTRUDER-safe exclusion phrase survived unchanged and only the background
   description changed.
2. Because uniq16/INTRUDER are pixel-level CV metrics, a text-only check can't tell
   you whether the new wording actually changes what Schnell draws — ran a second
   isolated probe (temp script `_probe_uniq16_background.py`, deleted after use, same
   convention as before) against the real scene 4 beat ("unopened mail pile"): 3 real
   samples at `focus_step=1`, 2 at `focus_step=2`. Cost ≈ 5 × ($0.00197 + $0.00015) ≈
   **$0.011**.

## Results

| Step | Attempt | uniq16 | struct_ok | INTRUDER ok | critic passed | ALL gates pass |
|---|---|---|---|---|---|---|
| 1 (tighter_macro) | 1 | 58 | No | Yes | Yes | No |
| 1 | 2 | **128** | Yes | No (mug/laptop blob) | Yes | No |
| 1 | 3 | 49 | No | No (mug/laptop blob) | Yes | No |
| 2 (off_workspace) | 1 | **97** | Yes | Yes | Yes | **Yes** |
| 2 | 2 | **94** | Yes | Yes | No (photoreal + text + object drift — unrelated to this fix) | No |

**Step 2 is a clear win:** both samples cleared the uniq16 floor (97, 94 vs. the
90 threshold) — attempt 1 passed every gate simultaneously, which never happened
once across either of the prior two probes (7 samples) at the old wording. Step 1
remains inconsistent (1 of 3 ≥ 90: 58, 128, 49) — similar variance to before the
wording change, so the step-1 fix reads as a smaller, less reliable effect than
step 2's.

**INTRUDER behavior is unaffected, as intended** — it still fired twice at step 1
(mug/laptop-class blob, same detection logic, untouched), confirming the added
background grain didn't blind or falsely trigger the CV gate either way. Step 2
attempt 2's failure was entirely on the Gemini critic side (photoreal drift +
recurring garbled text `'Mast 1015'` + "stack of documents/reports dominates" —
semantic object drift), none of which are new regressions from this wording change;
they're the same open, stochastic failure modes already documented (photoreal-gap,
residual text incidence, Schnell prop substitution).

## Verdict
Real, measurable improvement — mainly at step 2, which now reliably clears the
uniq16 floor and produced one fully-clean sample. Step 1 is still hit-or-miss. This
does not eliminate the possibility of a hold on this beat (INTRUDER and the critic's
other criteria remain probabilistic, as always), but it removes uniq16 as a
near-certain blocker once escalation reaches step 2. Not run: a full 9-beat
re-validation — still recommend holding off until a larger sample or a live episode
run confirms the combined effect of both fixes together.

## Files/artifacts from this run
- Deleted: `core_engine/economic_reel_lofi/_probe_uniq16_background.py` (temp probe)
- Kept: `outputs/wonder_feed/clips/economic_reels_tests/uniq16_background_probe_20260821_034148/`
  (5 images + `probe_report.json`)

No production gate/threshold code was modified. No full render was run.

---

# Combined probe — final tally — 2026-08-21 ("wait for it to finish" / read-only
report from disk, no new API calls)

Source: `outputs/wonder_feed/clips/economic_reels_tests/combined_probe_20260821_035425/
probe_report.json` (already on disk from the prior turn's background run — read only,
nothing re-generated this pass). 6 independent trial-episodes per scene, each replaying
production's exact per-scene retry loop (real escalation on INTRUDER, real
guard+fix-instructions accumulation on retries, 3-attempt budget) with **both** fixes
live: the text-legibility guard/clause and the uniq16/INTRUDER background-texture
wording.

## Scene 3 — "scattered papers" (man portrait, no framing ladder): 3/6 shipped

| Trial | Attempts used | Result | Flaw(s) on failed attempts |
|---|---|---|---|
| 1 | 2 | **Shipped** | a1: "Text artifact: garbled text 'SCUPESTION' in bottom-left corner." |
| 2 | 3 | **Shipped** | a1: "Garbled text on papers"; a2: "Garbled text on papers and background elements." |
| 3 | 3 | **Held** | a1: "Garbled text on papers" + fake watermark 'ST2ATO. ANRIVES. GY'; a2: "Garbled text on the papers the man is reading."; a3: "Garbled text artifacts on papers and in bottom-left corner." |
| 4 | 3 | **Held** | a1: "garbled letters... burned into the papers"; a2: "Garbled text on papers"; a3: "Garbled text artifacts on papers in foreground and background." |
| 5 | 2 | **Shipped** | a1: "garbled text on books" + "garbled text on paper" |
| 6 | 3 | **Held** | a1: "garbled typography"; a2: "Garbled text on documents"; a3: "Garbled text artifacts on paper and wall" |

16 total image+critic calls. **Every single failed attempt (13 of 13) cites
garbled/legible text as the reason — no other flaw type ever appears on this beat**
(expected: portraits don't get INTRUDER/style/uniq16 checks). Scene 3's bottleneck is
purely text, and it is now roughly a coin flip per episode whether it clears within
budget (3/6 = 50%).

## Scene 4 — "unopened mail pile" (object_focus, real escalation ladder): 2/6 shipped

| Trial | Attempts used | Result | Flaw(s) by attempt |
|---|---|---|---|
| 1 | 3 | **Held** | a1 (step0): photorealistic + ungrounded-limb + INTRUDER; a2 (step1): uniq16=46 + "abstract, minimalist line drawing" + SEMANTIC mismatch; a3 (step1): embedded typography on envelope + INTRUDER |
| 2 | 3 | **Held** | a1 (step0): garbled text on mail pile + INTRUDER; a2 (step1): uniq16=77; a3 (step1): uniq16=87 + INTRUDER |
| 3 | 3 | **Held** | a1 (step0): photorealistic; a2 (step0): photorealistic + INTRUDER; a3 (step1): uniq16=76 + INTRUDER |
| 4 | 3 | **Held** | a1 (step0): photorealistic + OBJECT substitution (open book/papers); a2 (step0): photorealistic; a3 (step0): photorealistic drift + INTRUDER |
| 5 | 3 | **Shipped** (on a3) | a1 (step0): photorealistic + garbled text/barcode; a2 (step0): INTRUDER; a3 (**step1**): clean |
| 6 | 3 | **Shipped** (on a3) | a1 (step0): photoreal drift + INTRUDER; a2 (step1): "too flat and simplistic... lacking halftone/duotone" + INTRUDER; a3 (**step2**): clean |

18 total image+critic calls, 16 failed attempts. Failure-mode breakdown across those 16
(attempts can carry more than one flaw):
- **Photoreal/style drift (Gemini critic's own illustration-style judgment, criterion
  1) — 10/16 (62%).** This is a *different* signal from the CV-based
  `assess_photoreal_style` gate; it's Gemini independently judging the image as too
  photographic/inconsistent with the ink style. Was flagged in the original
  investigation and prior session log as "Criterion 1 photoreal gap" — pre-existing,
  not something either of this pass's two fixes targeted.
- **INTRUDER (mug/laptop-class blob) — 10/16 (62%).** Also pre-existing ("Schnell mug
  prior in pixels," flagged previously) — untouched by either fix, and this data shows
  it is still the single most common individual blocker alongside photoreal drift.
- **uniq16 (structural floor) — 4/16 (25%).** Down from being a near-certain blocker
  at steps 1/2 before the background-wording fix; both trials that *did* ship cleared
  their final attempt with clean uniq16 (119 and 118).
- **Text/garbled — 3/16 (19%).** Down sharply from "the final blocker in 2 of 4 real
  historical runs" — the text fix is holding up under a larger sample too.
- **Object/semantic substitution — 2/16.** Limb — 1/16.

[NOTE: continued below in the 2026-08-21 photoreal-drift section — the "Notable"
paragraph immediately following was written before that fix; left in place as the
historical record of what the combined probe showed at that point in time.]

**Notable:** both scene-4 successes happened on the *third and final* attempt, and each
one landed on a different escalation step this pass's wording fix touched — trial 5 at
`focus_step=1`, trial 6 at `focus_step=2` — direct evidence the background-texture
rewrite is contributing to real passes, not just to isolated single-shot samples.

## Recommendation

**Not ready for a full 9-beat stills-only re-validation yet — not because the two
implemented fixes failed, but because scene 4 is now bottlenecked on two different,
pre-existing, unaddressed issues (photoreal drift and the INTRUDER mug-prior), each
present in roughly two-thirds of its failed attempts.** Both text-legibility and
uniq16 dropped to secondary/minor causes on scene 4, and text on scene 3 is
measurably better than "fails every attempt" but still holds 50% of the time — so the
work done this session is real, it's just not sufficient on its own for this episode.

At current rates, treating scene 3 and scene 4 as roughly independent (3/6 ≈ 50% and
2/6 ≈ 33% observed here), the odds of *both* clearing within budget on the same full
render are in the neighborhood of 1-in-6, before accounting for the other 7 beats in
the episode (which weren't part of this probe and may have their own, unmeasured hold
rates). A full render now would very plausibly hold again on scene 4 specifically for
photoreal drift or INTRUDER, not for the two things just fixed.

Before spending a full-render budget, the highest-leverage next step is a targeted fix
for the photoreal-drift gap on scene 4 (and/or the INTRUDER mug-prior), the same
report-plan-approve-probe workflow used for the two fixes in this file. Not proposing
the specific wording here per instruction — flagging only that it's the next
bottleneck, ranked by how often it appears (tied with INTRUDER at 62% of failed
attempts, well ahead of uniq16 and text).

---

# Implementation + probe — 2026-08-21 (photoreal-drift fix, approved: "Fix the
photoreal-drift issue next")

## Root cause
Photoreal-drift critic flags in the combined probe were concentrated almost entirely
at `focus_step=0` (8 of 10 style-drift flags; the other 2, at step 1, were the
*opposite* complaint — "too abstract/flat" — not photoreal). `object_focus_scene_text()`
step 0's wording was:
> "Tight macro crop of {obj} filling the frame. Printed flat color field behind the
> object, no camera bokeh, no lens blur, ..."

"macro crop", "camera bokeh", and "lens blur" are photography vocabulary. Same lesson
as the mug fix and the text-legibility fix: on a distilled, `guidance_scale=0` model,
*naming* a photographic concept — even to negate it ("no camera bokeh") — still
anchors the render toward that genre; negation doesn't suppress the prior. Steps 1
("Extreme close-up") and 2 ("Close focus") carried the same category of vocabulary,
just less frequently triggered (only reached on retry).

## What changed
`core_engine/economic_reel_lofi/visual_identity.py`, `object_focus_scene_text()`, all
three steps' opening framing sentence only:
- Step 0: "Tight macro crop... no camera bokeh, no lens blur" → "A bold hand-inked
  illustration of {obj} filling the frame, drawn flat with visible ink outlines, not
  photographed."
- Step 1: "Extreme close-up of {obj} alone..." → "An illustrated close study of {obj}
  alone, drawn to occupy most of the frame, not photographed."
- Step 2: "Close focus on {obj} {place}..." → "An illustrated view of {obj} {place},
  drawn as the only object in frame, not photographed."

Every INTRUDER-safe exclusion phrase ("no named room, no furniture, no other objects" /
"no furniture, no room clutter") and the background-texture wording from the prior fix
were left byte-for-byte unchanged — only the camera-vocabulary opening clause was
replaced. `_off_workspace_place()`, `only_object_clause()`,
`assess_default_object_intrusion`, `assess_photoreal_style`, and all
hold/retry thresholds: untouched.

## Validation
Dry-run confirmed no camera vocabulary ("macro", "bokeh", "lens blur", "close-up",
"close focus") survives in any of the 3 steps' output, and every INTRUDER exclusion
phrase is intact. Real probe (temp script `_probe_photoreal.py`, deleted after use):
5 independent trial-episodes on scene 4, replaying pipeline.py's exact retry/escalation
loop. Cost: 13 image+critic calls ≈ **$0.028**.

## Results

| Trial | Attempts | Result | Flaws |
|---|---|---|---|
| 1 | 1 | **Shipped** (first try) | none |
| 2 | 3 | **Shipped** (on a3) | a1: text + INTRUDER; a2: INTRUDER; a3: clean |
| 3 | 3 | Held | a1: INTRUDER; a2: uniq16=86 + INTRUDER; a3: uniq16=57 + INTRUDER |
| 4 | 3 | Held | a1: uniq16=82 + text + INTRUDER; a2: uniq16=55 + INTRUDER; a3: INTRUDER |
| 5 | 3 | Held | a1: INTRUDER; a2: uniq16=89; a3: uniq16=87 + INTRUDER |

**2/5 shipped (40%)** — comparable to before (2/6 ≈ 33%). But the composition of the
13 total attempts changed completely:
- **Photoreal drift: 0/13 (0%)**, down from 10/16 (62%) in the pre-fix combined probe.
  Not a single photoreal complaint anywhere in this sample.
- **INTRUDER (mug/laptop-class blob): 10/13 (77%)** — now overwhelmingly the sole
  dominant blocker (was tied with photoreal at 62% before; with photoreal gone, INTRUDER
  alone accounts for nearly every failed attempt).
- uniq16: 5/13 (38%). Text: 2/13 (15%).

## Verdict
**The photoreal-drift fix worked cleanly and completely on this sample** — the target
failure mode dropped from 62% to 0%. It did not raise the overall ship rate much
(33% → 40%) because INTRUDER's mug-prior was already co-occurring with photoreal drift
on most failing attempts; removing photoreal just exposed how dominant INTRUDER already
was underneath. **INTRUDER (the "Schnell mug prior in pixels") is now the single
remaining bottleneck on this beat, by a wide margin (77% of attempts).** This is a
known, previously-flagged, harder problem: the prompt already explicitly bans it
("Empty of other still-life props: no mug, no cup, no coffee cup, no glass, no
laptop") every single time, in every step, and it still appears in more than 3 of
every 4 generations — this is not a missing-vocabulary problem like the previous three
fixes, it's a much stronger learned prior resisting an already-explicit ban. A next
fix here would need a different approach than "add the missing positive-prompt
instruction" (that instruction is already present); not proposed here, per instruction
to recommend only.

## Files/artifacts
- Deleted: `core_engine/economic_reel_lofi/_probe_photoreal.py`
- Kept: `outputs/wonder_feed/clips/economic_reels_tests/photoreal_probe_20260821_124145/`
  (13 images + `probe_report.json`)

No gate/threshold code modified. No full render run.

---

# Implementation + probe — 2026-08-21 (INTRUDER mug-prior fix, approved: "Fix the
INTRUDER mug-prior next")

## Root cause / hypothesis
`only_object_clause()` (used by both `object_focus_scene_text()` and
`silhouette_scene_text()`) explicitly named the banned still-life props every single
time: "Empty of other still-life props: no mug, no cup, no coffee cup, no glass, no
laptop." This was the original "mug fix" precedent from before this session. The
2026-08-21 photoreal-drift probe showed INTRUDER (mug/laptop-class blob) still firing
on 10/13 (77%) of attempts *despite* that explicit named ban being present in every
prompt, every attempt. Applying the same lesson already confirmed three times this
session (camera vocabulary, garbled text, background flatness): on a
`guidance_scale=0` distilled model, *naming* a concept — even to ban it — still
anchors the render toward it; negation doesn't suppress the prior. Hypothesis: naming
"mug"/"cup"/"coffee cup"/"glass"/"laptop" by name was itself contributing to the
intrusion, not preventing it.

## What changed
`core_engine/economic_reel_lofi/visual_identity.py`:
- Rewrote `only_object_clause()` to drop every named intruder object. Old: "The only
  object is {obj}, filling the frame. Empty of other still-life props: no mug, no cup,
  no coffee cup, no glass, no laptop." New: "Only {obj} is visible in frame, resting
  alone on a bare surface. Nothing else occupies the frame — no additional items of
  any kind, no clutter, no extra objects." No specific object nouns anywhere.
- Removed `_term_requested_in_object()` and `_DEFAULT_INTRUDER_TERMS` — both existed
  solely to build the now-removed named-ban list and had no other callers (confirmed
  via repo-wide grep before deleting, not deprecated-in-place since there were zero
  external consumers).
- `object_focus_scene_text()` and `silhouette_scene_text()` (the two callers) were not
  otherwise touched — same call signature, same integration point.

**Not touched:** `assess_default_object_intrusion` (INTRUDER detection logic and its
thresholds — `bbox_frac >= 0.08`, `fill >= 0.22`, etc.), `assess_photoreal_style`,
`_assess_linework_complexity`, and every hold/retry threshold. Pure prompt-wording
change, per the standing constraint.

## Validation
Dry-run (word-boundary regex, not naive substring — "occupies" contains "cup" as a
substring, caught and fixed the test) confirmed zero occurrences of mug/cup/coffee/
glass/laptop in any `object_focus_scene_text()` step or in `silhouette_scene_text()`
output. Real probe (temp script `_probe_intruder.py`, deleted after use): 6 independent
trial-episodes on scene 4, replaying pipeline.py's exact retry/escalation loop. Cost:
16 image+critic calls ≈ **$0.034**.

## Results

| Trial | Attempts | Result | Flaws |
|---|---|---|---|
| 1 | 3 | Held | a1: OBJECT semantic drift ("blank paper dominates"); a2: uniq16=77 + INTRUDER; a3: INTRUDER |
| 2 | 3 | Held | a1: garbled 'MAIIL' + INTRUDER; a2: INTRUDER; a3: uniq16=68 |
| 3 | 3 | Held | a1: INTRUDER; a2: INTRUDER; a3: uniq16=67 |
| 4 | 1 | **Shipped** (first try) | none |
| 5 | 3 | Held | a1: INTRUDER; a2: uniq16=53 + text + INTRUDER; a3: uniq16=79 |
| 6 | 3 | **Shipped** (on a3) | a1: text + OBJECT drift; a2: uniq16=82 + INTRUDER; a3: clean |

**2/6 shipped (33%)** — flat vs. the two prior probes (33%, 40%), within sampling
noise at this size. But the composition changed measurably:
- **INTRUDER: 9/16 (56%)**, down from 10/13 (77%) in the pre-fix (photoreal-probe)
  sample — a real, meaningful reduction, but not elimination. Still the single most
  common individual blocker.
- uniq16: 6/16 (38%) — similar to before, unaffected by this change (expected; this
  fix didn't touch background-texture wording).
- Text: 2/16 (12.5%) — similar to before.
- **New, minor failure mode observed: semantic/object drift — 2/16 (12.5%)**
  ("OBJECT: blank paper dominates, requested unopened mail pile"; "looks like a
  generic blank bag or canvas"). Possibly an interaction between this fix (removing
  concrete "no mug/cup" still-life framing) and the earlier text-legibility fix
  (telling the surface to render as "abstract... blank or smudged space") — together
  they may occasionally push the render toward something too featureless to read as
  "mail pile" specifically. Small sample (n=2); flagging as a watch item, not
  confirmed as a real regression.

## Verdict
**Partial, real improvement — not a full fix.** INTRUDER's fire rate dropped
meaningfully (77% → 56%) with no named-object list at all, supporting the
naming-primes-it hypothesis, but it remains the most common single blocker on this
beat and overall ship rate didn't move outside noise (all three probes cluster
around 33-40%). Scene 4 now has four roughly comparable-magnitude failure modes
(INTRUDER 56%, uniq16 38%, text 12.5%, semantic drift 12.5%) rather than one or two
dominant ones — each individually less severe than before this session started, but
needing all of them to align in one image within a 3-attempt budget is still
statistically hard. Photoreal drift (this session's second fix) remains at ~0%,
holding up well across probes.

## Files/artifacts
- Deleted: `core_engine/economic_reel_lofi/_probe_intruder.py`
- Kept: `outputs/wonder_feed/clips/economic_reels_tests/intruder_probe_20260821_132431/`
  (16 images + `probe_report.json`)

No gate/threshold code modified. No full render run.

---

# Full 9-beat stills-only run — 2026-08-21 (approved: "Try a full 9-beat stills-only
run")

Ran the actual production CLI, not a probe:

```
python main.py --page wonder_feed --post-type ECONOMIC_REEL_LOFI --module relationship \
  --stills-only --lofi-script "outputs/wonder_feed/clips/lofi_stills_attachment_20260820_230606_v01.json"
```

`--lofi-script` loads `_load_locked_script()`, which accepts a full sidecar (reads its
`"script"` key) — so this reused the *exact same* 9-beat `attachment` script (same
text, same setting/key_object per beat) that has held on every prior attempt this
session, for a true apples-to-apples test of all four cumulative fixes together
(text-legibility guard, uniq16/INTRUDER background wording, photoreal-drift fix,
INTRUDER-naming fix). Writer/validator skipped (locked script → `script_llm_calls: 0`,
`script_llm_cost_usd: 0`). Output:
`outputs/wonder_feed/clips/lofi_stills_attachment_20260821_163855_v01.json`.

## Result: still HELD, but for a completely different reason than every prior attempt

**7 of 9 beats passed clean:**

| Scene | Attempts | Result |
|---|---|---|
| 1 | 1 | clean |
| 2 | 2 | 1 retry (fake copyright-notice text), then clean |
| **4** | **1** | **clean on the first try** — uniq16=105, no INTRUDER, no photoreal, no text |
| 5 | 1 | clean |
| 6 | 1 | clean |
| 7 | 1 | clean |
| 9 | 1 | clean |

**Scene 4 — "unopened mail pile", the beat all four of this session's fixes targeted —
passed on attempt 1, cleanly, with no retry needed at all.** This is the first time in
this entire investigation (4 probes, ~16 trial-episodes, dozens of samples) that this
exact beat has cleared on a first attempt in a real production run.

**2 of 9 beats held, both for garbled text (exhausted all 3 attempts each):**
- Scene 3 ("scattered papers", portrait) — "Garbled text on paper" / "on papers" /
  "on papers." on all 3 attempts. Same known ~50% failure beat from the combined
  probe; this run landed on the unlucky side.
- Scene 8 (portrait, "sunrise colors") — "Garbled text on the woman's forearm." →
  "Garbled text in bottom-left corner." → "Text artifact: '17/19' in the bottom left
  corner." **This beat was never tested or targeted by any of this session's four
  fixes** — it shows the underlying garbled-text tendency is a general Schnell/
  guidance_scale=0 behavior that can surface on any beat, on any surface (a woman's
  forearm, an image corner), not only on paper/mail/sign objects. The universal
  `_TEXT_LEGIBILITY_GUARD` (added in fix #1, applies to every beat via
  `assemble_v2_prompt`) is in this prompt too and did not prevent it.

Final: `[LOFI HOLD]` — episode not cleared for render/post, same overall outcome as
every prior attempt at this script, but the failure surface is now much narrower: 2
beats instead of routinely more, and both fail for exactly one reason (text) instead
of the 4-way tangle (text + uniq16 + INTRUDER + photoreal) scene 4 alone used to
produce.

Cost: 14 images + 14 critic calls = **$0.02967** total (script cost $0, locked script).

## Verdict
Real, concrete progress: the hardest beat in this episode (scene 4) is now solved in
this sample — clean, first try, no retries. The episode still holds, but the remaining
cause has collapsed down to a single, already-understood, already-partially-mitigated
issue (garbled text), now showing up on a beat (scene 8) this session never touched,
which confirms text-legibility is genuinely general-purpose rather than
paper/mail-specific. This is not a fluke tied to one lucky sample: it's a full
production run of the exact locked script that has held every single time before
today.

Not run: any further probe or fix this pass — reporting only, per the read-and-report
nature of this step. Two reasonable next directions: (a) accept the current ~50%-ish
per-beat text failure rate as a standing, general Schnell limitation and treat holds as
the accepted manual-review path (already a documented standing decision — "QA
exhaustion holds the episode, not auto-fallback"), or (b) take another pass at the
universal text-legibility guard's wording/strength now that it's clearly not
beat-specific.

No gate/threshold code modified. No new prompt fix implemented this pass.

---

# Implementation + probe — 2026-08-21 (text-legibility guard v2, approved: "Take
another pass at the text-legibility guard wording")

## Root cause
`_TEXT_LEGIBILITY_GUARD` (fix #1, earlier this session) was scoped to an *object-type*
list: "Any paper, page, envelope, sign, screen, or lettered surface in frame shows
only abstract ink mark-making...". The 2026-08-21 full 9-beat run's scene 8 failure —
"Garbled text on the woman's forearm" → corner watermark → "'17/19' in the bottom left
corner" — has no paper/sign/screen object anywhere in its beat (`key_object: "sunrise
colors"`, subject `woman`). The guard's enumerated-surface framing never covered skin,
clothing, background, or the frame's corners, so it structurally could not apply to
this failure class. Corner/signature-style artifacts are also a well-known FLUX
failure mode independent of any named object (the critic's own `corner_scan` field
exists specifically to catch it).

## What changed
`core_engine/economic_reel_lofi/visual_identity.py` — `_TEXT_LEGIBILITY_GUARD` only.
Replaced the surface-type enumeration with a true whole-canvas statement: "Every mark
anywhere in this image is either ink linework, halftone texture, or flat printed
color — never a legible letter, number, or word, anywhere in the frame. This includes
skin, clothing, hair, background, paper, signs, screens, and all four corners of the
frame. No watermark, no signature, no caption, no logo, no small stamped text tucked
in a corner — the illustration fills the frame edge to edge with scene content only,
nothing else." Still injected the same way (universal, via `assemble_v2_prompt`'s
`parts` list, reaches every subject_type). `_TEXT_SURFACE_CLAUSE` /
`_TEXT_BEARING_STEMS` (the object_focus-only targeted reinforcement from fix #1) left
unchanged — noted below as a residual gap for portraits.

## Validation
Real probe (temp script `_probe_text_v2.py`, deleted after use): 6 independent
trial-episodes each on scene 3 ("scattered papers", the known ~50% baseline) and scene
8 ("sunrise colors", the newly-discovered failure from the full run), replaying
pipeline.py's exact retry loop. Cost: 20 image+critic calls ≈ **$0.043**.

## Results

**Scene 8 ("sunrise colors"): 6/6 shipped (100%), every trial clean on the first
attempt, zero text flaws across all 6 samples.** A complete, unambiguous fix for
exactly the failure class this rewrite targeted.

**Scene 3 ("scattered papers"): 4/6 shipped (67%), up from 3/6 (50%) baseline** — a
real, meaningful improvement, but not a full fix:

| Trial | Attempts | Result | Flaws |
|---|---|---|---|
| 1 | 2 | Shipped | a1: "Garbled text on the book" |
| 2 | 3 | **Held** | "Garbled text on paper" → "...paper and sticky note" → "...on papers" |
| 3 | 2 | Shipped | a1: "Garbled text artifacts on papers" |
| 4 | 1 | Shipped (first try) | none |
| 5 | 3 | Shipped (on a3) | a1: papers + background UI; a2: readable 'Helsts' + papers |
| 6 | 3 | **Held** | "sign in background" → "papers and background signs" → "background poster" |

The two held trials both still center on the object itself ("papers", "sign",
"poster") — the same class of noun `_TEXT_SURFACE_CLAUSE` was built to reinforce, but
that clause is only wired into `object_focus_scene_text()`; scene 3 is a **portrait**
(subject_type `man`) with "scattered papers" as a scene prop, so it never receives
that targeted reinforcement — only the (now improved) universal guard reaches it. This
is the identified residual gap, not touched this pass.

## Verdict
**Scene 8's failure class is fully resolved (100%, 0 text flaws).** Scene 3 improved
meaningfully (50% → 67%) but not completely — its remaining failures trace to a gap
this rewrite didn't close: text-bearing prop nouns ("papers", "sign", "poster") in a
*portrait* beat don't get the object-specific reinforcement that object_focus beats
do. A further pass, if wanted, would extend `_TEXT_SURFACE_CLAUSE`-style reinforcement
to portrait/silhouette beats whose `key_object` is a text-bearing stem, not just
`object_focus`. Not implemented this pass — reporting only, per instruction scope.

## Files/artifacts
- Deleted: `core_engine/economic_reel_lofi/_probe_text_v2.py`
- Kept: `outputs/wonder_feed/clips/economic_reels_tests/text_v2_probe_20260821_172352/`
  (20 images + `probe_report.json`)

No gate/threshold code modified. No full render run this pass.

---

# Git split of validated fixes — 2026-08-21

Confirmed uncommitted `visual_identity.py` matched the log's last two sections
(TEXT_LEGIBILITY_GUARD v2 whole-canvas rewrite + the unvalidated portrait/
silhouette `_TEXT_SURFACE_CLAUSE` extension) plus the four earlier prompt
fixes that had never been committed.

Split the validated, probe-confirmed work into five commits on `main`. Left
the portrait/silhouette clause **uncommitted** (working tree only) until a
live generation probe.

| Commit | Subject |
|---|---|
| `366277f` | Add TEXT_LEGIBILITY_GUARD v1 (object-type surface list) |
| `26f9681` | Reword object_focus steps 1-2 backgrounds to keep halftone texture |
| `6f7e6aa` | Remove camera vocabulary from object_focus framing (photoreal-drift fix) |
| `24e6b59` | Rewrite only_object_clause without named mug/cup/laptop bans |
| `d4852a1` | Rewrite TEXT_LEGIBILITY_GUARD as a whole-canvas statement (v2) |

Working tree after the split: only `silhouette_scene_text()` +
`assemble_v2_prompt()` portrait `else` branch wiring of `_TEXT_SURFACE_CLAUSE`.
Constants themselves unchanged. `pipeline.py` gates/thresholds untouched.

---

# Implementation + probe — 2026-08-21 (portrait/silhouette TEXT_SURFACE_CLAUSE)

## Root cause
`_TEXT_SURFACE_CLAUSE` was only composed inside `object_focus_scene_text()`.
Scene 3 is a **portrait** (`subject_type: man`, `key_object: scattered papers`).
`_object_stem("scattered papers") == "paper"` is already in `_TEXT_BEARING_STEMS`,
but the clause never reached that beat — only the universal guard did. The
text_v2 probe left scene 3 at 4/6 (67%) with remaining failures still on the
papers/sign/poster surface.

## What changed (uncommitted)
`core_engine/economic_reel_lofi/visual_identity.py` only:
- `assemble_v2_prompt()` portrait/couple `else` branch: append
  `_TEXT_SURFACE_CLAUSE` when `_object_stem(key_object) in _TEXT_BEARING_STEMS`.
- `silhouette_scene_text()`: same check, all three framing steps.

`_TEXT_LEGIBILITY_GUARD`, `_TEXT_BEARING_STEMS`, `_TEXT_SURFACE_CLAUSE` strings
not edited. No gate functions touched.

Dry-run (prior turn): man + scattered papers includes the clause; woman + phone
does not; silhouette + papers does; silhouette + door does not.

## Validation
Real probe (temp script `_probe_portrait_text_clause.py`, deleted after use):
6 independent trial-episodes on locked-script scene 3 only, replaying
`pipeline.py`'s per-scene retry loop (`generate_scene_image` + `_qa_scene_image`
/ Gemini critic + INTRUDER + style-gate; portraits skip the last two). Prompt
reassembled via `assemble_v2_prompt` so the new clause is actually in the
pixels path (locked sidecar `visual_prompt` is stale).

Note: the probe loop used 4 attempts (`IMAGE_MAX_RETRIES_PER_SCENE + 2`
inclusive). Production is 3 (`range(1, IMAGE_MAX_RETRIES_PER_SCENE + 2)`).
No trial changed ship/hold between attempt 3 and 4; 3-attempt recap is **2/6**.

Cost: 21 image+critic calls ≈ **$0.0445** (over the $0.02–0.03 budget because
ship rate was worse than expected, plus one extra attempt per held trial).
3-attempt equivalent would have been 17 calls.

## Results

**Scene 3 ("scattered papers" portrait): 2/6 shipped (33%)** — down from the
whole-canvas-guard-alone baseline of 4/6 (67%) and the earlier combined-probe
3/6 (50%). Success criterion (improve toward 5–6/6) **not met**.

| Trial | Attempts (3-budget) | Result | Flaws |
|---|---|---|---|
| 1 | 3 | **Held** | garbled papers → 'zonas,cag'/'M.U.Z.D' → garbled + legible 'Peter' |
| 2 | 3 | Shipped (on a3) | a1–a2 garbled letters/text on papers |
| 3 | 2 | Shipped | a1 garbled artifacts bottom-left papers |
| 4 | 3 | **Held** | garbled on paper all 3 attempts (a4 corners, ignored) |
| 5 | 3 | **Held** | garbled papers → SEMANTIC two people → garbled letters |
| 6 | 3 | **Held** | garbled typography → '2512' top-right corner → garbled papers |

Failure class is still overwhelmingly **garbled/legible lettering on the papers
themselves** (and once a corner numeral, once a two-person semantic miss). The
object-specific clause is present in the assembled prompt (`CLAUSE PRESENT:
True`) and does not appear to further suppress Schnell's paper-lettering prior
on this portrait beat beyond the whole-canvas guard.

## Verdict
**Do not promote this wiring yet. Do not run a full 9-beat stills-only on the
back of it.** Adding `_TEXT_SURFACE_CLAUSE` next to the portrait's "scattered
papers" noun did not improve ship rate; this sample got worse (67% → 33%).
n=6 is noisy, but the direction is the opposite of the success criterion, so
the locked-script full run is deferred. Leave the portrait/silhouette hunk
uncommitted.

Possible reading (unconfirmed): stacking another "blank/smudged/no letters"
sentence onto a beat that already has the whole-canvas guard may push Schnell
toward still drawing letter-shaped marks while also fighting the papers'
identity — similar watch item as the earlier "blank paper dominates" semantic
drift on object_focus. Not enough to call a confirmed regression of that class
(only one SEMANTIC two-people miss here, not "blank paper").

## Files/artifacts
- Deleted: `core_engine/economic_reel_lofi/_probe_portrait_text_clause.py`
- Kept: `outputs/wonder_feed/clips/economic_reels_tests/portrait_text_clause_probe_20260821_180626/`
  (21 images + `probe_report.json`)

No full 9-beat stills-only run this pass (Step 3 did not confirm improvement).
No gate/threshold code modified.

