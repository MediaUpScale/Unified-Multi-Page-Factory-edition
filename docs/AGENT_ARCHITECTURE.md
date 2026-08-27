# Agent Architecture

This document describes the orchestrator (`main.py`) and the individual
generation agents that produce a finished sequence-reel episode. It is
written as a reference for future editors: every agent lists its file /
function, its inputs and outputs, what runs before and after it, which
config / RAG source it pulls channel guidance from, and where the common
extension points live.

Scope: the AK (Ancient Knowledge) sequence-reel path is the reference
pipeline. Other channels reuse the same agents through the
`BaseChannelConfig` adapter interface.

Domain folders: `agents/writer/` (captions + LOFI scripts), `agents/rag/` (channel RAG + LOFI lookup), `agents/mcp/` (model API flows + `text_model.complete_script`), `agents/posting/` (YouTube / Pinterest / Facebook CLIs), `agents/media/` (audio/video/providers), `agents/orchestrator/` (agentic pipeline), `quality/VisualQA_Agent/`, `core/` (reel engines). Swap the LOFI script LLM with env `LOFI_SCRIPT_MODEL=deepseek|gemini|claude` (default: DeepSeek then Gemini).

---

## 1. High-level orchestrator sequence

`main.py::cli()` -> `main.py::_produce_variant_worker()` runs one full
variant per worker thread. Inside a variant, the phases are:

| Phase | Purpose | Primary agent / file |
|-------|---------|----------------------|
| A | Resolve topic, load channel config, pick episode angle | `main.py` variant setup + `core/interfaces/factory.py::ChannelFactory` |
| B | Generate the voiceover script (Gemini Flash) | `agents/writer/caption_engine.py::CaptionEngine.generate_sequence_voiceover` -> `core/reel_sequence_engine.py::build_sequence_script_prompt` |
| C | Synthesise narration + CTA audio (ElevenLabs / F5-TTS), measure real duration, pad if short | `main.py::_synthesize_sequence_voice_track` + `agents/media/audio_engine.py::generate_voiceover`, `pad_narration_to_minimum` |
| D | Plan per-episode visual sequence (subject / shot / lighting), plan per-act durations | `agents/media/prompt_alignment.py::plan_episode_visual_sequence` + `core/reel_sequence_engine.py::plan_bucket_act_durations` |
| E | Generate one image per act via FLUX (Together or remote-GPU) | `main.py` per-act loop -> `agents/media/providers/image_provider.py::get_image_adapter` |
| F | Generate background music (ElevenLabs Music v2) | `agents/media/audio_engine.py::generate_master_mei_soundscape` -> `generate_music_v2_bed` -> `generate_dynamic_music_prompt` |
| G | Compile final MP4 (motion, subtitles, audio mix) | `core/reel_sequence_engine.py::compile_sequence_reel` |
| H | Visual QA (per-image critic pass) | `quality/VisualQA_Agent/agent_loop.py` + `agents/orchestrator/agents/visual_qa.py` |

Every phase's output is the next phase's input; there is no cross-phase
branching. Phase D depends on Phase C's measured audio (the Round-5
lesson: never plan against a pre-TTS estimate).

**Duration scaling (Round 7 — Measure-Then-Correct 2026-08-15).** The
entire pipeline is driven end-to-end by `page_ctx.reel_duration`, which
honours the CLI `--video-length` override without clamping to the
channel's 80-90 s window. But — **the pipeline no longer PREDICTS how
long N words will take to speak using any stored constant.** Every
calibration bug this project has hit (2.25 vs 1.70 vs 3.15 vs 1.77 WPS,
across ElevenLabs / F5-TTS, across voice-preset changes) traced back to
that anti-pattern.

The new philosophy is **measure, don't predict**:

1. Phase B (script) generates ONE draft at the seed word count from
   `config.words_for_duration(reel_duration)`. Word count is a hint,
   not a gate.
2. Phase C (TTS) synthesises the narration ONCE. Measures the actual
   audio duration + observed WPS live for this voice + engine + speed.
3. If the resulting total (narration + 1 s silence + CTA audio) is
   outside ±15 % of `reel_duration`, does EXACTLY ONE corrective
   regeneration using the just-measured WPS (not any stored constant),
   TTS again, and accepts whatever comes back. CTA audio from
   attempt 1 is reused — its text hasn't changed.
4. Phase D (image bucket planner + hook/CTA) already syncs to the
   measured audio duration from step 2/3 — no change.

Hard ceiling: **at most 2 narration script-generation calls per reel**
(initial + at most 1 corrective), regardless of voice/engine, ever.
`NARRATION_WORDS_PER_SECOND` in `config.py` is now marked SEED-ONLY —
never build acceptance/rejection logic against it. Section 7 covers the
full flow.

---

## 2. Individual agents

### 2.1 Script / voiceover agent

**What it does.** Builds an LLM prompt that instructs Gemini Flash to
write an N-act voiceover script whose spoken duration matches the
target. Runs the prompt ONCE per call — no internal word-count retry
for non-warrior channels (Round 7 change). Duration accuracy is a
downstream measurement problem, not a prompt-level guessing problem.
The Master Mei (warrior) path still retries on strict word-floor
failure because MEI has structural act-length requirements that are
NOT duration-derived.

**Where it lives.**

* Prompt builder — `core/reel_sequence_engine.py::build_sequence_script_prompt`
* Caller / retry loop — `agents/writer/caption_engine.py::CaptionEngine.generate_sequence_voiceover`
* Orchestrator hook — `main.py::_synthesize_sequence_voice_track`

