# -*- coding: utf-8 -*-
"""
Together AI image generation — dynamic model selection within Together.

Hard default: ``black-forest-labs/FLUX.1-schnell`` (ultra cost-efficient).
Override via ``TOGETHER_IMAGE_MODEL`` env or per-call ``model_name=``.
"""
from __future__ import annotations

import base64
import contextlib
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FLUX_SCHNELL_MODEL: str = "black-forest-labs/FLUX.1-schnell"
FLUX_DEV_MODEL: str = "black-forest-labs/FLUX.1-dev"
FLUX_DEFAULT_STEPS: int = 4
FLUX_DEV_DEFAULT_STEPS: int = 28
SDXL_DEFAULT_STEPS: int = 20

# Estimated USD per image (Together AI approximate mid-2026 rates)
FLUX_COST_USD: float = 0.003  # Schnell default (back-compat alias)

_TOGETHER_COST_TABLE: dict[str, float] = {
    "flux.1-schnell": 0.003,
    "flux-schnell": 0.003,
    "flux.1-dev": 0.025,
    "flux-dev": 0.025,
    "flux.1-pro": 0.050,
    "flux-pro": 0.050,
    "flux.1-pro-1.1": 0.050,
    "sdxl": 0.008,
    "stable-diffusion-xl": 0.008,
    "stable-diffusion": 0.006,
}

# Aspect presets — native portrait for all outputs (including --test-images)
_ORIENTATION_SIZE: dict[str, tuple[int, int]] = {
    "horizontal": (1344, 768),  # 16:9
    "vertical": (768, 1344),    # 9:16 native
}
_DRAFT_ORIENTATION_SIZE: dict[str, tuple[int, int]] = {
    "horizontal": (1344, 768),
    "vertical": (768, 1344),
}

# ROLE C only — never append to Master / Disciple prompts
MASTER_STYLE_ANCHOR: str = (
    "dark 80s dystopian cyberpunk, brutalist concrete architecture, ancient stone "
    "monoliths, dark muted cinematic tones, heavy ash rain, rusted iron, monochrome "
    "CRT monitor walls, high contrast cinematic lighting, 35mm film grain, "
    "8k photorealistic shot"
)

# ROLE A / B — traditional organic finish (no cyberpunk bleed)
TRADITIONAL_STYLE_ANCHOR: str = (
    "cinematic 8k photorealistic raw photography, volumetric natural mist and dawn light, "
    "detailed skin textures, octane render, 35mm film grain, photorealistic shot, "
    "warm ember gold and charcoal stone, ancestral temple aesthetic"
)

MANDATORY_NEGATIVE_PROMPT: str = (
    "gore, blood, open wounds, open flesh, graphic body horror, mutilation, "
    "exposed organs, body mutilation, bloody cables, liquid dripping, "
    "repetitive VR goggle portrait, identical headset close-up, "
    "purple light, gym, tank top, sneakers, "
    "static portrait, blank stare into camera, idle pose, standing frozen, "
    "posing for camera, looking at viewer, "
    "Chinese characters, Japanese characters, Korean characters, CJK text, "
    "kanji, hangul, hiragana, katakana, oriental calligraphy on signs, "
    "Asian neon typography, Chinese neon signs, Japanese neon signs, "
    "midjourney, --ar, --no, "
    "(deformed samurai, warped body, extra limbs, mutated hands, "
    "glitched geometry, bad anatomy, blurry faces:1.5), "
    "distorted limbs, extra fingers, warped faces, glitched samurai bodies"
)

# Forbidden positive-prompt leaks (brutalist engine)
_BANNED_SOFT_RE = re.compile(
    r"\b(?:"
    r"gore|blood|bleeding|open\s+wounds?|visceral\s+horror|body\s+horror|"
    r"mutilation|grotesque|internal\s+organs?|fleshy\s+wires?|"
    r"liquid\s+dripping|mouth\s+fluid|black\s+fluid|"
    r"\bmouth\b|\bteeth\b|\btongue\b|"
    r"smartphones?|iphones?|cell\s*phones?|handheld\s+screens?|"
    r"glowing\s+collars?|neon\s+chokers?|neural\s+chokers?|neural\s+collars?|"
    r"purple\s+(?:fiber|light|neon|glow)|cyan\s+(?:optical|fiber|neon|glow)|"
    r"fiber-?optic\s+tethers?|luminous\s+purple|"
    r"tank\s*tops?|sneakers?|shorts|gym(?:\s*(?:clothes?|gear))?|"
    r"fitness\s*model|gym[\s-]?bros?|midjourney|"
    r"neon\s*sign(?:\s*(?:writing|text))?|lettering|"
    r"cyberpunk\s*monk|oriental\s*monk|meditation|"
    r"3d\s*render|cgi|H\.?\s*R\.?\s*Giger"
    r")\b[^,.]*",
    re.IGNORECASE,
)

