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

---

## 1. High-level orchestrator sequence

`main.py::cli()` -> `main.py::_produce_variant_worker()` runs one full
variant per worker thread. Inside a variant, the phases are:

| Phase | Purpose | Primary agent / file |
|-------|---------|----------------------|
| A | Resolve topic, load channel config, pick episode angle | `main.py` variant setup + `core_engine/interfaces/factory.py::ChannelFactory` |
| B | Generate the voiceover script (Gemini Flash) | `avatar_engine/caption_engine.py::CaptionEngine.generate_sequence_voiceover` -> `core_engine/reel_sequence_engine.py::build_sequence_script_prompt` |
| C | Synthesise narration + CTA audio (ElevenLabs / F5-TTS), measure real duration, pad if short | `main.py::_synthesize_sequence_voice_track` + `avatar_engine/audio_engine.py::generate_voiceover`, `pad_narration_to_minimum` |
| D | Plan per-episode visual sequence (subject / shot / lighting), plan per-act durations | `avatar_engine/prompt_alignment.py::plan_episode_visual_sequence` + `core_engine/reel_sequence_engine.py::plan_bucket_act_durations` |
| E | Generate one image per act via FLUX (Together or remote-GPU) | `main.py` per-act loop -> `avatar_engine/providers/image_provider.py::get_image_adapter` |
| F | Generate background music (ElevenLabs Music v2) | `avatar_engine/audio_engine.py::generate_master_mei_soundscape` -> `generate_music_v2_bed` -> `generate_dynamic_music_prompt` |
| G | Compile final MP4 (motion, subtitles, audio mix) | `core_engine/reel_sequence_engine.py::compile_sequence_reel` |
| H | Visual QA (per-image critic pass) | `VisualQA_Agent/agent_loop.py` + `core_engine/agentic_pipeline/agents/visual_qa.py` |

Every phase's output is the next phase's input; there is no cross-phase
branching. Phase D depends on Phase C's measured audio (the Round-5
lesson: never plan against a pre-TTS estimate).

---

## 2. Individual agents

### 2.1 Script / voiceover agent

**What it does.** Builds an LLM prompt that instructs Gemini Flash to
write an N-act voiceover script whose spoken duration matches the target.
Runs the prompt, gates the output on word count, retries once if short,
then hands the cleaned prose to the TTS agent.

**Where it lives.**

* Prompt builder — `core_engine/reel_sequence_engine.py::build_sequence_script_prompt`
* Caller / retry loop — `avatar_engine/caption_engine.py::CaptionEngine.generate_sequence_voiceover`
* Orchestrator hook — `main.py::_synthesize_sequence_voice_track`

**Inputs.**

* `topic`, `niche`, `persona_voice` (from channel adapter / page config)
* `n_acts`, `duration_s`, `total_words_target` (from `config.words_for_duration()`)
* `narrative_mode` (`investigative` or `warrior_discipline`)
* `channel_name` (new; used by the RAG bridge -- see below)
* `previously_generated_hooks` (anti-repeat)

**Outputs.** A raw multi-act script string with `[ACT N]` markers.
Markers are stripped before TTS.

**RAG source.** `core_engine/channel_rag_bridge.py::get_script_guidance(channel)`.
The bridge queries `VisualQA_Agent/channel_rag.py::get_channel_rules` for
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
  the channel's entry in `VisualQA_Agent/channel_rag.py::CHANNEL_DNA_SEED`
  (list of strings or a single string). No code change needed in the
  bridge or the prompt builder.

---

### 2.2 Voiceover / TTS agent

**What it does.** Sends the script to ElevenLabs (default) or RunPod
F5-TTS (when `ENABLE_REMOTE_GPU_WORKFLOWS=true`), measures the actual
audio duration, pads with silence when short of target.

**Where it lives.**

* Provider selection — `model_api_flows.py::env_default_flow` +
  `core_engine/remote_gpu_manager.py::is_remote_gpu_enabled("audio")`