**Inputs.**

* `topic`, `niche`, `persona_voice` (from channel adapter / page config)
* `n_acts`, `duration_s`, `total_words_target` (from `config.words_for_duration()`)
* `narrative_mode` (`investigative` or `warrior_discipline`)
* `channel_name` (new; used by the RAG bridge -- see below)
* `previously_generated_hooks` (anti-repeat)

**Outputs.** A raw multi-act script string with `[ACT N]` markers.
Markers are stripped before TTS.

**RAG source.** `agents/rag/channel_rag.py::get_script_guidance(channel)`.
The bridge queries `quality/VisualQA_Agent/channel_rag.py::get_channel_rules` for
a `script_rules` section. That section does NOT exist in the RAG today —
the bridge logs a `CHANNEL_RAG_GAP | section=script_rules` warning and
falls back to the `target_audience_rules` field as a best-available
proxy.

**Runs before.** Phase C (TTS synthesis).
**Runs after.** Phase A (topic / angle resolution).

**Extension points.**

* Add a new narrative mode: extend the `_mode` branch in
  `build_sequence_script_prompt`.
* Add channel-specific script rules: add a `"script_rules"` section to
  the channel's entry in `quality/VisualQA_Agent/channel_rag.py::CHANNEL_DNA_SEED`
  (list of strings or a single string). No code change needed in the
  bridge or the prompt builder.

---

### 2.2 Voiceover / TTS agent

**What it does.** Sends the script to ElevenLabs (default) or RunPod
F5-TTS (when `ENABLE_REMOTE_GPU_WORKFLOWS=true`), measures the actual
audio duration, pads with silence when short of target.

**Where it lives.**

* Provider selection — `agents/mcp/model_api_flows.py::env_default_flow` +
  `core/remote_gpu_manager.py::is_remote_gpu_enabled("audio")`
* ElevenLabs path — `agents/media/audio_engine.py::generate_voiceover` /
  `generate_voiceover_with_timestamps`
* Padding — `agents/media/audio_engine.py::pad_narration_to_minimum`
* Sequential CTA stitch (narration + 1.0s silence + CTA) —
  `agents/media/audio_engine.py::_stitch_audio_sequential`
* Orchestrator hook — `main.py::_synthesize_sequence_voice_track`

**Inputs.** Cleaned script prose, target voice ID, CTA line, silence gap.
**Outputs.** `(narration_path, cta_path, stitched_path, measured_duration_s)`.

**RAG source.** None directly. The engine choice is env-driven
(`ENABLE_REMOTE_GPU_WORKFLOWS`), not channel-driven.

**Runs before.** Phase D (visual + timing planning).
**Runs after.** Phase B (script generation).

**Extension points.**

* Add a new TTS provider: extend `agents/mcp/model_api_flows.py` and add a new
  branch in `main.py::_synthesize_sequence_voice_track`.
* Change per-channel WPS calibration: replace
  `NARRATION_WORDS_PER_SECOND` in `config.py` (see Round-5 note — the
  timing planner is proportional, so this only affects word-count target
  math, not video length).

---

### 2.3 Image-prompt agent

**What it does.** For each act, builds one FLUX prompt whose composition
is guaranteed to be visually distinct from adjacent acts on THREE
independent dimensions: subject type, shot type, and lighting setup.
Sends the prompt to whichever image adapter is active (Together or
remote-GPU); both providers use the SAME prompt string.

**Where it lives.**

* Planner — `agents/media/prompt_alignment.py::plan_episode_visual_sequence`
* Per-act alignment block — `agents/media/prompt_alignment.py::build_aligned_visual_block`
* Orchestrator loop — `main.py`, AK sequence-reel branch (`for _act_i in range(_sr_act_start, _seq_n)`)
* Image adapter factory — `agents/media/providers/image_provider.py::get_image_adapter`
* Providers: `RemoteGPUImageAdapter` (`core/remote_gpu_manager.py`)
  and `GeminiImageAdapter` (`agents/media/providers/image_provider.py`)

**Inputs.**

* `n_acts`, `topic`, `seed` (episode stem)
* `channel_name` (drives RAG lookup)
* Per-act spoken snippet (from the act boundary splitter)

**Outputs.** One `Path` per act pointing at a generated PNG.

**Anti-monotony pools (module-level constants, immutable tuples — safe under
concurrent workers).**

| Pool | Length | File |
|------|--------|------|
| `_SUBJECT_POOL` — decides WHAT the image is about | 12 items | `agents/media/prompt_alignment.py` |
| `_SHOT_POOL` — decides the camera framing | 7 items | `agents/media/prompt_alignment.py` |
| `_LIGHTING_POOL` — decides the lighting setup | 7 items | `agents/media/prompt_alignment.py` |

The subject pool splits into two groups: the first 6 entries (Artifact
Macro, Wide Landscape Ruins, Anomalous Object, Monument Unusual Vantage,
Impossible Geological Detail, Human Explorer, plus Night Sky) are always
relevant; the remaining 5 entries (Underwater Ocean-Floor Ruins,
Volcanic / Geothermal, Forest / Jungle-Reclaimed, Desert / Arid Vastness,
Ice / Glacial) are *topic-gated* by a relevance predicate stored as the
3rd element of each pool tuple. Predicates match keywords in the episode
topic string (e.g. `_rel_underwater` matches "submerged", "atlantis",
"yonaguni"; `_rel_desert` matches "giza", "pyramid", "petra"). An entry
whose predicate returns False for the current episode is dropped from
this run's rotation entirely.

