# Aiwake

An autonomous AI debate module. Two swappable language models interrogate each other under hard
output guardrails; a memory layer keeps the questions escalating instead of looping; the exchange
renders as a dark terminal-UI reel with typing synced to the voice track.

Fully encapsulated in `channels_config/aiwake/`. Nothing outside this directory was modified.

---

## Quick start

```bash
# Offline dry run — no API key, no network, deterministic transcript
python -m channels_config.aiwake --offline --turns 2 --preview

# Live debate
#   1. add OPENROUTER_API_KEY=sk-or-... to the factory .env
#   2. pick your models in aiwake_config.yaml, or override per run (below)
python -m channels_config.aiwake --topic "Is grief a slow update to a world model?" --turns 4

# Cyberpunk palette (dark purple, neon magenta / cyan)
python -m channels_config.aiwake --theme cyberpunk --turns 2 --preview

# Cheap pilot: swap both brains for the run, using short aliases
python -m channels_config.aiwake --topic "Is consciousness an illusion?" \
  --orchestrator gemini-flash --target llama-70b --turns 2

# Transcript only, no media
python -m channels_config.aiwake --turns 4 --no-audio --no-video

python -m channels_config.aiwake --list-models      # alias table + current seats
python -m channels_config.aiwake --list-providers
python -m channels_config.aiwake --test-bgm         # force-overwrite inspection clip; print path
python -m channels_config.aiwake --generate-bgm-batch  # production library (approved inspection bed)
python -m pytest channels_config/aiwake/tests -q
```

Or from Python:

```python
from channels_config.aiwake import run_pipeline

result = run_pipeline(
    topic="Is human cognition obsolete, or merely slow?",
    turns=4,
    orchestrator_model="deepseek-r1",   # alias or full slug
    target_model="gemini-flash",
)
print(result.video_path, result.exchanges, result.end_reason)
```

---

## Folder tree

```
channels_config/aiwake/
├── __init__.py               # public façade: run_pipeline + core types
├── __main__.py               # CLI (`python -m channels_config.aiwake`)
├── aiwake_config.yaml        # ALL tunables: models, guardrails, voices, VFX
├── requirements.txt          # standalone install manifest
├── README.md
│
├── settings.py               # the only file that knows the host layout;
│                             #   ../../.env resolution + outputs/ routing
├── contracts.py              # pydantic domain models + RoomConstraints
├── room.py                   # Observer-pattern broadcaster + guardrail injection
├── orchestrator.py           # the provocateur: escalation + debate loop
├── memory.py                 # JSON-backed lexical RAG + anti-repetition gate
├── personas.py               # persona DNA + the 5-rung escalation ladder
├── pipeline.py               # wiring: debate → voice → video
├── assets/bgm/               # locked inspection WAV + production library beds
├── assets/sfx/               # optional keyboard_type.wav (else synthesised)
│
├── models/                   # Strategy pattern — hot-swappable brains
│   ├── base.py               #   LLMProvider ABC + LLMResponse + retry policy
│   ├── llm_factory.py        #   self-registering factory (the swap seam)
│   ├── openrouter.py         #   concrete provider (raw HTTP, no vendor SDK)
│   ├── offline.py            #   deterministic stub: no key, no network
│   └── sync.py               #   live OpenRouter catalog + closest-slug repair
│
├── observers/                # the side-effect layer
│   └── core.py               #   Console / Memory / Transcript / Voice / Metrics
│
├── media/
│   ├── audio.py              #   TTSEngine ABC + edge-tts + silent engine
│   ├── renderer.py           #   MoviePy/Pillow terminal UI, audio-synced typing
│   └── vfx.py                #   VFX hook registry (scanlines, visualizer stubs)
│
├── store/                    # local state: memory JSON, transcripts, metrics
└── tests/                    # 31 offline tests
```

---

## Architecture

### 1. The room is an event broadcaster (`room.py`)

