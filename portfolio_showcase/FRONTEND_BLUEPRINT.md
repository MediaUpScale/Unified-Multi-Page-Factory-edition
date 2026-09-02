# Unified Multi-Page Factory — Frontend Blueprint

**Status:** specification only. Do not implement the Next.js app from this document until a separate build ticket lands.  
**Product:** a **shell/registry** that statically generates one presentation layer per automated pipeline in the factory.  
**Audience:** Senior Tech Recruiters (Software Engineering, HCI/UX, systems/media).  
**Data owner:** `portfolio_showcase/` — decoupled; deleting it must not break any engine.

If a field or path is not listed here, the UI must not invent it.

---

## 1. Scope

The factory is not one model demo. It is an **ecosystem of swappable content engines** that share posting, QA, and media conventions. The site is a dark, typographic map of that ecosystem.

| Slug | Product name | Engine (today) | v1 content |
|---|---|---|---|
| `aiwake` | Aiwake | `channels_config/aiwake` | `architecture.json` telemetry (EventBus) |
| `wonder_feed` | Wonder Feed (Riso Retro Flat) | `core/economic_reel_lofi` | narrative `.md` + pipeline PNGs |
| `master_mei` | Master Mei | `core/economic_reel_lofi` | `project.json` registry stub — Wonder Feed's on-screen persona, split into its own page |
| `endless_summer_paradise` | Endless Summer Paradise | `channels_config/endless_summer_paradise` | `project.json` registry stub |
| `ancient_knowledge` | Ancient Knowledge | `channels_config/ancient_knowledge` | registry stub |
| `anna_protocol` | Anna's Garden (Anna Protocol) | `channels_config/anna_protocol` | registry stub |
| `momma_circle` | Momma Circle | `channels_config/momma_circle` + Playwright scheduler | registry stub |

**Routes**

| Route | Role |
|---|---|
| `/` | Global index / factory hero + timeline |
| `/projects/[slug]` | Scroll-telling project view (SSG) |

A project is **included in the build** iff `data/projects/[slug]/project.json` exists. Missing telemetry or markdown is an empty section, not a 404.

The web app lives in a **separate repo or `apps/portfolio-web/`**. It must never `import` `channels_config.*` or `core.economic_reel_lofi`.

---

## 2. Tech stack rationale

| Layer | Choice | Why |
|---|---|---|
| App | **Next.js App Router**, React, TypeScript | `generateStaticParams()` is the registry. RSC reads JSON/MD on the server; the client only runs Framer Motion. |
| Style | **Tailwind CSS** | Factory chrome, not a component kit. Tokens: `#07090C` bg, `#12161C` chrome, `#00FF66` accent. |
| Motion | **Framer Motion** | Scroll-triggered narrative. `prefers-reduced-motion: reduce` → opacity only. |
| Data | **Jamstack / SSG** | No database, no runtime Python, no ISR against engines. Rebuild = new evidence. |

Zero-latency load: HTML is the last export of `portfolio_showcase/data`. TTFB is the CDN.

---

## 3. Data ingestion contract (SSG)

### 3.1 Scan rule — `generateStaticParams()`

At `next build`, after CI copies `portfolio_showcase/` into the app:

```ts
// app/projects/[slug]/page.tsx
export async function generateStaticParams() {
  const root = join(process.cwd(), "content/showcase/projects");
  const dirs = await readdir(root, { withFileTypes: true });
  return dirs
    .filter((d) => d.isDirectory())
    .filter((d) => existsSync(join(root, d.name, "project.json")))
    .map((d) => ({ slug: d.name }));
}
```

Unknown slugs 404. New factory channels appear by adding `data/projects/<slug>/project.json` and rebuilding — no route file edits.

### 3.2 Hybrid files per slug

```
data/projects/[slug]/
  project.json              # required — registry card
  architecture.json         # optional — machine telemetry (graphs, stages, patterns)
  *.md | *.mdx              # optional — narrative architecture (MDX/Markdown)
```