**Planner guarantees.**

* No two consecutive acts repeat the same subject type OR shot type OR
  lighting setup.
* Same `(seed, topic, channel_name)` -> same plan (reproducibility).
* Subject-pool items whose generated text contains a word in the
  RAG's `forbidden_tokens` (merged with a built-in doorway / portal /
  corridor / archway / gateway / chamber-interior / framed-through cap)
  are used at most `max_doorway_uses` times (default 1) per episode.
* Topic-relevance filter: pool entries with a topic-specific predicate
  are dropped when the predicate returns False (e.g. no
  underwater-ruins subject in a Sahara-desert episode). Safety floor
  restores the full pool if the filter collapses it below 2 entries.
* Topic-theme weighting (Round 6): when the topic-relevance filter keeps
  one or more environment-specific entries (i.e. entries whose predicate
  actually fires), that environment is BOOSTED to roughly 30–40 % of the
  act slots (target `round(n * 0.35)`, clamped to `n // 2`), still
  respecting the no-consecutive-repeat rule. Without this, a submerged-
  Atlantis reel would spend ≤ 1/N acts on underwater imagery because
  round-robin gives every retained pool entry equal weight.
* Concurrency-safe: every state used by `plan_episode_visual_sequence`
  is either a module-level immutable tuple (`_SUBJECT_POOL`,
  `_SHOT_POOL`, `_LIGHTING_POOL`, `_DEFAULT_DOORWAY_WORDS`) or a per-
  call local (`random.Random(sha256(seed))`, local shuffled lists).
  Safe under the batch orchestrator's `max_workers=N` variant thread
  pool.
* Independent of act count — works for 3 acts or 30.

**Compiled prompt order (main.py).**

```
{subject-pool text}   -- drives WHAT to draw; leads so FLUX weights it above the topic anchor
TOPIC ANCHOR: {geo-anchor identity-only phrase — place + material + era, no framing}.
{channel base style}.
{alignment block: SPOKEN BEAT + SHOT TYPE + LIGHTING + NOUN ANCHORS + SUBJECT CAP}
{parallax directive: DEPTH LAYERS foreground/background, no monument-frame composition}
{technical suffix}
{RAG image guidance block: mandatory_elements + lighting_style + forbidden_tokens}
```

`_GEO_ANCHORS` in `main.py` are IDENTITY-ONLY strings (place + material +
era). Camera framing, angle, and lighting were stripped in Round 5 —
templates that used to say "wide interior shot, dramatic divine light
beams" were re-anchoring FLUX to a centered-doorway default regardless
of what the shot / lighting pool assigned. The `TOPIC ANCHOR:` label was
also chosen deliberately over `VISUAL SUBJECT:` so it does not compete
with the leading subject-pool text for FLUX's attention.

**RAG source.** `agents/rag/channel_rag.py::get_image_guidance(channel)`.
The RAG's `mandatory_elements`, `forbidden_tokens`, and `visual_concepts`
sections are populated for the seeded channels (`master_mei`,
`lofi_economic`, `ancient_knowledge`). `lighting_style` was neutralized
in Round 5 for `ancient_knowledge` — it now defers to the per-act
lighting pool rather than forcing warm-amber backlight on every image.
The channel's `mandatory_elements` no longer contains "centrally framed"
for `ancient_knowledge` (Round 5 fix).

**Runs before.** Phase G (video compile).
**Runs after.** Phase D (episode visual planning).

**Extension points.**

* Add a new subject / shot / lighting pool item: append a tuple to
  `_SUBJECT_POOL`, `_SHOT_POOL`, or `_LIGHTING_POOL` in
  `agents/media/prompt_alignment.py`. The planner picks it up
  automatically.
* Add a new topic-relevance predicate: define a `_rel_xxx(topic: str) ->
  bool` helper near `_rel_underwater` in the same file, then append a
  3-tuple `(name, template_fn, _rel_xxx)` to `_SUBJECT_POOL`. The topic-
  theme weighting boost is automatic for any predicate that is not
  `_rel_always` or `_rel_night_sky` (both counted as universal).
* Add a new channel forbidden-token entry: add it to that channel's
  `forbidden_tokens` list in `quality/VisualQA_Agent/channel_rag.py::CHANNEL_DNA_SEED`;
  the planner's forbidden-word filter reads the RAG directly.
* Tune the doorway cap: pass `max_doorway_uses=N` to
  `plan_episode_visual_sequence` at the call site in `main.py`.
* Tune the theme-boost fraction: edit the `target_theme_count = max(2,
  int(round(n * 0.35)))` line inside `plan_episode_visual_sequence`. The
  clamp `min(target_theme_count, n // 2)` guarantees themed subjects
  never dominate more than half the reel.
* Add a new image provider: implement the `adapter.generate(prompt, ...)`
  interface and register in `agents/media/providers/image_provider.py::get_image_adapter`.
* Silence the per-image "Together LoRA skipped" warning on a specific
  channel: set `IMAGE_LORA_ENABLED = False` in that channel's
  `page_config.py`. The advisory is otherwise logged once per process
  per `(reason, model_id)` combination via
  `model_api_flows._lora_advisory_once` — never once per image call.

---

### 2.4 Video / motion agent

**What it does.** Assembles the per-act still images into a single MP4
with Ken-Burns-style motion, subtitles, audio mix (narration + CTA +
ambient + music + impact SFX), and cinematic post-FX (flicker, light
rays, dust particles, refraction).