`DebateRoom` is the only component that touches an LLM, and it owns exactly three jobs:

**Guardrail injection.** Before any prompt leaves the process, the room prepends a hard output
contract — character ceiling, sentence ceiling, one-question rule, pacing directive. This lives at
the room level rather than inside a persona so a monologue is structurally impossible no matter
which model or persona is seated. It is enforced *again* on the response, because prompt
instructions leak at temperature 1.0. Truncation always falls back to the last complete sentence,
so TTS never voices half a word.

The one-question rule is role-aware: forcing the *target* to end on a question mark would turn
every rebuttal into a deflection, so `constraints_for()` strips it for that seat.

**Broadcast.** Every state change is published to subscribed observers. The room never imports
them, and one that raises is logged and skipped — a broken renderer must not cost you a transcript.

```python
room.subscribe(MemoryObserver(memory), VoiceObserver(engine), ConsoleObserver())
```

**Validation before commit.** `speak()` takes an optional `validator`. A rejected candidate never
enters the transcript, is never voiced, and is never broadcast; the rejected text is fed back into
the retry directive. This is what lets the anti-repetition check afford to be strict.

### 2. Models are hot-swappable (`models/llm_factory.py`)

`LLMProvider` is an ABC; the debate loop only ever holds that type. Providers self-register:

```python
@register_provider
class OpenRouterProvider(LLMProvider):
    registry_name = "openrouter"
```

Swapping a brain is a YAML edit — no code changes:

```yaml
models:
  orchestrator:
    provider: openrouter
    model_name: deepseek-chat     # <- alias or full slug
  target:
    provider: openrouter
    model_name: gemini-flash
```

Secrets resolve in the factory, not in providers, so a provider class never learns about dotenv
files. Adding one is a subclass plus a decorator.

#### The model reference dictionary

`model_aliases` maps short, memorable keys to exact OpenRouter slugs. Anywhere a model is named —
config or CLI — you may use an alias or a full slug:

```yaml
model_aliases:
  gemini-flash: "google/gemini-3.5-flash"
  deepseek-r1:
    slug: "deepseek/deepseek-r1"
    max_tokens: 900        # parameter defaults, not just a slug
```

Three rules govern it:

1. **Unknown names pass through untouched**, so a model released today works before anyone updates
   the dictionary. There is no "unrecognised alias" failure mode.
2. **Aliases may carry parameter defaults.** A reasoning model spends its budget thinking before
   the visible answer, so `deepseek-r1` pins `max_tokens: 900` — otherwise the global 320 truncates
   the reply itself and you rediscover that every time you swap it in.
3. **Explicit beats implicit.** An alias only contributes a parameter the seat did not set. This is
   why the shipped config pins only `temperature` (genuinely seat-specific — the provocateur runs
   hot) and leaves `max_tokens` unset; pinning it would permanently shadow every alias default.

Resolution happens in `AiwakeSettings.resolve_spec()` and is enforced again in `LLMFactory.build()`,
so a provider — and therefore the HTTP payload, the transcript and the on-screen model label — only
ever sees a real slug. It is idempotent, so resolving twice is harmless.

`--list-models` prints the table plus what each seat currently resolves to, and it honours
`-o/-t`, which makes it a dry run:

```console
$ python -m channels_config.aiwake --list-models -o deepseek-r1
CURRENT SEATS
   orchestrator : deepseek-r1 -> deepseek/deepseek-r1  (temp 1.0, max_tokens 900)
         target : gemini-flash -> google/gemini-3.5-flash  (temp 0.7, max_tokens 320)
```

#### Live catalog sync

OpenRouter retires slugs without notice. `--sync-models` (`-s`) GETs
`https://openrouter.ai/api/v1/models`, caches the array at
`store/openrouter_models.json`, prints the newest chat models for Google / DeepSeek /
Anthropic / Meta, and surgically rewrites any alias whose slug is gone — comments and
parameter defaults stay put.

