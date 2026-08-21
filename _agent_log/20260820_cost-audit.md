# Cost Audit — 2026-08-20 (read-only, no scripts run, no API calls made)

## Files read
- core_engine/economic_reel_lofi/script_agent.py
- core_engine/economic_reel_lofi/validator_agent.py
- core_engine/economic_reel_lofi/pipeline.py (lines ~400-520, ~780-1070, plus grep hits)
- core_engine/economic_reel_lofi/config.py (grep for retry/scene constants)
- core_engine/economic_reel_lofi/visual_identity.py (lines ~1127-1290)
- core_engine/economic_reel_lofi/image_gen.py (full)
- config.py (root — get_best_claude_model, SAFE_CLAUDE_MODEL, ANTHROPIC_API_KEY)
- avatar_engine/providers/together_image.py (header consts + generate_image retry loop, lines ~1-60, ~757-1120)
- avatar_engine/providers/model_router.py (TOGETHER_IMAGE_COST_USD table, grep hits)
- avatar_engine/audio_engine.py (grep for cost/price/usd)
- VisualQA_Agent/visual_critic.py (grep for model/cost, lines ~570-700)
- VisualQA_Agent/config.py (lines 70-148)

## Part A — Script generation LLM call count

Single LLM step per attempt: `ScriptGeneratorAgent.generate_script()` in script_agent.py.
`ValidatorAgent.validate_script()` is **100% rule-based** (regex, dedup similarity via
embeddings/history lookup, banned-quote overlap) — despite the "no rubber-stamp incentive"
docstring implying a second LLM judge, it makes **zero API calls**. Good news: no hidden
validator-LLM cost.

Two nested retry loops around the one real LLM call, both burn real $ per iteration:
1. Inner, inside `generate_script()` (script_agent.py:1216): `for attempt in range(1,3)` —
   up to 2 Claude calls if the JSON response fails to parse.
2. Outer, in `pipeline._generate_validated_script()` (pipeline.py:441): `for attempt in
   range(1, SCRIPT_MAX_RETRIES+1)`, SCRIPT_MAX_RETRIES=3 (config.py:40) — up to 3 full
   `generate_script()` invocations if the regex validator rejects the script, feeding
   back rejection reasons as prompt feedback each time.
- **Worst case: 3 × 2 = 6 Claude Sonnet calls for one episode script.**
- **Typical/best case: 1 call** (valid JSON, validator passes first try).
- After 3 outer rejections, a deterministic zero-cost Python fallback (`_fallback_script`,
  no LLM) is used and validated once more — so cost is capped, not unbounded.
- Model: Claude Sonnet, resolved dynamically via `get_best_claude_model()` which calls
  `client.models.list()` (an Anthropic *metadata* endpoint — not billed per-token, but
  adds one extra network round-trip per attempt). Falls back to Gemini (cheap text chain)
  only if Claude fails outright (missing key / network / non-temperature errors).
- Prompt size: the thematic prompt template (script_agent.py `_thematic_script_prompt`)
  is a large fixed instruction block (~700-900 words) plus a variable detail/pool block —
  roughly 1200-1800 input tokens. `max_tokens=6000` is the output ceiling; realistic output
  (8-9 short beats + monologue + visual fields as JSON) is more like 600-1000 tokens.

**No redundant/duplicate LLM calls found** beyond the legitimate retry-on-rejection design
above. `enforce_concrete_beat_visuals()` IS called twice — once inside `generate_script()`
for thematic scripts (script_agent.py:1286), once again inside
`apply_v2_prompts_to_lines()` during image-prompt assembly (visual_identity.py:1274) — but
this function is pure Python (setting/object bookkeeping, no network call), so the
duplication has **zero $ cost impact**, just wasted CPU/redundant code path.

