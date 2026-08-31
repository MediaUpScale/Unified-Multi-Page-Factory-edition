# -*- coding: utf-8 -*-
"""Cinematic terminal-UI renderer with audio-synced typing.

Design constraints that shaped this file:

**Typing is synced to audio, not to a fixed CPS.** Each utterance's segment
lasts exactly as long as its voice track, and the character reveal is a function
of normalised progress through that segment. Text therefore lands on the last
syllable regardless of how fast the voice is. A short hold at the end
(:data:`_TAIL_HOLD_RATIO`) keeps the finished line readable instead of cutting on
the final character.

**The screen is a Gemini-style dark chat.** The landing prompt is a fat rounded
box centred on charcoal (`#131314`). On first send it empties and eases to the
bottom (with a healthy footer margin); the question becomes a right-aligned
user bubble at the top of the thread. Body copy is white; green is reserved
for model/system tags.

**Frames are generated lazily, not accumulated.** A 1080x1920 frame is ~6 MB;
buffering two minutes at 24fps would need ~17 GB. The renderer builds a static
chrome layer once, then per frame composites only the changing text region via a
MoviePy ``VideoClip`` callback. Frames are additionally memoised by revealed
character count, so a 24fps render only performs as many PIL passes as there are
distinct character states.

**VFX are a hook chain, not inline code.** See :mod:`.vfx`.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

try:
    from ..contracts import DebateTranscript, SpeakerRole, Utterance, pretty_model_name
    from ..settings import AiwakeSettings, RenderConfig, Theme, resolve_outputs_dir, resolve_scratch_dir
    from .audio import AudioAsset, concat_audio, estimate_duration
    from .vfx import FrameContext, apply_chain, chain_needs_audio, resolve_chain
except ImportError:  # pragma: no cover — standalone extraction
    from contracts import DebateTranscript, SpeakerRole, Utterance, pretty_model_name  # type: ignore[no-redef]
    from media.audio import AudioAsset, concat_audio, estimate_duration  # type: ignore[no-redef]
    from media.vfx import (  # type: ignore[no-redef]
        FrameContext,
        apply_chain,
        chain_needs_audio,
        resolve_chain,
    )
    from settings import (  # type: ignore[no-redef]
        AiwakeSettings,
        RenderConfig,
        resolve_outputs_dir,
        resolve_scratch_dir,
    )

_LOG = logging.getLogger("aiwake.media.renderer")

# --------------------------------------------------------------------------- #
# Item 5, Step 1 — mandatory diagnostic trace at the exact label paste site.
# --------------------------------------------------------------------------- #
#: Set ``AIWAKE_LABEL_TRACE=1`` to log the RGB/alpha reaching the ONE call
#: site that ever pastes a label's pixels onto a frame (``_draw_pending_labels``
#: below), for a handful of instances. Off by default so normal renders never
#: pay the logging cost.
_LABEL_TRACE_ENV = "AIWAKE_LABEL_TRACE"
_LABEL_TRACE_MAX = 8
_label_trace_count = 0


def _trace_label_paint(
    tag: str,
    fill: tuple[int, int, int],
    alpha: float,
    paste_xy: tuple[int, int],
) -> None:
    """Log what is about to be pasted, right at the paste call site.

    This is the literal instrumentation the item-5 diagnostic asked for: at the
    exact line that touches the label's pixels (``canvas.paste(label_img, ...,
    label_img)`` in ``_draw_pending_labels``), record the RGB/alpha reaching
    that call. There is nothing to "walk up the call stack" for — ``label_img``
    is a freshly-created ``RGBA`` image built two lines above the paste, drawn
    directly from ``fill``/``alpha`` with no intervening ``Image.blend``,
    ``Image.alpha_composite``, or semi-transparent-overlay paste in between, so
    the trace also records that structural guarantee.
    """
    global _label_trace_count  # noqa: PLW0603
    if _label_trace_count >= _LABEL_TRACE_MAX or not os.environ.get(_LABEL_TRACE_ENV):
        return
    _label_trace_count += 1
    _LOG.info(
        "LABEL_TRACE #%d tag=%r rgb=%s alpha=%.4f paste_xy=%s "
        "path=fresh-RGBA-label_img->canvas.paste(label_img,xy,label_img) "
        "no-intervening-blend-or-alpha_composite=True",
        _label_trace_count,
        tag,
        fill,
        alpha,
        paste_xy,
    )


#: Fraction of each segment spent holding the completed line on screen.
_TAIL_HOLD_RATIO = 0.18

#: Blink period of the terminal caret, in seconds.
_CARET_PERIOD_S = 0.9

#: History helper: cap wraps so a packed preview still fits the body.
_HISTORY_MAX_LINES = 4


def ease_in_out(u: float) -> float:
    """Quintic smoothstep, clamped to 0–1. Used for the masked Y-scroll."""
    t = min(1.0, max(0.0, float(u)))
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def ease_out(u: float) -> float:
    """Cubic ease-out for the dock morph / downward descents (fast start, soft land)."""
    t = min(1.0, max(0.0, float(u)))
    return 1.0 - (1.0 - t) * (1.0 - t) * (1.0 - t)


def ease_in(u: float) -> float:
    """Cubic ease-in for upward rises (starts slow, accelerates to rest)."""
    t = min(1.0, max(0.0, float(u)))
    return t * t * t


def ease(u: float) -> float:
    """Plain smooth ease: fluid acceleration and deceleration."""
    t = min(1.0, max(0.0, float(u)))
    return t * t * (3.0 - 2.0 * t)


def motion_curve(u: float, easing: str = "ease", deceleration: float = 0.0) -> float:
    """Resolve a named easing and optionally bias it toward a soft landing.

    ``deceleration`` blends the selected base curve toward cubic ease-out,
    making the start slightly faster and the finish slightly slower without
    replacing the requested base easing.
    """
    t = min(1.0, max(0.0, float(u)))
    curves = {
        "linear": lambda value: value,
        "ease": ease,
        "ease_in": ease_in,
        "ease_out": ease_out,
        "ease_in_out": ease_in_out,
    }
    base = curves.get((easing or "ease").strip().lower(), ease)(t)
    bias = min(1.0, max(0.0, float(deceleration)))
    return base * (1.0 - bias) + ease_out(t) * bias


def _fly_ease_out(u: float) -> float:
    """Alias kept for older call sites; identical to ``ease_out``."""
    return ease_out(u)


def short_model_name(slug: str) -> str:
    """Vendor-stripped model label for the compose bar (``openai/gpt-4o`` → ``gpt-4o``)."""
    token = (slug or "").strip()
    if token.lower() in {"", "n/a", "na"}:
        return "model"
    if "/" in token:
        token = token.rsplit("/", 1)[-1]
    return token or "model"


_MODEL_ACCENTS: tuple[tuple[tuple[str, ...], tuple[int, int, int]], ...] = (
    (("gemini", "llama", "meta-llama", "gpt-4", "gpt-5", "gpt-3", "openai", "chatgpt", "deepseek"), (4, 81, 177)),
    (("claude", "anthropic", "mistral"), (255, 170, 0)),
)
_COMPOSE_FILL = (30, 31, 32)
_CHAT_TEXT = (232, 234, 237)
_USER_BUBBLE = (47, 48, 51)
_TITLE_WHITE = (255, 255, 255)  # pure #FFFFFF for the header wordmark
_FOOTER_COPY = "// autonomous exchange — unedited"

#: Signature accents. Source-of-truth colors come from `Theme.defaults().colors`
#: (see the sourced aliases after `_hex_to_rgb`). Every blue model collapses to
#: the primary orchestrator blue; the caret orange is the responder accent.

#: Vertical clearance (px at full scale) from the canvas bottom to the docked
#: compose box. Known-good: 150px classic rest + 150px Meta Reels safe-zone +
#: 80px raised resting position so the bar clears Reels chrome comfortably.
#: Scales 1:1 with ``preview_scale`` (380 → 190 at preview_scale=0.5).
# Visual/geometry constants once hardcoded here now live in `Theme` (settings.py)
# as the single source of truth. The names below are kept purely as aliases so
# anything still referencing them resolves to the active default theme rather
# than an independent literal.
_THEME = Theme.defaults()
_DOCK_CLEARANCE_PX = _THEME.dock_clearance_px
_BUBBLE_OPACITY = _THEME.bubble_opacity
_SCROLL_GAP_PX = _THEME.scroll_gap_px
_SCROLL_STREAM_EXTRA_PX = _THEME.scroll_stream_extra_px
_SCROLL_TAU_S = _THEME.scroll_tau_s
_HISTORY_LINE_SPACING = _THEME.line_spacing_history
_ORCH_BUBBLE_WIDTH_FRAC = _THEME.orch_bubble_width_frac
_TARGET_BUBBLE_WIDTH_FRAC = _THEME.target_bubble_width_frac

# Assumed leading silence inside an edge-tts clip before real speech onset.
# Typing waits this long so text never starts ahead of the voice (R4 item 14).
_SPEECH_ONSET_S = 0.035

# -- End-of-video Call-to-Action (CTA) ------------------------------------ --#
# The CTA is a typewriter end-card the moment the final line clears: it fades
# the chat to black, types the borrowed follow line, then holds. A short breath
# after the last spoken/typed word precedes ``cta_wait_s`` so the cut never feels
# chopped. The CTA voice is generated by ``_prepare_cta_voice`` and MUST be mixed
# into ``_master_audio`` (postroll + overlay) so A/V durations stay aligned.
_CTA_TAIL_S = 1.0  # breathing room after last word before CTA wait/fade
_CTA_WAIT_S = 1.5
_TITLE_COPY = "Aiwake"
_CTA_FADE_S = 0.45  # snappier fade into the end-card
_CTA_HOLD_S = 2.0
_CTA_TYPE_FRACTION = 0.36  # CTA reveals fully by ~36% of the VO window (calmer type speed)
_CTA_TTS_RATE = "+28%"  # edge-tts rate bump for a quicker spoken CTA
_CTA_PAUSE_S = 0.30  # short beat between the two CTA lines (visual typewriter beat)
# Extra viewport shrink while streaming so the typing edge clears the input mask.
_STREAM_SCROLL_PAD_PX = 36
# The fixed first line (lead phrase, ends with a period) and the weighted second-
# clause library are sourced from `Theme.defaults()` so the CTA copy lives in the
# same single source of truth as every other skin constant.
_CTA_HEAD = Theme.defaults().cta_head
_CTA_LINES: tuple[tuple[str, int], ...] = Theme.defaults().cta_lines


def ensure_cta_two_line(line: str) -> str:
    """Force the two-line, period-based CTA shape.

    ``Follow Aiwake.`` (period, not comma) on the first line; the given clause
    re-cased as its own sentence on a second line. Rebuilds any legacy single-
    line/comma-joined text so no phrasing variant can collapse back to one
    flowing line.
    """
    text = (line or "").strip().replace("\n", " ").strip()
    if text.lower().startswith(_CTA_HEAD.lower()):
        tail = text[len(_CTA_HEAD):].lstrip(" ,.")
    else:
        tail = text
    tail = tail.strip()
    if tail and tail[0].islower():
        tail = tail[0].upper() + tail[1:]
    if tail and tail[-1] not in ".!?…":
        tail += "."
    return f"{_CTA_HEAD}.\n{tail}"


def pick_cta(seed: str) -> str:
    """Weighted CTA pick, stable for a session so a rerender does not drift.

    Always normalised into the two-line shape: ``Follow Aiwake.`` on line one, a
    short beat, then the second clause as its own sentence on line two.
    """
    total = sum(weight for _, weight in _CTA_LINES)
    digest = hashlib.md5((seed or "aiwake").encode("utf-8")).digest()
    needle = int.from_bytes(digest[:8], "big") % total
    cursor = 0
    for line, weight in _CTA_LINES:
        cursor += weight
        if needle < cursor:
            return ensure_cta_two_line(line)
    return ensure_cta_two_line(_CTA_LINES[0][0])


def cta_revealed_chars(text: str, local_s: float, duration_s: float) -> int:
    """How many CTA characters are visible at ``local_s`` into the VO window.

    The CTA is a two-line, period-based card: ``Follow Aiwake.`` types first,
    then a short beat (``_CTA_PAUSE_S``), then the second sentence types in.
    Both lines finish typing by ``_CTA_TYPE_FRACTION`` of the VO duration
    (keeping the read pace energetic and ahead of the voice); the rest holds.
    """
    if not text:
        return 0
    head, _, tail = text.partition("\n")
    head_len = len(head)
    tail_len = len(tail)
    total = head_len + tail_len
    if duration_s <= 1e-6:
        return total
    # The type phase finishes both lines by this fraction of the VO window.
    type_end = max(1e-6, _CTA_TYPE_FRACTION * float(duration_s))
    if local_s <= 0.0:
        return 0
    if local_s >= type_end:
        return total
    # Reserve the short beat before the tail; share the remaining typing time
    # proportionally to character count so both lines keep a similar pace.
    work_t = max(0.0, type_end - _CTA_PAUSE_S)
    head_t = (head_len / total) * work_t if total else work_t
    tail_start = head_t + _CTA_PAUSE_S
    if local_s <= head_t:
        frac = local_s / head_t if head_t > 0 else 1.0
        return int(round(min(head_len, max(0.0, frac) * head_len)))
    if local_s < tail_start:
        return head_len  # the short beat: first line fully typed, caret resting
    tail_frac = (local_s - tail_start) / max(1e-6, type_end - tail_start)
    tail_show = int(round(min(tail_len, max(0.0, tail_frac) * tail_len)))
    return head_len + tail_show


def send_click_alpha(local_s: float, window_s: float) -> float:
    """Opacity pulse (1 -> 0.32 -> 1) over the send-click window."""
    if window_s <= 1e-6 or local_s < 0.0 or local_s > window_s:
        return 1.0
    u = local_s / window_s
    if u < 0.45:
        return 1.0 + (0.32 - 1.0) * (u / 0.45)
    return 0.32 + (1.0 - 0.32) * ((u - 0.45) / 0.55)


def model_accent(slug: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    """Send-button colour for the active model family.

    Every blue-collapsing family maps to the primary ``#0451b1``; the fallback
    defaults to ``_GEMINI_BLUE`` when none is supplied.
    """
    needle = (slug or "").lower()
    for keys, color in _MODEL_ACCENTS:
        if any(key in needle for key in keys):
            return color
    return fallback if fallback else _GEMINI_BLUE


class RenderError(RuntimeError):
    """Raised when the video cannot be produced."""


@dataclass(slots=True)
class Segment:
    """One utterance's slice of the timeline.

    Attributes:
        utterance: What is being spoken.
        start_s: Segment start on the master timeline.
        duration_s: Segment length, taken from the voice track.
        audio: The synthesised track, when one exists.
    """

    utterance: Utterance
    start_s: float
    duration_s: float
    audio: AudioAsset | None = None
    typing_hold_ratio: float = _TAIL_HOLD_RATIO

    @property
    def end_s(self) -> float:
        return self.start_s + self.duration_s

    def progress_at(self, t: float) -> float:
        """Normalised 0.0–1.0 position within this segment."""
        if self.duration_s <= 0:
            return 1.0
        return min(1.0, max(0.0, (t - self.start_s) / self.duration_s))

    def revealed_chars(self, t: float) -> int:
        """How many characters should be visible at time ``t``.

        Reveal is paced against the *spoken* portion of the segment (excluding a
        short post-speech hold), so on-screen typing tracks the TTS clip rather
        than racing ahead into silence or lagging behind speech.
        """
        text_len = len(self.utterance.text)
        if text_len <= 0:
            return 0
        typing_window = max(1e-6, 1.0 - self.typing_hold_ratio)
        # Account for the TTS clip's tiny leading silence so on-screen typing does
        # not start ahead of the voice; the reveal waits for real speech onset.
        local = max(0.0, float(t) - self.start_s - _SPEECH_ONSET_S)
        speech_s = max(1e-6, self.duration_s * typing_window - _SPEECH_ONSET_S)
        ratio = min(1.0, local / speech_s)
        return int(round(ratio * text_len))


# --------------------------------------------------------------------------- #
# Font resolution
# --------------------------------------------------------------------------- #
def _load_font(config: RenderConfig, size: int) -> Any:
    """Load the first available monospace font, else PIL's bitmap default.

    The default is ugly but keeps a headless container renderable, which matters
    more than typography in CI.
    """
    from PIL import ImageFont  # noqa: PLC0415

    for candidate in config.font_candidates:
        path = Path(candidate)
        if not path.is_file():
            continue
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    _LOG.warning("no configured font resolved; falling back to PIL default bitmap font")
    return ImageFont.load_default()


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    raw = value.lstrip("#")
    if len(raw) != 6:
        raise ValueError(f"palette colour must be #RRGGBB, got {value!r}")
    return tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


#: Signature accent aliases — sourced from the default theme (single source of
#: truth). `model_accent` and any module-level callers read these instead of an
#: independently-maintained literal.
_GEMINI_BLUE = _hex_to_rgb(Theme.defaults().colors.orchestrator)
#: Orchestrator name/title label fill — MUST stay fully opaque in every frame.
_ORCH_LABEL_FILL = _GEMINI_BLUE
_ORANGE_CARET = _hex_to_rgb(Theme.defaults().colors.target)


def _mix(color: tuple[int, int, int], other: tuple[int, int, int], weight: float) -> tuple[int, int, int]:
    """Linear blend, ``weight`` = share of ``other``."""
    return tuple(int(round(c * (1 - weight) + o * weight)) for c, o in zip(color, other))  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Renderer
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class _Block:
    """Pre-measured utterance card used by the rolling transcript layout."""

    lines: list[str]
    line_height: int
    label_height: int
    height: int
    font: Any
    faded: bool


def _clip_lines(lines: Sequence[str], max_lines: int) -> list[str]:
    """Keep the first ``max_lines`` wraps; ellipsis the last if truncated."""
    if len(lines) <= max_lines:
        return list(lines) or [""]
    clipped = list(lines[:max_lines])
    last = clipped[-1].rstrip()
    if len(last) > 3:
        clipped[-1] = last[:-3] + "..."
    else:
        clipped[-1] = (last + "...")[-max(3, len(last) + 3) :]
    return clipped


@dataclass(slots=True)
class _Layout:
    """Pre-computed pixel geometry for the terminal UI.

    ``body_top`` / ``body_bottom`` bound the region the utterance block is
    centred inside; the block itself is positioned per utterance from its full
    wrapped height, which keeps text vertically stable while it types.
    """

    width: int
    height: int
    margin_x: int
    header_y: int
    speaker_y: int
    body_top: int
    body_bottom: int
    body_width_chars: int
    line_height: int
    footer_y: int

    @property
    def body_center_y(self) -> int:
        return (self.body_top + self.body_bottom) // 2


def _reveal_lines(lines: Sequence[str], revealed: int) -> list[str]:
    """Slice a pre-wrapped block to the first ``revealed`` characters.

    Wrapping the *full* text once and revealing into those fixed line breaks is
    what stops words from jumping between lines as they complete — re-wrapping a
    growing prefix every frame reads as a glitch on video.
    """
    out: list[str] = []
    budget = revealed
    for line in lines:
        if budget <= 0:
            break
        out.append(line[:budget])
        budget -= len(line) + 1  # the break itself consumed a space
    return out or [""]


class TerminalRenderer:
    """Renders a :class:`DebateTranscript` into a vertical cinematic video.

    Args:
        settings: Validated Aiwake settings.
        output_dir: Destination directory. Defaults to the global engine
            ``outputs/aiwake/``, falling back to a module-local folder.
    """

    def __init__(self, settings: AiwakeSettings, *, output_dir: Path | None = None) -> None:
        self.settings = settings
        self.config = settings.render
        self.output_dir = output_dir or resolve_outputs_dir(settings)
        self.width, self.height = self.config.scaled_size
        # Single source of truth for every skin-defining visual value.
        self.theme = settings.resolve_theme()
        pal = self.theme.colors
        self.palette = {
            "background": _hex_to_rgb(pal.background),
            "chrome": _hex_to_rgb(pal.chrome),
            "orchestrator": _hex_to_rgb(pal.orchestrator),
            "target": _hex_to_rgb(pal.target),
            "dim": _hex_to_rgb(pal.dim),
        }
        # Skin colours the draw calls reference (theme-driven, not literals).
        self._GEMINI_BLUE = self.palette["orchestrator"]     # send button/caret
        self._ORCH_LABEL_FILL = _hex_to_rgb(self.theme.orchestrator_label_color)
        self._COMPOSE_MODEL_LABEL_FILL = _hex_to_rgb(self.theme.compose_model_label_color)
        self._ORANGE_CARET = self.palette["target"]           # responder caret/label
        self._COMPOSE_FILL = _hex_to_rgb(self.theme.compose_fill)
        self._CHAT_TEXT = (232, 234, 237)
        self._USER_BUBBLE = (47, 48, 51)
        self._TITLE_WHITE = (255, 255, 255)
        self._font = _load_font(self.config, self.config.scaled_font_size)
        self._font_title = _load_font(self.config, max(10, int(self.config.scaled_font_size * self.theme.font_scale_title)))
        self._font_small = _load_font(self.config, max(8, int(self.config.scaled_font_size * self.theme.font_scale_small)))
        # History / thread body: ~reference sizing (was 0.66 — too small vs the
        # 20260828 video). Compose draft sits just under that so the input still
        # reads as the "active" larger field without dwarfing history.
        self._font_history = _load_font(self.config, max(11, int(self.config.scaled_font_size * self.theme.font_scale_history)))
        self._font_compose = _load_font(self.config, max(14, int(self.config.scaled_font_size * self.theme.font_scale_compose)))
        self._font_cta = _load_font(self.config, max(18, int(self.config.scaled_font_size * self.theme.font_scale_cta)))
        self._layout = self._build_layout()
        self._chrome_cache: dict[str, Any] = {}
        self._wrap_cache: dict[str, list[str]] = {}
        self._frame_cache: dict[tuple[Any, ...], np.ndarray] = {}
        self._char_w_cache: dict[int, float] = {}
        # Continuous eased-scroll state, so frame N eases from frame N-1 instead
        # of snapping to a per-segment baseline (which caused hard jumps).
        self._scroll_ease: dict[str, float] = {
            "px": 0.0,
            "anchor_t": -1e9,
            "sig": "",
        }
        self._last_scroll_gap = 0
        # Sender label draw calls are deferred and painted last on the final
        # canvas — NEVER on the faded viewport / shared fly overlay — so every
        # label composites at full opacity, unaffected by bottom/top edge fades
        # or the bubble's history dim (item: orchestrator title not diluted).
        # Entries are (absolute_x, absolute_y, text, fill_rgb, alpha). ``alpha``
        # (item 5) is the SAME effective opacity the label's own bubble body
        # carries this frame — history dim x top-viewport mask, or the fly
        # overlay's own fade — never a bare 1.0 default.
        self._pending_labels: list[tuple[int, int, str, tuple[int, int, int], float]] = []

    # -- Geometry ----------------------------------------------------------- #
    def _build_layout(self) -> _Layout:
        """Derive all pixel positions from the canvas size and font metrics."""
        from PIL import Image, ImageDraw  # noqa: PLC0415

        probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        # Monospace assumption: measure one glyph and multiply.
        box = probe.textbbox((0, 0), "M" * 10, font=self._font)
        char_w = max(1.0, (box[2] - box[0]) / 10.0)
        line_h = int((box[3] - box[1]) * 1.85) or self.config.scaled_font_size * 2

        margin_x = int(self.width * 0.055)
        usable = self.width - margin_x * 2
        # Round-13 item 6: raise the top-of-viewport fade band ~20px (canvas
        # px, scaled by preview_scale) closer to the header so scrolled
        # content starts fading sooner instead of staying fully visible for a
        # larger gap under the header divider first. Floored so an
        # aggressive override can never clip into the header chrome.
        top_mask_offset = int(round(self.theme.top_mask_start_offset_px * self.config.preview_scale))
        anchor_offset = int(round(self.theme.message_anchor_offset_px * self.config.preview_scale))
        body_top = max(
            int(self.height * 0.06),
            int(self.height * 0.145) - top_mask_offset - anchor_offset,
        )
        return _Layout(
            width=self.width,
            height=self.height,
            margin_x=margin_x,
            header_y=int(self.height * 0.078),
            speaker_y=int(self.height * 0.19),
            body_top=body_top,
            body_bottom=int(self.height * 0.78),
            body_width_chars=max(12, int(usable / char_w)),
            line_height=line_h,
            footer_y=int(self.height * 0.972),
        )

    def _wrapped(self, text: str) -> list[str]:
        """Wrap and cache an utterance's full text at the body column width."""
        cached = self._wrap_cache.get(text)
        if cached is None:
            cached = textwrap.wrap(text, width=self._layout.body_width_chars) or [""]
            self._wrap_cache[text] = cached
        return cached

    def _char_width(self, font: Any) -> float:
        """Monospace glyph width for ``font``, cached by object id."""
        key = id(font)
        cached = self._char_w_cache.get(key)
        if cached is not None:
            return cached
        from PIL import Image, ImageDraw  # noqa: PLC0415

        probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        box = probe.textbbox((0, 0), "M" * 10, font=font)
        width = max(1.0, (box[2] - box[0]) / 10.0)
        self._char_w_cache[key] = width
        return width

    def _wrap_for_font(self, text: str, font: Any, *, width_frac: float = 1.0) -> list[str]:
        usable = int((self.width - self._layout.margin_x * 2) * max(0.35, min(1.0, width_frac)))
        width_chars = max(12, int(usable / self._char_width(font)))
        key = f"{width_chars}:{text}"
        cached = self._wrap_cache.get(key)
        if cached is None:
            cached = textwrap.wrap(text, width=width_chars) or [""]
            self._wrap_cache[key] = cached
        return cached

    def _line_height_of(self, font: Any, *, scale: float = 1.90) -> int:
        from PIL import Image, ImageDraw  # noqa: PLC0415

        probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        box = probe.textbbox((0, 0), "Mg", font=font)
        return max(1, int((box[3] - box[1]) * scale))

    def _turn_gap(self) -> int:
        return max(8, int(self._layout.line_height * 0.28))

    def _measure_block(
        self,
        utterance: Utterance,
        *,
        font: Any,
        max_height: int,
        max_lines: int | None = None,
        faded: bool = False,
    ) -> _Block:
        """Size one speaker card so it cannot overflow ``max_height``."""
        bubble = utterance.role is SpeakerRole.ORCHESTRATOR
        lines = self._wrap_for_font(
            utterance.text,
            font,
            width_frac=self.theme.orch_bubble_width_frac if bubble else self.theme.target_bubble_width_frac,
        )
        if max_lines is not None:
            lines = _clip_lines(lines, max_lines)
        # Point 3: line height is locked and independent of `faded` — streaming,
        # live-settled, and history-dimmed bubbles use the identical vertical
        # rhythm so nothing jumps when the caret disappears or a line settles.
        native_lh = self._line_height_of(font, scale=self.theme.line_spacing_history)
        label_h = max(1, int(native_lh * self.theme.label_height_scale))
        pad_y = max(8, int(self.height * self.theme.body_pad_frac_y)) if bubble else 0
        available_text = max(1, max_height - label_h - pad_y * 2)
        line_height = min(native_lh, max(1, available_text // max(1, len(lines))))
        while len(lines) > 1 and label_h + pad_y * 2 + len(lines) * line_height > max_height:
            lines = _clip_lines(lines, len(lines) - 1)
            line_height = min(native_lh, max(1, available_text // max(1, len(lines))))
        height = label_h + pad_y * 2 + len(lines) * line_height
        return _Block(
            lines=lines,
            line_height=line_height,
            label_height=label_h,
            height=height,
            font=font,
            faded=faded,
        )

    def visible_history(
        self,
        prior: Sequence[Segment],
        current_height: int,
    ) -> list[Segment]:
        """Newest-first pack of finished turns that still fit above ``current``.

        Caps at ``render.history_turns`` (default 3) and drops the oldest
        whenever the remaining body budget is too small.
        """
        cap = self.config.history_turns
        if cap <= 0 or not prior:
            return []
        layout = self._layout
        region = layout.body_bottom - layout.body_top
        budget = region - current_height - self._turn_gap()
        if budget < 24:
            return []
        selected: list[Segment] = []
        used = 0
        gap = self._turn_gap()
        for segment in reversed(list(prior)[-cap:]):
            block = self._measure_block(
                segment.utterance,
                font=self._font_history,
                max_height=budget,
                max_lines=_HISTORY_MAX_LINES,
                faded=True,
            )
            need = block.height + (gap if selected else 0)
            if used + need > budget:
                break
            selected.append(segment)
            used += need
        selected.reverse()
        return selected

    def _fade(self, color: tuple[int, int, int], opacity: float) -> tuple[int, int, int]:
        """Composite ``color`` over the background at ``opacity`` (0–1)."""
        return _mix(self.palette["background"], color, opacity)

    def _fade_viewport_top(self, image: Any, *, fade_h: int) -> None:
        """Blend the top of the chat mask into the background so clips read as a fade."""
        from PIL import Image  # noqa: PLC0415

        fade_h = min(max(0, int(fade_h)), image.size[1])
        if fade_h <= 0:
            return
        arr = np.array(image, dtype=np.float32)
        bg = np.array(self.palette["background"], dtype=np.float32)
        weights = np.linspace(1.0, 0.0, fade_h, dtype=np.float32)[:, None, None]
        arr[:fade_h] = arr[:fade_h] * (1.0 - weights) + bg * weights
        image.paste(Image.fromarray(arr.astype(np.uint8)))

    def _fade_viewport_bottom(self, image: Any, *, fade_h: int) -> None:
        """Blend the bottom of the chat mask so lines tuck under the compose bar."""
        from PIL import Image  # noqa: PLC0415

        fade_h = min(max(0, int(fade_h)), image.size[1])
        if fade_h <= 0:
            return
        arr = np.array(image, dtype=np.float32)
        bg = np.array(self.palette["background"], dtype=np.float32)
        weights = np.linspace(0.0, 1.0, fade_h, dtype=np.float32)[:, None, None]
        arr[-fade_h:] = arr[-fade_h:] * (1.0 - weights) + bg * weights
        image.paste(Image.fromarray(arr.astype(np.uint8)))

    def _top_mask_alpha_at_row(self, row: int, fade_h: int) -> float:
        """Effective top-of-viewport fade alpha at viewport-local ``row``.

        Mirrors, pixel-for-pixel, the per-row weight ``_fade_viewport_top``
        applies when it blends the viewport toward the background
        (``weights = np.linspace(1.0, 0.0, fade_h)``; alpha = 1 - weight). This
        is the single source of truth both the bubble body (via the raster
        blend) and its sender label (via the deferred paste in
        ``_draw_pending_labels``) must agree on — item 5's fix for labels that
        previously ignored this mask entirely.
        """
        if fade_h <= 0:
            return 1.0
        if row < 0:
            return 0.0
        if row >= fade_h or fade_h <= 1:
            return 1.0
        return row / float(fade_h - 1)

    def _draw_send_arrow(
        self,
        draw: Any,
        origin: tuple[int, int],
        *,
        size: int,
        fill: tuple[int, int, int],
    ) -> None:
        """Stroke-only up-arrow: a thin chevron flowing into a stem, no fill.

        Mirrors the Gemini/ChatGPT send glyph: three equal-width line strokes —
        left arm, right arm, vertical stem — that all meet at the apex so there
        is NO seam between the chevron and the stem (earlier stroke builds put
        the stem's top below the apex, which left a visible gap). No polygon,
        no solid triangle. Optically centred with a small down/right nudge.
        """
        x, y = origin
        w = max(14, int(size))
        h = max(16, int(size))
        # No horizontal nudge: the glyph's own centre must sit exactly on the
        # caller's origin centre so its centroid lands on the circle's centre x
        # (the call site already biases the origin box onto that centre). Only a
        # tiny downward optical nudge, which is symmetric and does not shift the
        # centroid horizontally.
        nudge_y = 1
        cx = x + w // 2
        cy = y + h // 2 + nudge_y
        stroke = max(3, int(round(w * self.theme.arrow_stroke_frac)))
        # Apex (top point) — every stroke begins here so the glyph is one
        # continuous outline from tip through shoulders down to the tail.
        apex = (cx, cy - int(round(h * 0.38)))
        left = (cx - int(round(w * 0.40)), cy - int(round(h * 0.05)))
        right = (cx + int(round(w * 0.40)), cy - int(round(h * 0.05)))
        stem_bot = (cx, cy + int(round(h * 0.40)))
        draw.line([left, apex], fill=fill, width=stroke)
        draw.line([apex, right], fill=fill, width=stroke)
        draw.line([apex, stem_bot], fill=fill, width=stroke)

    def _draw_block(
        self,
        draw: Any,
        utterance: Utterance,
        block: _Block,
        origin_y: int,
        *,
        revealed: int | None = None,
        caret_on: bool = False,
        send_flash: bool = False,
        origin_x: int | None = None,
        mask_fade_h: int = 0,
    ) -> None:
        """Paint one speaker card. ``revealed is None`` means fully visible."""
        _ = send_flash
        if origin_y + block.height <= 0:
            return
        layout = self._layout
        user_bubble = utterance.role is SpeakerRole.ORCHESTRATOR
        # Responder labels MUST match the orange typing caret; orchestrator
        # labels stay solid #0451b1. Do not route responders through
        # ``model_accent`` — that maps llama/gemini families to blue and was
        # painting "Llama 3.3 70B" teal instead of the caret orange.
        # Latest screenshot acceptance baseline compares orchestrator label
        # ink directly against the full-strength send button in the same PNG.
        # Keep the raw fill pure and do NOT multiply label alpha by the body's
        # history opacity (the old 0.40 multiplier produced the reported
        # RGB(11,42,83)). Viewport masking still applies so labels disappear
        # cleanly when they scroll under the header.
        label_color = self._ORCH_LABEL_FILL if user_bubble else self._ORANGE_CARET
        opacity = self.config.history_opacity if block.faded else 1.0
        label_alpha = max(
            0.0,
            min(1.0, self._top_mask_alpha_at_row(origin_y, mask_fade_h)),
        )
        text_color = self._fade(self._CHAT_TEXT, opacity)
        pad_x = max(14, int(self.width * self.theme.body_pad_frac_x)) if user_bubble else 0
        pad_y = max(8, int(self.height * self.theme.body_pad_frac_y)) if user_bubble else 0
        visible = block.lines if revealed is None else _reveal_lines(block.lines, revealed)
        char_w = self._char_width(block.font)
        text_w = max((len(line) * char_w for line in (visible or [""])), default=char_w)
        bubble_w = int(text_w + pad_x * 2)
        content_right = self.width - layout.margin_x
        if user_bubble:
            x0 = content_right - bubble_w + pad_x
            bubble_x0 = content_right - bubble_w
        else:
            x0 = layout.margin_x if origin_x is None else origin_x
            bubble_x0 = x0

        tag = pretty_model_name(utterance.model_slug) if utterance.model_slug else utterance.speaker_name
        abs_label_top = origin_y + self._layout.body_top
        if user_bubble:
            tag_box = draw.textbbox((0, 0), tag, font=self._font_small)
            tag_w = tag_box[2] - tag_box[0]
            self._pending_labels.append((content_right - tag_w, abs_label_top, tag, label_color, label_alpha))
        else:
            self._pending_labels.append((x0, abs_label_top, tag, label_color, label_alpha))

        y = origin_y + block.label_height
        if user_bubble:
            bubble_h = pad_y * 2 + max(1, len(visible)) * block.line_height
            radius = max(self.theme.bubble_radius_min, min(self.theme.bubble_radius_max, int(self.theme.bubble_radius_base * self.config.preview_scale)))
            fill = self._fade(self._USER_BUBBLE, opacity)
            draw.rounded_rectangle(
                [bubble_x0, y, bubble_x0 + bubble_w, y + bubble_h],
                radius=radius,
                fill=fill,
            )
            y += pad_y
            x0 = bubble_x0 + pad_x

        for line in visible:
            draw.text((x0, y), line, font=block.font, fill=text_color)
            y += block.line_height

        if caret_on and not block.faded and not user_bubble:
            caret_row_top = y - block.line_height
            box = draw.textbbox((x0, caret_row_top), visible[-1] if visible else "", font=block.font)
            caret_x = box[2] + int(self.config.scaled_font_size * 0.18)
            draw.rectangle(
                [
                    caret_x,
                    caret_row_top,
                    caret_x + max(4, int(self.config.scaled_font_size * 0.5)),
                    caret_row_top + int(self.config.scaled_font_size * 1.05),
                ],
                fill=self._ORANGE_CARET,
            )

    def composer_state(
        self,
        segments: Sequence[Segment],
        index: int,
        t: float,
    ) -> tuple[str, tuple[int, int, int], bool]:
        """Active compose-bar model, accent, and whether the send control is lit.

        Gemini send sequence: the label stays on the *current* speaker
        (orchestrator) through typing AND the 1s read-hold; it only rotates to
        the upcoming responder once the send click actually fires.
        """
        current = segments[index]
        typing_end = self._typing_end_s(current)
        click_start = self._click_start_s(current)
        clicked = t >= click_start and current.revealed_chars(t) >= len(current.utterance.text)
        send_flash = (
            current.utterance.role is SpeakerRole.ORCHESTRATOR
            and clicked
            and (t - click_start) < float(self.config.send_flash_s)
        )
        if clicked and index + 1 < len(segments):
            nxt = segments[index + 1].utterance
            fallback = self.palette.get(nxt.role.value, self.palette["target"])
            return short_model_name(nxt.model_slug), model_accent(nxt.model_slug, fallback), send_flash
        fallback = self.palette.get(current.utterance.role.value, self.palette["orchestrator"])
        return short_model_name(current.utterance.model_slug), model_accent(current.utterance.model_slug, fallback), send_flash

    def _compose_radius(self) -> int:
        return max(14, min(32, int(round(self.theme.compose_radius_base * self.config.preview_scale))))

    def _compose_min_h(self) -> int:
        return max(96, int(self.height * 0.095))

    def _compose_max_h(self) -> int:
        """Tallest the compose box may grow so a full orchestrator prompt stays
        fully visible while typing (never silently clipping the beginning).

        The box is docked against the bottom clearance and grows upward, so its
        top stays comfortably inside the chat body. A generous cap (40% of the
        canvas) comfortably holds a guardrail-capped ~30-word prompt wrapped to
        several lines; the earlier 22% cap was tight enough that a long first
        question wrapped past the box and got clipped.
        """
        return max(self._compose_min_h(), int(self.height * 0.40))

    def _compose_side_margin(self) -> int:
        return max(24, int(self.width * 0.055))

    def _footer_reserve(self) -> int:
        """Pixels reserved at the bottom for footer copy + gap under the docked box."""
        return max(36, int(self.height * 0.038))

    def _bottom_margin(self) -> int:
        """Healthy gap between the docked prompt and the footer."""
        return max(18, int(self.height * 0.022))

    def _compose_box_h(self, lines: Sequence[str], line_h: int) -> int:
        pad = max(16, int(self.height * 0.012))
        btn = self._send_btn_size()
        btn_row = btn + pad
        text_h = line_h * max(1, len(lines) or 1) + pad
        raw_h = pad + text_h + btn_row
        return min(self._compose_max_h(), max(self._compose_min_h(), raw_h))

    def _send_btn_size(self) -> int:
        # Larger circle so the stroke arrow has room and reads clearly on Reels.
        return max(36, int(round(52 * self.config.preview_scale)))

    def _dock_clearance(self) -> int:
        """Pixels from the canvas bottom to the docked compose box.

        The classic rest is ``_DOCK_CLEARANCE_PX`` at full scale plus the Reels
        safe-zone raise; both scale down 1:1 with ``preview_scale`` so a reduced
        preview keeps the same chat budget as the full render.
        """
        return max(48, int(round(self.theme.dock_clearance_px * self.config.preview_scale)))

    def _orchestrator_slug(self) -> str:
        """Raw model slug of the orchestrator seat (empty if it cannot be read)."""
        try:
            spec = self.settings.spec_for("orchestrator")
            return str(getattr(spec, "model", "") or "")
        except Exception:  # noqa: BLE001 — never break a paint for a label lookup
            return ""

    def _wrap_compose_draft(self, draft: str, box_w: int) -> tuple[list[str], int]:
        pad = max(16, int(self.height * 0.012))
        inner_w = max(32, box_w - pad * 2)
        font = self._font_compose
        width_chars = max(12, int(inner_w / max(1.0, self._char_width(font))))
        text = (draft or "").strip()
        lines = textwrap.wrap(text, width=width_chars) if text else []
        line_h = self._line_height_of(font, scale=self.theme.line_spacing_compose)
        box_h = self._compose_box_h(lines, line_h)
        visible_budget = box_h - pad - (self._send_btn_size() + pad)
        max_lines = max(1, visible_budget // max(1, line_h))
        # CRITICAL: keep the FIRST lines, never the tail. A sent orchestrator
        # prompt must show its beginning while typing; ``lines[-max_lines:]`` was
        # silently dropping the opening of the first message (data loss).
        if lines and len(lines) > max_lines:
            lines = lines[:max_lines]
        return lines, line_h

    def _compose_geometry(self, draft: str, *, dock: float = 1.0, anchor: str = "bottom") -> tuple[int, int, int, int, list[str], int]:
        """Return ``(left, top, right, bottom, lines, line_h)``.

        ``dock`` 0 = centred landing prompt; 1 = anchored rest position.
        ``anchor`` selects the rest position the box eases to:
          - ``"bottom"`` (default): the fixed input bar, docked above the footer;
          - ``"top"``: the top-anchored chat position (turn-0 first prompt),
            where the input box rises to become the first message.
        """
        mx = self._compose_side_margin()
        left, right = mx, self.width - mx
        lines, line_h = self._wrap_compose_draft(draft, right - left)
        box_h = self._compose_box_h(lines, line_h)
        center_top = (self.height - box_h) // 2
        center_bot = center_top + box_h
        if anchor == "top":
            rest_top = self._layout.body_top
            rest_bot = rest_top + box_h
        else:
            rest_bot = self.height - self._dock_clearance()
            rest_top = rest_bot - box_h
        u = min(1.0, max(0.0, float(dock)))
        bar_top = int(round(center_top + (rest_top - center_top) * u))
        bar_bot = int(round(center_bot + (rest_bot - center_bot) * u))
        return left, bar_top, right, bar_bot, lines, line_h

    def _send_slide_start_delay_s(self) -> float:
        """Delay from message-rise start to compose-box descent start."""
        return max(0.0, float(getattr(self.config, "send_slide_start_delay_s", 0.0) or 0.0))

    def dock_progress(self, segments: Sequence[Segment], t: float) -> float:
        """0 while the first prompt is still being entered; then eases to 1.

        The empty compose chrome morphs from the centred landing position down
        to the docked footer rest — via an actual Y-position slide (never a
        single-frame swap; see the Round-14 ``bar_dock`` fix in ``_compose``,
        which must keep riding this value all the way to 1.0). The typed
        glyphs themselves travel UP via ``bubble_fly_progress`` /
        ``_paste_flying_send`` — never by dragging the whole input box into
        the thread.

        Phase B starts after ``send_slide_start_delay_s`` and uses its own
        duration/easing/deceleration settings, intentionally slower and
        smoother than the message rise.
        """
        first = next((item for item in segments if item.utterance.role is SpeakerRole.ORCHESTRATOR), None)
        if first is None:
            return 0.0
        leave = self._box_until_s(first) + self._send_slide_start_delay_s()
        if t <= leave:
            return 0.0
        dock_s = max(0.05, float(getattr(self.config, "send_slide_s", 0.0) or 0.55))
        return motion_curve(
            (t - leave) / dock_s,
            getattr(self.config, "send_slide_easing", "ease"),
            getattr(self.config, "send_slide_deceleration", 0.0),
        )

    # -- Bubble fly / glide timing ---------------------------------------- #
    # These accessors drive the submit → fly → land lifecycle. On submit the
    # compose draft is CLEARED (``landing_gone``) and the glyphs travel up via
    # ``_paste_flying_send`` while the empty chrome docks. Never leave the
    # typed text drawn both in the bar and in the thread at once.

    def _typing_end_s(self, segment: Segment) -> float:
        """Time the orchestrator line finishes typing (reveal-complete)."""
        typing_window = max(1e-6, 1.0 - segment.typing_hold_ratio)
        return segment.start_s + segment.duration_s * typing_window

    def _send_hold_s(self) -> float:
        """Post-typing hold before the send click (0 when the slot is absent)."""
        return max(0.0, float(getattr(self.config, "send_hold_s", 0.0) or 0.0))

    def _send_slide_hold_s(self) -> float:
        """Legacy accessor; fly/dock now leave at ``_box_until_s`` (post-click)."""
        return max(0.0, float(getattr(self.config, "send_slide_hold_s", 0.0) or 0.0))

    def _send_click_s(self) -> float:
        """Duration of the send-button click flash."""
        return max(0.0, float(self.config.send_flash_s))

    def _click_start_s(self, segment: Segment) -> float:
        """Time the send click begins, after typing and the hold."""
        return self._typing_end_s(segment) + self._send_hold_s()

    def _send_clicking(self, segment: Segment, t: float) -> bool:
        """True while an orchestrator line's send button is mid-click."""
        if segment.utterance.role is not SpeakerRole.ORCHESTRATOR:
            return False
        start = self._click_start_s(segment)
        return start <= t < start + self._send_click_s()

    def _box_until_s(self, segment: Segment) -> float:
        """Time the line remains in the input box before its rise begins."""
        click_end = self._click_start_s(segment) + self._send_click_s()
        rise_delay = max(0.0, float(getattr(self.config, "send_rise_delay_s", 0.1) or 0.0))
        return click_end + rise_delay

    def _in_box_until_s(self, segment: Segment) -> float:
        """Time the orchestrator's typed line is still visually in the box.

        Ends at the send click. After that ``landing_gone`` clears the draft
        and the glyphs travel via the fly overlay — they must not linger in
        the compose bar.
        """
        if segment.utterance.role is not SpeakerRole.ORCHESTRATOR:
            return segment.end_s
        return self._box_until_s(segment)

    def bubble_fly_progress(self, segment: Segment, t: float) -> float:
        """0 while an orchestrator line is still in the box; 1 once it has landed.

        The raw timeline progress is transformed by the configured plain-ease
        curve plus a small configurable deceleration bias. Opacity remains
        unchanged throughout this motion.
        """
        if segment.utterance.role is not SpeakerRole.ORCHESTRATOR:
            return 1.0
        leave = self._box_until_s(segment)
        if t < leave:
            return 0.0
        duration = max(0.1, float(getattr(self.config, "send_rise_duration_s", 0.4)))
        return motion_curve(
            (t - leave) / duration,
            getattr(self.config, "send_rise_easing", "ease"),
            getattr(self.config, "send_rise_deceleration", 0.0),
        )

    def landing_fade_progress(self, segment: Segment, t: float) -> float:
        """0 through the rise, then 0→1 only after the message has landed."""
        if segment.utterance.role is not SpeakerRole.ORCHESTRATOR:
            return 1.0
        rise_end = self._box_until_s(segment) + max(
            0.1, float(getattr(self.config, "send_rise_duration_s", 0.4))
        )
        fade_start = rise_end + max(
            0.0, float(getattr(self.config, "send_landing_fade_delay_s", 0.0) or 0.0)
        )
        fade_s = max(0.0, float(getattr(self.config, "send_landing_fade_s", 0.2) or 0.0))
        if t <= fade_start:
            return 0.0
        if fade_s <= 1e-6:
            return 1.0
        return ease((t - fade_start) / fade_s)

    def response_loader_progress(
        self,
        segments: Sequence[Segment],
        index: int,
        t: float,
    ) -> float | None:
        """Return loader progress immediately before an upcoming AI response."""
        next_index = index + 1
        if next_index >= len(segments):
            return None
        upcoming = segments[next_index]
        if upcoming.utterance.role is not SpeakerRole.TARGET:
            return None
        duration = max(0.0, float(getattr(self.config, "response_loader_s", 0.36) or 0.0))
        if duration <= 1e-6:
            return None
        start = upcoming.start_s - duration
        if t < start or t >= upcoming.start_s:
            return None
        return min(1.0, max(0.0, (t - start) / duration))

    def _line_rise(self, segment: Segment, t: float) -> float:
        """Back-compat alias for ``bubble_fly_progress``."""
        return self.bubble_fly_progress(segment, t)

    def _first_orchestrator(self, segments: Sequence[Segment]) -> Segment | None:
        """First orchestrator segment on the timeline (the landing prompt)."""
        for item in segments:
            if item.utterance.role is SpeakerRole.ORCHESTRATOR:
                return item
        return None

    def _paste_flying_send(
        self,
        canvas: Any,
        utterance: Utterance,
        *,
        fly_u: float,
        landing_fade_u: float,
        rest_y: int,
        start_dock: float,
    ) -> None:
        """Ease the typed prompt up from the compose box into its thread bubble.

        Width/wrap are locked to the FINAL destination bubble for every frame —
        never interpolated from the wider compose-bar wrap — so long multi-line
        prompts never bleed past the right safe edge mid-flight.

        The message follows one continuous configured Y-curve. Text remains at
        full opacity throughout the rise; only after Y reaches its landing
        anchor does ``landing_fade_u`` ease it toward the configured settled
        opacity. The model label stays full-strength except for viewport mask.
        """
        from PIL import Image, ImageDraw  # noqa: PLC0415

        u = min(1.0, max(0.0, float(fly_u)))
        text = (utterance.text or "").strip()
        left, bar_top, _right, _bar_bot, _compose_lines, _compose_lh = self._compose_geometry(
            text, dock=start_dock, anchor="bottom"
        )
        pad = max(16, int(self.height * 0.012))
        start_y = bar_top + pad

        rise_u = u
        landed_opacity = min(
            1.0, max(0.0, float(getattr(self.config, "send_landed_text_opacity", 0.4)))
        )
        landing_fade_u = min(1.0, max(0.0, float(landing_fade_u)))
        text_opacity = 1.0 - landing_fade_u * (1.0 - landed_opacity)
        text_a = max(0, min(255, int(round(255 * text_opacity))))

        dest = self._measure_block(utterance, font=self._font_history, max_height=10_000)
        dest_pad_x = max(14, int(self.width * self.theme.body_pad_frac_x))
        dest_pad_y = max(8, int(self.height * self.theme.body_pad_frac_y))
        dest_lines = dest.lines or [""]
        # Measure real glyph widths so the bubble matches the settled thread card.
        probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        dest_text_w = max(
            ((tb := probe.textbbox((0, 0), line, font=self._font_history))[2] - tb[0] for line in dest_lines),
            default=self._char_width(self._font_history),
        )
        dest_bubble_w = int(dest_text_w + dest_pad_x * 2)
        content_right = self._content_right_ceiling()
        # Clamp so the bubble never exceeds the safe right edge on any frame.
        dest_bubble_w = min(dest_bubble_w, max(8, content_right - self._layout.margin_x))
        dest_text_x = content_right - dest_bubble_w + dest_pad_x
        dest_text_y = rest_y + dest.label_height + dest_pad_y

        # Y travels only during the rise phase; X/width stay locked to dest.
        text_x = dest_text_x
        text_y = start_y + (dest_text_y - start_y) * rise_u

        text_h = max(1, len(dest_lines)) * dest.line_height
        text_layer = Image.new(
            "RGBA",
            (max(1, int(dest_text_w) + 4), max(1, int(text_h) + 4)),
            (0, 0, 0, 0),
        )
        text_draw = ImageDraw.Draw(text_layer)
        ty = 0
        for line in dest_lines:
            text_draw.text((0, ty), line, font=self._font_history, fill=(*self._CHAT_TEXT, text_a))
            ty += dest.line_height

        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        tw, th = text_layer.size
        bubble_a = int(round(255 * self.theme.bubble_opacity))
        radius = max(self.theme.bubble_radius_min, min(self.theme.bubble_radius_max, int(self.theme.bubble_radius_base * self.config.preview_scale)))
        bx0 = int(round(text_x - dest_pad_x))
        by0 = int(round(text_y - dest_pad_y))
        bx1 = bx0 + dest_bubble_w
        by1 = by0 + int(round(th + dest_pad_y * 2))
        # Hard clamp against the right ceiling every frame.
        if bx1 > content_right:
            shift = bx1 - content_right
            bx0 -= shift
            bx1 -= shift
            text_x -= shift
        draw.rounded_rectangle(
            [bx0, by0, bx1, by1],
            radius=radius,
            fill=(*self._USER_BUBBLE, bubble_a),
        )
        overlay.paste(text_layer, (int(round(text_x)), int(round(text_y))), text_layer)

        tag = pretty_model_name(utterance.model_slug) if utterance.model_slug else utterance.speaker_name
        tag_box = draw.textbbox((0, 0), tag, font=self._font_small)
        tag_w = max(1, tag_box[2] - tag_box[0])
        tag_h = dest.label_height
        tag_x = int(round(bx1 - tag_w))
        tag_y = int(round(by0 - tag_h))
        # Latest acceptance baseline compares this label directly to the send
        # button in one lossless screenshot. Keep its ink at the theme's pure
        # accent and full alpha; only viewport masking may affect settled labels.
        self._pending_labels.append((tag_x, tag_y, tag, self._ORCH_LABEL_FILL, 1.0))

        canvas.paste(overlay, (0, 0), overlay)

    def apply_scroll_offset(self, stack_h: int, vh: int, pad: int) -> int:
        """Virtual-scroll window offset: lift content up only when it overflows.

        ``scroll_offset = max(0, total_content + top_pad - viewport_h)``. A stack
        that fits the viewport stays top-anchored (0); an overflowing stack shifts
        up by exactly the overflow so the newest bubble keeps its gap above the
        input box. Never negative.
        """
        return max(0, int(stack_h) + int(pad) - int(vh))

    def _content_right_ceiling(self) -> int:
        """Hard right clamp: right-most x any right-aligned element may occupy."""
        return self.width - self._layout.margin_x

    def _draw_compose_bar(
        self,
        canvas: Any,
        *,
        model_slug: str,
        accent: tuple[int, int, int],
        send_flash: bool,
        send_flash_u: float | None = None,
        draft: str = "",
        dock: float = 1.0,
        anchor: str = "bottom",
        caret_on: bool = False,
    ) -> int:
        """Fat Gemini prompt: large type on top, model-tinted send at the bottom-right.

        Returns the box top so the chat viewport can sit above it.
        """
        from PIL import ImageDraw  # noqa: PLC0415

        draw = ImageDraw.Draw(canvas)
        left, bar_top, right, bar_bot, lines, line_h = self._compose_geometry(draft, dock=dock, anchor=anchor)
        if bar_bot - bar_top < 28:
            return bar_top
        radius = self._compose_radius()
        fill = _mix(self._COMPOSE_FILL, self.palette["chrome"], 0.35)
        draw.rounded_rectangle([left, bar_top, right, bar_bot], radius=radius, fill=fill)

        pad = max(16, int(self.height * 0.012))
        btn = self._send_btn_size()
        btn_right = right - pad
        btn_left = btn_right - btn
        btn_bot = bar_bot - pad
        btn_top = btn_bot - btn
        # Press depth on click: the circle brightens slightly and the arrow DIMS
        # (a fade-press), then recovers — a lit/press contrast, not a hard flash.
        # When a live progress ``send_flash_u`` is supplied the press fades in
        # and out through an eased curve (1->pressed->released) instead of a
        # hard boolean toggle, so the arrow never snaps across frames.
        press = 1.0
        if send_flash_u is not None and 0.0 <= send_flash_u <= 1.0:
            press = 1.0 - send_click_alpha(send_flash_u, 1.0)  # 0..~0.68..0
        press = min(1.0, max(0.0, float(press))) if send_flash else 0.0
        btn_fill = _mix(accent, (255, 255, 255), 0.34 * press) if press > 0 else accent
        draw.ellipse([btn_left, btn_top, btn_right, btn_bot], fill=btn_fill)
        size = max(14, int(btn * 0.50))
        arrow_fill = _mix(self._TITLE_WHITE, (0, 0, 0), 0.55 * press) if press > 0 else self._TITLE_WHITE
        self._draw_send_arrow(
            draw,
            ((btn_left + btn_right - size) // 2, btn_top + (btn - size) // 2),
            size=size,
            fill=arrow_fill,
        )
        # Soft press shadow just under the circle while active.
        if send_flash:
            draw.ellipse(
                [btn_left, btn_top, btn_right, btn_bot],
                outline=_mix(accent, (0, 0, 0), 0.25),
                width=max(2, int(self.config.preview_scale)),
            )

        label = pretty_model_name(model_slug or "")
        box = draw.textbbox((0, 0), label, font=self._font_small)
        label_w = box[2] - box[0]
        label_h = box[3] - box[1]
        label_x = btn_left - max(14, int(btn * 0.50)) - label_w
        label_y = btn_top + max(0, (btn - label_h) // 2)
        draw.text(
            (label_x, label_y),
            label,
            font=self._font_small,
            fill=self._COMPOSE_MODEL_LABEL_FILL,
        )

        text_x = left + pad
        text_y = bar_top + pad
        if lines:
            for line in lines:
                draw.text((text_x, text_y), line, font=self._font_compose, fill=self._CHAT_TEXT)
                text_y += line_h
            # Blue orchestrator caret while the draft is still being typed / held.
            if caret_on:
                last = lines[-1]
                caret_row_top = text_y - line_h
                bbox = draw.textbbox((text_x, caret_row_top), last, font=self._font_compose)
                caret_x = bbox[2] + max(3, int(self.config.scaled_font_size * 0.14))
                caret_w = max(4, int(self.config.scaled_font_size * 0.42))
                caret_h = max(10, int(line_h * 0.78))
                draw.rectangle(
                    [caret_x, caret_row_top + 2, caret_x + caret_w, caret_row_top + 2 + caret_h],
                    fill=self._GEMINI_BLUE,
                )
        else:
            draw.text(
                (text_x, text_y),
                "Message",
                font=self._font_compose,
                fill=self._fade(self.palette["dim"], 0.7),
            )
        return bar_top

    # -- Static chrome ------------------------------------------------------ #
    def _chrome(self, topic: str) -> Any:
        """Build (and cache) the static background: frame, rules, topic, footer.

        Cached per topic because it is identical for every frame of a render —
        redrawing it 2,880 times is pure waste.
        """
        cached = self._chrome_cache.get(topic)
        if cached is not None:
            return cached.copy()

        from PIL import Image, ImageDraw  # noqa: PLC0415

        canvas = Image.new("RGB", (self.width, self.height), self.palette["background"])
        draw = ImageDraw.Draw(canvas)
        layout = self._layout
        dim = self.palette["dim"]
        # Centered Title-Case wordmark, font bumped up slightly and pure white.
        title_y = max(16, int(self.height * 0.032))
        title = "Aiwake"
        title_box = draw.textbbox((0, 0), title, font=self._font_title)
        title_w = title_box[2] - title_box[0]
        title_x = (self.width - title_w) // 2
        draw.text(
            (title_x, title_y),
            title,
            font=self._font_title,
            fill=self._TITLE_WHITE,
        )
        title_drawn = draw.textbbox((title_x, title_y), title, font=self._font_title)
        rule_y = title_drawn[3] + max(14, int(self.height * 0.013))
        # Header rule under "Aiwake" — explicit 0.8 opacity toward the dim grey.
        rule_color = _mix(self.palette["background"], dim, self.theme.header_divider_opacity)
        draw.line(
            [(layout.margin_x, rule_y), (self.width - layout.margin_x, rule_y)],
            fill=rule_color,
            width=max(1, int(round(self.theme.header_rule_width_scale * self.config.preview_scale))),
        )
        # Optional echo is a Theme control. It defaults off because this second
        # adjacent stroke was making a nominally thin divider read as 2–3 px.
        echo_opacity = min(
            1.0, max(0.0, float(getattr(self.theme, "header_rule_echo_opacity", 0.0)))
        )
        if echo_opacity > 0.0:
            draw.line(
                [
                    (layout.margin_x, rule_y + max(2, int(round(1.4 * self.config.preview_scale)))),
                    (
                        self.width - layout.margin_x,
                        rule_y + max(2, int(round(1.4 * self.config.preview_scale))),
                    ),
                ],
                fill=_mix(self.palette["background"], dim, echo_opacity),
                width=max(1, int(round(self.config.preview_scale))),
            )

        footer_box = draw.textbbox((0, 0), _FOOTER_COPY, font=self._font_small)
        footer_h = footer_box[3] - footer_box[1]
        # Moved up slightly and rendered at the (slightly larger) small font.
        footer_y = self.height - max(30, int(self.height * 0.030)) - footer_h
        # Exact horizontal centre: X sits at width/2 minus half the text width.
        footer_x = (self.width - (footer_box[2] - footer_box[0])) // 2
        draw.text(
            (footer_x, footer_y),
            _FOOTER_COPY,
            font=self._font_small,
            fill=_mix(dim, (0, 0, 0), 0.18),
        )

        self._chrome_cache[topic] = canvas
        return canvas.copy()

    # -- Per-frame composition ---------------------------------------------- #
    def _measure_stack(self, segments: Sequence[Segment], *, include_current: Segment | None = None) -> int:
        """Pixel height of a chronological chat stack (no line-count cap)."""
        gap = self._turn_gap()
        total = 0
        count = 0
        for segment in segments:
            block = self._measure_block(
                segment.utterance,
                font=self._font_history,
                max_height=10_000,
                faded=True,
            )
            total += block.height + (gap if count else 0)
            count += 1
        if include_current is not None:
            block = self._measure_block(
                include_current.utterance,
                font=self._font_history,
                max_height=10_000,
            )
            total += block.height + (gap if count else 0)
        return total

    def _streaming_current_height(self, segment: Segment, revealed: int) -> int:
        """Height of the currently-streaming line grown to its partial reveal.

        Lets the viewport ease upward continuously as tokens stream in instead of
        waiting for the line to finish and jumping. Uses the same history font
        and locked line rhythm as the renderer, so partial and settled heights
        line up exactly (no jitter).
        """
        if revealed >= len(segment.utterance.text):
            block = self._measure_block(segment.utterance, font=self._font_history, max_height=10_000)
            return block.height
        text = segment.utterance.text[: revealed if revealed > 0 else 1]
        bubble = segment.utterance.role is SpeakerRole.ORCHESTRATOR
        lines = self._wrap_for_font(
            text,
            font=self._font_history,
            width_frac=self.theme.orch_bubble_width_frac if bubble else self.theme.target_bubble_width_frac,
        )
        native_lh = self._line_height_of(self._font_history, scale=self.theme.line_spacing_history)
        label_h = max(1, int(native_lh * self.theme.label_height_scale))
        pad_y = max(8, int(self.height * self.theme.body_pad_frac_y)) if bubble else 0
        return label_h + pad_y * 2 + max(1, len(lines)) * native_lh


    def viewport_scroll_px(
        self,
        segments: Sequence[Segment],
        index: int,
        t: float,
        *,
        revealed: int | None = None,
        bar_draft: str = "",
        bar_dock: float = 1.0,
    ) -> tuple[int, bool]:
        """Return ``(scroll_px, send_flash)`` for time ``t`` on ``segments[index]``.

        ``scroll_px`` is how many pixels of the stacked transcript sit above the
        top of the chat mask. The viewport height matches the *actual* compose
        layout drawn this frame (computed from ``bar_draft``/``bar_dock``, so a
        multi-line prompt box is accounted for) — this is what keeps the thread
        from over-scrolling into a huge gap.

        There is exactly ONE behaviour: every transition eases toward the target
        offset over ``scroll_s``. No branch ever moves the thread instantly. The
        target is tightly bounded to leave a fixed ``_SCROLL_GAP_PX`` breathing
        gap between the newest bubble bottom and the compose bar top — it retargets
        that gap continuously as content grows (landing a bubble or streaming a
        response) and never overshoots past it.
        """
        layout = self._layout
        gap = self._turn_gap()
        body_top = layout.body_top
        # Compose bar top for the CURRENT draft/dock (identical to `_compose`).
        _, bar_top, _, _, _, _ = self._compose_geometry(bar_draft, dock=bar_dock, anchor="bottom")
        body_bottom = min(layout.body_bottom, bar_top - max(10, int(self.height * 0.012)))
        vh = max(1, body_bottom - body_top)
        current = segments[index]
        prev = segments[:index]
        composing = (
            current.utterance.role is SpeakerRole.ORCHESTRATOR
            and t < self._box_until_s(current)
        )
        prev_h = self._measure_stack(prev)
        revealed_count = revealed if revealed is not None else current.revealed_chars(t)
        streaming = (
            not composing
            and revealed_count < len(current.utterance.text)
        )
        # NOTE: keep ``vh`` identical to ``_compose`` (no pad shrink here). The
        # stream-pad subtraction caused the viewport to over-scroll during
        # streaming, leaving a large gap. The target below is derived from the
        # EXACT stack ``_compose`` draws this frame, so the resulting gap to the
        # box top lands on ``_SCROLL_GAP_PX`` (never a fixed/estimated amount).
        #
        # ``_compose`` draws: prev bubbles + current ONLY when current is not
        # still in the compose box (composing). Mirror that here so the scroll
        # target matches the drawn stack 1:1:
        #   - composing (orchestrator prompt in the box): stack = prev only
        #   - streaming or settled:                          stack = prev + current
        if composing:
            stack_h = self._measure_stack(prev)
        elif streaming:
            stack_h = prev_h + self._streaming_current_height(current, revealed_count)
            stack_h += gap if prev else 0
        else:
            stack_h = self._measure_stack(prev, include_current=current)
        # Target so the last drawn content's bottom sits exactly GAP above the
        # box top: content-bottom screen-Y = body_top + stack_h - target = bar_top - GAP
        # While typing/streaming, add a small relative nudge so the active line
        # clears the compose-bar bottom mask by a slightly larger margin.
        gap_goal = int(self.theme.scroll_gap_px) + (self.theme.scroll_stream_extra_px if streaming else 0)
        target = max(0, body_top + stack_h - (bar_top - gap_goal))
        scroll_s = max(0.05, float(self.config.scroll_s))

        # ONE continuous eased motion: ease the *current* eased px toward
        # ``target`` with a bounded approach and a per-frame velocity cap, never
        # a hard snap. State persists per renderer so frame N continues from
        # frame N-1 (this is what fixed the 50px+ one-frame jumps).
        # Signature that changes when a new render/transcript starts.
        sig = (
            len(segments),
            int(target),
            int(round(t * 8)),
        )
        ease = self._scroll_ease
        if sig[0] != len(segments) or ease["sig"] != str(id(segments)):
            ease["px"] = float(target)
            ease["anchor_t"] = float(t)
            ease["sig"] = str(id(segments))
        # ONE continuous, frame-rate-independent exponential approach applied on
        # EVERY rendered frame. Whatever the state (composing / streaming / just
        # landed), the offset eases a small, bounded fraction of the remaining
        # distance toward the current target every frame:
        #     offset += (target - offset) * (1 - exp(-dt / tau))
        # because `dt` is the live per-frame time. A target jump (a line finishing
        # and wrapping, a bubble landing) is therefore spread across many frames
        # instead of snapping in one — no zero-then-jump, no re-pin to target.
        # The target itself is recomputed each frame from the current content
        # height, so while content grows the offset keeps gliding toward it.
        dt = float(t) - ease["anchor_t"]
        if dt < 0.0 or dt > 4.0:
            # Out-of-order / isolated single-frame queries (tests probe arbitrary
            # times): treat as already-settled at the current target.
            ease["px"] = float(target)
            ease["anchor_t"] = float(t)
            scroll_px = target
        else:
            ratio = 1.0 - math.exp(-max(dt, 1e-6) / self.theme.scroll_tau_s)
            ease["px"] += (float(target) - ease["px"]) * ratio
            ease["anchor_t"] = float(t)
            # Within ~1px of the target, lock on so we never trail on a stable
            # stack. This lock is NEAR target, so it does not skip a glide.
            if abs(float(target) - ease["px"]) <= 1.0:
                ease["px"] = float(target)
            scroll_px = ease["px"]

        # Actual on-screen gap between the last drawn content bottom and the
        # compose-box top (using the LIVE eased scroll, not the target), so tests
        # / logs can assert the real measured gap per turn.
        self._last_scroll_gap = max(
            0, int(round(bar_top - (body_top + stack_h - scroll_px)))
        )

        click_start = self._click_start_s(current)
        send_flash = (
            current.utterance.role is SpeakerRole.ORCHESTRATOR
            and t >= click_start
            and (t - click_start) < float(self.config.send_flash_s)
            and current.revealed_chars(t) >= len(current.utterance.text)
        )
        return int(round(scroll_px)), send_flash

    def _top_mask_px(self, scroll_px: int, vh: int) -> int:
        """Top fade height (40-50px) once content scrolls under the header.

        Resting stacks (``scroll_px <= 0``) keep the first bubble fully visible;
        any overflow scrolls into a smooth 40-50px alpha-to-background blend
        instead of a hard clip at ``y = 0``.
        """
        if int(scroll_px) <= 0 or vh <= 0:
            return 0
        return min(max(40, int(self.height * 0.028)), max(40, int(vh * 0.055)), vh - 1)

    def _draw_response_loader(
        self,
        draw: Any,
        origin_y: int,
        progress: float,
    ) -> None:
        """Draw a lightweight Gemini-style three-dot pre-response loader."""
        duration = max(1e-6, float(getattr(self.config, "response_loader_s", 0.36)))
        pulse_s = max(0.05, float(getattr(self.config, "response_loader_pulse_s", 0.18)))
        count = max(1, int(getattr(self.config, "response_loader_dot_count", 3)))
        radius = max(2, int(round(5 * self.config.preview_scale)))
        gap = max(radius * 3, int(round(16 * self.config.preview_scale)))
        x0 = self._layout.margin_x + radius
        y0 = origin_y + max(radius * 2, int(self._layout.line_height * 0.55))
        elapsed = min(1.0, max(0.0, float(progress))) * duration
        for index in range(count):
            phase = ((elapsed / pulse_s) - index * 0.42) % 1.0
            pulse = 1.0 - abs(phase * 2.0 - 1.0)
            intensity = 0.30 + 0.70 * pulse
            color = _mix(self.palette["background"], self.palette["target"], intensity)
            cx = x0 + index * gap
            draw.ellipse(
                [cx - radius, y0 - radius, cx + radius, y0 + radius],
                fill=color,
            )

    def _compose(
        self,
        topic: str,
        history: Sequence[Segment],
        current: Segment | None,
        revealed: int,
        caret_on: bool,
        *,
        scroll_px: int = 0,
        send_flash: bool = False,
        send_flash_u: float | None = None,
        composer_slug: str = "",
        composer_accent: tuple[int, int, int] | None = None,
        draft: str = "",
        composing: bool = False,
        dock: float = 1.0,
        fly: float = 1.0,
        landing_fade: float | None = None,
        response_loader: float | None = None,
        compose_source: str = "",
        cta: str = "",
        rise: float = 1.0,
        suppress_bar: bool = False,
    ) -> np.ndarray:
        """Draw one frame: chrome + scrolling chat + compose box.

        Landing (dock=0): fat prompt centred with the live draft. On submit the
        draft is CLEARED (``landing_gone``), the empty chrome morphs down to the
        docked footer, and the glyphs fly up via ``_paste_flying_send`` into the
        thread. The typed text must never be drawn both in the bar and in the
        thread at the same time — that is the duplication bug.
        """
        from PIL import Image, ImageDraw  # noqa: PLC0415

        _ = rise  # legacy kw; fly progress is authoritative
        canvas = self._chrome(topic)
        layout = self._layout
        self._pending_labels = []
        fly_u = min(1.0, max(0.0, float(fly)))
        if landing_fade is None:
            landing_fade_u = 0.0 if fly_u < 0.999 else 1.0
        else:
            landing_fade_u = min(1.0, max(0.0, float(landing_fade)))
        seq = tuple(history) + ((current,) if current is not None else ())
        first_core = self._first_orchestrator(seq)
        current_is_core = current is not None and current.utterance.role is SpeakerRole.ORCHESTRATOR
        in_box = bool(current_is_core and composing)
        flying = bool(
            current_is_core
            and not in_box
            and (fly_u < 0.999 or landing_fade_u < 0.999)
        )
        # Root cause fix: once the line has left the box (or dock has moved, or
        # CTA/outro), the compose bar MUST render empty. Keeping the draft here
        # while also drawing the thread bubble is what froze a duplicate on screen.
        landing_gone = bool(
            flying
            or (not in_box and float(dock) > 0.0)
            or bool(cta)
            or suppress_bar
        )
        if landing_gone:
            # Round-14 fix: the FIRST orchestrator send's box must keep
            # riding the live, continuously-eased ``dock`` value all the way
            # to 1.0 — not just while ``flying`` is still True. Snapping to a
            # hardcoded 1.0 the instant ``flying`` flipped False (well before
            # Phase B's own descend even finished) is what produced the
            # "box already fully docked" instant-swap regression: the box
            # never actually animated, it just appeared already-done.
            bar_dock, bar_draft, bar_source = (
                (float(dock), "", "") if current is first_core else (1.0, "", "")
            )
        else:
            bar_dock = float(dock)
            bar_draft = draft
            bar_source = compose_source or cta
        _ = bar_source
        _, bar_top, _, _, _, _ = self._compose_geometry(bar_draft, dock=bar_dock, anchor="bottom")
        body_bottom = min(layout.body_bottom, bar_top - max(10, int(self.height * 0.012)))
        vh = max(1, body_bottom - layout.body_top)
        chat_top = layout.body_top
        viewport = Image.new("RGB", (self.width, vh), self.palette["background"])
        draw = ImageDraw.Draw(viewport)
        gap = self._turn_gap()

        items: list[tuple[Segment, _Block, bool]] = []
        for segment in history:
            items.append(
                (
                    segment,
                    self._measure_block(
                        segment.utterance,
                        font=self._font_history,
                        max_height=10_000,
                        faded=True,
                    ),
                    True,
                )
            )
        # Reserve the flying line's slot in the stack (so rest_y is correct) but
        # do not paint it as a settled bubble until fly completes.
        if current is not None and not in_box:
            settled_after_send = bool(
                current_is_core
                and fly_u >= 0.999
                and landing_fade_u >= 0.999
            )
            current_block = self._measure_block(
                current.utterance,
                font=self._font_history,
                max_height=10_000,
                faded=settled_after_send,
            )
            items.append((current, current_block, False))

        stack_h = sum(block.height for _, block, _ in items) + gap * max(0, len(items) - 1)
        y = -int(scroll_px) if stack_h > vh else 0
        fly_rest_y = chat_top
        # Computed ONCE and shared by the body's raster blend (below) and every
        # label's deferred alpha (item 5) — the single source of truth for "how
        # faded is this row of the viewport right now".
        mask_fade_h = self._top_mask_px(scroll_px, vh) if vh > 8 else 0

        for segment, block, is_hist in items:
            is_live = not is_hist
            is_flying_current = flying and current is not None and segment is current
            if is_flying_current:
                fly_rest_y = chat_top + y
                y += block.height + gap
                continue
            self._draw_block(
                draw,
                segment.utterance,
                block,
                y,
                revealed=revealed if is_live else None,
                caret_on=caret_on if is_live else False,
                send_flash=False,
                mask_fade_h=mask_fade_h,
            )
            y += block.height + gap

        if response_loader is not None:
            self._draw_response_loader(draw, y, response_loader)

        if vh > 8:
            self._fade_viewport_top(viewport, fade_h=mask_fade_h)
            # Short bottom fade so the newest line stays crisp near the compose
            # box instead of dissolving into a huge dark gap above the input bar.
            self._fade_viewport_bottom(viewport, fade_h=max(10, min(22, int(self.height * 0.012))))
            canvas.paste(viewport, (0, chat_top))

        # Z-ORDER (item 1): draw the compose bar FIRST, then paste the departing
        # fly bubble ON TOP. This guarantees the new empty compose box never
        # visually covers the still-departing bubble while both are mid-transition,
        # regardless of the dock/descend duration tuning.
        slug = composer_slug
        accent = composer_accent
        orchestrator_slug = self._orchestrator_slug()
        if orchestrator_slug:
            slug = orchestrator_slug
        if not slug and current is not None:
            slug = short_model_name(current.utterance.model_slug)
            accent = model_accent(
                current.utterance.model_slug,
                self.palette.get(current.utterance.role.value, self.palette["orchestrator"]),
            )
        if not suppress_bar:
            self._draw_compose_bar(
                canvas,
                model_slug=slug or "model",
                accent=accent or self.palette["orchestrator"],
                send_flash=send_flash and not landing_gone,
                send_flash_u=send_flash_u,
                draft=bar_draft,
                dock=bar_dock,
                anchor="bottom",
                caret_on=bool(composing and caret_on and bar_draft),
            )

        if flying and current is not None:
            is_first = current is first_core
            self._paste_flying_send(
                canvas,
                current.utterance,
                fly_u=fly_u,
                landing_fade_u=landing_fade_u,
                rest_y=fly_rest_y,
                start_dock=0.0 if is_first else 1.0,
            )

        self._draw_pending_labels(canvas)
        return np.asarray(canvas, dtype=np.uint8)

    def _draw_pending_labels(self, canvas: Any) -> None:
        """Paint every deferred sender label as the LAST operation for the frame.

        Item 5 bulletproof technique: each label is rendered onto its own tiny
        RGBA image (``label_img``) with a fully transparent background and the
        glyphs drawn at the pure, unblended fill colour at alpha=255 — this
        image has never touched any other colour/alpha value in the pipeline.
        The label's OWN assigned alpha for this frame (viewport mask only;
        history dim intentionally does not affect label ink) is then applied
        by scaling ONLY that image's alpha channel, never its RGB. The final
        composite is a plain ``canvas.paste(label_img, xy,
        label_img)`` — a per-pixel alpha blend driven solely by ``label_img``'s
        own alpha band. ``Image.paste`` with a mask does not accept, and cannot
        be scaled by, any additional opacity from this call's surrounding
        scope, so it is architecturally impossible for anything upstream
        (bubble fade, history dim, fly overlay, viewport mask) to dilute or
        brighten a label beyond the alpha explicitly carried on its own entry.
        This is the ONLY place any label's pixels reach the frame.
        """
        from PIL import Image, ImageDraw  # noqa: PLC0415

        if not self._pending_labels:
            return
        probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        for x, y, tag, fill, alpha in self._pending_labels:
            alpha = max(0.0, min(1.0, float(alpha)))
            if alpha <= 0.003:
                continue
            box = probe.textbbox((0, 0), tag, font=self._font_small)
            w = max(1, box[2] - box[0])
            h = max(1, box[3] - box[1])
            label_img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(label_img).text((-box[0], -box[1]), tag, font=self._font_small, fill=(*fill, 255))
            if alpha < 0.999:
                arr = np.array(label_img)
                arr[..., 3] = np.round(arr[..., 3].astype(np.float32) * alpha).astype(np.uint8)
                label_img = Image.fromarray(arr)
            paste_xy = (int(round(x)) + box[0], int(round(y)) + box[1])
            _trace_label_paint(tag, fill, alpha, paste_xy)
            canvas.paste(label_img, paste_xy, label_img)

    # -- Timeline ----------------------------------------------------------- #
    @staticmethod
    def build_segments(
        transcript: DebateTranscript,
        audio_by_turn: dict[int, AudioAsset] | None = None,
        *,
        typing_hold_ratio: float = _TAIL_HOLD_RATIO,
        preroll_s: float = 0.0,
        reply_gap_s: float = 0.0,
        post_response_s: float = 0.0,
    ) -> list[Segment]:
        """Lay utterances on the timeline with preroll and post-send gaps.

        Falls back to a word-rate estimate for any utterance without audio, so a
        partially-synthesised transcript still renders with sane pacing.

        ``reply_gap_s`` is inserted after an orchestrator prompt (before the
        reply); ``post_response_s`` is inserted after a target reply (before the
        next prompt). These MUST mirror the audio mixer's ``concat_audio`` gaps
        so a turn's send click, arrow flash, and fly all start on the same master
        timestamp across every turn (no cumulative drift).
        """
        assets = audio_by_turn or {}
        segments: list[Segment] = []
        cursor = max(0.0, float(preroll_s))
        utterances = list(transcript.utterances)
        for index, utterance in enumerate(utterances):
            asset = assets.get(utterance.turn_index)
            duration = asset.duration_s if asset else (utterance.audio_duration_s or estimate_duration(utterance.text))
            segments.append(
                Segment(
                    utterance=utterance,
                    start_s=cursor,
                    duration_s=duration,
                    audio=asset,
                    typing_hold_ratio=typing_hold_ratio,
                )
            )
            cursor += duration
            if index + 1 >= len(utterances):
                continue
            if utterance.role is SpeakerRole.ORCHESTRATOR:
                cursor += max(0.0, float(reply_gap_s))
            else:
                cursor += max(0.0, float(post_response_s))
        return segments

    def _live_end_s(self, segment: Segment) -> float:
        """Time a segment stops being the *current* rendered turn.

        For a sent orchestrator prompt this is the instant its submit fly
        completes, not merely its speech end. The send click + post-hold can run
        past ``end_s``, and the fly then needs to keep animating upward across
        real frames after that — otherwise ``_segment_at`` advances to the next
        turn the moment ``end_s`` passes and the bubble snaps into place in one
        frame instead of flying.
        """
        if segment.utterance.role is SpeakerRole.ORCHESTRATOR:
            rise_s = max(0.1, float(getattr(self.config, "send_rise_duration_s", 0.4)))
            fade_delay_s = max(
                0.0, float(getattr(self.config, "send_landing_fade_delay_s", 0.0) or 0.0)
            )
            fade_s = max(
                0.0, float(getattr(self.config, "send_landing_fade_s", 0.2) or 0.0)
            )
            return max(
                segment.end_s,
                self._box_until_s(segment) + rise_s + fade_delay_s + fade_s,
            )
        return segment.end_s

    def _segment_at(self, segments: Sequence[Segment], t: float) -> tuple[Segment | None, int]:
        """Locate the segment covering ``t``.

        Times before the first line (preroll) return ``(None, -1)``. Times in
        the hold between a sent question and the reply stay on the previous
        (completed) segment. An orchestrator segment stays *current* through its
        send+fly window (``_live_end_s``) so the submit animation is actually
        rendered across multiple frames.
        """
        if not segments or t < segments[0].start_s:
            return None, -1
        last = segments[0]
        last_index = 0
        for index, segment in enumerate(segments):
            live_end = self._live_end_s(segment)
            if t < segment.start_s:
                return last, last_index
            last = segment
            last_index = index
            if t < live_end:
                return segment, index
        return segments[-1], len(segments) - 1

    # -- Render ------------------------------------------------------------- #
    def render(
        self,
        transcript: DebateTranscript,
        *,
        audio_by_turn: dict[int, AudioAsset] | None = None,
        filename: str | None = None,
    ) -> Path:
        """Produce the final MP4.

        Args:
            transcript: The debate to visualise. System annotations render as
                title cards like any other line.
            audio_by_turn: Voice tracks keyed by ``turn_index``. Absent entries
                fall back to estimated durations (silent video).
            filename: Override the output name.

        Returns:
            Path to the written video.

        Raises:
            RenderError: MoviePy/Pillow are unavailable, the transcript is
                empty, or encoding failed.
        """
        if not transcript.utterances:
            raise RenderError("nothing to render — transcript is empty")

        try:
            from moviepy import AudioFileClip, VideoClip  # noqa: PLC0415
        except ImportError as exc:
            raise RenderError("moviepy is required to render — `pip install moviepy`") from exc

        segments = self.build_segments(
            transcript,
            audio_by_turn,
            typing_hold_ratio=self.config.typing_hold_ratio,
            preroll_s=float(self.config.preroll_s),
            reply_gap_s=float(self.config.reply_gap_s),
            post_response_s=float(self.config.post_response_s),
        )
        # CTA end-card: pick a stable follow line, prep its voice track (when the
        # audio backend supports it), and extend the timeline so the card types
        # out and holds without cutting the VO. A short tail after the last word
        # gives breathing room before the fade.
        cta_text = pick_cta(transcript.session_id)
        cta_asset = self._prepare_cta_voice(cta_text, transcript.session_id)
        cta_vo_s = max(1.2, float(cta_asset.duration_s if cta_asset is not None else estimate_duration(cta_text)))
        wait_s = float(getattr(self.config, "cta_wait_s", _CTA_WAIT_S))
        tail_s = float(_CTA_TAIL_S)
        postroll = tail_s + wait_s + _CTA_FADE_S + cta_vo_s + _CTA_HOLD_S
        total_duration = (segments[-1].end_s if segments else 0.0) + postroll
        last_end = segments[-1].end_s if segments else 0.0
        # CTA VO starts the moment the end-card finishes fading in.
        cta_audio_start_s = last_end + tail_s + wait_s + _CTA_FADE_S
        topic = transcript.topic
        fps = self.config.fps

        vfx_chain = resolve_chain(self.settings.vfx)
        wants_audio_samples = bool(vfx_chain) and chain_needs_audio(self.settings.vfx)

        def make_frame(t: float) -> np.ndarray:
            segment, index = self._segment_at(segments, t)
            if segment is None or index < 0:
                first = segments[0]
                composer_slug, composer_accent, _ = self.composer_state(segments, 0, first.start_s)
                dock = self.dock_progress(segments, t)
                key = ("preroll", composer_slug, int(round(dock * 48)))
                frame = self._frame_cache.get(key)
                if frame is None:
                    frame = self._compose(
                        topic,
                        (),
                        None,
                        0,
                        False,
                        composer_slug=composer_slug,
                        composer_accent=composer_accent,
                        draft="",
                        composing=True,
                        dock=dock,
                    )
                    self._frame_cache[key] = frame
                if not vfx_chain:
                    return frame
                context = FrameContext(
                    t=t,
                    frame_index=int(t * fps),
                    fps=fps,
                    size=(self.width, self.height),
                    speaker="",
                    progress=0.0,
                    audio_samples=None,
                )
                return apply_chain(frame.copy(), context, vfx_chain)

            revealed = segment.revealed_chars(t)
            typing = revealed < len(segment.utterance.text)
            typing_window = max(1e-6, 1.0 - segment.typing_hold_ratio)
            typing_end = segment.start_s + segment.duration_s * typing_window
            # Keep the draft IN the compose box through typing + read-hold + click.
            # Only after ``_box_until_s`` does ``landing_gone`` clear it and the
            # fly overlay take over — never leave text in both places.
            in_box = (
                segment.utterance.role is SpeakerRole.ORCHESTRATOR
                and t < self._box_until_s(segment)
            )
            composing = in_box
            # Caret blinks while the compose input is active (typing OR the
            # post-type hold before send) OR while a response is still rendering
            # chars. Once a line finishes typing the caret turns OFF — it never
            # stays lit when idle (that froze both the blue and orange carets).
            caret_on = (
                (t % _CARET_PERIOD_S) < (_CARET_PERIOD_S * 0.55)
                if (in_box or typing)
                else False
            )
            history = segments[:index]
            if in_box:
                draft = segment.utterance.text[:revealed] if typing else segment.utterance.text
            else:
                draft = ""
            dock = self.dock_progress(segments, t)
            fly = self.bubble_fly_progress(segment, t)
            landing_fade = self.landing_fade_progress(segment, t)
            response_loader = self.response_loader_progress(segments, index, t)
            # Pass the live compose draft/dock so scroll targets the ACTUAL bar
            # geometry being drawn (a multi-line prompt box is taller than an
            # empty one) — this stops the over-scroll that left a huge gap.
            scroll_px, send_flash = self.viewport_scroll_px(
                segments, index, t, revealed=revealed, bar_draft=draft, bar_dock=dock
            )
            composer_slug, composer_accent, composer_flash = self.composer_state(segments, index, t)
            send_flash = send_flash or composer_flash
            # Normalised progress (0..1) through the send-click flash window so
            # the arrow press/fade eases instead of snapping, when active.
            send_flash_u = None
            if send_flash:
                cs = self._click_start_s(segment)
                wnd = self._send_click_s()
                if wnd > 1e-6 and t >= cs:
                    send_flash_u = min(1.0, max(0.0, (t - cs) / wnd))
            # Outro: the input box stays on screen and is carried by the CTA
            # black-fade instead of being cut instantly the moment the last line
            # clears. Once the fade to black completes it is no longer visible.
            cta_fade_end_s = last_end + tail_s + wait_s + _CTA_FADE_S
            suppress_bar = t >= cta_fade_end_s

            # CTA window: breath after last word, wait, fade to black, then type
            # the borrowed follow line as an end-card.
            fade_u = 0.0
            cta_local = -1.0
            cta_caret = False
            if t >= last_end:
                elapsed = t - last_end
                if elapsed >= tail_s + wait_s:
                    fade_u = ease_in_out((elapsed - tail_s - wait_s) / max(1e-6, _CTA_FADE_S))
                    if fade_u >= 0.999:
                        cta_local = elapsed - tail_s - wait_s - _CTA_FADE_S
                        preview = cta_revealed_chars(cta_text, cta_local, cta_vo_s)
                        cta_caret = preview < len(cta_text) and (
                            (t % _CARET_PERIOD_S) < (_CARET_PERIOD_S * 0.55)
                        )

            key = (
                segment.utterance.turn_index,
                revealed,
                caret_on,
                send_flash,
                int(round(send_flash_u * 24)) if send_flash_u is not None else -1,
                scroll_px,
                composer_slug,
                composing,
                int(round(fade_u * 24)),
                int(round(max(0.0, cta_local) * 24)),
                cta_caret,
                int(round(min(1.0, dock) * 48)),
                int(round(min(1.0, fly) * 96)),
                int(round(min(1.0, landing_fade) * 48)),
                (
                    int(round(min(1.0, response_loader) * 24))
                    if response_loader is not None
                    else -1
                ),
                suppress_bar,
                tuple(item.utterance.turn_index for item in history),
            )
            frame = self._frame_cache.get(key)
            if frame is None:
                if fade_u >= 0.999:
                    revealed_cta = cta_revealed_chars(cta_text, cta_local, cta_vo_s)
                    frame = self._draw_cta_frame(cta_text, revealed_cta, caret_on=cta_caret)
                else:
                    frame = self._compose(
                        topic,
                        history,
                        segment,
                        revealed,
                        caret_on,
                        scroll_px=scroll_px,
                        send_flash=send_flash,
                        send_flash_u=send_flash_u,
                        composer_slug=composer_slug,
                        composer_accent=composer_accent,
                        draft=draft,
                        composing=composing,
                        dock=dock,
                        fly=fly,
                        landing_fade=landing_fade,
                        response_loader=response_loader,
                        suppress_bar=suppress_bar,
                    )
                    if fade_u > 0.0:
                        frame = self._fade_frame_to_black(frame, fade_u)
                # Bound the cache: distinct states per segment are few, but a
                # long session across many segments would otherwise creep.
                if len(self._frame_cache) > 512:
                    self._frame_cache.clear()
                self._frame_cache[key] = frame

            if not vfx_chain:
                return frame

            context = FrameContext(
                t=t,
                frame_index=int(t * fps),
                fps=fps,
                size=(self.width, self.height),
                speaker=segment.utterance.role.value,
                progress=segment.progress_at(t),
                audio_samples=None if not wants_audio_samples else self._audio_window(segment, t),
            )
            # copy(): hooks mutate in place, the cache entry must stay pristine.
            return apply_chain(frame.copy(), context, vfx_chain)

        clip = VideoClip(make_frame, duration=total_duration)
        master_audio = self._master_audio(
            segments,
            transcript.session_id,
            postroll_s=postroll,
            cta_overlay_path=cta_asset.path if cta_asset is not None else None,
            cta_overlay_start_s=cta_audio_start_s,
        )
        audio_clip = None
        if master_audio is not None:
            audio_clip = AudioFileClip(str(master_audio))
            clip = clip.with_audio(audio_clip)

        destination = self.output_dir / (filename or f"aiwake_debate_{transcript.session_id}.mp4")
        destination.parent.mkdir(parents=True, exist_ok=True)

        _LOG.info(
            "rendering %s — %.1fs, %dx%d @ %dfps, %d segment(s)",
            destination.name,
            total_duration,
            self.width,
            self.height,
            fps,
            len(segments),
        )
        try:
            clip.write_videofile(
                str(destination),
                fps=fps,
                codec=self.config.codec,
                audio=master_audio is not None,
                preset=self.config.preset,
                ffmpeg_params=["-pix_fmt", "yuv420p", "-crf", str(self.config.crf)],
                logger=None,
                temp_audiofile_path=str(resolve_scratch_dir()),
            )
        except Exception as exc:  # noqa: BLE001
            raise RenderError(f"encoding failed: {exc}") from exc
        finally:
            clip.close()
            if audio_clip is not None:
                audio_clip.close()

        _LOG.info("wrote %s (%.1f MB)", destination, destination.stat().st_size / 1e6)
        return destination

    # -- Audio helpers ------------------------------------------------------ #
    def _master_audio(
        self,
        segments: Sequence[Segment],
        session_id: str,
        *,
        postroll_s: float = 0.0,
        cta_overlay_path: Path | None = None,
        cta_overlay_start_s: float = 0.0,
    ) -> Path | None:
        """Stitch segment tracks into one file matching the video timeline.

        ``postroll_s`` pads silence through the CTA window; ``cta_overlay_path``
        is mixed at ``cta_overlay_start_s`` so the invite is spoken over the
        end-card. Together these keep A/V durations aligned end-to-end.
        """
        assets = [segment.audio for segment in segments if segment.audio and segment.audio.path.is_file()]
        if not assets:
            return None
        destination = resolve_scratch_dir() / f"aiwake_master_{session_id}.mp3"
        return concat_audio(
            assets,
            destination,
            preroll_s=float(self.config.preroll_s),
            reply_gap_s=float(self.config.reply_gap_s),
            post_response_s=float(self.config.post_response_s),
            postroll_s=float(postroll_s),
            send_hold_s=float(self.config.send_hold_s),
            cta_overlay_path=cta_overlay_path,
            cta_overlay_start_s=float(cta_overlay_start_s),
            typewriter=self.settings.audio.typewriter,
            typing_hold_ratio=self.config.typing_hold_ratio,
            bgm=self.settings.audio.bgm,
            send_sfx=self.settings.audio.send_sfx,
            master_gain_db=float(getattr(self.settings.audio, "master_gain_db", 6.0) or 0.0),
        )

    def _prepare_cta_voice(self, cta_text: str, session_id: str) -> AudioAsset | None:
        """Synthesise (or estimate) the CTA voice track into scratch.

        Falls back to ``None`` when the audio backend has no ``synthesize_cta_line``
        entry point (e.g. during standalone extraction), so the visual CTA end-card
        still renders with an estimated duration.
        """
        synthesize_cta_line = None
        try:
            from ..media.audio import synthesize_cta_line  # noqa: PLC0415
        except ImportError:  # pragma: no cover
            try:
                from media.audio import synthesize_cta_line  # type: ignore[no-redef]
            except ImportError:  # pragma: no cover
                synthesize_cta_line = None
        if synthesize_cta_line is None:
            _LOG.debug("CTA voice synthesis unavailable; using word-rate estimate")
            return None
        try:
            dest = resolve_scratch_dir() / f"cta_{session_id}.mp3"
            return synthesize_cta_line(cta_text, dest, config=self.settings.audio)
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("CTA voice synthesis failed: %s", exc)
            return None

    def _fade_frame_to_black(self, frame: np.ndarray, u: float) -> np.ndarray:
        """Cross-fade a composed frame toward black by progress ``u`` (0..1)."""
        u = min(1.0, max(0.0, float(u)))
        if u <= 0.0:
            return frame
        out = frame.astype(np.float32)
        out *= (1.0 - u)
        if u >= 1.0:
            return np.zeros_like(out)
        return out.astype(np.uint8)

    def _draw_cta_frame(self, text: str, revealed: int, *, caret_on: bool) -> np.ndarray:
        """Paint the black CTA end-card as a two-line period structure.

        "Follow Aiwake." is line one and the follow-on sentence is line two, both
        drawn with the SAME font (``_font_cta``) so the two lines read as one
        matching invitation rather than a mismatched headline + footnote. Both
        lines wrap inside ~76% of the safe column so long invites never bleed
        past either screen edge.
        """
        from PIL import Image, ImageDraw  # noqa: PLC0415

        canvas = Image.new("RGB", (self.width, self.height), (0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        full = (text or "").strip()
        accent = self.palette["orchestrator"]
        # Two-line, period-based CTA: split at the explicit newline separator into
        # headline ("Follow Aiwake.") and the second sentence. A '\n' is written
        # by ``ensure_cta_two_line``; fall back to sentence-splitting for any
        # legacy single-line text.
        if "\n" in full:
            head, _, tail = full.partition("\n")
        else:
            head, sep, tail = full.partition(". ")
            if not tail.strip():
                head, tail = full, ""
        tail = tail.strip()
        if not head:
            head, tail = full, ""
        # Typewriter reveal: the headline types first (so the caret rests at the
        # end of ``Follow Aiwake.`` during the short beat), then the tail bleeds
        # in beneath it after ``_CTA_PAUSE_S``.
        head_visible = head[: min(len(head), revealed)]
        head_len = len(head)
        remaining = max(0, revealed - head_len)
        tail_visible = tail[:remaining] if tail else ""

        ly = int(self.height * 0.40)
        last_lx, last_lw, last_ly, last_lh = self._layout.margin_x, 0, ly, max(2, self.width // 80)
        if head_visible:
            hlines = self._wrap_for_font(head_visible, font=self._font_cta, width_frac=0.76) or [""]
            hline_h = self._line_height_of(self._font_cta, scale=self.theme.line_spacing_cta)
            for i, line in enumerate(hlines):
                bbox = draw.textbbox((0, 0), line or " ", font=self._font_cta)
                lw = max(1, bbox[2] - bbox[0]) if line else 0
                lx = (self.width - max(1, lw)) // 2
                lx = max(self._layout.margin_x, min(lx, self.width - self._layout.margin_x - max(1, lw)))
                if line:
                    draw.text((lx, ly + i * hline_h), line, font=self._font_cta, fill=self._TITLE_WHITE)
            bline_h = self._line_height_of(self._font_cta, scale=self.theme.line_spacing_cta)
            last_lx, last_lw, last_ly, last_lh = (
                lx,
                lw,
                ly + (len(hlines) - 1) * bline_h,
                max(1, bbox[3] - bbox[1]),
            )
            ly += len(hlines) * bline_h

        if tail_visible or head_visible:
            # Same-font remainder line(s) immediately beneath the headline so the
            # two CTA sentences share an identical font size.
            ly += int(self.height * 0.045)
            tlines = self._wrap_for_font(tail_visible, font=self._font_cta, width_frac=0.76) if tail_visible else [""]
            tline_h = self._line_height_of(self._font_cta, scale=self.theme.line_spacing_cta)
            for j, line in enumerate(tlines):
                bbox = draw.textbbox((0, 0), line or " ", font=self._font_cta)
                lw = max(1, bbox[2] - bbox[0]) if line else 0
                lx = (self.width - max(1, lw)) // 2
                lx = max(self._layout.margin_x, min(lx, self.width - self._layout.margin_x - max(1, lw)))
                if line:
                    draw.text((lx, ly + j * tline_h), line, font=self._font_cta, fill=self._TITLE_WHITE)
                if not tail_visible or (tail_visible and j == len(tlines) - 1):
                    last_lx, last_lw, last_ly, last_lh = (
                        lx,
                        lw,
                        ly + j * tline_h,
                        max(1, bbox[3] - bbox[1]),
                    )

        if caret_on and revealed < len(full):
            cx = last_lx + last_lw
            draw.rectangle(
                [cx, last_ly, cx + max(2, self.width // 140), last_ly + max(2, last_lh)],
                fill=accent,
            )
        return np.asarray(canvas)

    def _audio_window(self, segment: Segment, t: float) -> np.ndarray | None:
        """Mono sample window under the current frame, for visualizer hooks.

        Stubbed to None: implement alongside the ``audio_visualizer`` hook by
        decoding the segment track once into a cached array and slicing it here.
        Kept as a seam so the hook signature does not change later.
        """
        return None


# --------------------------------------------------------------------------- #
# Convenience entry point
# --------------------------------------------------------------------------- #
def render_transcript(
    transcript: DebateTranscript,
    settings: AiwakeSettings,
    *,
    audio_by_turn: dict[int, AudioAsset] | None = None,
    output_dir: Path | None = None,
) -> Path | None:
    """Render a transcript, returning None when rendering is disabled.

    Convenience wrapper for callers (CLI, parent engine) that do not want to own
    a renderer instance.
    """
    if not settings.render.enabled:
        _LOG.info("render disabled in config — skipping video")
        return None
    renderer = TerminalRenderer(settings, output_dir=output_dir)
    return renderer.render(transcript, audio_by_turn=audio_by_turn)


__all__ = [
    "RenderError",
    "Segment",
    "TerminalRenderer",
    "cta_revealed_chars",
    "ease_in_out",
    "ease_in",
    "ease_out",
    "model_accent",
    "pick_cta",
    "render_transcript",
    "send_click_alpha",
    "short_model_name",
]