# Legacy alias (apparel injection removed)
_APPAREL_OVERRIDE_RE = _BANNED_SOFT_RE


def normalize_together_model_id(raw: str | None) -> str:
    """Normalize a Together image model id; empty → FLUX.1-schnell."""
    name = (raw or "").strip().strip('"').strip("'")
    if not name:
        return FLUX_SCHNELL_MODEL
    # Short aliases
    low = name.lower()
    aliases = {
        "schnell": FLUX_SCHNELL_MODEL,
        "flux-schnell": FLUX_SCHNELL_MODEL,
        "flux.1-schnell": FLUX_SCHNELL_MODEL,
        "dev": FLUX_DEV_MODEL,
        "flux-dev": FLUX_DEV_MODEL,
        "flux.1-dev": FLUX_DEV_MODEL,
    }
    if low in aliases:
        return aliases[low]
    if "/" not in name and name.lower().startswith("flux"):
        return f"black-forest-labs/{name}"
    return name


def default_together_image_model() -> str:
    """
    Resolve default Together image model.

    ``TOGETHER_IMAGE_MODEL`` from env/config if set, else FLUX.1-schnell.
    """
    try:
        import config as app_config

        cfg = getattr(app_config, "TOGETHER_IMAGE_MODEL", None)
        if cfg:
            return normalize_together_model_id(str(cfg))
    except Exception:  # noqa: BLE001
        pass
    return normalize_together_model_id(os.getenv("TOGETHER_IMAGE_MODEL"))


def estimate_together_image_cost(model_id: str | None) -> float:
    """Return estimated USD cost per image for a Together model id."""
    mid = normalize_together_model_id(model_id)
    low = mid.lower()
    for key, price in _TOGETHER_COST_TABLE.items():
        if key in low:
            return float(price)
    # Unknown Together image model — conservative mid-tier estimate
    return 0.015


def cost_key_for_together_model(model_id: str | None) -> str:
    """Map Together model → CostTracker ``_PRICE`` key."""
    low = normalize_together_model_id(model_id).lower()
    if "schnell" in low:
        return "image_flux_schnell"
    if "flux.1-dev" in low or "flux-dev" in low or "/flux.1-dev" in low:
        return "image_flux_dev"
    if "flux.1-pro" in low or "flux-pro" in low:
        return "image_flux_pro"
    if "sdxl" in low or "stable-diffusion-xl" in low:
        return "image_sdxl"
    if "stable-diffusion" in low:
        return "image_sdxl"
    return "image_flux_schnell"


def default_steps_for_model(model_id: str | None) -> int:
    """Cost-efficient default step count for the active Together model."""
    low = normalize_together_model_id(model_id).lower()
    if "schnell" in low:
        return FLUX_DEFAULT_STEPS
    if "flux.1-dev" in low or "flux-dev" in low:
        return FLUX_DEV_DEFAULT_STEPS
    if "sdxl" in low or "stable-diffusion" in low:
        return SDXL_DEFAULT_STEPS
    return FLUX_DEFAULT_STEPS


# ---------------------------------------------------------------------------
# PROMPT SANITIZER — strip internal code metadata, rule-text, and Midjourney
# syntax before anything reaches the Together / FLUX API.
#
# Upstream prompt builders (mei_visual.py, sequence-reel act descriptors,
# etc.) are expected to emit clean visual language already — this is the
# LAST-LINE safety net so pollution (rule sentences, file paths, `--no`,
# `--ar`) can never leak through to FLUX, regardless of which code path
# produced the prompt. FLUX has no concept of Midjourney parameters or
# English "rule" sentences — sending them wastes prompt budget and actively
# confuses the model into hallucinating the very things being banned.
# ---------------------------------------------------------------------------

