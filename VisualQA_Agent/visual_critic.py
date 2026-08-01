# -*- coding: utf-8 -*-
"""
Multimodal Visual Critic — Gemini Vision Art Director for Master Mei rigor.

Loads the candidate + ALL ``style_reference`` images and returns strict JSON:
``score``, ``passed``, ``flaws``, ``fix_instructions``.

HARD CAP: clean fitness / studio / commercial looks → score must be < 4.0.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any, Sequence

from google import genai
from google.genai import types
from pydantic import BaseModel, Field, field_validator, model_validator
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from VisualQA_Agent import config

_LOG = logging.getLogger(__name__)
_console = Console()

_CLEAN_MODEL_FLAW_RE = re.compile(
    r"fitness\s*model|gym[\s-]?bro|clean[\s-]?skin|handsome|studio[\s-]?lit|"
    r"studio\s+lighting|softbox|commercial\s+fashion|fashion\s+shoot|"
    r"polished\s+3d|3d\s+render|underwear|calendar\s+model|influencer|"
    r"smooth\s+skin|airbrushed|beauty\s+portrait|clean[\s-]?shaven\s+model",
    re.IGNORECASE,
)


class CriticVerdict(BaseModel):
    """Structured Art Director evaluation (Gemini response_schema)."""

    score: float = Field(..., ge=0.0, le=10.0, description="Quality score 0–10.")
    passed: bool = Field(
        ..., description="True only if score >= QUALITY_THRESHOLD and no hard flaws."
    )
    flaws: list[str] = Field(
        default_factory=list,
        description="Concrete visual flaws found in the generated image.",
    )
    fix_instructions: str = Field(
        default="",
        description="Precise rewrite instructions for the next prompt attempt.",
    )

    # Back-compat aliases for older agent_loop callers
    @property
    def detected_flaws(self) -> list[str]:
        return self.flaws

    @property
    def prompt_fix_instructions(self) -> str:
        return self.fix_instructions

    @field_validator("score")
    @classmethod
    def _round_score(cls, v: float) -> float:
        return round(float(v), 2)

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "flaws" not in data and "detected_flaws" in data:
                data["flaws"] = data.pop("detected_flaws")
            if "fix_instructions" not in data and "prompt_fix_instructions" in data:
                data["fix_instructions"] = data.pop("prompt_fix_instructions")
        return data


def _mime_for(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    if guessed:
        return guessed
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    return "image/jpeg"


def _load_image_part(path: Path) -> types.Part:
    data = path.read_bytes()
    if not data:
        raise ValueError(f"Empty image file: {path}")
    return types.Part.from_bytes(data=data, mime_type=_mime_for(path))


def list_reference_images(style_folder: Path | str) -> list[Path]:
    """Load ALL image files from the channel style_reference folder."""
    folder = Path(style_folder)
    if not folder.is_dir():
        return []
    return [
        p for p in sorted(folder.iterdir(), key=lambda x: x.name.lower())
        if p.is_file() and p.suffix.lower() in config.IMAGE_EXTENSIONS
    ]


def _build_critic_instruction(
    channel_name: str,
    rules: dict[str, Any],
    quality_threshold: float,
    hard_cap: float,
    n_refs: int,
) -> str:
    forbidden = ", ".join(rules.get("forbidden_tokens") or [])
    mandatory = ", ".join(rules.get("mandatory_elements") or [])
    lighting = rules.get("lighting_style") or ""
    audience = rules.get("target_audience_rules") or ""
    anchor = config.MASTER_STYLE_ANCHOR

    return f"""
You are a ruthless Art Director for channel "{channel_name}".

You are given:
- IMAGE 1 = NEW CANDIDATE (judge the PIXELS, not the prompt text)
- REFERENCE STYLE IMAGES 1–{n_refs} = the ONLY accepted visual DNA

Compare the candidate PIXEL-BY-PIXEL against the reference images and CHANNEL RULES.

CHANNEL RULES:
- FORBIDDEN: {forbidden}
- MANDATORY: {mandatory}
- LIGHTING: {lighting}
- AUDIENCE: {audience}
- TARGET STYLE ANCHOR: {anchor}