**Where it lives.**

* Motion profile cycle (4 profiles keyed on `act_index % 4`) —
  `core/reel_sequence_engine.py::_MOTION_PROFILES`
* Per-act clip build + Ken Burns — same file, `_animate_act_frame` and
  friends
* Compiler entry point — `core/reel_sequence_engine.py::compile_sequence_reel`
* Orchestrator hook — `main.py`, Phase D block

**Inputs.**

* `image_paths` — one per act (from Phase E)
* `voice_audio` — stitched narration+silence+CTA (from Phase C)
* `ambient_audio`, `sfx_loop_audio`, `impact_sfx_audio`
* `act_durations` — pre-planned by `plan_bucket_act_durations`
* `strict_act_durations=True` — enforces planner-owned timing (video
  duration = sum of act_durations exactly)
* `page_id` — drives per-channel post-FX toggles

**Outputs.** Final MP4 at `output_path`.

**RAG source.** `agents/rag/channel_rag.py::get_motion_guidance(channel)`.
The RAG has no `motion_rules` section for any channel today — the bridge
logs a `CHANNEL_RAG_GAP | section=motion_rules` warning and returns "".
The motion cycle continues to use the hardcoded `_MOTION_PROFILES`.

**Duration invariant (Round-5).** `video_duration ==
narration_duration + silence_before_cta_s + cta_audio_s`. The planner
guarantees this by construction. The "last still absorbs remaining
audio" fallback is DELETED — never re-add it.

**Runs before.** Phase H (visual QA).
**Runs after.** Phase F (music generation).

**Extension points.**

* Add / remove a motion profile: edit `_MOTION_PROFILES` in
  `core/reel_sequence_engine.py`.
* Change per-channel post-FX defaults: adjust flags in the channel's
  `page_config.py` (`ENABLE_FLICKER`, `ENABLE_LIGHT_RAYS`, etc.).
* Add channel-owned motion rules: add a `"motion_rules"` section to
  `quality/VisualQA_Agent/channel_rag.py::CHANNEL_DNA_SEED`; the bridge already
  logs the block into `compile_sequence_reel`'s log so operators can
  see it was picked up. A future editor should read the block in
  `compile_sequence_reel` and use it to override `_MOTION_PROFILES` per
  channel.

---

### 2.5 Music-prompt agent

**What it does.** Generates a UNIQUE per-episode ElevenLabs music prompt
via a Gemini `music_prompt` LLM call. Enforces mood / tempo constraints
(mysterious, slow, ~55 BPM, no upbeat / EDM / triumphant language) by:

* Loading a channel-specific directive file
  (`channels_config/{channel}/prompts/music_prompt_directive.txt`) as
  the LLM's system directive.
* Scrubbing the LLM output against a forbidden-word list (`upbeat`,
  `fast tempo`, `EDM`, specific fast-BPM ranges, etc.) and injecting
  mandatory phrasing (`Slow tempo 55 BPM`, `minimal percussion, no
  driving beat`) if the LLM omitted them.

**Where it lives.**

* Prompt builder — `agents/media/audio_engine.py::generate_dynamic_music_prompt`
* Directive loader — `agents/media/audio_engine.py::_load_music_prompt_directive`
* Music bed compose — `agents/media/audio_engine.py::generate_music_v2_bed`
* Soundscape wrapper — `agents/media/audio_engine.py::generate_master_mei_soundscape`
* Orchestrator hook — `main.py`, Phase F

**Inputs.** `topic`, `subject`, `directive_path`, `style_profile`
(`warrior` for master_mei; `mystery` for ancient_knowledge),
`channel_name`.
**Outputs.** A single music-prompt string, then a rendered music bed MP3.

**RAG source.** `agents/rag/channel_rag.py::get_music_guidance(channel)`.
The RAG has no `music_rules` section for any channel today — the bridge
logs a `CHANNEL_RAG_GAP | section=music_rules` warning and falls back to
the `lighting_style` field as a mood proxy (dark cinematic light
strongly correlates with the mystery music profile the audio engine
already uses). The full directive file at
`channels_config/{channel}/prompts/music_prompt_directive.txt` remains
the primary creative-direction source until `music_rules` is populated.

**Runs before.** Phase G (video compile).
**Runs after.** Phase E (image generation).

**Extension points.**

* Add a new style profile: extend the `profile` branch in
  `generate_dynamic_music_prompt`.
* Change per-channel music directive: edit
  `channels_config/{channel}/prompts/music_prompt_directive.txt`.
* Add channel-owned music rules: add a `"music_rules"` section to
  `quality/VisualQA_Agent/channel_rag.py::CHANNEL_DNA_SEED`; the bridge
  automatically prepends the block to the LLM user block.

---

### 2.6 Research agent (for isolated channels only)

**What it does.** Provides the topic-scoped research context that the
CaptionEngine bundles into its LLM prompts for isolated channels
(currently `ancient_knowledge`, `master_mei`, `lofi_economic`).

**Where it lives.** Each channel adapter implements
`get_rag_context(topic)`:

* `channels_config/ancient_knowledge/channel_adapter.py::AncientKnowledgeAdapter.get_rag_context`
* `channels_config/master_mei/...` (via `channels_config/master_mei/page_config.py` and prompt files)
* `core/interfaces/legacy_adapter.py::get_rag_context` for
  non-isolated (Anna-shaped) channels — reads a PDF research corpus

