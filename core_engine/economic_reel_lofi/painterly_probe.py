# -*- coding: utf-8 -*-
"""
Isolated FLUX.1-schnell painterly stills probe.

Does NOT run a reel, does NOT touch RAG stores, does NOT change the live
visual identity bank. Custom negative prompt omits the production bans on
painterly lighting / linework so we can see whether Schnell can approach
the reference illustrated look at all.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core_engine.economic_reel_lofi.image_gen import LOFI_IMAGE_MODEL, LOFI_IMAGE_STEPS

PAINTERLY_STYLE = (
    "painterly gouache illustration, visible brushstrokes, soft color blends, "
    "storybook editorial illustration, warm textured paper, cinematic still, "
    "rich mixed pigments, atmospheric color, not a comic, not risograph, "
    "not halftone poster, not ink lineart"
)

PAINTERLY_NEGATIVE = (
    "photorealistic, photograph, 3d render, cgi, comic panel, manga, anime, "
    "risograph, halftone dots, heavy black ink outlines, woodcut, "
    "text, watermark, logo, typography, subtitles, ui, nsfw, nude, "
    "deformed hands, extra fingers"
)

PROBES: tuple[dict[str, str], ...] = (
    {
        "id": "wf_betrayal",
        "prompt": (
            f"{PAINTERLY_STYLE}. Vertical 9:16. A woman standing in a dim kitchen "
            "at night, looking down at a phone face-down on the table, unread "
            "texts faintly lighting her hands, quiet betrayal, mid shot, "
            "foreground: steam from a forgotten mug, background: dark window."
        ),
    },
    {
        "id": "wf_detachment",
        "prompt": (
            f"{PAINTERLY_STYLE}. Vertical 9:16. A person walking away down a "
            "dusk city street, coat moving, back turned, letting go, wide "
            "environmental shot, foreground: fallen leaves, background: "
            "warm apartment windows and a fading sky."
        ),
    },
    {
        "id": "mc_repair",
        "prompt": (
            f"{PAINTERLY_STYLE}. Vertical 9:16. A mother and small child hugging "
            "in warm silhouette against a kitchen window at dusk, repair after "
            "a hard moment, central framing, foreground: a small stuffed bear "
            "on the floor, background: golden interior light and evening sky."
        ),
    },
)


def _engine_root() -> Path:
    return Path(__file__).resolve().parents[2]


def run_painterly_probe(page_id: str = "wonder_feed") -> Path:
    from avatar_engine.providers.together_image import TogetherImageGenerator

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = (
        _engine_root()
        / "outputs"
        / page_id
        / "clips"
        / "economic_reels_tests"
        / f"painterly_probe_{stamp}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    gen = TogetherImageGenerator(model=LOFI_IMAGE_MODEL)
    print(f"[LOFI painterly probe] dir={out_dir}")
    print(
        f"[LOFI painterly probe] model={LOFI_IMAGE_MODEL} steps={LOFI_IMAGE_STEPS} "
        "lora=OFF | production visual identity NOT used"
    )
    for item in PROBES:
        dest = out_dir / f"{item['id']}.png"
        print(f"[LOFI painterly probe] generating {item['id']} -> {dest.name}")
        print(f"[LOFI painterly probe] prompt={item['prompt']!r}")
        gen.generate_image(
            item["prompt"],
            dest,
            orientation="vertical",
            width=768,
            height=1344,
            negative_prompt=PAINTERLY_NEGATIVE,
            model_name=LOFI_IMAGE_MODEL,
            steps=LOFI_IMAGE_STEPS,
            allow_lora=False,
        )
        if not dest.is_file():
            raise FileNotFoundError(f"painterly probe missing output: {dest}")
        print(f"[LOFI painterly probe] wrote {dest} ({dest.stat().st_size} bytes)")
    return out_dir


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv

        load_dotenv(_engine_root() / ".env", override=False)
    except Exception:  # noqa: BLE001
        pass
    path = run_painterly_probe()
    print(f"[LOFI painterly probe] DONE {path}")
