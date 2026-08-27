# Portfolio Showcase

This module is completely decoupled from every factory engine. It listens to optional core events (Aiwake EventBus) and holds **all** recruiter-facing architecture artifacts. It can be safely removed without affecting generation, TTS, or publishing.

## Layout

```
portfolio_showcase/
  FRONTEND_BLUEPRINT.md   # Next.js multi-project spec (do not implement the app from here)
  core/                   # event-bus plugin + telemetry writers
  tools/                  # PNG/doc generators (not ingested by the frontend)
  data/
    global_timeline.json
    projects/[slug]/      # project.json + telemetry JSON + narrative MD/MDX
  assets/
    images/[slug]/
    videos/[slug]/
    canvas/[slug]/
```

## Contract

Engines never import this package. Aiwake loads `core/plugin.py` **by file path** when present; if this folder is deleted, events go nowhere.

Per-run Aiwake telemetry writes only to:

`data/projects/aiwake/architecture.json`
