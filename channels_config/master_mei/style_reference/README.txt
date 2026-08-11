Master Mei — Style Reference Images
====================================

Drop 2-3 key reference images here (*.png, *.jpg, *.jpeg) to lock the
biomechanical cyberpunk / body-horror aesthetic (Module 3 — Modular Style
Reference Configuration).

- Loaded dynamically at runtime by page_loader.PageContext.resolve_style_reference_images().
- Sorted by filename; capped at STYLE_REFERENCE_MAX_IMAGES (default 3) from page_config.py.
- Applied with style weight STYLE_REFERENCE_WEIGHT (default 0.72, recommended 0.65-0.80).
- Used as genuine image-conditioned style reference on the Gemini reference-image path
  (avatar-locked Master Mei frames). The default FLUX.1-schnell backend used for B-roll
  shots does not support image-conditioned style transfer, so on that path these images
  are noted in the prompt as a textual style directive only.

This directory is empty by default (no bundled images) — content generation falls
back gracefully to text-only prompts (MASTER_STYLE_ANCHOR + concrete visual prompt)
until reference images are added here.