**Inputs.** `topic` (string).
**Outputs.** A prompt-ready string that the CaptionEngine wraps in
`CHANNEL RAG BEGIN … CHANNEL RAG END` markers.

**RAG source.** Owned by the adapter. The AK adapter stitches both
sources: the VisualQA aesthetic DNA (`_visualqa_rules_block()`) plus its
own local visual-concept lookup (`_match_visual_concept()`).

**Runs before.** Phase B.
**Runs after.** Phase A.

**Extension points.**

* Add a new channel: implement `BaseChannelConfig` in
  `channels_config/{channel}/channel_adapter.py`. The abstract method
  `get_rag_context` must return the channel's own retrieval — no
  cross-channel leakage.

---

### 2.7 Visual QA agent

**What it does.** Runs post-generation critic passes on each image
(forbidden-token detection, mandatory-element presence, mask overlap,
etc.) and marks fails for regeneration.

**Where it lives.**

* Entry — `quality/VisualQA_Agent/agent_loop.py`
* Rules registry — `quality/VisualQA_Agent/channel_rag.py` (JSON + optional Chroma)
* Pipeline agent — `agents/orchestrator/agents/visual_qa.py`
* Prompt validator — `agents/media/visual_inspector.py`

**Inputs.** Per-act image paths, channel name.
**Outputs.** Pass/fail per image + optional regeneration list.

**RAG source.** Reads directly from `quality/VisualQA_Agent/channel_rag.py`
(does not go through the bridge — the bridge is for pre-generation
guidance, VisualQA is post-generation).

**Runs after.** Phase G.

**Extension points.**

* Add per-channel critic rules: extend
  `quality/VisualQA_Agent/channel_rag.py::CHANNEL_DNA_SEED` (same
  `forbidden_tokens`, `mandatory_elements` keys the bridge uses for the
  image agent — VisualQA and the image agent share the same source of
  truth).

---

## 3. Channel RAG — two mechanisms

The codebase has TWO complementary RAG mechanisms. They serve different
callers and should not be confused.

### 3.1 VisualQA aesthetic DNA store

* File: `quality/VisualQA_Agent/channel_rag.py`
* Storage: JSON at `quality/VisualQA_Agent/channel_dna_store.json` (source of
  truth) + optional ChromaDB mirror at `config.CHROMA_PERSIST_DIR`
  (set `VISUALQA_USE_CHROMA=1` to enable — off by default because Chroma
  native deps can segfault on some Python 3.14 / Windows builds)
* Scope: cross-channel registry of channel-level aesthetic rules
  (topic-independent)
* Public API: `get_channel_rules(name)`, `register_channel_dna`,
  `rules_as_prompt_block`, `seed_default_channels`
* Sections per channel today: `forbidden_tokens`, `mandatory_elements`,
  `lighting_style`, `target_audience_rules`, `visual_concepts`,
  `critic_profile` (some channels also carry `frame_lore`,
  `prompt_file_overrides`, `golden_prompt_anchor`)

### 3.2 Channel adapter per-topic RAG

* Files: `core/interfaces/channel.py::BaseChannelConfig` +
  `channels_config/{channel}/channel_adapter.py`
* Storage: adapter-owned (PDF corpus, JSON, prompt-template files,
  or a slice of the VisualQA DNA store — the AK adapter stitches
  the VisualQA block into its `get_rag_context` output)
* Scope: per-channel per-topic retrieval, used by
  `CaptionEngine.humanize_smart_bait` and the batch researcher
* Public API: `channel.get_rag_context(topic)`,
  `channel.get_visual_rules()`, `channel.get_system_prompt()`,
  `channel.get_ctas()`, `channel.get_narrative_angles(mode)`

### 3.3 The bridge (single choke-point for the 4 agents)

* File: `agents/rag/channel_rag.py`
* Purpose: one function per generation agent so each agent has a single
  place to fetch its channel-specific guidance (image / script / motion
  / music) without touching either RAG mechanism directly.
* Functions: `get_image_guidance`, `get_script_guidance`,
  `get_motion_guidance`, `get_music_guidance`, `get_full_bundle`
* Content-type coverage today (Final Round 2026-08-15):

  | Agent | RAG section queried | ancient_knowledge | master_mei | lofi_economic |
  |-------|--------------------|-------------------|------------|---------------|
  | Image-prompt | `mandatory_elements`, `lighting_style`, `forbidden_tokens`, `visual_concepts` | Populated | Populated | Populated |
  | Script / VO | `script_rules` | **Populated** (voice, word_budget, structural_pacing, hook_rule, cta, forbidden_phrases) | Gap → `target_audience_rules` fallback | Gap → `target_audience_rules` fallback |
  | Video / motion | `motion_rules` | **Populated** (motion_cadence, parallax_depth_directive, shot_variety_rule, post_fx_toggles) | Gap → hardcoded `_MOTION_PROFILES` fallback | Gap → hardcoded `_MOTION_PROFILES` fallback |
  | Music-prompt | `music_rules` | **Populated** (mood, sound_palette, mix_levels, generation, forbidden) | Gap → `lighting_style` mood proxy | Gap → `lighting_style` mood proxy |

  All three previously-gap sections for `ancient_knowledge` were populated
  in the Final Round; no `CHANNEL_RAG_GAP | channel=ancient_knowledge`
  warning fires anymore. `master_mei` and `lofi_economic` still emit gap
  warnings by design — their creative direction lives in dedicated prompt
  files inside their respective `channels_config/{channel}/prompts/`
  folders, not in the RAG.

