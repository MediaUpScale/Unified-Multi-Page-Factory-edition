# -*- coding: utf-8 -*-
"""Full-canvas snapshot of lofi-four-stage-pipeline.canvas.tsx → PNG."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_SHOWCASE = Path(__file__).resolve().parents[1]
_IMG = _SHOWCASE / "assets" / "images" / "wonder_feed"
OUT = _IMG / "lofi_four_stage_pipeline_20260825.png"
OUT_DARK = _IMG / "lofi_four_stage_pipeline_20260825_dark.png"
W, H = 1600, 3400

LIGHT = {
    "bg": (250, 250, 249),
    "ink": (28, 28, 26),
    "muted": (90, 90, 86),
    "line": (210, 210, 204),
    "accent": (180, 110, 20),
    "info": (30, 90, 140),
    "ok": (30, 110, 70),
    "danger": (150, 40, 40),
    "warn_bg": (255, 244, 228),
    "info_bg": (232, 242, 250),
    "ok_bg": (232, 246, 236),
    "danger_bg": (255, 236, 236),
    "gate_fill": (255, 248, 236),
    "stage_fill": (240, 240, 236),
    "card": (255, 255, 255),
    "head_bg": (236, 236, 232),
    "title": (28, 28, 26),
    "h2_pipeline": (28, 28, 26),
    "h2_cost": (28, 28, 26),
    "h2_audit": (28, 28, 26),
    "node_stage": (28, 28, 26),
    "node_gate": (28, 28, 26),
    "intro": (90, 90, 86),
}
DARK = {
    "bg": (6, 6, 8),
    "ink": (226, 232, 255),
    "muted": (150, 158, 190),
    "line": (42, 46, 64),
    "accent": (255, 196, 72),
    "info": (80, 220, 255),
    "ok": (90, 255, 170),
    "danger": (255, 92, 148),
    "warn_bg": (48, 28, 8),
    "info_bg": (8, 36, 52),
    "ok_bg": (8, 42, 28),
    "danger_bg": (48, 12, 28),
    "gate_fill": (48, 32, 8),
    "stage_fill": (12, 22, 40),
    "card": (14, 14, 18),
    "head_bg": (22, 24, 36),
    "title": (80, 220, 255),
    "h2_pipeline": (255, 120, 200),
    "h2_cost": (255, 196, 72),
    "h2_audit": (90, 255, 170),
    "node_stage": (80, 220, 255),
    "node_gate": (255, 196, 72),
    "intro": (186, 160, 255),
}


def apply_theme(t: dict) -> None:
    global BG, INK, MUTED, LINE, ACCENT, INFO, OK, DANGER
    global WARN_BG, INFO_BG, GATE_FILL, STAGE_FILL, WHITE
    global TITLE, H2_PIPELINE, H2_COST, H2_AUDIT, NODE_STAGE, NODE_GATE, INTRO
    global OK_BG, DANGER_BG, HEAD_BG
    BG = t["bg"]
    INK = t["ink"]
    MUTED = t["muted"]
    LINE = t["line"]
    ACCENT = t["accent"]
    INFO = t["info"]
    OK = t["ok"]
    DANGER = t["danger"]
    WARN_BG = t["warn_bg"]
    INFO_BG = t["info_bg"]
    GATE_FILL = t["gate_fill"]
    STAGE_FILL = t["stage_fill"]
    WHITE = t["card"]
    TITLE = t["title"]
    H2_PIPELINE = t["h2_pipeline"]
    H2_COST = t["h2_cost"]
    H2_AUDIT = t["h2_audit"]
    NODE_STAGE = t["node_stage"]
    NODE_GATE = t["node_gate"]
    INTRO = t["intro"]
    OK_BG = t["ok_bg"]
    DANGER_BG = t["danger_bg"]
    HEAD_BG = t["head_bg"]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = (
        ("segoeuib.ttf", "segoeui.ttf")
        if bold
        else ("segoeui.ttf", "arial.ttf")
    )
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    return ImageFont.load_default()


F_H1 = font(28, True)
F_H2 = font(18, True)
F_BODY = font(14)
F_SMALL = font(12)
F_TINY = font(11)
F_STAT = font(20, True)


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, width: int) -> list[str]:
    words = text.replace("\n", " ").split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=fnt) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def rounded(draw: ImageDraw.ImageDraw, box, fill, outline=None, r=8):
    draw.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=1)


def main(*, dark: bool = False, out: Path | None = None) -> None:
    apply_theme(DARK if dark else LIGHT)
    dest = out or (OUT_DARK if dark else OUT)
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    x, y = 48, 36

    d.text((x, y), "LOFI four-stage pipeline — 2026-08-25", font=F_H1, fill=TITLE)
    y += 42
    intro = (
        "Rebuild after the riso_retro_flat_v4 audit. Live path: core/economic_reel_lofi. "
        "Style lives in style_modules/riso_retro_flat_v4.py. Default review_required=true — "
        "no Flux or TTS before Gate 2."
    )
    for line in wrap(d, intro, F_BODY, W - 96):
        d.text((x, y), line, font=F_BODY, fill=INTRO)
        y += 20
    y += 16

    stats = [
        ("4 stages", "Script → concept → prompt → image+VO", INFO),
        ("2 gates", "Human hold; --lofi-no-review auto-passes", ACCENT),
        ("Retired", "_EPISODE_WORLDS no longer sources setting", DANGER),
        ("One module", "riso_retro_flat_v4 swap = zero Stage 1–2 edits", INK),
    ]
    sw = (W - 96 - 36) // 4
    for i, (val, lab, col) in enumerate(stats):
        sx = x + i * (sw + 12)
        rounded(d, (sx, y, sx + sw, y + 88), WHITE, LINE)
        d.text((sx + 14, y + 12), val, font=F_STAT, fill=col)
        ly = y + 44
        for ln in wrap(d, lab, F_TINY, sw - 28):
            d.text((sx + 14, ly), ln, font=F_TINY, fill=MUTED)
            ly += 14
    y += 108

    # Audit callout
    rounded(d, (x, y, W - 48, y + 92), WARN_BG, ACCENT, r=8)
    d.text((x + 16, y + 10), "What the audit said the old loop was", font=F_H2, fill=ACCENT)
    callout = (
        "Cond named the room three times (scene + world + “object is part of the environment”). "
        "CFG 5.5 cannot lose to a blanket kettle/window negative. object-gate skipped when "
        "key_object was mug. Retry asked for “detailed interior objects.” warm_frac treated "
        "a mug macro as a sunset doorway."
    )
    cy = y + 38
    for ln in wrap(d, callout, F_SMALL, W - 128):
        d.text((x + 16, cy), ln, font=F_SMALL, fill=INK)
        cy += 16
    y += 108

    d.text((x, y), "New pipeline", font=F_H2, fill=H2_PIPELINE)
    y += 26
    d.text(
        (x, y),
        "Accent nodes are review gates. Image and TTS sit only in Stage 4.",
        font=F_SMALL,
        fill=MUTED,
    )
    y += 32

    nodes = [
        ("Stage 1 Script", False),
        ("Gate 1", True),
        ("Stage 2 Concept", False),
        ("Stage 3 Prompt", False),
        ("Gate 2", True),
        ("Stage 4 Image+VO", False),
    ]
    nw, nh, gap = 200, 48, 22
    total = 6 * nw + 5 * gap
    start = (W - total) // 2
    ny = y
    for i, (label, gate) in enumerate(nodes):
        nx = start + i * (nw + gap)
        fill = GATE_FILL if gate else STAGE_FILL
        outline = ACCENT if gate else (120, 120, 114)
        rounded(d, (nx, ny, nx + nw, ny + nh), fill, outline, r=6)
        tw = d.textlength(label, font=F_SMALL)
        d.text((nx + (nw - tw) / 2, ny + 15), label, font=F_SMALL, fill=NODE_GATE if gate else NODE_STAGE)
        if i < 5:
            d.line(
                (nx + nw, ny + nh / 2, nx + nw + gap, ny + nh / 2),
                fill=(160, 160, 154),
                width=2,
            )
    y += 72

    # Stage / Input / Output / Cost
    d.text((x, y), "Stage / Input / Output / Cost", font=F_H2, fill=H2_COST)
    y += 28
    headers = ["Stage", "Input", "Output", "Cost"]
    col_w = [170, 340, 720, 210]
    rows = [
        (
            "1 Script",
            "duration_s, niche stub, RAG writer",
            "Spoken lines + scene count + 3s timing. Visual fields stripped.",
            "LLM writer only",
            None,
        ),
        (
            "Gate 1",
            "review_required",
            "HOLD until --lofi-approve-gate 1. Locked --lofi-script auto-clears.",
            "None",
            WARN_BG,
        ),
        (
            "2 Concept",
            "Approved beats + character RAG appearance",
            "Per-beat visual_concept, setting, licensed_objects, not_in_frame. Place locked after beat 1.",
            "LLM translator only",
            None,
        ),
        (
            "3 Prompt",
            "Style module + concept + episode_anchor_stem",
            "One scene paragraph + derived negative. No Flux/TTS. Then Gate 2.",
            "None",
            INFO_BG,
        ),
        (
            "Gate 2",
            "Assembled visual_prompt + negative_prompt",
            "HOLD until --lofi-approve-gate 2. Human reviews the actual Flux prompts, not concept text alone.",
            "None",
            WARN_BG,
        ),
        (
            "4 Image + VO",
            "Assembled prompt",
            "Flux stills, QA gates, then ElevenLabs VO (stills-only skips VO).",
            "Image + TTS",
            OK_BG,
        ),
    ]
    hx = x
    rounded(d, (x, y, W - 48, y + 28), HEAD_BG, LINE, r=0)
    for h, w in zip(headers, col_w):
        d.text((hx + 8, y + 6), h, font=F_SMALL, fill=MUTED)
        hx += w
    y += 28
    for stage, inp, out, cost, bg in rows:
        cells = [stage, inp, out, cost]
        wrapped = [wrap(d, c, F_TINY, cw - 16) for c, cw in zip(cells, col_w)]
        rh = 10 + 14 * max(len(w) for w in wrapped)
        rounded(d, (x, y, W - 48, y + rh), bg or WHITE, LINE, r=0)
        cx = x
        for lines, cw in zip(wrapped, col_w):
            ty = y + 6
            for ln in lines:
                d.text((cx + 8, ty), ln, font=F_TINY, fill=INK)
                ty += 14
            cx += cw
        y += rh
    y += 24

    d.text((x, y), "Audit finding → change", font=F_H2, fill=H2_AUDIT)
    y += 28
    a_headers = ["Audit finding", "Change", "Where"]
    a_w = [380, 820, 240]
    a_rows = [
        (
            "Kitchen restated 3× on figure beats",
            "Stage 2 writes one scene_description. Assembler uses that paragraph; no world clause, no “object is part of the environment”.",
            "assemble_v2_prompt_dev",
            DANGER_BG,
        ),
        (
            "_EPISODE_WORLDS hardcoded zones per pack",
            "Retired as source. Stage 2 LLM decides place from the spoken line and carries it across beats 2–9.",
            "visual_concept.py",
            DANGER_BG,
        ),
        (
            "Static negative + stem subtraction cannot beat cond",
            "Negative = style-only list + per-beat not_in_frame from the concept. No global kettle/jar/window list.",
            "compose_beat_negative",
            WARN_BG,
        ),
        (
            "episode_anchor_stem unused (variety only)",
            "Copied onto every beat and injected: “Episode motif {stem}: keep that object recognizable when it is in frame.”",
            "apply_v2_prompts_to_lines_dev",
            INFO_BG,
        ),
        (
            "object-gate skipped when key_object is mug",
            "Mug blob is treated as intended; extra compact blobs still fail. Gate stays on.",
            "assess_default_object_intrusion",
            DANGER_BG,
        ),
        (
            "Retry “detailed interior objects”",
            "Linework guard is style-module outlines/grain/planes only. Clutter phrasing stripped if it ever returns.",
            "_retry_prompt_extras + StyleConfig.linework_guard",
            DANGER_BG,
        ),
        (
            "warm_frac mug macro → sunset_doorway / sunset_disc",
            "macro_no_setting / object_focus never take sunset labels. Thresholds live on the style module.",
            "pixel_lighting_label / _episode_style_class",
            WARN_BG,
        ),
        (
            "VisualQA and spoken_prop independently decided unspoken",
            "licensed_objects_for_beat is the shared list. Critic prompt names LICENSED OBJECTS; spoken_prop reads the same stems.",
            "licensed_objects.py",
            WARN_BG,
        ),
        (
            "VO before stills / before concept review",
            "ElevenLabs runs in Stage 4 only, after Gate 2.",
            "_produce_one",
            INFO_BG,
        ),
    ]
    hx = x
    rounded(d, (x, y, W - 48, y + 28), HEAD_BG, LINE, r=0)
    for h, w in zip(a_headers, a_w):
        d.text((hx + 8, y + 6), h, font=F_SMALL, fill=MUTED)
        hx += w
    y += 28
    for finding, change, where, bg in a_rows:
        cells = [finding, change, where]
        wrapped = [wrap(d, c, F_TINY, cw - 16) for c, cw in zip(cells, a_w)]
        rh = 10 + 14 * max(len(w) for w in wrapped)
        rounded(d, (x, y, W - 48, y + rh), bg, LINE, r=0)
        cx = x
        for lines, cw in zip(wrapped, a_w):
            ty = y + 6
            for ln in lines:
                d.text((cx + 8, ty), ln, font=F_TINY, fill=INK)
                ty += 14
            cx += cw
        y += rh
    y += 24

    card_w = (W - 96 - 16) // 2
    ch = 150
    rounded(d, (x, y, x + card_w, y + ch), WHITE, LINE)
    d.text((x + 16, y + 12), "Isolated from story", font=F_H2, fill=INK)
    d.text((x + card_w - 150, y + 14), "style module", font=F_TINY, fill=ACCENT)
    body = (
        "style_modules/riso_retro_flat_v4.py owns open/technique, model, CFG 5.5, "
        "LoRA slots, linework/exposure guards, style_negative, palettes, and warm_frac "
        "thresholds. Swap LOFI_STYLE_MODULE later without touching Stage 1–2 or QA boolean logic."
    )
    by = y + 42
    for ln in wrap(d, body, F_TINY, card_w - 32):
        d.text((x + 16, by), ln, font=F_TINY, fill=INK)
        by += 14

    cx = x + card_w + 16
    rounded(d, (cx, y, cx + card_w, y + ch), WHITE, LINE)
    d.text((cx + 16, y + 12), "Niche config (future)", font=F_H2, fill=INK)
    d.text((cx + card_w - 70, y + 14), "stub", font=F_TINY, fill=MUTED)
    body2 = (
        "niche_config.py exposes get_active_niche(). Only relationship_reflective is live. "
        "Parenting is a stub. Stage 1 logs the niche id; do not block this round on a second niche."
    )
    by = y + 42
    for ln in wrap(d, body2, F_TINY, card_w - 32):
        d.text((cx + 16, by), ln, font=F_TINY, fill=INK)
        by += 14
    y += ch + 20

    rounded(d, (x, y, W - 48, y + 56), INFO_BG, INFO)
    d.text((x + 16, y + 8), "Duration", font=F_H2, fill=INFO)
    d.text(
        (x + 16, y + 32),
        "Default 27s → 9 × 3s unchanged. Larger duration_s adds beats at 3s (45s → 15, 90s → 30). Cap MAX_DURATION_S=90.",
        font=F_SMALL,
        fill=INK,
    )
    y += 72
    footer = (
        "Resume: --lofi-resume-from lofi_pipeline_*.json --lofi-approve-gate 1|2.  "
        "Auto-pass: --lofi-no-review.  Code: visual_concept.py, review_gates.py, "
        "style_modules/, licensed_objects.py, pipeline._produce_one."
    )
    for ln in wrap(d, footer, F_TINY, W - 96):
        d.text((x, y), ln, font=F_TINY, fill=MUTED)
        y += 14

    crop = img.crop((0, 0, W, min(H, y + 40)))
    dest.parent.mkdir(parents=True, exist_ok=True)
    crop.save(dest, "PNG")
    print(f"wrote {dest} {crop.size}")


if __name__ == "__main__":
    main(dark=False, out=OUT)
    main(dark=True, out=OUT_DARK)