**Cost estimate (derived from code, not measured):** using public Claude Sonnet list
pricing as a rough yardstick (~$3/M input, ~$15/M output — not verified against this
account's actual billing), one call ≈ $0.015-0.02. Best case ≈ $0.02/episode for script
generation; worst case (6 calls) ≈ $0.09-0.12/episode. This is the ONLY LLM cost in the
pre-image part of the pipeline.

## Part B — Cost-tracking module coverage

The only place `est_cost_usd` is computed anywhere in the repo is pipeline.py lines
999-1057, and it **only fires inside the `stills_only` debug/smoke-test branch**
(`stills_only=True`, used for cheap QA runs without TTS/video encode). It captures:
- `img_cost = 0.00197 * n_image_calls` (hardcoded flat rate)
- `critic_cost = 0.00015 * n_critic_calls` (hardcoded flat rate)
- sum stored as `est_cost_usd` in the stills sidecar JSON, alongside raw
  `image_calls`/`critic_calls` counts.

**Coverage gaps — confirmed by grep across the whole repo:**
1. **Script generation (Claude/Gemini) is never counted, in either stills-only or full
   render paths.** This is the very first cost incurred per episode and the one this
   audit was asked to trace — it has zero visibility in any sidecar/log.
2. **TTS (ElevenLabs) is never counted.** `avatar_engine/audio_engine.py` has no cost
   constant and no per-call cost logging at all. The only mention of TTS cost anywhere is
   a hand-written inline comment in pipeline.py ("~$0.03-0.08" for 9 captions) used purely
   for a printed comparison message — not computed from actual character counts, not
   persisted anywhere.
3. **The full production render path (`stills_only=False` — the path that actually ships
   posted content) computes NO cost metadata at all.** The `est_cost_usd` key does not
   exist in that branch's `meta` dict. Cost visibility exists only for the throwaway
   dev/test mode, not for real production runs — i.e., the run that actually spends money
   is the one nothing gets logged for.

**Accuracy of what IS tracked:**
- `critic_cost` constant (0.00015) matches `VisualQA_Agent/config.py`'s
  `COST_GEMINI_FLASH_USD` exactly (duplicated literal, not imported, but currently
  consistent). It is a **flat per-call guess**, not derived from Gemini's actual
  `usage_metadata` (input/output token counts), which the API does return — no code in
  this repo captures that for cost purposes.
- `img_cost` constant (0.00197/call) is reasonably close to the real formula-based cost
  for this pipeline's actual image settings. `image_gen.py` requests 768×1344 @ 4 steps
  (LOFI_IMAGE_STEPS=4). DeepInfra's own formula (`estimate_deepinfra_schnell_cost_usd` in
  together_image.py: `$0.0005 × MP × steps`) gives 0.0005 × 1.032 × 4 ≈ **$0.00206** for
  that resolution — pipeline.py's hardcoded $0.00197 understates by ~5%. Minor, but it's
  a second, independently hardcoded duplicate of a formula that already exists and could
  just be called directly with the real width/height/steps instead of guessed.