* ElevenLabs path — `avatar_engine/audio_engine.py::generate_voiceover` /
  `generate_voiceover_with_timestamps`
* Padding — `avatar_engine/audio_engine.py::pad_narration_to_minimum`
* Sequential CTA stitch (narration + 1.0s silence + CTA) —
  `avatar_engine/audio_engine.py::_stitch_audio_sequential`
* Orchestrator hook — `main.py::_synthesize_sequence_voice_track`

**Inputs.** Cleaned script prose, target voice ID, CTA line, silence gap.
**Outputs.** `(narration_path, cta_path, stitched_path, measured_duration_s)`.

**RAG source.** None directly. The engine choice is env-driven
(`ENABLE_REMOTE_GPU_WORKFLOWS`), not channel-driven.

**Runs before.** Phase D (visual + timing planning).
**Runs after.** Phase B (script generation).

**Extension points.**

* Add a new TTS provider: extend `model_api_flows.py` and add a new
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

* Planner — `avatar_engine/prompt_alignment.py::plan_episode_visual_sequence`
* Per-act alignment block — `avatar_engine/prompt_alignment.py::build_aligned_visual_block`
* Orchestrator loop — `main.py`, AK sequence-reel branch (`for _act_i in range(_sr_act_start, _seq_n)`)
* Image adapter factory — `avatar_engine/providers/image_provider.py::get_image_adapter`
* Providers: `RemoteGPUImageAdapter` (`core_engine/remote_gpu_manager.py`)
  and `GeminiImageAdapter` (`avatar_engine/providers/image_provider.py`)

**Inputs.**

* `n_acts`, `topic`, `seed` (episode stem)
* `channel_name` (drives RAG lookup)
* Per-act spoken snippet (from the act boundary splitter)

**Outputs.** One `Path` per act pointing at a generated PNG.

**Anti-monotony pools (module-level constants, immutable tuples — safe under
concurrent workers).**

| Pool | Length | File |
|------|--------|------|
| `_SUBJECT_POOL` — decides WHAT the image is about | 12 items | `avatar_engine/prompt_alignment.py` |
| `_SHOT_POOL` — decides the camera framing | 7 items | `avatar_engine/prompt_alignment.py` |
| `_LIGHTING_POOL` — decides the lighting setup | 7 items | `avatar_engine/prompt_alignment.py` |

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

**RAG source.** `core_engine/channel_rag_bridge.py::get_image_guidance(channel)`.
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
  `avatar_engine/prompt_alignment.py`. The planner picks it up
  automatically.
* Add a new topic-relevance predicate: define a `_rel_xxx(topic: str) ->
  bool` helper near `_rel_underwater` in the same file, then append a
  3-tuple `(name, template_fn, _rel_xxx)` to `_SUBJECT_POOL`. The topic-
  theme weighting boost is automatic for any predicate that is not
  `_rel_always` or `_rel_night_sky` (both counted as universal).
* Add a new channel forbidden-token entry: add it to that channel's
  `forbidden_tokens` list in `VisualQA_Agent/channel_rag.py::CHANNEL_DNA_SEED`;
  the planner's forbidden-word filter reads the RAG directly.
* Tune the doorway cap: pass `max_doorway_uses=N` to
  `plan_episode_visual_sequence` at the call site in `main.py`.
* Tune the theme-boost fraction: edit the `target_theme_count = max(2,
  int(round(n * 0.35)))` line inside `plan_episode_visual_sequence`. The
  clamp `min(target_theme_count, n // 2)` guarantees themed subjects
  never dominate more than half the reel.
* Add a new image provider: implement the `adapter.generate(prompt, ...)`
  interface and register in `avatar_engine/providers/image_provider.py::get_image_adapter`.
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
  `core_engine/reel_sequence_engine.py::_MOTION_PROFILES`
* Per-act clip build + Ken Burns — same file, `_animate_act_frame` and
  friends
* Compiler entry point — `core_engine/reel_sequence_engine.py::compile_sequence_reel`
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