════════════════════════════════════════
HARD CAP RULE (MANDATORY — NON-NEGOTIABLE):
If the candidate looks like ANY of the following, you MUST assign score < {hard_cap}
(typically 1.0–3.5) REGARDLESS of narrative prompt match:
  • clean-skinned fitness model / gym-bro / handsome actor
  • studio-lit portrait / softbox beauty lighting
  • polished 3D render / CGI toy look
  • commercial fashion shoot / underwear-ad aesthetic
  • smooth airbrushed skin with no industrial grit
Do NOT give such images a score of 4.0 or higher. Cap is MAX {hard_cap - 0.01:.2f}.
════════════════════════════════════════

VISUAL TEXTURE REQUIREMENT (must match references):
- Raw 35mm physical film grain (not digital noise overlay only)
- Dirty rusted metal, unpolished practical FX
- Cables / copper conduits VISIBLY piercing flesh with scar tissue
- Grim dystopian industrial lighting matching the reference images
- Gaunt, scarred, exhausted industrial subjects — NOT fitness physiques

EVALUATE:
1) Anatomy / Artifacts — fitness models, clean skin, nipples/genitalia, weird hands,
   floating objects, deformed limbs, underwear-ad posing.
2) Atmospheric & 80s Biomechanical Style — H.R. Giger practical body horror adherence.
3) Reference DNA match — same grit, metal, cable-flesh integration as refs.

SCORING GUIDE:
- 9.0–10.0: production-ready hardcore body horror matching refs
- {quality_threshold}–8.9: minor issues only, still approved territory
- 4.0–{quality_threshold - 0.01:.1f}: wrong texture / weak biomech — FAIL
- Below {hard_cap}: CLEAN MODEL / STUDIO / COMMERCIAL HARD FAIL

Set passed=true ONLY if score >= {quality_threshold} AND no hard aesthetic flaws.

Return JSON fields exactly:
  score (float 0–10),
  passed (boolean),
  flaws (list of strings),
  fix_instructions (string — actionable FLUX rewrite directives; empty if passed).