- A **third, different** constant for the same FLUX-schnell call exists in
  `VisualQA_Agent/config.py`: `COST_FLUX_SCHNELL_USD = 0.003` — this is the old
  Together.ai flat rate (matches `_TOGETHER_COST_TABLE["flux.1-schnell"]` in
  model_router.py) from before the Deepinfra migration referenced in the latest commit
  ("model flux1 schnell from together.ai deprecated... replaced all calls for
  Deepinfra"). It's ~45% higher than the current real per-call cost. Not confirmed
  whether this constant is still read on the live economic_reel_lofi path (it's consumed
  by VisualQA_Agent/agent_loop.py and image_generator.py, which look like a separate/older
  agent-loop tool) — flagging as stale regardless of current call-site, since three
  different $ figures exist for what should be one number.
- **Retry-loop undercount risk:** `TogetherImageGenerator.generate_image()` (called from
  `image_gen.generate_scene_image()`) has its own internal retry loop
  (`_TOGETHER_MAX_RETRIES`, default 4, on 429/transient errors) *below* the point where
  pipeline.py increments `n_image_calls` (only +1 per outer pipeline attempt, regardless
  of how many internal retries fired underneath). If the provider bills any of those
  retried/failed sub-attempts, actual spend would be undercounted. Not verified against
  provider billing behavior — flagged as an open risk, not a confirmed bug.

## Recommendation (no code changes made — awaiting go-ahead)
1. Track script-generation LLM calls (count + model + rough token cost) alongside
   image/critic calls, and surface them in the *same* sidecar structure.
2. Compute `est_cost_usd` on the full production render path too, not only `stills_only`
   — that's the path that actually spends money.
3. Add a real TTS cost estimate driven by actual character counts sent to ElevenLabs.
4. Collapse the three different FLUX-schnell $/call constants (pipeline.py hardcode,
   VisualQA_Agent COST_FLUX_SCHNELL_USD, together_image.py's formula function) into one
   source of truth — ideally call `estimate_deepinfra_schnell_cost_usd(width, height,
   steps)` with the real values instead of a hardcoded literal.
5. Consider capturing Gemini's `usage_metadata` on the critic call for an exact cost
   instead of a flat per-call guess.

No code was modified. Waiting for direction on which of the above to act on.

---

# Implementation pass — 2026-08-20 (fixes #4, #1, #2)

Scope: implement audit recommendations #4 (collapse the 3 image-cost constants), #1
(track script-generation LLM calls/cost), #2 (compute est_cost_usd on the full render
path, not just stills_only). Explicitly out of scope this pass: #3 (TTS cost) and #5
(Gemini usage_metadata) — not touched.

## Additional files read this pass (beyond the audit read-list above)
- avatar_engine/providers/together_image.py (estimate_deepinfra_schnell_cost_usd exact
  signature/formula, lines 199-236)
- avatar_engine/providers/model_router.py (lines 55-174 — confirmed a 4th, broader
  multi-model cost table/router exists here; left untouched, see "Found but not touched")
- VisualQA_Agent/image_generator.py (lines 300-366 — confirmed the only other call site
  of COST_FLUX_SCHNELL_USD, via `config.estimate_flux_cost()`)
- config.py root (line 136 — found a 5th orphaned `TOGETHER_IMAGE_COST_USD` constant,
  unused elsewhere; left untouched, see below)
- core_engine/cost_tracker.py (full read — discovered a pre-existing, more mature
  `CostTracker` class used by other pipelines, e.g. agentic_pipeline. NOT wired into
  economic_reel_lofi. Deliberately not adopted this pass — see rationale below)
- core_engine/economic_reel_lofi/pipeline.py imports + `_generate_validated_script`
  call site (lines 1-60, 570-750) to find where script generation happens and how its
  result flows into every meta-writing branch

## Changes made

**core_engine/economic_reel_lofi/image_gen.py**
- Added `LOFI_IMAGE_WIDTH = 768`, `LOFI_IMAGE_HEIGHT = 1344` as named module constants
  (previously inline literal defaults on `generate_scene_image()`'s signature).
- `generate_scene_image()` now defaults `width`/`height` to these constants instead of
  bare literals, so pipeline.py's cost math and the actual image request can never drift
  apart.

**core_engine/economic_reel_lofi/script_agent.py**
- Added a per-episode LLM call log (`_SCRIPT_LLM_CALLS`, `reset_script_llm_call_log()`,
  `get_script_llm_call_log()`) — same pattern already used in this file for
  `_BATCH_USED_STRUCTURES`/`reset_batch_structure_ids()`.
- Added `_log_script_llm_call()`: rough chars/4 token estimate (same heuristic already
  used in core_engine/cost_tracker.py), costed at $3/M input + $15/M output tokens for
  Claude (the same figures used in the audit's Part A estimate), or Gemini's existing
  `GEMINI_FLASH_USD_PER_1M_TOKENS` (imported from core_engine.cost_tracker, not
  reinvented) for the Gemini fallback path.
- `_call_claude()` and `_call_gemini()` each call `_log_script_llm_call()` right after
  they have the response text (before the empty-text check in `_call_claude`, so even a
  billed-but-empty response is captured).

**core_engine/economic_reel_lofi/pipeline.py**
- Imports: added `LOFI_IMAGE_WIDTH/HEIGHT/STEPS` to the existing image_gen import, and
  `get_script_llm_call_log`/`reset_script_llm_call_log` to the existing script_agent
  import.
- `reset_script_llm_call_log()` called once per episode, right before the
  locked-script/generate-script branch.
- Right after a script is obtained (covers both the locked-script path, which logs 0
  calls, and the generated path), computed `script_llm_calls` / `script_llm_cost_usd`
  from the log — available to every downstream branch in this function.
- Replaced the stills-only-only, hardcoded `img_cost = 0.00197 * n_image_calls` /
  `critic_cost = 0.00015 * n_critic_calls` block with a computation that runs
  unconditionally (before the `if stills_only:` branch): `img_cost_per_call =
  estimate_deepinfra_schnell_cost_usd(LOFI_IMAGE_WIDTH, LOFI_IMAGE_HEIGHT,
  LOFI_IMAGE_STEPS)` (imported locally from `avatar_engine.providers.together_image`,
  matching this file's existing convention of local imports for avatar_engine/
  VisualQA_Agent deps), `critic_cost` now reads `COST_GEMINI_FLASH_USD` from
  `VisualQA_Agent.config` instead of a re-hardcoded `0.00015` literal.
- Added one `[LOFI cost]` print summarizing script/image/critic cost and the running
  total — fires on every path (stills_only, qa_hold, and full render) since it sits
  before the branch point.
- `est_cost_usd` (+ `image_calls`, `critic_calls`, `script_llm_calls`,
  `script_llm_cost_usd`) added to **all four** meta-writing branches: `script_only`
  (script cost only — no images happened), `stills_only` (unchanged total, now sourced
  from the single formula instead of a duplicate hardcode), `qa_hold` (previously had NO
  cost fields at all, despite real money having been spent on the images/critic that
  triggered the hold), and the full production success path (previously had NO cost
  fields at all — this was audit finding #2, the run that actually ships content had zero
  cost visibility).

**VisualQA_Agent/config.py**
- Added a comment marking `COST_FLUX_SCHNELL_USD` as deprecated (stale pre-DeepInfra-
  migration Together.ai rate), pointing at `estimate_deepinfra_schnell_cost_usd` as the
  real source of truth. Value left unchanged (0.003) since `estimate_flux_cost()` in this
  same file (line 232) and `image_generator.py:359` still read it, and rewiring that
  call site — a different pipeline/channel (VisualQA_Agent's own Master-Mei-style image
  generation loop, not economic_reel_lofi) — was not requested this pass. Deprecated in
  place rather than removed, per instructions.

## Found but not touched (reported, not fixed — out of scope this pass)
- `avatar_engine/providers/model_router.py`'s `TOGETHER_IMAGE_COST_USD` dict (line 67)
  and `estimate_image_route_cost()` — a broader multi-model (dev/pro/sdxl) cost router
  used elsewhere in avatar_engine. It already prefers `estimate_together_image_cost()`
  (together_image.py's own flat `_TOGETHER_COST_TABLE`, for models genuinely billed via
  Together, e.g. FLUX.1-dev LoRA) and only falls back to its local dict on exception.
  This is a different, legitimately-Together-billed code path, not one of the three
  constants named in fix #4 — left as-is.
- root `config.py:136` — a 5th, apparently orphaned `TOGETHER_IMAGE_COST_USD` float
  (env-overridable, default 0.003). No other file imports it (grepped repo-wide). Likely
  dead code; flagging for a future cleanup pass rather than touching it now (not part of
  the named three, no confirmed call site).
- `core_engine/cost_tracker.py` — a pre-existing, considerably more complete
  `CostTracker` class (image/text/audio/GPU tracking, telemetry JSON, category rollups)
  used by other pipelines (e.g. `core_engine/agentic_pipeline`) but never imported by
  economic_reel_lofi. Deliberately NOT adopted this pass: (a) it prices Claude Sonnet as
  a flat $0.005/call rather than per-token, which doesn't match the "same rough
  token-based estimate approach as the audit" the user asked for; (b) its own
  `image_flux_schnell` price is also the stale pre-DeepInfra $0.003 flat rate — adopting
  it here would have re-imported the exact staleness this pass is fixing; (c) wiring a
  new pipeline into a shared, threaded, telemetry-writing tracker class is a materially
  larger change than "purely additive/accounting" sidecar fields. Worth a dedicated future
  pass to fix `core_engine/cost_tracker.py`'s own FLUX pricing and consider migrating
  economic_reel_lofi onto it for consistency across the whole factory.

## Cheap validation performed (no render, no API calls)
1. `python -m py_compile` on all four edited files — clean.
2. Imported `core_engine.economic_reel_lofi.image_gen` and confirmed
   `estimate_deepinfra_schnell_cost_usd(LOFI_IMAGE_WIDTH, LOFI_IMAGE_HEIGHT,
   LOFI_IMAGE_STEPS)` returns `0.001969` (matches the pre-existing hardcoded 0.00197 to
   within rounding — confirms the single-sourced value didn't silently change pipeline
   behavior) and that `generate_scene_image`'s `width`/`height` defaults now resolve to
   the same named constants pipeline.py reads for cost math (via `inspect.signature`).
3. Exercised `script_agent.reset_script_llm_call_log()` /
   `_log_script_llm_call()` / `get_script_llm_call_log()` directly with synthetic
   prompt/output strings (no network calls) — confirmed calls accumulate, cost sums
   correctly (2 mock calls → $0.010528 total), and reset empties the log.
4. Imported `core_engine.economic_reel_lofi.pipeline` end-to-end (triggers all new
   top-level imports: `LOFI_IMAGE_WIDTH/HEIGHT/STEPS`, `get_script_llm_call_log`,
   `reset_script_llm_call_log`) — module loads without error, confirming no circular
   imports or missing-symbol errors from the new wiring.
5. Imported `VisualQA_Agent.config` and printed both `COST_GEMINI_FLASH_USD` and the
   now-annotated `COST_FLUX_SCHNELL_USD` — module still loads, values unchanged.
6. Grepped the edited region of pipeline.py for `stills_cost`/`media_cost`/
   `total_est_cost`/`img_cost`/`critic_cost`/`script_llm_*` to manually trace that: the
   new unconditional cost block runs before all three post-image branch points
   (stills_only / qa_hold / full success), `stills_cost` (branch-local) is only
   referenced inside the `stills_only` block, and all four meta dicts pick up
   `script_llm_calls`, `script_llm_cost_usd`, and `est_cost_usd` consistently.

No live render was run and no generation API (Claude, Gemini, DeepInfra/Together,
ElevenLabs) was called at any point in this pass.

## Confirmation: scope boundary held
Nothing in image generation, VisualQA/critic, or the object/style gate logic was
modified — every edit is either a new constant, a new bookkeeping/log function, or a new
key added to an existing metadata dict. No prompts, retry thresholds, quality
thresholds, or gate conditions changed. Fixes #3 (TTS cost) and #5 (Gemini
usage_metadata) were not implemented, as instructed.

## Files changed this pass
- core_engine/economic_reel_lofi/image_gen.py
- core_engine/economic_reel_lofi/script_agent.py
- core_engine/economic_reel_lofi/pipeline.py
- VisualQA_Agent/config.py