**RAG source.** `core_engine/channel_rag_bridge.py::get_motion_guidance(channel)`.
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
  `core_engine/reel_sequence_engine.py`.
* Change per-channel post-FX defaults: adjust flags in the channel's
  `page_config.py` (`ENABLE_FLICKER`, `ENABLE_LIGHT_RAYS`, etc.).
* Add channel-owned motion rules: add a `"motion_rules"` section to
  `VisualQA_Agent/channel_rag.py::CHANNEL_DNA_SEED`; the bridge already
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

* Prompt builder — `avatar_engine/audio_engine.py::generate_dynamic_music_prompt`
* Directive loader — `avatar_engine/audio_engine.py::_load_music_prompt_directive`
* Music bed compose — `avatar_engine/audio_engine.py::generate_music_v2_bed`
* Soundscape wrapper — `avatar_engine/audio_engine.py::generate_master_mei_soundscape`
* Orchestrator hook — `main.py`, Phase F

**Inputs.** `topic`, `subject`, `directive_path`, `style_profile`
(`warrior` for master_mei; `mystery` for ancient_knowledge),
`channel_name`.
**Outputs.** A single music-prompt string, then a rendered music bed MP3.

**RAG source.** `core_engine/channel_rag_bridge.py::get_music_guidance(channel)`.
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
  `VisualQA_Agent/channel_rag.py::CHANNEL_DNA_SEED`; the bridge
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
* `core_engine/interfaces/legacy_adapter.py::get_rag_context` for
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

* Entry — `VisualQA_Agent/agent_loop.py`
* Rules registry — `VisualQA_Agent/channel_rag.py` (JSON + optional Chroma)
* Pipeline agent — `core_engine/agentic_pipeline/agents/visual_qa.py`
* Prompt validator — `avatar_engine/visual_inspector.py`

**Inputs.** Per-act image paths, channel name.
**Outputs.** Pass/fail per image + optional regeneration list.

**RAG source.** Reads directly from `VisualQA_Agent/channel_rag.py`
(does not go through the bridge — the bridge is for pre-generation
guidance, VisualQA is post-generation).

**Runs after.** Phase G.

**Extension points.**

* Add per-channel critic rules: extend
  `VisualQA_Agent/channel_rag.py::CHANNEL_DNA_SEED` (same
  `forbidden_tokens`, `mandatory_elements` keys the bridge uses for the
  image agent — VisualQA and the image agent share the same source of
  truth).

---

## 3. Channel RAG — two mechanisms

The codebase has TWO complementary RAG mechanisms. They serve different
callers and should not be confused.

### 3.1 VisualQA aesthetic DNA store