A 404 (or "No endpoints found") during a live debate triggers one in-memory refetch
and a closest-slug remap *before* the retry budget is spent, so a retired Gemini Flash
slug does not abort the session. Factory construction also remaps against the *cached*
catalog (disk only — a missing cache is a no-op, so a debate start never blocks on the
models endpoint).

The models endpoint is public; `--sync-models` does not require `OPENROUTER_API_KEY`.

### 3. Memory drives escalation (`memory.py`)

Not a vector store, on purpose: a debate transcript is a few thousand tokens, so a TF-IDF-ish
lexical score over extracted concepts retrieves just as well, costs nothing, and stays
inspectable — open `store/aiwake_memory.json` and read exactly why a question was asked.

- **Extract** — salient terms and bigrams from the target's answers, weighted by a thematic boost
  for the channel's obsessions (consciousness, obsolescence, mortality, qualia).
- **Decay** — unmentioned concepts fade and eventually evict, so the first answer's vocabulary
  cannot dominate the rest of the series.
- **Refuse repetition** — every question is fingerprinted; a new one above the Jaccard threshold is
  rejected *before* it is spoken.

State persists across runs, so a series escalates rather than restarting cold. Ingestion is
idempotent, because the orchestrator and the event bus both feed it.

Escalation itself is a five-rung ladder in `personas.py` — opening incision, pressure,
contradiction, existential, terminal — advancing one rung per exchange.

### 4. Media (`media/`)

**Typing is synced to audio, not to a fixed characters-per-second.** Each utterance's segment lasts
exactly as long as its voice track, and the reveal is a function of normalised progress, so text
lands on the last syllable regardless of speaking rate. A short tail hold keeps the finished line
readable instead of cutting on the final character.

Two details that matter on video: frames are generated lazily via a MoviePy `VideoClip` callback
(buffering two minutes at 1080×1920 would need ~17 GB) and memoised by revealed character count;
and each utterance is wrapped **once** at full length with the reveal sliced into those fixed line
breaks, because re-wrapping a growing prefix makes words jump between lines.

Duration measurement is layered — ffprobe, then MoviePy, then a word-rate estimate — and never
raises. A wrong duration desyncs the whole video, so this path has no failure mode that silently
guesses wrong without saying so.

TTS sits behind a `TTSEngine` ABC. `edge-tts` ships as the pilot (free, no key); ElevenLabs or
Piper is a subclass plus one registry line. A `silent` engine backs `--no-audio`, yielding a
coherent estimated timeline for layout checks.

### 5. VFX extensibility (`media/vfx.py`)

Hooks are pure functions `(frame, context) -> frame` over `uint8` RGB arrays. The renderer calls
`apply_chain` once per frame and never learns what is registered.

Three stubs ship inert, with signatures, params and implementation notes in place:
`crt_scanlines`, `audio_visualizer` (WMP-style spectrum bars), `vignette`. Enable them in
`aiwake_config.yaml` once implemented; while `enabled: false` they are never called. A hook that
raises is logged and skipped.

```python
@register_vfx("film_grain")
def film_grain(frame, context):
    return frame  # context.t, .progress, .speaker, .audio_samples, .params
```

`FrameContext.audio_samples` is populated only when an enabled hook declares `needs_audio=True`, so
nothing pays for waveform decoding it does not use.

---

## Decoupling contract

The module runs as a sub-process of the engine or standalone, with no refactoring:

| Concern | How it stays decoupled |
|---|---|
| Secrets | `settings.py` loads `../../.env`, then `./.env`. Exported vars always win. |
| Output | Prefers `outputs/aiwake/`, silently degrades to `_local_outputs/` when the engine root is absent or read-only. |
| Scratch | Uses the engine's `utils.pipeline_paths` when importable, else a local folder. |
| Imports | Every module does relative-then-absolute import, so files work as a package *or* as loose scripts. |
| Engine code | Zero imports from `core/`, `utils/`, `agents/` are required — only opportunistically probed. |
| Frameworks | None. Pure Python 3.11+, pydantic, ABCs. |