* Gap-closing recipe: add the missing section to that channel's dict in
  `quality/VisualQA_Agent/channel_rag.py::CHANNEL_DNA_SEED` (as a string, a list
  of strings, or a dict). The bridge picks it up on the next call — no
  code change needed here. `_normalize_rules()` in the same file was
  extended so the three new sections (`script_rules`, `motion_rules`,
  `music_rules`) round-trip through the JSON store without being stripped.

---

## 4. File / function quick reference

| Concern | File | Symbol |
|---------|------|--------|
| Orchestrator entry | `main.py` | `cli()` |
| Per-variant worker | `main.py` | `_produce_variant_worker()` |
| Script prompt builder | `core/reel_sequence_engine.py` | `build_sequence_script_prompt()` |
| Script caller + retry | `agents/writer/caption_engine.py` | `CaptionEngine.generate_sequence_voiceover()` |
| TTS entry (ElevenLabs default) | `agents/media/audio_engine.py` | `generate_voiceover()`, `generate_voiceover_with_timestamps()` |
| TTS engine routing | `agents/mcp/model_api_flows.py` + `core/remote_gpu_manager.py` | `env_default_flow()`, `is_remote_gpu_enabled()` |
| Silence pad safety net | `agents/media/audio_engine.py` | `pad_narration_to_minimum()` |
| Visual planner (subject / shot / lighting) | `agents/media/prompt_alignment.py` | `plan_episode_visual_sequence()` |
| Per-act alignment block | `agents/media/prompt_alignment.py` | `build_aligned_visual_block()` |
| Timing planner (act durations) | `core/reel_sequence_engine.py` | `plan_bucket_act_durations()` |
| Image adapter factory | `agents/media/providers/image_provider.py` | `get_image_adapter()` |
| Motion profiles | `core/reel_sequence_engine.py` | `_MOTION_PROFILES` |
| Video compiler | `core/reel_sequence_engine.py` | `compile_sequence_reel()` |
| Music prompt builder | `agents/media/audio_engine.py` | `generate_dynamic_music_prompt()` |
| Music bed compose | `agents/media/audio_engine.py` | `generate_music_v2_bed()`, `generate_master_mei_soundscape()` |
| VisualQA critic loop | `quality/VisualQA_Agent/agent_loop.py` | `main()` |
| Channel adapter base | `core/interfaces/channel.py` | `BaseChannelConfig` |
| Channel adapter factory | `core/interfaces/factory.py` | `ChannelFactory.from_env()` |
| RAG bridge (single choke-point) | `agents/rag/channel_rag.py` | `get_image_guidance()`, `get_script_guidance()`, `get_motion_guidance()`, `get_music_guidance()`, `get_full_bundle()` |
| RAG aesthetic DNA (source) | `quality/VisualQA_Agent/channel_rag.py` | `CHANNEL_DNA_SEED`, `get_channel_rules()`, `register_channel_dna()`, `seed_default_channels()` |
| LOFI script writer | `agents/writer/script_agent.py` | `generate_script()`, `get_script_cost_log()` |
| LOFI script LLM | `agents/mcp/text_model.py` | `complete_script()` (`LOFI_SCRIPT_MODEL`) |
| LOFI RAG lookup | `agents/rag/lofi_rag.py` | `retrieve_script_seed()` |
| Pinterest CLI | `agents/posting/pinterest_main.py` | channel-scoped pin sync/schedule |
| YouTube existing-file CLI | `agents/posting/publish_existing.py` | upload/schedule existing MP4s |
| Global rate / duration constants | `config.py` | `NARRATION_WORDS_PER_SECOND`, `words_for_duration()`, `GEMINI_FLASH_CALLS`, `note_gemini_flash_call()` |

---

## 5. How to add a new channel

1. Create `channels_config/{new_channel}/`.
2. Implement `channels_config/{new_channel}/channel_adapter.py` against
   `BaseChannelConfig` (see `channels_config/ancient_knowledge/channel_adapter.py`
   as the reference).
3. Add a `page_config.py` in the same folder with the channel's
   duration, aspect-ratio, CTA text and post-FX toggles.
4. Register the channel's aesthetic DNA in
   `quality/VisualQA_Agent/channel_rag.py::CHANNEL_DNA_SEED`. At minimum
   populate `forbidden_tokens`, `mandatory_elements`, `lighting_style`
   and `target_audience_rules`. Optionally add `script_rules`,
   `motion_rules`, `music_rules` to close the content-type gaps for
   this channel — the bridge will pick them up automatically.
5. Add the channel to `ChannelFactory.from_env()` routing in
   `core/interfaces/factory.py`.
6. Set `ACTIVE_PAGE={new_channel}` and run a smoke episode.

## 6. How to add a new agent

1. Implement the agent as a function in its own module. It should take
   a `channel_name` string parameter and query the RAG bridge for its
   content-type block.
2. Add a `get_{agent}_guidance(channel)` function to
   `agents/rag/channel_rag.py` if the agent needs a new
   content-type section. Decide the RAG section key (e.g.
   `subtitle_rules`, `thumbnail_rules`), call `_flag_gap()` when the
   section is missing, and provide a sensible fallback.
3. Register the section in
   `quality/VisualQA_Agent/channel_rag.py::CHANNEL_DNA_SEED` for each channel
   that needs it.
4. Add the agent to Section 2 of this document and update Section 4's
   quick reference.

---

## 7. Two-tier pacing + duration scaling (Final Round 2026-08-15)