* File: `VisualQA_Agent/channel_rag.py`
* Storage: JSON at `VisualQA_Agent/channel_dna_store.json` (source of
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

* Files: `core_engine/interfaces/channel.py::BaseChannelConfig` +
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

* File: `core_engine/channel_rag_bridge.py`
* Purpose: one function per generation agent so each agent has a single
  place to fetch its channel-specific guidance (image / script / motion
  / music) without touching either RAG mechanism directly.
* Functions: `get_image_guidance`, `get_script_guidance`,
  `get_motion_guidance`, `get_music_guidance`, `get_full_bundle`
* Content-type coverage today:

  | Agent | RAG section queried | Populated? |
  |-------|--------------------|-----------|
  | Image-prompt | `mandatory_elements`, `lighting_style`, `forbidden_tokens`, `visual_concepts` | Yes for all 3 seeded channels |
  | Script / VO | `script_rules` | **No — gap** (falls back to `target_audience_rules`) |
  | Video / motion | `motion_rules` | **No — gap** (returns empty; motion cycle uses hardcoded defaults) |
  | Music-prompt | `music_rules` | **No — gap** (falls back to `lighting_style` mood proxy; directive file remains primary source) |

* Gap-closing recipe: add the missing section to that channel's dict in
  `VisualQA_Agent/channel_rag.py::CHANNEL_DNA_SEED` (as a string, a list
  of strings, or a dict). The bridge picks it up on the next call — no
  code change needed here.

---

## 4. File / function quick reference

| Concern | File | Symbol |
|---------|------|--------|
| Orchestrator entry | `main.py` | `cli()` |
| Per-variant worker | `main.py` | `_produce_variant_worker()` |
| Script prompt builder | `core_engine/reel_sequence_engine.py` | `build_sequence_script_prompt()` |
| Script caller + retry | `avatar_engine/caption_engine.py` | `CaptionEngine.generate_sequence_voiceover()` |
| TTS entry (ElevenLabs default) | `avatar_engine/audio_engine.py` | `generate_voiceover()`, `generate_voiceover_with_timestamps()` |
| TTS engine routing | `model_api_flows.py` + `core_engine/remote_gpu_manager.py` | `env_default_flow()`, `is_remote_gpu_enabled()` |
| Silence pad safety net | `avatar_engine/audio_engine.py` | `pad_narration_to_minimum()` |
| Visual planner (subject / shot / lighting) | `avatar_engine/prompt_alignment.py` | `plan_episode_visual_sequence()` |
| Per-act alignment block | `avatar_engine/prompt_alignment.py` | `build_aligned_visual_block()` |
| Timing planner (act durations) | `core_engine/reel_sequence_engine.py` | `plan_bucket_act_durations()` |
| Image adapter factory | `avatar_engine/providers/image_provider.py` | `get_image_adapter()` |
| Motion profiles | `core_engine/reel_sequence_engine.py` | `_MOTION_PROFILES` |
| Video compiler | `core_engine/reel_sequence_engine.py` | `compile_sequence_reel()` |
| Music prompt builder | `avatar_engine/audio_engine.py` | `generate_dynamic_music_prompt()` |
| Music bed compose | `avatar_engine/audio_engine.py` | `generate_music_v2_bed()`, `generate_master_mei_soundscape()` |
| VisualQA critic loop | `VisualQA_Agent/agent_loop.py` | `main()` |
| Channel adapter base | `core_engine/interfaces/channel.py` | `BaseChannelConfig` |
| Channel adapter factory | `core_engine/interfaces/factory.py` | `ChannelFactory.from_env()` |
| RAG bridge (single choke-point) | `core_engine/channel_rag_bridge.py` | `get_image_guidance()`, `get_script_guidance()`, `get_motion_guidance()`, `get_music_guidance()`, `get_full_bundle()` |
| RAG aesthetic DNA (source) | `VisualQA_Agent/channel_rag.py` | `CHANNEL_DNA_SEED`, `get_channel_rules()`, `register_channel_dna()`, `seed_default_channels()` |
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
   `VisualQA_Agent/channel_rag.py::CHANNEL_DNA_SEED`. At minimum
   populate `forbidden_tokens`, `mandatory_elements`, `lighting_style`
   and `target_audience_rules`. Optionally add `script_rules`,
   `motion_rules`, `music_rules` to close the content-type gaps for
   this channel — the bridge will pick them up automatically.
5. Add the channel to `ChannelFactory.from_env()` routing in
   `core_engine/interfaces/factory.py`.
6. Set `ACTIVE_PAGE={new_channel}` and run a smoke episode.

## 6. How to add a new agent

1. Implement the agent as a function in its own module. It should take
   a `channel_name` string parameter and query the RAG bridge for its
   content-type block.
2. Add a `get_{agent}_guidance(channel)` function to
   `core_engine/channel_rag_bridge.py` if the agent needs a new
   content-type section. Decide the RAG section key (e.g.
   `subtitle_rules`, `thumbnail_rules`), call `_flag_gap()` when the
   section is missing, and provide a sensible fallback.
3. Register the section in
   `VisualQA_Agent/channel_rag.py::CHANNEL_DNA_SEED` for each channel
   that needs it.
4. Add the agent to Section 2 of this document and update Section 4's
   quick reference.