""".strip()


def _apply_hard_cap(verdict: CriticVerdict, hard_cap: float) -> CriticVerdict:
    """Local enforcement: clean-model fingerprints force score below hard_cap."""
    blob = " ".join(verdict.flaws) + " " + (verdict.fix_instructions or "")
    triggered = bool(_CLEAN_MODEL_FLAW_RE.search(blob))

    # Also scan flaw list for common soft refusals that still imply commercial look
    soft_triggers = (
        "too clean", "too polished", "looks like a model", "catalog",
        "commercial", "studio", "fitness", "handsome", "smooth skin",
    )
    lo = blob.lower()
    if any(t in lo for t in soft_triggers):
        triggered = True

    if triggered and verdict.score >= hard_cap:
        _console.print(
            f"[red]HARD CAP[/red] clean/studio fingerprint detected — "
            f"clamping score {verdict.score:.2f} → {hard_cap - 0.5:.2f}"
        )
        verdict.score = round(hard_cap - 0.5, 2)
        verdict.passed = False
        if "HARD CAP" not in " ".join(verdict.flaws):
            verdict.flaws = list(verdict.flaws) + [
                "HARD CAP: clean-model / studio / commercial aesthetic detected"
            ]
        if not verdict.fix_instructions:
            verdict.fix_instructions = (
                "Describe sleek cybernetic digital servitude — hypnotized face lit by "
                "screen glare, metallic neural collar, luminous fiber-optic tethers to "
                "smartphone/VR, rain-slicked dystopian alley, gritty 35mm film grain. "
                "Never gore, blood, open wounds, or gym fitness apparel."
            )
    return verdict


def evaluate_image(
    generated_image: Path | str | bytes,
    *,
    channel_name: str,
    rules: dict[str, Any],
    style_folder: Path | str | None = None,
    reference_paths: Sequence[Path | str] | None = None,
    quality_threshold: float | None = None,
) -> CriticVerdict:
    """
    Judge a generated image with Gemini Vision against ALL style refs + rules.
    """
    if not config.GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY missing — set env or factory .env before critic runs."
        )

    threshold = (
        float(quality_threshold)
        if quality_threshold is not None
        else float(config.QUALITY_THRESHOLD)
    )
    hard_cap = float(config.CLEAN_MODEL_HARD_CAP)

    if isinstance(generated_image, (bytes, bytearray)):
        candidate_part = types.Part.from_bytes(
            data=bytes(generated_image), mime_type="image/png"
        )
        candidate_label = "<bytes>"
    else:
        candidate_path = Path(generated_image)
        if not candidate_path.is_file():
            raise FileNotFoundError(f"Generated image not found: {candidate_path}")
        candidate_part = _load_image_part(candidate_path)
        candidate_label = str(candidate_path)

    if reference_paths:
        refs = [Path(p) for p in reference_paths if Path(p).is_file()]
    else:
        folder = Path(style_folder) if style_folder else config.style_folder_for_channel(
            channel_name
        )
        refs = list_reference_images(folder)

    contents: list[Any] = [
        "IMAGE 1 — NEW CANDIDATE TO JUDGE (inspect pixels):",
        candidate_part,
    ]
    for i, ref in enumerate(refs, start=1):
        try:
            contents.append(f"REFERENCE STYLE IMAGE {i}: {ref.name}")
            contents.append(_load_image_part(ref))
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("CRITIC | skip ref %s (%s)", ref, exc)

    if not refs:
        _console.print(
            "[yellow]CRITIC WARNING[/yellow] no style_reference images found — "
            "judging from channel rules only"
        )

    contents.append(
        _build_critic_instruction(
            channel_name, rules, threshold, hard_cap, n_refs=len(refs),
        )
    )

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    model_id = config.GEMINI_CRITIC_MODEL
    if not model_id.startswith("models/"):
        model_id = f"models/{model_id}"

    _console.print(
        f"[cyan]CRITIC[/cyan] Gemini Vision | candidate={candidate_label} | "
        f"refs={len(refs)} (ALL loaded) | channel={channel_name} | model={model_id}"
    )

    response = client.models.generate_content(
        model=model_id,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CriticVerdict,
            temperature=0.15,
        ),
    )

    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, CriticVerdict):
        verdict = parsed
    elif parsed is not None and hasattr(parsed, "model_dump"):
        verdict = CriticVerdict.model_validate(parsed.model_dump())
    else:
        text = (getattr(response, "text", None) or "").strip()
        if not text:
            raise RuntimeError("Gemini critic returned empty response")
        # Tolerate fenced JSON
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].lstrip()
        try:
            verdict = CriticVerdict.model_validate_json(text)
        except Exception:
            verdict = CriticVerdict.model_validate(json.loads(text))

    verdict = _apply_hard_cap(verdict, hard_cap)

    if verdict.score < threshold:
        verdict.passed = False
    elif verdict.passed and verdict.score >= threshold:
        verdict.passed = True

    # Terminal: exact raw critique score
    table = Table(title="Gemini Vision Critic — Raw Verdict", show_header=True)
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("score", f"{verdict.score:.2f} / 10.0")
    table.add_row("passed", str(verdict.passed))
    table.add_row("flaws", "; ".join(verdict.flaws) or "(none)")
    table.add_row("fix_instructions", verdict.fix_instructions or "(none)")
    _console.print(table)
    _console.print(
        Panel(
            json.dumps(verdict.model_dump(), indent=2),
            title="[bold]RAW CRITIC JSON[/bold]",
            border_style="cyan",
        )
    )

    if config.LOG_COSTS:
        _console.print(
            f"[dim]COST[/dim] critic≈${config.COST_GEMINI_FLASH_USD:.5f}"
        )

    _LOG.info(
        "CRITIC | channel=%s score=%.2f passed=%s flaws=%s",
        channel_name, verdict.score, verdict.passed, verdict.flaws,
    )
    return verdict