This section describes the current act-count and word-budget architecture
that replaced the fixed-cap / import-time-constant model in Round 6.

### 7.1 Two-tier act count

`core/reel_sequence_engine.py::compute_two_tier_act_count`
computes the total number of stills for any requested `duration_s`:

* **Tier 1 — fast-cut opening.** `min(duration_s, tier1_horizon_s)`
  seconds of narration @ `~tier1_seconds_per_act` per still (default
  4.5 s), capped at `tier1_max_acts` stills (default 20). Preserves the
  punchy rhythm of the first ~90 s regardless of total duration.
* **Tier 2 — slower body.** `duration_s - tier1_horizon_s` seconds
  beyond the horizon @ `~tier2_seconds_per_act` per still (default
  10 s). Zero when `duration_s <= tier1_horizon_s` — short reels stay
  100 % Tier 1.
* **No upper ceiling on total acts** — a 5-minute reel produces ~41
  stills, a 10-minute one ~65. The former `REEL_IMAGE_COUNT = 16`
  ceiling that silently clipped long-video renders in Round 6 has been
  removed from the AK page config entirely.

Sample scaling table (AK defaults, `min_acts=8`):

| Requested duration | Tier 1 acts | Tier 2 acts | Total acts | Narration words | Est. narration audio |
|-------------------:|------------:|------------:|-----------:|----------------:|---------------------:|
| 60 s | 14 | 0 | 14 | 146 | 65 s |
| 85 s (default) | 19 | 0 | 19 | 207 | 92 s |
| 90 s | 20 | 0 | 20 | 219 | 97 s |
| 180 s | 20 | 9 | 29 | 437 | 194 s |
| 300 s | 20 | 21 | 41 | 729 | 324 s |

### 7.2 Two-tier bucket clamp bands

`plan_bucket_act_durations()` accepts an optional `tier2_body_start`
parameter (body-index where Tier 2 begins). When passed:

* Body acts `[0..tier2_body_start-1]` clamp to `[body_min_s,
  body_max_s]` = `[2.5, 9.0]` s (Tier-1 band).
* Body acts `[tier2_body_start..]` clamp to `[tier2_body_min_s,
  tier2_body_max_s]` = `[8.0, 12.0]` s (Tier-2 band).
* Body budget is split proportionally to the sum of word-weights in
  each tier so short-word Tier-2 acts don't monopolise time.
* CTA stays its own final slot in either tier, unaffected.

The no-consecutive-repeat rules (subject / shot / lighting) and the
Round-6 topic-theme weighting apply across BOTH tiers — a long-video
Tier 2 act still gets the same variety-rotation treatment as a Tier 1
act, just held longer on screen.