# Internal rule/metadata markers. Each one introduces a whole clause of
# code-level instruction (never visual content) — the marker AND everything
# through the next sentence terminator is dropped.
_META_RULE_MARKERS: tuple[str, ...] = (
    "ZERO HALLUCINATION RULE",
    "ZERO HALLUCINATION",
    "ZERO-HALLUCINATION",
    "AVATAR PATH",
    "STRICT SINGLE MASTER RULE",
    "STRICT CHARACTER LOCK",
    "STRICT BAN",
    "SPOKEN BEAT",
    "SHOT TYPE",
    "SHOT DURATION",
    "LITERAL NOUN ANCHORS",
    "ANTI-MONOTONY",
    "SUBJECT FREQUENCY CAP",
    "NEGATIVE EXCLUSIONS",
    "NEGATIVE PROMPT (STRICT EXCLUSIONS)",
    "REFERENCE LIKENESS",
    "CHARACTER LIKENESS",
)

# Short structural ALL-CAPS labels (e.g. "ENVIRONMENT:", "STYLE:", "ROLE:",
# "POSE (CONTROL/STILLNESS):") that sometimes precede genuinely useful
# descriptive text — strip ONLY the label itself, keep whatever text follows.
_STRUCTURAL_LABEL_RE = re.compile(
    r"\b[A-Z][A-Z][A-Z \-/]{1,38}(?:\s*\([^)]{0,60}\))?\s*:\s*"
)

# Midjourney-style CLI parameters — meaningless to the Together/FLUX API.
_MJ_NO_RE = re.compile(r"--no\b.*?(?:\.(?=\s|$)|$)", re.IGNORECASE | re.DOTALL)
_MJ_AR_RE = re.compile(r"--ar\s*\d+\s*:\s*\d+", re.IGNORECASE)

# "ACT I — ..." / "ACT 2: ..." style act/scene label PREFIXES used by some
# legacy prompt builders. Strip ONLY the label token, never the descriptive
# text that follows it — some builders put real scene content right after
# the label, so a full-clause removal here would risk deleting content.
_ACT_LABEL_RE = re.compile(
    r"\bACT\s+(?:[IVXLCDM]+|\d+)\s*(?:\([^)]{0,60}\))?\s*[—\-:]\s*",
    re.IGNORECASE,
)


# Soft location terms that contradict a dark cyberpunk wasteland register —
# strip these fragments when a cyberpunk/wasteland cue is also present so
# FLUX never sees "monastery steps / pale ice" mixed with "rust / ash storm".
_CONTRADICTORY_LOCATION_RE = re.compile(
    r"(?:,\s*)?(?:frozen\s+alpine\s+)?monastery\s+steps"
    r"|pale\s+ice"
    r"|bamboo\s+terrace"
    r"|stone\s+torii"
    r"|incense\s+smoke"
    r"|meditation\s+platform"
    r"|ancestral\s+(?:temple|samurai)\s+courtyard"
    r"|clean\s+(?:modern\s+)?(?:bedroom|room|office)"
    r"|peaceful\s+meditation",
    re.IGNORECASE,
)
_CYBER_WASTELAND_CUE_RE = re.compile(
    r"cyberpunk|wasteland|biomechanical|ash\s+storm|rust(?:ed|ing)?|"
    r"crt\s+monitor|neon|industrial\s+dungeon",
    re.IGNORECASE,
)


def _infer_visual_role(text: str) -> str:
    """Best-effort role inference when caller omits visual_role."""
    lo = (text or "").lower()
    if re.search(
        r"\b(?:master\s+mei|elder\s+sage|white\s+(?:beard|topknot)|traditional\s+linen\s+robes)\b",
        lo,
    ):
        return "master"
    if re.search(
        r"\b(?:disciple|waterfall\s+train|carrying\s+heavy\s+stones|martial\s+arts\s+disciple)\b",
        lo,
    ):
        return "disciple"
    if re.search(
        r"\b(?:vr\s+headset|digital\s+chain|neon\s+wire|cyberpunk|neural\s+cable|smartphone\s+glare)\b",
        lo,
    ):
        return "slave"
    return "slave"