| Kind | Ingest | Use |
|---|---|---|
| `project.json` | `JSON.parse` | Index card, title, tags, engine path |
| `architecture.json` (or any `*.json` except `project.json`) | typed `ProjectArchitecture` | Spine, pipeline stations, media flags |
| `*.md` / `*.mdx` | `gray-matter` + MDX compile **at build** | Long-form decisions (gates, rebuilds) |
| `data/global_timeline.json` | once, on `/` | Factory-wide decision log |

**Aiwake-specific:** the EventBus logger writes **only** `data/projects/aiwake/architecture.json`.  
**Wonder Feed-specific:** `riso_retro_flat_v4_*.md` and `lofi_four_stage_pipeline_*.md` are first-class MDX sources.

Do not parse `tools/` or `core/*.py` in Next.js.

### 3.3 CI copy → public CDN paths

```text
rsync -a portfolio_showcase/data/                        apps/portfolio-web/content/showcase/
rsync -a portfolio_showcase/assets/images/[slug]/        apps/portfolio-web/public/showcase/images/[slug]/
rsync -a portfolio_showcase/assets/videos/[slug]/        apps/portfolio-web/public/showcase/videos/[slug]/
rsync -a portfolio_showcase/assets/canvas/[slug]/        apps/portfolio-web/public/showcase/canvas/[slug]/
```

| Disk | Public URL |
|---|---|
| `assets/images/[slug]/<file>` | `/showcase/images/[slug]/<file>` |
| `assets/videos/[slug]/<file>` | `/showcase/videos/[slug]/<file>` |
| `assets/canvas/[slug]/<file>` | `/showcase/canvas/[slug]/<file>` |

**Never** use machine-absolute `media.video_path` from telemetry as `<video src>`. CI must copy the reel into `assets/videos/[slug]/` first. Allowed extensions: `.png` `.webp` `.jpg` `.mp4` `.webm`. Reject `http(s):` in JSON.

If `portfolio_showcase/` is absent (engine sold without the showcase), rsync no-ops; `/` renders an empty factory with HTTP 200.

---

## 4. Generalized schema — `ProjectArchitecture`

Aiwake’s orchestrator/target seats are **one encoding** of `pipeline_stages[]`, not the global model. New projects (ComfyUI, HeyGen, Kling) add stages without changing the route.

```ts
export const SHOWCASE_SCHEMA = "1.0" as const;

/** data/projects/[slug]/project.json — registry, always present */
export interface ProjectMeta {
  slug: string;
  title: string;
  codename?: string;
  status: "active" | "registry" | "archived";
  summary: string;
  engine_path: string;
  tags: string[];
  narrative?: string[];   // md filenames in the same folder, display order
  telemetry?: string;     // json filename, default "architecture.json"
}

/** Optional machine telemetry (Aiwake today; other engines later) */
export interface ProjectArchitecture {
  schema_version: "1.0";
  project_meta?: Partial<ProjectMeta>;
  tech_stack: string[];
  pipeline_stages: PipelineStage[];
  design_patterns: DesignPattern[];
  media_assets: MediaAssets;
  // Aiwake-shaped extras — legal, all optional
  frontend?: { consumer: string; framework: string; showcase: string };
  session_id?: string;
  topic?: string;
  started_at?: string;
  ended_at?: string;
  pipeline_execution_s?: number;
  end_reason?: string;
  agents?: AgentSeat[];
  models?: Record<string, string>;
  llm_providers?: Record<string, string>;
  rag?: { applied: boolean; recalls: RagRecall[] };
  decision_tree?: DecisionNode[];
  milestones?: Milestone[];
  media?: Record<string, unknown>;
  patterns?: Record<string, unknown>;
}

export interface PipelineStage {
  id: string;
  kind:
    | "llm"
    | "rag"
    | "tts"
    | "mix"
    | "render"
    | "comfyui"
    | "heygen"
    | "kling"
    | "qa"
    | "scheduler"
    | string;
  label: string;
  detail?: string;
  model?: string;
  provider?: string;
}

export interface DesignPattern {
  name: "Observer" | "Strategy" | "Factory" | "Pub/Sub" | "SOLID" | string;
  applied: boolean;
  where: string;
  principle?: "SRP" | "OCP" | "LSP" | "ISP" | "DIP";
}

export interface MediaAssets {
  poster?: string;      // filename under assets/images/[slug]/
  reel?: string;        // filename under assets/videos/[slug]/
  diagrams?: string[];  // filenames under assets/images/[slug]/
}

export interface AgentSeat {
  id: string;
  role: string;
  display_name: string;
  model: string;
  provider: string;
  persona?: string;
}

export interface RagRecall {
  turn_index: number | null;
  role: string | null;
  blocks: number;
  chars: number;
  preview: string[];
  applied: boolean;
}

export interface DecisionNode {
  step: string;
  pattern?: string;
  [key: string]: unknown;
}

export interface Milestone {
  event: string;
  t_s: number;
  [key: string]: unknown;
}
```