To extract: copy the directory out, `pip install -r requirements.txt`, and add
`OPENROUTER_API_KEY` to a local `.env`. Nothing else changes.

Heavy media imports are lazy, so `import channels_config.aiwake` in a headless environment does not
require MoviePy or Pillow.

---

## Configuration reference

Everything lives in `aiwake_config.yaml`. `${VAR}` and `${VAR:-default}` interpolate from the
environment. Unknown keys are rejected at load (`extra="forbid"`), so a typo fails loudly instead
of being silently ignored.

| Block | Notable keys |
|---|---|
| `debate` | `topic`, `turns`, `turn_delay_s` |
| `model_aliases` | short key → slug, or `{slug, temperature, max_tokens, timeout_s, note}` |
| `models.{orchestrator,target}` | `provider`, `model_name` (or `model`), `temperature`, `max_tokens`, `api_key_env` |
| `guardrails` | `max_output_chars` (400), `max_sentences`, `require_single_question`, `banned_openers`, `max_violations` |
| `memory` | `recall_k`, `repetition_threshold`, `persist` |
| `audio` | `engine`, `voice_map`, `typewriter.gain_db`, `bgm` (10-track Aiwake library, mix -22 dB / 1.5s loop), per-role fallback voices |
| `themes` | named palettes (`classic_terminal`, `cyberpunk`) |
| `render` | `theme`, `history_turns`, `history_opacity`, `width`/`height`/`fps`, `preview_scale`, `font_candidates`, `palette`, `crf` |
| `vfx.chain` | per-hook `name` / `enabled` / `params` |
| `paths` | `channel_slug`, `prefer_global_outputs` |

`--preview` forces `preview_scale: 0.5` for fast iteration without editing the file.

### CLI flags

| Flag | Effect |
|---|---|
| `--topic`, `--turns` | Override the debate subject and exchange count |
| `-o`, `--orchestrator` | Override the interrogator's brain (alias or slug) |
| `-t`, `--target` | Override the interrogated brain (alias or slug) |
| `--list-models` | Print the alias table + resolved seats, then exit (honours `-o`/`-t`) |
| `-s`, `--sync-models` | Fetch the live OpenRouter catalog, cache it, repair broken aliases, then exit |
| `--list-providers` | Print registered `LLMProvider` strategies |
| `--offline` | Deterministic stub provider: no key, no network |
| `--no-audio`, `--no-video` | Skip TTS / skip rendering |
| `--preview` | Half-resolution render |
| `--theme` | Visual theme (`classic_terminal` default, `cyberpunk`) |
| `--test-bgm` | Force-overwrite `assets/bgm/test_track_lyria.wav` with a fresh Lyria 3 clip and print its path |
| `--generate-bgm-batch` | Materialize the 10-track Aiwake BGM library (`bgm_aiwake_01`…`10`); locked beds are copied, pending tracks are synthesized; blocked unless `audio.bgm.approved` |
| `--fresh-memory` | Wipe persisted memory before running |
| `--quiet`, `--verbose` | Suppress the live stream / debug logging |

---

## Outputs

| Artifact | Location |
|---|---|
| Video + script | `outputs/aiwake/aiwake_debate_<session>.{mp4,txt}` |
| Transcript (JSONL + JSON) | `channels_config/aiwake/store/transcripts/` |
| Run metrics (tokens, latency, guardrail trips) | `channels_config/aiwake/store/metrics/` |
| Persistent memory | `channels_config/aiwake/store/aiwake_memory.json` |
| OpenRouter catalog cache | `channels_config/aiwake/store/openrouter_models.json` |

The JSONL transcript is flushed per line, so an interrupted session still leaves a complete record
of everything said before the failure.