def sanitize_prompt_for_flux(
    raw_prompt: str,
    *,
    visual_role: str | None = None,
) -> str:
    """
    Strip metadata / Midjourney / tactical apparel overrides.

    Role-aware (anti-contamination):
      - master / disciple → NEVER append dystopian MASTER_STYLE_ANCHOR;
        NEVER strip temple/meditation locations.
      - slave → cyberpunk anchor OK; strip temple/Mei leaks.
    """
    text = (raw_prompt or "").strip()
    if not text:
        return text

    role = (visual_role or "").strip().lower() or _infer_visual_role(text)
    if role not in ("master", "disciple", "slave"):
        role = _infer_visual_role(text)

    for marker in _META_RULE_MARKERS:
        pattern = re.compile(
            rf"{re.escape(marker)}\s*:?.*?(?:\.(?=\s|$)|$)",
            re.IGNORECASE | re.DOTALL,
        )
        text = pattern.sub(" ", text)

    text = _MJ_NO_RE.sub(" ", text)
    text = _MJ_AR_RE.sub(" ", text)
    text = _ACT_LABEL_RE.sub("", text)
    text = _STRUCTURAL_LABEL_RE.sub("", text)

    # Strip negative stuffing — bans belong in negative_prompt, not positives
    text = re.sub(
        r"(?:,\s*)?\b(?:NO|NEVER|ZERO)\s+[^,.;]+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bVisual details\s*:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bSTRICT:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bAbsolutely\s+", "", text, flags=re.IGNORECASE)

    # Soft bans are role-gated — meditation/smartphone must survive their roles
    if role == "slave":
        # Keep smartphone/VR/neon language for dystopia; strip soft lifestyle only
        soft = re.compile(
            r"\b(?:"
            r"gore|blood|bleeding|open\s+wounds?|"
            r"tank\s*tops?|sneakers?|gym(?:\s*(?:clothes?|gear))?|"
            r"fitness\s*model|midjourney|"
            r"cyberpunk\s*monk|oriental\s*monk|"
            r"3d\s*render|cgi|H\.?\s*R\.?\s*Giger"
            r")\b[^,.]*",
            re.IGNORECASE,
        )
        text = soft.sub("", text)
        # Strip accidental Master Mei / temple leaks from slave frames
        text = re.sub(
            r"\b(?:Master\s+Mei|elder\s+sage|white-?bearded\s+mentor|"
            r"ancestral\s+(?:temple|samurai)|bamboo\s+forest|tatami\s+room)[^,.]*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        if _CYBER_WASTELAND_CUE_RE.search(text):
            text = _CONTRADICTORY_LOCATION_RE.sub("", text)
            text = re.sub(
                r"\b(?:monastery|shaolin|oriental\s+monk|zen\s+garden|pale\s+ice|"
                r"peaceful\s+meditation|clean\s+modern\s+room)[^,.]*",
                "",
                text,
                flags=re.IGNORECASE,
            )
    else:
        # Master / Disciple: strip cyber implants from positives; keep temple/meditation
        soft = re.compile(
            r"\b(?:"
            r"gore|blood|bleeding|open\s+wounds?|"
            r"bionic\s+implants?|cybernetic(?:s)?|biomechanical|"
            r"vr\s+headsets?|glowing\s+wires?|neural\s+cables?|"
            r"tank\s*tops?|sneakers?|gym(?:\s*(?:clothes?|gear))?|"
            r"fitness\s*model|midjourney|"
            r"3d\s*render|cgi|H\.?\s*R\.?\s*Giger"
            r")\b[^,.]*",
            re.IGNORECASE,
        )
        text = soft.sub("", text)
        # Never strip temple locations for traditional roles

    text = re.sub(r"\s*\n\s*", ", ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"(?:\s*[,.]){2,}", ", ", text)
    text = re.sub(r"\s+,", ",", text)
    text = text.strip(" ,.\u2014-")

    lo = text.lower()
    if "35mm film grain" not in lo and "photorealistic shot" not in lo:
        if role == "slave":
            text = f"{text}, {MASTER_STYLE_ANCHOR}"
        else:
            text = f"{text}, {TRADITIONAL_STYLE_ANCHOR}"

    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"(?:\s*[,.]){2,}", ", ", text)
    return text.strip(" ,.\u2014-")


def merge_negative_prompt(role_negative: str | None = None) -> str:
    """Merge role-specific negatives into the mandatory FLUX negative string."""
    base = MANDATORY_NEGATIVE_PROMPT
    extra = (role_negative or "").strip()
    if not extra:
        return base
    return f"{base}, {extra}"


def get_mandatory_negative_prompt() -> str:
    """Hardcoded negative string appended to every Together/FLUX payload."""
    return MANDATORY_NEGATIVE_PROMPT


def aspect_ratio_to_orientation(aspect_ratio: str | None) -> str:
    """Map engine aspect ratios (3:4, 4:5, 9:16, 16:9, …) → vertical|horizontal."""
    raw = (aspect_ratio or "9:16").strip().lower().replace(" ", "")
    if raw in ("16:9", "4:3", "3:2", "21:9", "1.77", "landscape", "horizontal"):
        return "horizontal"
    return "vertical"


def orientation_to_size(orientation: str, *, draft: bool = False) -> tuple[int, int]:
    """
    Native portrait: 768×1344 vertical / 1344×768 horizontal for all outputs.
    ``draft`` is accepted for API compat but does not downscale.
    """
    del draft
    key = (orientation or "horizontal").strip().lower()
    if key in ("vertical", "portrait", "9:16"):
        return _ORIENTATION_SIZE["vertical"]
    return _ORIENTATION_SIZE["horizontal"]


# ---------------------------------------------------------------------------
# GLOBAL TOGETHER AI RATE LIMITER (process-wide, thread-safe)
#
# Bulk production launches up to N variant workers concurrently
# (ThreadPoolExecutor in main.py), each of which fires 18-22 sequential
# Together image calls. Without a global gate, N workers can still line up
# their individual calls at nearly the same instant, spiking concurrent
# requests to Together and tripping dynamic 429 rate limits.
#
# This limiter forces a HARD max of 1 in-flight Together image request at
# any time, process-wide, plus a small mandatory gap between the START of
# consecutive requests. It ONLY gates the Together HTTP call itself — LLM
# research, TTS, video compile, and uploads are untouched and keep running
# fully concurrently across workers.
# ---------------------------------------------------------------------------
class _TogetherSequentialRateLimiter:
    """Semaphore(1) + minimum inter-call spacing, shared across all threads."""

    def __init__(self, min_interval_s: float = 0.25) -> None:
        self._sema = threading.Semaphore(1)
        self._ts_lock = threading.Lock()
        self._last_call_started_at: float = 0.0
        self.min_interval_s = max(0.0, float(min_interval_s))

    @contextlib.contextmanager
    def slot(self):
        """Block until exclusive access is granted, honouring the micro-delay."""
        self._sema.acquire()
        try:
            with self._ts_lock:
                elapsed = time.monotonic() - self._last_call_started_at
                wait = self.min_interval_s - elapsed
            if wait > 0:
                time.sleep(wait)
            with self._ts_lock:
                self._last_call_started_at = time.monotonic()
            yield
        finally:
            self._sema.release()


def _resolve_rate_limit_setting(name: str, default: float) -> float:
    """Read a Together rate-limit tuning knob from config.py, env, or default."""
    try:
        import config as app_config

        val = getattr(app_config, name, None)
        if val is not None:
            return float(val)
    except Exception:  # noqa: BLE001
        pass
    try:
        return float(os.getenv(name) or default)
    except (TypeError, ValueError):
        return default


_TOGETHER_RATE_LIMITER = _TogetherSequentialRateLimiter(
    min_interval_s=_resolve_rate_limit_setting("TOGETHER_MIN_CALL_INTERVAL_S", 0.2)
)
_TOGETHER_MAX_RETRIES: int = int(_resolve_rate_limit_setting("TOGETHER_IMAGE_MAX_RETRIES", 4))
_TOGETHER_429_BACKOFF_MIN_S: float = _resolve_rate_limit_setting("TOGETHER_429_BACKOFF_MIN_S", 1.0)
_TOGETHER_429_BACKOFF_MAX_S: float = _resolve_rate_limit_setting("TOGETHER_429_BACKOFF_MAX_S", 2.5)


def _is_429_error(exc: BaseException) -> bool:
    """True for HTTP 429 / Too Many Requests / rate-limit errors."""
    status = str(
        getattr(exc, "status_code", None) or getattr(exc, "status", None) or ""
    )
    if "429" in status:
        return True
    msg = str(exc).upper()
    return "429" in msg or "TOO MANY REQUESTS" in msg or "RATE LIMIT" in msg or "RATE_LIMIT" in msg


def _is_other_retryable_error(exc: BaseException) -> bool:
    """True for transient (non-429) errors worth a retry: timeouts / 5xx / overloaded."""
    msg = str(exc).upper()
    return any(tok in msg for tok in ("TIMEOUT", "UNAVAILABLE", "503", "OVERLOADED", "CONNECTION"))


def _parse_retry_after_seconds(exc: BaseException) -> "float | None":
    """
    Best-effort extraction of a server-hinted retry delay from a 429 error —
    checks ``Retry-After`` / ``X-RateLimit-Reset*`` response headers first,
    then falls back to scanning the error message text for a numeric hint.
    """
    header_names = (
        "retry-after", "Retry-After",
        "x-ratelimit-reset", "X-RateLimit-Reset",
        "x-ratelimit-reset-requests", "X-RateLimit-Reset-Requests",
        "x-ratelimit-reset-tokens", "X-RateLimit-Reset-Tokens",
    )
    for attr in ("response", "http_response", "res"):
        resp = getattr(exc, attr, None)
        headers = getattr(resp, "headers", None) if resp is not None else None
        if not headers:
            continue
        for key in header_names:
            try:
                val = headers.get(key)
            except Exception:  # noqa: BLE001
                val = None
            if val:
                try:
                    return max(0.0, float(str(val).strip().rstrip("s")))
                except (TypeError, ValueError):
                    continue
    direct_headers = getattr(exc, "headers", None)
    if direct_headers:
        for key in header_names:
            try:
                val = direct_headers.get(key)
            except Exception:  # noqa: BLE001
                val = None
            if val:
                try:
                    return max(0.0, float(str(val).strip().rstrip("s")))
                except (TypeError, ValueError):
                    continue
    # Fallback: scan message text for "retry after 2.5s" / "try again in 3s" style hints
    match = re.search(r"(?:retry|try again)[^0-9]{0,20}(\d+(?:\.\d+)?)\s*s", str(exc), re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def _next_429_backoff(attempt: int, hinted_s: "float | None") -> float:
    """Short, precise 429 back-off — server hint when present, else a tight ladder."""
    if hinted_s is not None:
        return max(_TOGETHER_429_BACKOFF_MIN_S, min(hinted_s, _TOGETHER_429_BACKOFF_MAX_S))
    ladder = (_TOGETHER_429_BACKOFF_MIN_S, _TOGETHER_429_BACKOFF_MAX_S)
    return ladder[min(attempt - 1, len(ladder) - 1)]


class TogetherImageGenerator:
    """
    Thin Together SDK wrapper with dynamic model selection.

    Default model: FLUX.1-schnell (or ``TOGETHER_IMAGE_MODEL``).
    Per-call override: ``generate_image(..., model_name=...)``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = (api_key or os.getenv("TOGETHER_API_KEY") or "").strip()
        if not self.api_key:
            raise ValueError("TOGETHER_API_KEY missing from environment.")
        try:
            from together import Together  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "together package not installed. Run: pip install together"
            ) from exc
        self.client = Together(api_key=self.api_key)
        self.model = normalize_together_model_id(model or default_together_image_model())

    def generate_image(
        self,
        prompt: str,
        output_path: str | Path,
        orientation: str = "horizontal",
        steps: int | None = None,
        *,
        model_name: str | None = None,
        width: int | None = None,
        height: int | None = None,
        negative_prompt: str | None = None,
    ) -> str:
        """
        Generate one Together image and write bytes to *output_path*.

        Parameters
        ----------
        model_name:
            Optional per-call override (e.g. ``black-forest-labs/FLUX.1-dev``).
            Falls back to instance default / ``TOGETHER_IMAGE_MODEL`` / Schnell.
        steps:
            Diffusion steps. Defaults based on model (Schnell=4, Dev=28, SDXL=20).

        Returns the local filesystem path (str) for pipeline contract compatibility.
        """
        active_model = normalize_together_model_id(model_name or self.model)
        if width is None or height is None:
            width, height = orientation_to_size(orientation)

        if steps is None:
            steps = default_steps_for_model(active_model)
        else:
            steps = max(1, int(steps))

        # Schnell is calibrated for 4 steps — clamp runaway step counts
        if "schnell" in active_model.lower() and steps > 8:
            logger.warning(
                "FLUX Schnell steps=%d clamped to %d for cost efficiency.",
                steps, FLUX_DEFAULT_STEPS,
            )
            steps = FLUX_DEFAULT_STEPS

        est_cost = estimate_together_image_cost(active_model)
        # Quiet by default — batch loops must not spam console; summary prints once.
        _verbose = (os.getenv("MODEL_ROUTER_VERBOSE") or "").strip().lower() in (
            "1", "true", "yes", "on",
        )
        if _verbose:
            print(
                f"[Model Router] Task: image | Tier: together | Active Model: {active_model} "
                f"| Est. Cost: ${est_cost:.3f}/img",
                flush=True,
            )
        logger.debug(
            "Together image | model=%s | est_cost=$%.3f | %dx%d steps=%d",
            active_model, est_cost, width, height, steps,
        )

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        # GLOBAL RATE LIMIT: only one Together image request in flight process-wide
        # at any time, with a small mandatory gap between call starts. Fully
        # sequential across every concurrent variant worker — LLM/TTS/video/upload
        # tasks in other threads are completely unaffected.
        max_attempts = max(1, _TOGETHER_MAX_RETRIES)
        last_exc: BaseException | None = None
        for attempt in range(1, max_attempts + 1):
            retry_wait: float | None = None  # set below when this attempt should be retried
            with _TOGETHER_RATE_LIMITER.slot():
                try:
                    gen_kwargs: dict[str, Any] = {
                        "prompt": prompt,
                        "model": active_model,
                        "width": int(width),
                        "height": int(height),
                        "steps": steps,
                        "n": 1,
                        "response_format": "base64",
                    }
                    # FLUX.1-schnell supports negative_prompt on Together
                    if "schnell" in active_model.lower() or "flux.1" in active_model.lower():
                        gen_kwargs["negative_prompt"] = merge_negative_prompt(
                            negative_prompt
                        )
                    try:
                        response = self.client.images.generate(**gen_kwargs)
                    except TypeError:
                        # SDK/model may reject negative_prompt — retry without it
                        gen_kwargs.pop("negative_prompt", None)
                        response = self.client.images.generate(**gen_kwargs)
                    b64 = self._extract_b64(response)
                    image_bytes = base64.b64decode(b64)
                    with open(out, "wb") as f:
                        f.write(image_bytes)
                    if not out.is_file() or out.stat().st_size <= 0:
                        raise RuntimeError(f"Together image write produced empty file: {out}")
                    logger.info(
                        "Together image saved | model=%s | $%.3f | %dx%d steps=%d | %s",
                        active_model, est_cost, width, height, steps, out.name,
                    )
                    # Stash last-used metadata for adapters / cost tracker
                    self.last_model_used = active_model
                    self.last_estimated_cost_usd = est_cost
                    return str(out.resolve())
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    is_429 = _is_429_error(exc)
                    should_retry = attempt < max_attempts and (is_429 or _is_other_retryable_error(exc))
                    if not should_retry:
                        break  # non-retryable, or attempts exhausted — fail fast to the raise below
                    if is_429:
                        hinted = _parse_retry_after_seconds(exc)
                        retry_wait = _next_429_backoff(attempt, hinted)
                        logger.warning(
                            "Together 429 rate limit | attempt %d/%d | sleeping %.2fs%s",
                            attempt, max_attempts, retry_wait,
                            " (server-hinted)" if hinted is not None else " (fixed short backoff)",
                        )
                    else:
                        retry_wait = min(_TOGETHER_429_BACKOFF_MAX_S * attempt, 6.0)
                        logger.warning(
                            "Together transient error | attempt %d/%d (%s) | sleeping %.2fs",
                            attempt, max_attempts, exc, retry_wait,
                        )
            # Sleep OUTSIDE the rate-limit slot so other queued workers can use it
            # while this attempt backs off.
            if retry_wait is not None:
                time.sleep(retry_wait)
        raise RuntimeError(
            f"Together image generation failed ({active_model}): {last_exc}"
        ) from last_exc

    @staticmethod
    def _extract_b64(response: Any) -> str:
        data = getattr(response, "data", None) or []
        if not data:
            raise RuntimeError("Together response contained no image data.")
        item = data[0]
        for attr in ("b64_json", "b64", "base64"):
            val = getattr(item, attr, None)
            if isinstance(val, str) and val.strip():
                return val.strip()
        if isinstance(item, dict):
            for key in ("b64_json", "b64", "base64"):
                val = item.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        raise RuntimeError("Together response missing base64 image payload.")


class TogetherImageAdapter:
    """
    Pipeline-compatible adapter (same ``generate()`` contract as legacy Gemini adapter).

    Reference / style images are not passed to Together txt2img models;
    likeness guidance is appended to the prompt when avatar_mode=ON.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model_id: str | None = None,
        *,
        tier: str | None = None,
        use_premium: bool | None = None,
        page_cost_tier: str | None = None,
    ) -> None:
        import config as app_config
        from avatar_engine.providers.model_router import image_model as _route_image

        key = api_key or getattr(app_config, "TOGETHER_API_KEY", None) or os.getenv(
            "TOGETHER_API_KEY"
        )
        # Priority: explicit model_id → TOGETHER_IMAGE_MODEL → Schnell
        resolved = normalize_together_model_id(
            model_id or default_together_image_model()
        )
        self._gen = TogetherImageGenerator(api_key=key, model=resolved)
        route = _route_image(
            task="image",
            tier=tier,
            use_premium=use_premium,
            page_cost_tier=page_cost_tier,
            model_override=resolved,
            preferred=resolved,
            log=True,
        )
        self._router_tier = route.tier
        self._model_id = resolved
        self._image_chain = [resolved, FLUX_SCHNELL_MODEL] if resolved != FLUX_SCHNELL_MODEL else [resolved]
        self.last_gemini_image_model_used: str | None = resolved
        self.last_gemini_image_failure_model_id: str | None = None
        self.last_api_call_count: int = 0
        self.last_estimated_cost_usd: float = estimate_together_image_cost(resolved)
        self.last_cost_key: str = cost_key_for_together_model(resolved)
        logger.info(
            "TogetherImageAdapter | model=%s | est_cost=$%.3f/img | tier=%s",
            resolved, self.last_estimated_cost_usd, route.tier,
        )

    def generate(
        self,
        prompt: str,
        *,
        reference_image_path: Path | None = None,
        style_reference_path: Path | None = None,
        style_reference_paths: "list[Path] | None" = None,
        style_reference_weight: "float | None" = None,
        output_stem: str = "avatar_post",
        output_directory: Path | None = None,
        aspect_ratio: str | None = None,
        avatar_mode: str = "ON",
        reference_image_weight: float | None = None,
        model_name: str | None = None,
        steps: int | None = None,
        draft: bool = False,
        width: int | None = None,
        height: int | None = None,
        visual_role: str | None = None,
        negative_prompt: str | None = None,
    ) -> Path:
        import config as app_config
        from avatar_engine.text_utils import safe_output_stem

        self.last_api_call_count = 0
        self.last_gemini_image_failure_model_id = None

        active = normalize_together_model_id(model_name or self._model_id)
        ratio = aspect_ratio or getattr(app_config, "GEMINI_IMAGE_ASPECT_RATIO", "9:16")
        orientation = aspect_ratio_to_orientation(ratio)
        if width is None or height is None:
            width, height = orientation_to_size(orientation, draft=draft)

        final_prompt = (prompt or "").strip()
        if avatar_mode == "ON" and reference_image_path is not None:
            # txt2img has no image-conditioned likeness input — the identity lock is
            # already carried by the visual description itself (dna/pose text built
            # upstream); just log it, never inject a labeled "rule" clause into the
            # prompt sent to FLUX.
            logger.info(
                "Together | avatar reference noted (txt2img — likeness via prompt "
                "description only) | %s",
                Path(reference_image_path).name,
            )
        # MODULE 3 — dynamic multi-image style reference. FLUX.1-schnell (the default
        # txt2img backend here) has no image-conditioned style-transfer input. Style
        # guidance for Together is carried entirely by the plain-language style anchor
        # already baked into the incoming prompt (see mei_visual.py) — no extra labeled
        # directive is appended here. Genuine image-conditioned style guidance happens
        # on the Gemini reference-image path (GeminiImageAdapter._generate_with_gemini_reference).
        sref_list = list(style_reference_paths or [])
        if not sref_list and style_reference_path is not None:
            sref_list = [style_reference_path]
        sref_valid = [Path(p) for p in sref_list if p and Path(p).is_file()]
        if sref_valid:
            sw = style_reference_weight if style_reference_weight is not None else 0.72
            logger.info(
                "Together | %d style reference(s) noted (txt2img, weight≈%.2f) | %s",
                len(sref_valid), sw, ", ".join(p.name for p in sref_valid),
            )

        # LAST-LINE SAFETY NET: role-aware sanitizer — never bolt cyberpunk onto
        # Master Mei / Disciple prompts (subject-contamination fix).
        final_prompt = sanitize_prompt_for_flux(
            final_prompt, visual_role=visual_role
        )

        out_dir = Path(output_directory or getattr(app_config, "OUTPUTS_DIR", "outputs"))
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = safe_output_stem(output_stem)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        out_path = out_dir / f"{slug}_{ts}.png"

        try:
            self.last_api_call_count = 1
            saved = self._gen.generate_image(
                final_prompt,
                out_path,
                orientation=orientation,
                steps=steps,
                model_name=active,
                width=width,
                height=height,
                negative_prompt=negative_prompt,
            )
            self.last_gemini_image_model_used = getattr(
                self._gen, "last_model_used", active
            )
            self.last_estimated_cost_usd = float(
                getattr(self._gen, "last_estimated_cost_usd", estimate_together_image_cost(active))
            )
            self.last_cost_key = cost_key_for_together_model(active)
            return Path(saved).resolve()
        except Exception as exc:  # noqa: BLE001
            self.last_gemini_image_failure_model_id = active
            logger.error("TogetherImageAdapter generate failed (%s): %s", active, exc)
            raise