**Adapter (Aiwake JSON → generalized):** if `pipeline_stages` is missing but `agents` / `patterns` exist, derive stages in the loader (LLM seats, RAG, TTS, mix, render) so `/projects/aiwake` uses the same components as Wonder Feed. Do not special-case JSX per slug except MDX filename lists from `project.json.narrative`.

**Wonder Feed v1:** no `architecture.json` yet — `project.json` + markdown + `MediaAssets.diagrams` from `assets/images/wonder_feed/`.

---

## 5. UX & scroll-telling

### 5.1 `/` — Factory index (hero)

Minimalist dashboard. Not a blog.

- **Eyebrow:** `UNIFIED MULTI-PAGE FACTORY`
- **Title:** `An ecosystem of automated pipelines.`
- **Sub:** `Each channel is a swappable engine. This site is a static registry of how they are built — not a CMS.`
- **Grid:** one card per `project.json` (`title`, `codename`, `status`, `tags[0..3]`, `summary`). `status: registry` cards are visually quieter (outline only).
- **Timeline:** `global_timeline.json` → `pipeline_nodes[*].entries[]` filtered to `kind ∈ {module_close, root_cause_fix, architecture_decision, milestone}`. Show `date`, `what`, `why`. File paths: basename only (never deep-link the private factory tree).

Motion: cards stagger `0.08s`. Reduced-motion: no Y offset.

### 5.2 `/projects/[slug]` — four beats

One page, vertical scroll, progress rail 1–4. Keyboard `j`/`k` snaps sections.

**Global motion:** enter `opacity 0→1`, `y 24→0`, `0.55s`, cubic `[0.4, 0, 0.2, 1]`.

#### Beat 1 — Output (reel / still)

Proof before vocabulary.

- Video: `/showcase/videos/[slug]/` first `*.mp4` by mtime, else `media_assets.reel`.
- Else poster: `media_assets.poster` or first diagram in `assets/images/[slug]/`.
- Meta row from telemetry when present: `session_id` · `topic` · `pipeline_execution_s` · `end_reason`.
- `<video muted loop playsInline>` — autoplay only if `prefers-reduced-motion: no-preference`.

Wonder Feed fallback: `lofi_four_stage_pipeline_20260825_dark.png` as the still if no MP4.

#### Beat 2 — Architecture spine

Scroll-pinned data flow.

- If `milestones[]` exist (Aiwake): nodes light in `t_s` order (`ON_PIPELINE_START` … `ON_PIPELINE_FINISH`).
- Else: nodes from `pipeline_stages[]` or markdown H2s of the first `narrative` file.
- Pattern chips from `design_patterns[]` or Aiwake `patterns.solid` / `patterns.strategy` / `patterns.observer`.
- Optional 8% opacity diagram from `media_assets.diagrams[0]`.

