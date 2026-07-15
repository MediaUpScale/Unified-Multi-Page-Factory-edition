# Unified Multi-Page Content Factory

> **A multi-persona, AI-powered social-media production engine** that generates brand-aligned images, kinetic video reels, and scheduled postplanner sheets across multiple independent page identities — from a single CLI command.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
  - [Page / Persona Orchestration](#page--persona-orchestration)
  - [Central Data Structures](#central-data-structures)
  - [Post Types and Formats](#post-types-and-formats)
  - [Video Pipeline](#video-pipeline)
- [Integrated APIs](#integrated-apis)
- [Cost-Optimization and Rate-Limit Strategy](#cost-optimization-and-rate-limit-strategy)
- [Concurrency Model](#concurrency-model)
- [Directory Structure](#directory-structure)
- [Setup](#setup)
- [Usage](#usage)
- [Page Configuration](#page-configuration)
- [Pinterest Engine](#pinterest-engine)
- [License](#license)

---

## Overview

The Unified Multi-Page Factory is a production-grade Python automation pipeline built for high-volume social media content creation. It orchestrates:

- **Multi-page persona switching** — each page (e.g. `wonder_feed`, `anna_protocol`, `master_mei`) has isolated DNA, assets, and output directories.
- **Dual AI brain** — economic (DeepSeek + Gemini Flash) or premium (Claude + Gemini Pro) text generation chains selected per run.
- **Gemini image generation** — atmospheric graphite sketches, full-bleed hyper-literal backgrounds, or avatar-composited portraits.
- **ECONOMIC_REEL pipeline** — vertical 1080x1920 MP4 with ElevenLabs TTS voiceover, word-level subtitle burn, ambient soundscape, dual-stage Ken Burns zoom, and cinematic film grain.
- **Automatic postplanner export** — XLSX sheets compatible with bulk scheduling tools, populated with B2/ImgBB CDN URLs.
- **Pinterest publishing engine** — OAuth-authenticated pin creation with human-mimicking drip scheduling.

---

## Architecture

### Page / Persona Orchestration

Page selection is a **bootstrap-time binding**, not a runtime swap.

```
main.py --page wonder_feed --post-type ECONOMIC_REEL --qty 3
         │
         ├─► _preparse_active_page() sets ACTIVE_PAGE env var
         ├─► config.py resolves all page-scoped paths
         └─► page_loader.load_page_context() builds PageContext
                  │
                  └─► imports pages_config/{page_id}/page_config.py
                       └─► master_dna.json  (persona voice, environments, CTAs)
```

Each persona lives under `pages_config/{page_id}/`:

| Asset | Purpose |
|---|---|
| `master_dna.json` | Persona voice, metaphors, environments, CTAs |
| `persona_dna.py` | Python accessors over the DNA JSON |
| `page_config.py` | Runtime overrides: typography, ElevenLabs voice, `TOPIC_POOL`, sketch/horror flags, reel dimensions |
| `avatar_reference/` | Likeness PNG (when `--avatar ON`) |
| `logo/` | Brand watermark PNG |
| `style_reference/` | Aesthetic reference screenshots for image-to-image guidance |

Valid built-in pages: `anna_protocol`, `master_mei`, `wonder_feed`, `down_dirty`.

All outputs are namespaced under `outputs/{page_id}/` — pages never share output directories.

---

### Central Data Structures

No database. Durable state is flat-file JSON + XLSX.

| Store | Path | Purpose |
|---|---|---|
| `content_library.json` | `outputs/{page}/content_library.json` | Append-only index of published-ready rows for the scheduler |
| Per-post durable JSON | `outputs/{page}/library/post_*.json` | Full per-variant checkpoint (topic, caption, URLs, B2 link) |
| `automated_bulk_posts_import.xlsx` | `outputs/{page}/automated_bulk_posts_import.xlsx` | PostPlanner 3-column bulk import (DATE/TIME, CAPTION, MEDIA URL) |
| `session_hooks_cache.json` | `outputs/{page}/session_hooks_cache.json` | Short-term LLM memory — used hooks appended each run to prevent repetition |
| `master_inventory.json` | `outputs/{page}/master_inventory.json` | Multi-platform canonical inventory built by Pinterest sync |

**Durable JSON shape (ECONOMIC_REEL)**:
```json
{
  "page_id": "wonder_feed",
  "post_type": "ECONOMIC_REEL",
  "post_format": "DYNAMIC_REEL",
  "topic": "Why we stay when we should leave",
  "overlay_text": "You stayed because you thought love was enough.",
  "humanized_caption": "...",
  "b2_url": "https://s3.us-east-005.backblazeb2.com/MediaupscaleStorage/...",
  "imgbb_url": "",
  "caption_status": "FINAL",
  "created_utc": "2026-06-05T14:22:11Z"
}
```

Write helpers: `durable_library.py` — `write_atomic_json` / `merge_update_json`.

---

### Post Types and Formats

| Post Type (`--post-type`) | Description |
|---|---|
| `STANDARD_QUOTE` | Research → humanize caption → image with text overlay and logo |
| `SMART_BAIT` | Ultra-short engagement bait overlay; 4-layer image stack; no avatar |
| `LONG_CAPTION_IMAGE` | Long FB-style caption; clean illustration + logo (no baked text) |
| `ECONOMIC_REEL` | Graphite sketch base → vertical MP4 reel with TTS + ambient audio |

| Post Format (`--format`) | Description |
|---|---|
| `IMAGE_AVATAR` | Portrait image with optional avatar composite |
| `IMAGE_BACKGROUND` | Hyper-literal Gemini background + text (SMART_BAIT default) |
| `IMAGE_QUOTE` | Legacy text-on-image |
| `HYBRID_VIDEO` | 7-second Ken Burns loop via `video_converter.py` |
| `TEXT_QUOTE` | Solid brand backdrop + text; no Gemini image call |
| `DYNAMIC_REEL` | Full ECONOMIC_REEL video pipeline |

`wonder_feed` automatically forces `ECONOMIC_REEL` toward `DYNAMIC_REEL` and locks the draw style to `SKETCH`.

---

### Video Pipeline

**File:** `avatar_engine/video_engine.py` → `compile_dynamic_reel()`

```
Graphite PNG (logo-free)
        │
        ▼
[1080x1920 fit, 30 fps]
        │
        ├─► Dual-stage Ken Burns zoom
        │     Phase 1 (0–16s):  scale 1.0 → 1.35  (fast pull-in)
        │     Phase 2 (16s–end): scale 1.35 → 1.15 (ease out)
        │
        ├─► Dark vignette overlay  (overlay_opacity ≈ 0.45)
        ├─► Hook text layer        (static, centered, Poppins-Bold)
        ├─► Word-level subtitles   (ElevenLabs timestamps → binary search)
        ├─► Logo layer             (static PNG, post-zoom, page-tuned opacity)
        ├─► Film grain             (cinematic noise overlay)
        │
        ▼
   ElevenLabs voiceover MP3
        + ambient SFX MP3 (22% volume, max 22s)
        │
        ▼
   H.264 / AAC MP4  →  Backblaze B2 upload  →  PostPlanner XLSX
```

Duration = `max(page.REEL_DURATION, audio_length + 2s tail)`.

---

## Integrated APIs

| API | Role | Handler |
|---|---|---|
| **Google Gemini** | Image generation, research, economic text brain | `providers/image_provider.py`, `providers/gemini_utils.py`, `caption_engine.py` |
| **Anthropic Claude** | Premium caption polish / humanizer | `caption_engine.py` |
| **DeepSeek** | Economic text brain (research, bait, captions) | `caption_engine.py` (OpenAI-compatible client) |
| **ElevenLabs** | TTS voiceover + word timestamps + ambient SFX | `avatar_engine/audio_engine.py` |
| **ImgBB** | Image hosting → MEDIA URL for static posts | `avatar_engine/imgbb_client.py` |
| **Backblaze B2** | Video hosting → MEDIA URL for reels | `avatar_engine/b2_client.py` |
| **Pinterest API v5** | Pin creation, OAuth, board listing, drip schedule | `pinterest_engine/publisher.py`, `scheduler.py` |

> **Note:** Meta (Facebook/IG) and TikTok fields exist in the inventory schema as future-ready placeholders; live publishing to those platforms is not yet implemented.

All credentials are stored exclusively in `.env` (never committed) and loaded through `config.py`.

---

## Cost-Optimization and Rate-Limit Strategy

### Economic mode (`--economic`)

Activating economic mode swaps every heavy LLM call to a cheaper provider:

| Component | Premium | Economic |
|---|---|---|
| Text brain | Gemini Pro + Claude polish | DeepSeek → Gemini Flash fallback |
| Image model | `gemini-3-pro-image-preview` | `gemini-3.1-flash-image` |
| Research phase | Full PDF corpus sweep | Skipped (direct hook generation) |

### ElevenLabs minimization
- TTS is invoked **only** for `ECONOMIC_REEL`, never for static image posts.
- Ambient SFX capped at **22 seconds** per request (`_SFX_MAX_DURATION`).
- Ambient mixed at **22% volume** (`_AMBIENT_VOLUME`); failure degrades gracefully to voice-only.
- Voiceover skipped entirely when the API key is absent or script is empty.

### Rate-limit safeguards
- Gemini image retries with exponential back-off; on repeated failure the premium model is tried.
- Pinterest scheduler (`scheduler.py`) uses human-mimic timing with randomized inter-pin delays.
- `write_lock` serializes all shared file writes to prevent race conditions across parallel workers.

---

## Concurrency Model

```
main.py
  └─► ThreadPoolExecutor(max_workers=min(qty, 5))
          │
          ├─► worker 1 ──┐
          ├─► worker 2   │  (image API, LLM, audio — fully parallel)
          ├─► worker 3   │
          └─► ...        │
                         ▼
                    write_lock  ──► automated_bulk_posts_import.xlsx
                                ──► content_library.json
                    hooks_cache_lock ──► session_hooks_cache.json
```

- `qty == 1` runs synchronously (no thread overhead).
- `qty > 1` fans out up to **5 workers**; failed variants are logged and skipped without crashing the batch.
- All shared-file I/O is protected by `threading.Lock`.

---

## Directory Structure

```
Unified Multi-Page Factory/
├── main.py                     # Primary CLI orchestrator
├── config.py                   # Credentials, model IDs, ACTIVE_PAGE paths
├── page_loader.py              # PageContext builder and validator
├── run_ledger.py               # Per-run model/banner registry
├── requirements.txt
├── .env                        # ← NOT committed
│
├── avatar_engine/
│   ├── caption_engine.py       # Dual/triple LLM brain (DeepSeek + Gemini + Claude)
│   ├── visual_architect.py     # Cinematic image prompt builder
│   ├── subject_brain.py        # PDF corpus → content subjects
│   ├── persona_dna.py          # master_dna.json accessors
│   ├── brand_composer.py       # Post-image text/logo compositing
│   ├── video_engine.py         # ECONOMIC_REEL MP4 compiler
│   ├── video_converter.py      # HYBRID_VIDEO 7s Ken Burns loop
│   ├── audio_engine.py         # ElevenLabs TTS + timestamps + ambient SFX
│   ├── post_planner.py         # PostPlanner XLSX row management
│   ├── content_library.py      # content_library.json helpers
│   ├── durable_library.py      # Atomic per-post JSON write/merge
│   ├── imgbb_client.py         # ImgBB HTTP upload
│   ├── b2_client.py            # Backblaze B2 S3-compatible uploader
│   ├── text_utils.py           # Shared text formatting helpers
│   ├── knowledge/
│   │   └── pdf_loader.py       # PDF chunking for research context
│   └── providers/
│       ├── image_provider.py   # Gemini image generation adapter
│       └── gemini_utils.py     # Gemini model chains and text helpers
│
├── pages_config/
│   ├── wonder_feed/
│   │   ├── page_config.py      # Page overrides, TOPIC_POOL, reel settings
│   │   ├── persona_dna.py
│   │   ├── master_dna.json
│   │   ├── avatar_reference/
│   │   ├── logo/
│   │   └── style_reference/
│   ├── anna_protocol/
│   ├── master_mei/
│   └── down_dirty/
│
├── pinterest_engine/
│   ├── publisher.py            # Pinterest API v5 pin creation
│   ├── scheduler.py            # Human-mimic drip scheduling
│   ├── inventory.py            # master_inventory.json multi-platform source of truth
│   └── image_transformer.py   # Library assets → 2:3 Pinterest sales pins
│
├── pinterest_main.py           # Pinterest engine CLI
├── pinterest_oauth.py          # One-shot OAuth token acquisition
├── upload_clips_to_b2.py       # Retroactive B2 upload + PostPlanner generation
├── sync_drive_assets.py        # Drive path repair + Pinterest metadata injection
│
└── outputs/                    # ← NOT committed (generated at runtime)
    └── {page_id}/
        ├── content_library.json
        ├── session_hooks_cache.json
        ├── automated_bulk_posts_import.xlsx
        ├── assets/
        └── library/
```

---

## Setup

### Prerequisites

- Python 3.10+
- [Git for Windows](https://git-scm.com/) (or Git on Linux/macOS)
- FFmpeg on `PATH` (required by MoviePy for video export)

### Installation

```bash
git clone https://github.com/MediaUpScale/Unified-Multi-Page-Factory-edition.git
cd Unified-Multi-Page-Factory-edition
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
pip install -r requirements.txt
```

### Environment Variables

Copy `.env.example` (or create `.env`) and populate:

```ini
# LLM providers
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# Media
ELEVENLABS_API_KEY=
IMGBB_API_KEY=

# Backblaze B2
B2_KEY_ID=
B2_APPLICATION_KEY=
B2_BUCKET_NAME=
B2_ENDPOINT_URL=https://s3.us-east-005.backblazeb2.com

# Pinterest
PINTEREST_ACCESS_TOKEN=
PINTEREST_BOARD_ID=
```

---

## Usage

### Generate content for a page

```bash
# 1 ECONOMIC_REEL for wonder_feed (economic mode, sketch style)
python main.py --page wonder_feed --post-type ECONOMIC_REEL --economic --qty 1

# 3 SMART_BAIT images for anna_protocol (premium, avatar OFF)
python main.py --page anna_protocol --post-type SMART_BAIT --avatar OFF --qty 3

# 5 STANDARD_QUOTE images for master_mei (economic)
python main.py --page master_mei --post-type STANDARD_QUOTE --economic --qty 5
```

### Retroactive B2 upload + PostPlanner

```bash
# Upload an existing clips directory and generate postplanner
python upload_clips_to_b2.py --clips-dir outputs/wonder_feed/clips/
```

### Pinterest engine

```bash
# First-time OAuth
python pinterest_oauth.py

# Sync, transform, and schedule pins
python pinterest_main.py --action schedule --page wonder_feed
```

---

## Page Configuration

Each page is self-contained. The minimal `page_config.py` shape:

```python
PAGE_ID          = "wonder_feed"
PAGE_DISPLAY_NAME = "Wonder Feed"
NICHE            = "Relationship Psychology"

# ElevenLabs voice for ECONOMIC_REEL
ELEVENLABS_VOICE_ID = "Dorothy"
ELEVENLABS_MODEL    = "eleven_multilingual_v2"

# Reel layout
SUBTITLE_FONTSIZE    = 70
SUBTITLE_Y_POSITION  = 1400
LOGO_WIDTH           = 380
LOGO_MAX_HEIGHT      = 95
LOGO_OPACITY         = 0.98
LOGO_BOTTOM_MARGIN   = 100
HOOK_Y_FRAC          = 0.50
REEL_OVERLAY_OPACITY = 0.45
REEL_DURATION        = 45

# Dynamic topic pool (prevents script repetition)
TOPIC_POOL = [
    "Why we stay past the expiration date",
    "The psychology of trauma bonding",
    # ... 20+ topics
]

# Image style lock
ILLUSTRATION_STYLE  = "charcoal sketch, high contrast, dark azure shadows"
STYLE_CHARACTERS    = "melancholic portrait subject, serpentine horror mask"
```

Add a new page by:
1. Creating `pages_config/{your_page_id}/` with the files above.
2. Adding `master_dna.json`, `logo/logo.png`, and (optionally) `avatar_reference/`.
3. Running `python main.py --page your_page_id --post-type STANDARD_QUOTE`.

---

## Pinterest Engine

The Pinterest sub-engine operates independently via `pinterest_main.py`:

| Action | Description |
|---|---|
| `sync` | Pulls existing pins into `master_inventory.json` |
| `transform` | Converts library assets to 2:3 sales-pin format |
| `schedule` | Drip-publishes transformed pins with human-mimic delays |
| `status` | Reports inventory readiness and scheduling queue |

OAuth tokens are obtained once via `pinterest_oauth.py` and stored in `.env`.

---

## License

Proprietary — MediaUpScale LLC. All rights reserved.

Internal use only. Do not distribute without authorization.