`main.py` gates the two-tier path on `page_ctx.use_two_tier_pacing`
(opt-in via `USE_TWO_TIER_PACING = True` in the channel's `page_config.py`).
Other channels continue to use `compute_dense_act_count` or
`compute_hook_body_act_count` with their own `REEL_IMAGE_COUNT` ceiling.

### 7.3 The 5 duration-scaling fixes

Applied in the Final Round to make the pipeline actually honour long
`--video-length` requests:

| # | File / line | Before | After |
|---|-------------|--------|-------|
| 1 | `channels_config/ancient_knowledge/page_config.py:163` | `REEL_IMAGE_COUNT = 16` | Removed. `channel_loader.reel_image_count` returns 9999 (effective ∞) when `USE_TWO_TIER_PACING=True` and no explicit cap is set. The two-tier planner computes the actual count from `reel_duration`. |
| 2 | Same file, lines 164-166 | `REEL_NARRATION_WORDS = words_for_duration(REEL_DURATION_TARGET_MIN)` (import-time constant → pinned to 194) | Deleted. `channel_loader.reel_narration_words` is now a `@property` that returns `words_for_duration(self.reel_duration)` at read time — `--video-length 180` requests 437 words, `300` requests 729. Same for `reel_narration_min_words` and `reel_narration_max_words`. |
| 3 | `agents/writer/caption_engine.py:1720-1737` | `_min_words = words_for_duration(80.0)`; `_max_words = words_for_duration(80.0) + 20` (hardcoded to 80 s regardless of the `duration_s` argument!) | `_min_words = int(words_for_duration(duration_s))`; `_max_words = int(words_for_duration(duration_s) + max(20, duration_s * 0.25))` — proportional to the runtime target. |
| 4 | `config.py:270-272` | `SEQUENCE_VOICEOVER_MIN_WORDS: int = words_for_duration(80.0)` (import-time constant) | Deprecated but retained for backwards compat. New callable `config.sequence_voiceover_min_words(duration_s)` computes on demand. Caption engine no longer references the module-level scalar. |
| 5 | `main.py:3571` | `_words_tgt = page_ctx.reel_narration_words` (was reading a stale import-time value) | Unchanged — but now resolves correctly at runtime because of fix #2. |

### 7.3.5 Round 7 — Measure-Then-Correct (supersedes 7.3.1 WPS calibration)

Round 7 abandoned per-engine / per-voice calibration entirely. The
`NARRATION_WORDS_PER_SECOND` constant remains in `config.py` at 1.77
but is now marked **SEED ONLY** — the pipeline never uses it as a
duration gate.

**The new flow, per reel (AK path):**

1. **Early script** — `main.py::seq_reel block ~line 2652`. Single
   Gemini call at `total_words_target = words_for_duration(reel_duration)`
   (the seed). Whatever word count comes back is accepted. Uniqueness
   gate may loop this call up to `MAX_UNIQUENESS_RETRIES` times, but
   there is no per-attempt word-floor retry anymore.
2. **TTS + measure — attempt 1** —
   `main.py::_synthesize_sequence_voice_track`. Narration + CTA are
   synthesised (2 ElevenLabs calls, CTA once and only once). Total
   audio is measured. `observed_wps = narration_words /
   narration_seconds` is computed from the actual result.
3. **Correction decision** — if `|total_s - reel_duration| /
   reel_duration <= 0.15` (±15 %), accept attempt 1 and return.
4. **Corrective regen — attempt 2** — otherwise, compute
   `desired_narr_s = reel_duration - cta_dur - 1.0`, `corrected_words =
   round(desired_narr_s * observed_wps)`, call
   `caption_engine.generate_sequence_voiceover(...,
   total_words_target=corrected_words, ...)`. This is the second (and
   final) Gemini call for narration in the reel.
5. **TTS + measure — attempt 2** — synthesise the corrected
   narration. Reuse the CTA audio from attempt 1 (its text is
   unchanged). Re-stitch. Log the final observed WPS.
6. **Accept.** No third attempt, ever. Downstream (two-tier planner,
   hook/CTA slotting) picks up the measured audio duration and plans
   image act durations from that.

**Guarantees:**

* At most **2 Gemini script-generation calls per reel** for narration
  (early gen + optional corrective gen). The uniqueness gate is a
  separate axis and its retries are unaffected.
* At most **3 ElevenLabs TTS calls per reel** (narration attempt 1 +
  CTA once + narration attempt 2 only if corrective). Attempt 2 is
  skipped entirely when attempt 1 lands within tolerance.
* Zero dependency on a stored WPS constant for accuracy — the
  correction math uses the WPS that was just observed on this specific
  voice/engine/speed for this specific run. Changing the ElevenLabs
  voice, switching to F5-TTS, adjusting `speed`, or any other change
  that shifts the delivery rate will self-correct on the next reel
  without any config edit.
* Sanity guards: if `observed_wps` falls outside `[0.8, 4.0]` (broken
  measurement / near-zero narration duration) the code accepts attempt
  1 and logs a warning instead of running the correction math on garbage.

**What replaced what:**

| Old (Round 6 / Final Round) | New (Round 7) |
|-----------------------------|---------------|
| `_MAX_AUDIO_DURATION_RETRIES = 1` loop that alternated "VOICE DURATION SHORT → regenerate longer" and "VOICE DURATION LONG → trim" against a pre-calibrated `NARRATION_WORDS_PER_SECOND` | Single `_VOICE_TOLERANCE_FRACTION = 0.15` window + single corrective regen with observed WPS |
| Widening the 80-90 s acceptance window to `reel_duration × (0.92, 1.12)` for `--video-length` overrides | Same 15 % tolerance applies uniformly at every duration; the widening code is gone |
| Word-floor retry inside `caption_engine.generate_sequence_voiceover` (non-warrior path) | Removed. Word count is a seed hint. Warrior/MEI still retries on its structural length floor. |
| Word-floor retry in `main.py`'s early-script block | Removed. Single Gemini call, uniqueness gate stays. |
| `config.NARRATION_WORDS_PER_SECOND = 2.25 → 1.77` calibration edit as fix #6 | Constant left at 1.77 but reduced to a **seed only** — never gates anything |

The 5 duration-scaling fixes from Section 7.3 are still in place — they
made the pipeline actually honour `--video-length` end-to-end. Round 7
sits on top of them and removes the last remaining assumption (the
stored WPS gate).

### 7.4 Extension points

* Change the Tier-1 horizon or per-still cadence per channel: set
  `REEL_TIER1_HORIZON_S`, `REEL_TIER1_SECONDS_PER_ACT`,
  `REEL_TIER1_MAX_ACTS`, or `REEL_TIER2_SECONDS_PER_ACT` in that
  channel's `page_config.py`. The `channel_loader` properties expose
  them via `reel_tier1_*` / `reel_tier2_*` and `main.py` passes them
  into `compute_two_tier_act_count` at call time.
* Opt a new channel into two-tier pacing: set `USE_TWO_TIER_PACING =
  True` in its `page_config.py` and delete any `REEL_IMAGE_COUNT` entry
  so the ceiling doesn't fight the runtime planner.
* Widen or narrow the measure-then-correct tolerance: edit
  `_VOICE_TOLERANCE_FRACTION` in
  `main.py::_synthesize_sequence_voice_track` (default `0.15` =
  ±15 %). Tighter values force more corrective regens (higher LLM
  cost, tighter duration match); looser values accept more variance
  (fewer regens, slightly more duration slack).
* Adjust the observed-WPS sanity window: edit `_VOICE_MIN_OBSERVED_WPS`
  (0.8) and `_VOICE_MAX_OBSERVED_WPS` (4.0) in the same file. Only
  used to reject the correction path when the first-attempt
  measurement itself looks broken; too-narrow values will cause
  correction to no-op on unusual but real voices.
* Change the initial seed word count: edit
  `config.NARRATION_WORDS_PER_SECOND` (1.77 today, from the AK
  ElevenLabs baseline). Only affects the FIRST draft's rough length —
  the correction step self-adjusts regardless of this value.
* Add a new voice / TTS engine: no config change required. The
  measure-then-correct flow will discover its WPS on the first reel
  and apply the correction on attempt 2 automatically.