#### Beat 3 — Content pipeline

Horizontal stations (`<md`: stack). Bind `pipeline_stages[]`. Examples:

| Kind | Typical station |
|---|---|
| `llm` | Prompted generation (gpt-4o, Gemini, …) |
| `rag` | Memory / theme bank |
| `comfyui` / Flux | Still generation (`riso_retro_flat_v4`) |
| `tts` | Sequential VO |
| `mix` / `render` | FFmpeg / MoviePy |
| `qa` | Ship gates / visual QA |
| `heygen` / `kling` | Avatar / video vendors when a project adds them |

MDX below the track: compile `project.json.narrative` in order (Wonder Feed: four-stage map → riso pipeline → gates).

#### Beat 4 — Automation / schedulers

Generation is not the product; unattended ship is.

- Stage `kind: "scheduler"` when present (Momma Circle Playwright queue).
- Else static copy: Generate → Queue → Publish.
- Do not fetch Google Sheets at runtime.

---

## 6. Directory map (source of truth)

Paths relative to `portfolio_showcase/`.

```
portfolio_showcase/
├── FRONTEND_BLUEPRINT.md
├── README.md
├── core/                          # Python plugins — not ingested by Next.js
├── tools/                         # _render_four_stage_png.py — not ingested
├── data/
│   ├── global_timeline.json
│   └── projects/
│       ├── aiwake/
│       │   ├── project.json
│       │   └── architecture.json    # written by Aiwake EventBus logger
│       ├── wonder_feed/
│       │   ├── project.json
│       │   └── *.md
│       ├── endless_summer_paradise/project.json
│       ├── ancient_knowledge/project.json
│       ├── anna_protocol/project.json
│       └── momma_circle/project.json
└── assets/
    ├── images/[slug]/
    ├── videos/[slug]/
    └── canvas/[slug]/
```

`docs/architecture/` is **deprecated**. Do not add files there.

---

## 7. Suggested Next.js tree (not implemented)

```
apps/portfolio-web/
├── app/
│   ├── layout.tsx
│   ├── page.tsx                      # factory index
│   └── projects/[slug]/page.tsx      # generateStaticParams
├── components/
│   ├── FactoryGrid.tsx
│   ├── GlobalTimeline.tsx
│   ├── ProjectHero.tsx
│   ├── ArchitectureSpine.tsx
│   ├── PipelineStations.tsx
│   └── AutomationClose.tsx
├── lib/
│   ├── registry.ts                   # scan project.json
│   ├── mdx.ts                        # compile narrative md
│   └── types.ts                      # §4
├── content/showcase/                 # copied data/ (CI)
└── public/showcase/                  # copied assets/ (CI)
```

---

## 8. Non-goals (v1)

- No live socket into a running engine.
- No “chat with the orchestrator” demo.
- No per-slug custom page files (`page.tsx` is one template).
- No parsing of `tools/` or Python in the browser.
- No dual write to `docs/architecture/`.

---

## 9. Acceptance checklist (future implementer)

- [ ] `generateStaticParams()` is driven only by `project.json` folders.
- [ ] `/projects/wonder_feed` renders the three migrated markdown files.
- [ ] `/projects/aiwake` renders telemetry when `architecture.json` exists.
- [ ] Registry stubs (`status: "registry"`) still get a route and an honest empty output beat.
- [ ] Images resolve via `/showcase/images/[slug]/…`, never `docs/architecture/`.
- [ ] Missing showcase folder → HTTP 200 empty index.
- [ ] `schema_version` mismatch on telemetry → skip graphs, keep `project.json`.
- [ ] Zero `from "channels_config"` / `from "core.economic_reel_lofi"` imports.
- [ ] `prefers-reduced-motion` disables parallax and autoplay.

When the app is built, this file remains the spec. Code that contradicts §3–§4 loses.
