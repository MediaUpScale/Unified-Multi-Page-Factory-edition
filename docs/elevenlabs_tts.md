# ElevenLabs TTS (LOFI pipeline)

Speed always lives inside `voice_settings`, never as a standalone `convert()` / `convert_with_timestamps()` keyword. The official JSON shape is:

```json
{
  "stability": 1.0,
  "similarity_boost": 1.0,
  "style": 0.0,
  "use_speaker_boost": true,
  "speed": 0.80
}
```

## First-class config (`core_engine/economic_reel_lofi/config.py`)

Change these — do not hardcode speed/model/voice at the call site:

| Name | Role | Production default |
|---|---|---|
| `TTS_VOICE_ID` | ElevenLabs voice | `hNtG3AcS155nfu8sfWXk` |
| `TTS_MODEL` | Model id | `eleven_multilingual_v2` |
| `TTS_SPEED` | Narration speed | `0.80` |

Aliases `LOFI_VOICE_ID` / `LOFI_TTS_MODEL` / `LOFI_VOICE_SPEED` track the same values.

Helpers: `tts_voice_id()`, `tts_model()`, `tts_speed()`.

## Valid `TTS_SPEED` range: **0.7–1.2**

- API floor is **0.70** (values below that are rejected or ignored).
- API default is **1.0**.
- Production LOFI uses **0.80** on `eleven_multilingual_v2`.
- `eleven_v3` ignores `voice_settings.speed`. LOFI stays on v2 so speed is applied.

## Inter-line silence

Concat trims leading/trailing hush on each TTS clip, then inserts a flat `VO_INTERLINE_SILENCE_S` (**0.30 s**) of digital zeros. BGM duck windows are those same 300 ms intervals — not the variable per-clip tails.
