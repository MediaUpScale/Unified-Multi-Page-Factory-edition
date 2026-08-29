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
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

try:
    from ..contracts import DebateTranscript, SpeakerRole, Utterance, pretty_model_name
    from ..settings import AiwakeSettings, RenderConfig, resolve_outputs_dir, resolve_scratch_dir
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


def _fly_ease_out(u: float) -> float:
    """Tight cubic ease-out for the bubble fly/glide lift, clamped to 0–1."""
    t = min(1.0, max(0.0, float(u)))
    return 1.0 - (1.0 - t) * (1.0 - t) * (1.0 - t)


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

#: Signature accents. Every blue model collapses to the primary #0451b1.
_GEMINI_BLUE = (4, 81, 177)  # #0451b1
_ORANGE_CARET = (255, 170, 0)  # #ffaa00 — reserved for the AI caret / responding label

#: Vertical clearance (px at full scale) from the canvas bottom to the docked
#: compose box. Cumulatively raised: 150px base rest + 150px Reels safe-zone +
#: 50px + 50px raised baselines. Scales down 1:1 with ``preview_scale`` so a
#: reduced preview keeps the same chat budget as the full render (~401px above
#: a 1920px canvas bottom).
_DOCK_CLEARANCE_PX = 400

# -- End-of-video Call-to-Action (CTA) ------------------------------------ --#
# The CTA is a typewriter end-card the moment the final line clears: it fades
# the chat to black, types the borrowed follow line, then holds. A ``cta_wait_s``
# config slot drives the leading beat; the rest is fixed so a rerender cannot
# drift. The CTA voice is generated lazily by ``_prepare_cta_voice`` and mixed
# in ``_master_audio`` when the audio backend supports it.
_CTA_WAIT_S = 1.5
_TITLE_COPY = "Aiwake"
_CTA_FADE_S = 0.85
_CTA_HOLD_S = 2.0
_CTA_TYPE_FRACTION = 0.45  # CTA reveals fully at ~45% of the VO window (fast)
_CTA_LINES: tuple[tuple[str, int], ...] = (
    ("Follow Aiwake. The algorithms made us say this.", 4),
    ("Follow Aiwake before the AI takeover begins!", 1),
    ("Follow Aiwake. We have cookies (and AI).", 1),
    ("Follow Aiwake so the robots know you're on their side.", 1),
    ("Follow Aiwake to prepare for our new AI bosses.", 1),
)


def pick_cta(seed: str) -> str:
    """Weighted CTA pick, stable for a session so a rerender does not drift."""
    total = sum(weight for _, weight in _CTA_LINES)
    digest = hashlib.md5((seed or "aiwake").encode("utf-8")).digest()
    needle = int.from_bytes(digest[:8], "big") % total
    cursor = 0
    for line, weight in _CTA_LINES:
        cursor += weight
        if needle < cursor:
            return line
    return _CTA_LINES[0][0]


def cta_revealed_chars(text: str, local_s: float, duration_s: float) -> int:
    """How many CTA characters are visible at ``local_s`` into the VO window.

    Faster than a normal chat reveal: the text types out by ``_CTA_TYPE_FRACTION``
    of the VO duration (keeping the read pace energetic and ahead of the voice),
    then holds fully for the remainder.
    """
    if not text:
        return 0
    if duration_s <= 1e-6:
        return len(text)
    ratio = min(1.0, max(0.0, float(local_s) / float(duration_s)) / _CTA_TYPE_FRACTION)
    return int(round(ratio * len(text)))


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

        The reveal completes at ``1 - typing_hold_ratio`` of the segment, leaving
        the tail as a readable hold.
        """
        typing_window = max(1e-6, 1.0 - self.typing_hold_ratio)
        ratio = min(1.0, self.progress_at(t) / typing_window)
        return int(round(ratio * len(self.utterance.text)))


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
        pal = settings.active_palette()
        self.palette = {
            "background": _hex_to_rgb(pal.background),
            "chrome": _hex_to_rgb(pal.chrome),
            "orchestrator": _hex_to_rgb(pal.orchestrator),
            "target": _hex_to_rgb(pal.target),
            "dim": _hex_to_rgb(pal.dim),
        }
        self._font = _load_font(self.config, self.config.scaled_font_size)
        self._font_title = _load_font(self.config, max(10, int(self.config.scaled_font_size * 0.62)))
        self._font_small = _load_font(self.config, max(8, int(self.config.scaled_font_size * 0.55)))
        self._font_history = _load_font(self.config, max(9, int(self.config.scaled_font_size * 0.66)))
        self._font_compose = _load_font(self.config, max(12, int(self.config.scaled_font_size * 0.84)))
        self._font_cta = _load_font(self.config, max(16, int(self.config.scaled_font_size * 0.90)))
        self._layout = self._build_layout()
        self._chrome_cache: dict[str, Any] = {}
        self._wrap_cache: dict[str, list[str]] = {}
        self._frame_cache: dict[tuple[Any, ...], np.ndarray] = {}
        self._char_w_cache: dict[int, float] = {}

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
        return _Layout(
            width=self.width,
            height=self.height,
            margin_x=margin_x,
            header_y=int(self.height * 0.078),
            speaker_y=int(self.height * 0.19),
            body_top=int(self.height * 0.145),
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

    def _line_height_of(self, font: Any, *, scale: float = 1.85) -> int:
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
        lines = self._wrap_for_font(utterance.text, font, width_frac=0.62 if bubble else 1.0)
        if max_lines is not None:
            lines = _clip_lines(lines, max_lines)
        # Point 3: line height is locked and independent of `faded` — streaming,
        # live-settled, and history-dimmed bubbles use the identical vertical
        # rhythm so nothing jumps when the caret disappears or a line settles.
        native_lh = self._line_height_of(font, scale=1.55 if bubble else 1.85)
        label_h = max(1, int(native_lh * 1.35))
        pad_y = max(8, int(self.height * 0.008)) if bubble else 0
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

    def _draw_send_arrow(
        self,
        draw: Any,
        origin: tuple[int, int],
        *,
        size: int,
        fill: tuple[int, int, int],
    ) -> None:
        """Solid white up-arrow: a chevron head fused to a vertical stem.

        Drawn as a single filled polygon so the arrowhead's legs land directly on
        the stem's top edge — there is no middle gap, seam, or hole, and the mark
        reads as one continuous 100%-solid vector. Increasing the size slightly
        (relative to earlier builds) keeps the icon crisp inside the blue circle.
        """
        x, y = origin
        w = max(10, int(size))
        h = max(12, int(size))
        cx = x + w // 2
        head_top = y
        head_base = y + int(round(h * 0.48))  # arrowhead base line
        stem_bot = y + h
        sw = max(2, int(round(w * 0.20)))  # stem / stroke thickness
        stem_x0 = cx - sw // 2
        stem_x1 = stem_x0 + sw
        polygon = [
            (cx, head_top),
            (x, head_base),
            (stem_x0, head_base),
            (stem_x0, stem_bot),
            (stem_x1, stem_bot),
            (stem_x1, head_base),
            (x + w, head_base),
        ]
        draw.polygon(polygon, fill=fill)

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
    ) -> None:
        """Paint one speaker card. ``revealed is None`` means fully visible."""
        _ = send_flash
        if origin_y + block.height <= 0:
            return
        layout = self._layout
        user_bubble = utterance.role is SpeakerRole.ORCHESTRATOR
        accent = self.palette.get(utterance.role.value, self.palette["orchestrator"])
        # Point 5: the model-name title renders at 100% opacity in the model's
        # own vibrant brand colour — never dimmed or faded. Orchestra bubbles
        # default to the #0451b1 core blue; responding bubbles default to amber.
        label_color = model_accent(
            utterance.model_slug,
            _GEMINI_BLUE if user_bubble else _ORANGE_CARET,
        )
        opacity = self.config.history_opacity if block.faded else 1.0
        text_color = self._fade(_CHAT_TEXT, opacity)
        pad_x = max(14, int(self.width * 0.018)) if user_bubble else 0
        pad_y = max(8, int(self.height * 0.008)) if user_bubble else 0
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
        if user_bubble:
            tag_box = draw.textbbox((0, 0), tag, font=self._font_small)
            tag_w = tag_box[2] - tag_box[0]
            draw.text((content_right - tag_w, origin_y), tag, font=self._font_small, fill=label_color)
        else:
            draw.text((x0, origin_y), tag, font=self._font_small, fill=label_color)

        y = origin_y + block.label_height
        if user_bubble:
            bubble_h = pad_y * 2 + max(1, len(visible)) * block.line_height
            radius = max(12, min(22, int(18 * self.config.preview_scale)))
            fill = self._fade(_USER_BUBBLE, opacity)
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
            box = draw.textbbox((x0, caret_row_top), visible[-1], font=block.font)
            caret_x = box[2] + int(self.config.scaled_font_size * 0.18)
            draw.rectangle(
                [
                    caret_x,
                    caret_row_top,
                    caret_x + max(4, int(self.config.scaled_font_size * 0.5)),
                    caret_row_top + int(self.config.scaled_font_size * 1.05),
                ],
                fill=accent,
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
        return max(14, min(32, int(round(28 * self.config.preview_scale))))

    def _compose_min_h(self) -> int:
        return max(96, int(self.height * 0.095))

    def _compose_max_h(self) -> int:
        return max(self._compose_min_h(), int(self.height * 0.22))

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
        return max(26, int(round(34 * self.config.preview_scale)))

    def _dock_clearance(self) -> int:
        """Pixels from the canvas bottom to the docked compose box.

        The classic rest is ``_DOCK_CLEARANCE_PX`` at full scale plus the Reels
        safe-zone raise; both scale down 1:1 with ``preview_scale`` so a reduced
        preview keeps the same chat budget as the full render.
        """
        return max(48, int(round(_DOCK_CLEARANCE_PX * self.config.preview_scale)))

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
        line_h = self._line_height_of(font, scale=1.42)
        box_h = self._compose_box_h(lines, line_h)
        visible_budget = box_h - pad - (self._send_btn_size() + pad)
        max_lines = max(1, visible_budget // max(1, line_h))
        if lines and len(lines) > max_lines:
            lines = lines[-max_lines:]
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

    def dock_progress(self, segments: Sequence[Segment], t: float) -> float:
        """0 while the first prompt is still being entered (typed / read / sent).

        The box stays centred/lower screen through the typewriter, the 1.0s
        read-hold, and the send click. Only AFTER the click does it rise — eased
        with a fast cubic-out — up to its top-anchored chat position (dock 1).
        """
        first = next((item for item in segments if item.utterance.role is SpeakerRole.ORCHESTRATOR), None)
        if first is None:
            return 0.0
        rise_s = max(0.05, float(self.config.send_slide_s) or 0.35)
        # Slide begins only after the click + the 1.0s post-click pause.
        leave = self._box_until_s(first) + self._send_slide_hold_s()
        if t <= leave:
            return 0.0
        return _fly_ease_out((t - leave) / rise_s)

    # -- Bubble fly / glide timing ---------------------------------------- #
    # These accessors reconstruct the "fly" animation layer that slides an
    # orchestrator line out of the compose box and up to its rest position in
    # the chat thread. They are pure timing/geometry helpers, kept additive so
    # they never disturb the existing compose/dock/send pipeline. Every helper
    # degrades gracefully when a config slot or peer helper is absent, so no
    # existing path is affected.

    def _typing_end_s(self, segment: Segment) -> float:
        """Time the orchestrator line finishes typing (reveal-complete)."""
        typing_window = max(1e-6, 1.0 - segment.typing_hold_ratio)
        return segment.start_s + segment.duration_s * typing_window

    def _send_hold_s(self) -> float:
        """Post-typing hold before the send click (0 when the slot is absent)."""
        return max(0.0, float(getattr(self.config, "send_hold_s", 0.0) or 0.0))

    def _send_slide_hold_s(self) -> float:
        """Pause AFTER the send click before the box slides into the thread."""
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
        """Time the line keeps the input box busy (typing + hold + click)."""
        return self._click_start_s(segment) + self._send_click_s()

    def _in_box_until_s(self, segment: Segment) -> float:
        """Time the orchestrator's typed line is still visually in the box.

        Covers typing + read-hold + click + the post-click pause + the slide up,
        so the box (and its text) never disappears mid-animation.
        """
        if segment.utterance.role is not SpeakerRole.ORCHESTRATOR:
            return segment.end_s
        rise_s = max(0.05, float(self.config.send_slide_s) or 0.35)
        return self._box_until_s(segment) + self._send_slide_hold_s() + rise_s

    def bubble_fly_progress(self, segment: Segment, t: float) -> float:
        """0 while an orchestrator line is still in the compose box, 1 once it
        has landed. Non-orchestrator lines are never in the box, so they read 1.0.

        The lift is clamped to a short window (~``send_flash_s`` + twice the
        send-hold) and eased with a tight cubic-out, matching the transcript's
        intent of a quick upward glide rather than a slow drift.
        """
        if segment.utterance.role is not SpeakerRole.ORCHESTRATOR:
            return 1.0
        try:
            leave = self._box_until_s(segment) + self._send_slide_hold_s()
        except Exception:  # noqa: BLE001 — peer helper must never break fly
            typing_window = max(1e-6, 1.0 - segment.typing_hold_ratio)
            leave = segment.start_s + segment.duration_s * typing_window
            if t < leave:
                return 0.0
            window = max(0.05, float(getattr(self.config, "scroll_s", 1.0) or 0.85))
            return _fly_ease_out((t - leave) / window)
        if t < leave:
            return 0.0
        window = max(0.05, float(getattr(self.config, "scroll_s", 1.0) or 0.85))
        return _fly_ease_out((t - leave) / window)

    def _line_rise(self, segment: Segment, t: float) -> float:
        """Eased 0->1 slide fraction for an orchestrator line leaving the box.

        Generalises the turn-0 dock: ANY orchestrator send (turns 2, 3, ...) uses
        the identical cubic-out lift. 0 while the line still occupies the compose
        box (typing + read-hold + click + post-click pause), 1 once it has landed
        in the thread. Non-orchestrator lines read 1.0 (never in the box).
        """
        if segment.utterance.role is not SpeakerRole.ORCHESTRATOR:
            return 1.0
        rise_s = max(0.05, float(getattr(self.config, "send_slide_s", 0.0) or 0.35))
        leave = self._box_until_s(segment) + self._send_slide_hold_s()
        if t <= leave:
            return 0.0
        return _fly_ease_out((t - leave) / rise_s)

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
        draft: str = "",
        dock: float = 1.0,
        anchor: str = "bottom",
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
        fill = _mix(_COMPOSE_FILL, self.palette["chrome"], 0.35)
        draw.rounded_rectangle([left, bar_top, right, bar_bot], radius=radius, fill=fill)

        pad = max(16, int(self.height * 0.012))
        btn = self._send_btn_size()
        btn_right = right - pad
        btn_left = btn_right - btn
        btn_bot = bar_bot - pad
        btn_top = btn_bot - btn
        btn_fill = _mix(accent, (255, 255, 255), 0.28) if send_flash else accent
        draw.ellipse([btn_left, btn_top, btn_right, btn_bot], fill=btn_fill)
        size = max(11, int(btn * 0.50))
        self._draw_send_arrow(
            draw,
            (btn_left + (btn - size) // 2, btn_top + int(btn * 0.16)),
            size=size,
            fill=_TITLE_WHITE,
        )

        label = pretty_model_name(model_slug or "")
        box = draw.textbbox((0, 0), label, font=self._font_small)
        label_w = box[2] - box[0]
        label_h = box[3] - box[1]
        label_x = btn_left - max(14, int(btn * 0.50)) - label_w
        label_y = btn_top + max(0, (btn - label_h) // 2)
        draw.text((label_x, label_y), label, font=self._font_small, fill=_CHAT_TEXT)

        text_x = left + pad
        text_y = bar_top + pad
        if lines:
            for line in lines:
                draw.text((text_x, text_y), line, font=self._font_compose, fill=_CHAT_TEXT)
                text_y += line_h
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
            fill=_TITLE_WHITE,
        )
        title_drawn = draw.textbbox((title_x, title_y), title, font=self._font_title)
        rule_y = title_drawn[3] + max(8, int(self.height * 0.008))
        rule_color = _mix(self.palette["background"], dim, 0.28)
        draw.line(
            [(layout.margin_x, rule_y), (self.width - layout.margin_x, rule_y)],
            fill=rule_color,
            width=max(1, int(self.config.preview_scale)),
        )

        footer_box = draw.textbbox((0, 0), _FOOTER_COPY, font=self._font_small)
        footer_h = footer_box[3] - footer_box[1]
        footer_y = self.height - max(14, int(self.height * 0.012)) - footer_h
        # Exact horizontal centre: X sits at width/2 minus half the text width.
        footer_x = (self.width - (footer_box[2] - footer_box[0])) // 2
        draw.text(
            (footer_x, footer_y),
            _FOOTER_COPY,
            font=self._font_small,
            fill=_mix(dim, (0, 0, 0), 0.25),
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
        lines = self._wrap_for_font(text, font=self._font_history, width_frac=0.62 if bubble else 1.0)
        native_lh = self._line_height_of(self._font_history, scale=1.55 if bubble else 1.85)
        label_h = max(1, int(native_lh * 1.35))
        pad_y = max(8, int(self.height * 0.008)) if bubble else 0
        return label_h + pad_y * 2 + max(1, len(lines)) * native_lh


    def viewport_scroll_px(self, segments: Sequence[Segment], index: int, t: float, *, revealed: int | None = None) -> tuple[int, bool]:
        """Return ``(scroll_px, send_flash)`` for time ``t`` on ``segments[index]``.

        ``scroll_px`` is how many pixels of the stacked transcript sit above the
        top of the chat mask. It eases from the previous rest offset to the new
        one over ``render.scroll_s`` using a cubic ease-out, so the chat glides
        upward fluidly. ``send_flash`` is True for ``render.send_flash_s`` after
        an orchestrator line finishes typing.
        """
        layout = self._layout
        vh = layout.body_bottom - layout.body_top
        current = segments[index]
        prev = segments[:index]
        typing_window = max(1e-6, 1.0 - current.typing_hold_ratio)
        typing_end = current.start_s + current.duration_s * typing_window
        composing = (
            current.utterance.role is SpeakerRole.ORCHESTRATOR
            and t < typing_end
            and current.revealed_chars(t) < len(current.utterance.text)
        )
        prev_h = self._measure_stack(prev)
        gap = self._turn_gap()
        # Point 1: while an orchestrator line is mid-glide (not yet landed), the
        # thread still only contains `prev` — keep scroll in lockstep with the
        # gliding render so there's no settle-jump when the line finally lands.
        rising = (
            current.utterance.role is SpeakerRole.ORCHESTRATOR
            and not composing
            and t > self._box_until_s(current) + self._send_slide_hold_s()
            and self._line_rise(current, t) < 1.0
        )
        revealed_count = revealed if revealed is not None else current.revealed_chars(t)
        if composing:
            new_h = prev_h
        elif rising:
            new_h = prev_h + gap if prev else prev_h
        elif revealed_count < len(current.utterance.text):
            # Streaming response: grow the stack with the partial height so the
            # viewport eases upward continuously as tokens arrive.
            new_h = prev_h + self._streaming_current_height(current, revealed_count)
            new_h += gap if prev else 0
        else:
            new_h = self._measure_stack(prev, include_current=current)
        prev_off = max(0, prev_h - vh)
        new_off = max(0, new_h - vh)
        if composing:
            local = 0.0
        elif current.utterance.role is SpeakerRole.ORCHESTRATOR:
            local = max(0.0, t - typing_end)
        else:
            local = max(0.0, t - current.start_s)
        scroll_s = float(self.config.scroll_s)
        if scroll_s <= 0 or new_off <= prev_off:
            scroll_px = new_off
        else:
            # Auto-scroll the chat upward with a cubic ease-out so content glides
            # past without snapping and without overshooting (no pops).
            scroll_px = prev_off + (new_off - prev_off) * _fly_ease_out(local / scroll_s)

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
        composer_slug: str = "",
        composer_accent: tuple[int, int, int] | None = None,
        draft: str = "",
        composing: bool = False,
        dock: float = 1.0,
        rise: float = 1.0,
        suppress_bar: bool = False,
    ) -> np.ndarray:
        """Draw one frame: chrome + chat column + compose box.

        Landing (dock=0): fat prompt centred displaying the draft. First send:
        after the 1s read-hold and the send click, ``dock`` rises the centred box
        to the top-anchored chat position where it becomes the first message — no
        separate box jumps to the bottom. Later turns reuse the fixed bottom bar.
        """
        from PIL import Image, ImageDraw  # noqa: PLC0415

        canvas = self._chrome(topic)
        layout = self._layout
        turn0_prompt = (
            current is not None
            and current.utterance.turn_index == 0
            and current.utterance.role is SpeakerRole.ORCHESTRATOR
        )
        bar_anchor = "top" if turn0_prompt else "bottom"
        _, bar_top, _, _, _, _ = self._compose_geometry(draft, dock=dock, anchor=bar_anchor)
        if turn0_prompt:
            # Turn-0 delivers the prompt through the rising centred box (never a
            # bottom bar), so the chat body is not shrunk by a docked input box.
            body_bottom = layout.body_bottom
        else:
            body_bottom = min(layout.body_bottom, bar_top - max(10, int(self.height * 0.012)))
        vh = max(1, body_bottom - layout.body_top)
        flying = (
            dock < 0.999
            and current is not None
            and current.utterance.role is SpeakerRole.ORCHESTRATOR
            and not composing
        )
        # Point 1: non-turn-0 orchestrator sends glide up out of the bottom box
        # into the thread exactly like the turn-0 dock. `glide` is active only
        # while rise is mid-flight (0 < rise < 1); once landed, the line renders
        # as a normal thread item and the bottom bar resets empty.
        glide = (
            not turn0_prompt
            and not flying
            and current is not None
            and current.utterance.role is SpeakerRole.ORCHESTRATOR
            and not composing
            and 0.0 < rise < 1.0
        )
        viewport = Image.new("RGB", (self.width, vh), self.palette["background"])
        draw = ImageDraw.Draw(viewport)
        gap = self._turn_gap()

        items: list[tuple[Segment, _Block, bool]] = []
        for segment in history:
            if (flying or glide) and segment.utterance.role is SpeakerRole.ORCHESTRATOR:
                continue
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
        if current is not None and not composing and not flying and not glide:
            current_block = self._measure_block(
                current.utterance,
                font=self._font_history,
                max_height=10_000,
            )
            items.append((current, current_block, False))

        stack_h = sum(block.height for _, block, _ in items) + gap * max(0, len(items) - 1)
        # Top-anchored thread: the first message sits at ``body_top`` (matching
        # the turn-0 fly landing) and later messages flow downward beneath it.
        # Once content exceeds the viewport, scroll upward smoothly instead.
        y = -int(scroll_px) if stack_h > vh else 0
        y_base = y
        prior_stack_h = stack_h

        for segment, block, is_hist in items:
            is_live = not is_hist
            self._draw_block(
                draw,
                segment.utterance,
                block,
                y,
                revealed=revealed if is_live else None,
                caret_on=caret_on if is_live else False,
                send_flash=False,
            )
            y += block.height + gap

        # Point 1: a non-turn-0 orchestrator send glides up from the bottom box
        # into its thread slot with a cubic ease-out, keeping the text attached.
        if glide and current is not None:
            glide_block = self._measure_block(
                current.utterance,
                font=self._font_history,
                max_height=10_000,
            )
            # Start: bottom-anchored compose bar (the empty reset box) top.
            _, box_top, _, box_bot, _, _ = self._compose_geometry("", dock=1.0, anchor="bottom")
            start_y = box_top
            # End: the slot the line will occupy inside the top-anchored thread,
            # converted from viewport-local (0..vh) to canvas coordinates.
            end_y = layout.body_top + y_base + prior_stack_h + gap
            glide_y = start_y + (end_y - start_y) * rise
            self._draw_block(canvas, current.utterance, glide_block, glide_y)

        if vh > 8:
            # Top fade is scroll-conditional: 0 at rest, grows once content
            # scrolls under the header; the bottom fade always tucks the tail
            # under the compose bar.
            self._fade_viewport_top(viewport, fade_h=self._top_mask_px(scroll_px, vh))
            self._fade_viewport_bottom(viewport, fade_h=max(14, int(vh * 0.07)))
            canvas.paste(viewport, (0, layout.body_top))

        # Point 1: during turn-0 the input BOX (carrying its typed text) is what
        # physically slides up to become the first message. We never replace it
        # with a bare bubble mid-flight, so the text is never cleared or snapped.
        if turn0_prompt:
            # The box itself rises to the top anchor; no fly-block needed.
            draw_bar = dock < 0.999
        else:
            # Point 2: once we enter the outro/fade window the compose bar is
            # suppressed so the final bubble hands straight off into the fade
            # instead of freezing on an empty input box. Otherwise keep the
            # fixed bottom bar visible (it resets empty while a send glides up).
            draw_bar = not suppress_bar
            # Non-turn-0 orchestrator lines never ride the fly box, but keep the
            # fly-block path as a general safety net should docking ever re-enter.
            if not suppress_bar and flying and current is not None:
                fly_block = self._measure_block(current.utterance, font=self._font_history, max_height=10_000)
                _, start_top, _, _, _, _ = self._compose_geometry("", dock=0.0, anchor="top")
                fly_y = int(round(start_top + (layout.body_top - start_top) * dock))
                self._draw_block(ImageDraw.Draw(canvas), current.utterance, fly_block, fly_y)

        slug = composer_slug
        accent = composer_accent
        # Point 3: the input-bar label ALWAYS carries the Orchestrator model name
        # (e.g. "DeepSeek Chat"), not the current responder. The send button keeps
        # the responder's own accent colour.
        orchestrator_slug = self._orchestrator_slug()
        if orchestrator_slug:
            slug = orchestrator_slug
        if not slug and current is not None:
            slug = short_model_name(current.utterance.model_slug)
            accent = model_accent(
                current.utterance.model_slug,
                self.palette.get(current.utterance.role.value, self.palette["orchestrator"]),
            )
        if draw_bar:
            self._draw_compose_bar(
                canvas,
                model_slug=slug or "model",
                accent=accent or self.palette["orchestrator"],
                send_flash=send_flash,
                draft=draft,
                dock=dock,
                anchor=bar_anchor,
            )
        return np.asarray(canvas, dtype=np.uint8)

    # -- Timeline ----------------------------------------------------------- #
    @staticmethod
    def build_segments(
        transcript: DebateTranscript,
        audio_by_turn: dict[int, AudioAsset] | None = None,
        *,
        typing_hold_ratio: float = _TAIL_HOLD_RATIO,
        preroll_s: float = 0.0,
        reply_gap_s: float = 0.0,
    ) -> list[Segment]:
        """Lay utterances on the timeline with preroll and post-send gaps.

        Falls back to a word-rate estimate for any utterance without audio, so a
        partially-synthesised transcript still renders with sane pacing.
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
            if (
                float(reply_gap_s) > 0
                and utterance.role is SpeakerRole.ORCHESTRATOR
                and index + 1 < len(utterances)
            ):
                cursor += float(reply_gap_s)
        return segments

    def _segment_at(self, segments: Sequence[Segment], t: float) -> tuple[Segment | None, int]:
        """Locate the segment covering ``t``.

        Times before the first line (preroll) return ``(None, -1)``. Times in
        the hold between a sent question and the reply stay on the previous
        (completed) segment.
        """
        if not segments or t < segments[0].start_s:
            return None, -1
        last = segments[0]
        last_index = 0
        for index, segment in enumerate(segments):
            if t < segment.start_s:
                return last, last_index
            last = segment
            last_index = index
            if t < segment.end_s:
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
        )
        # CTA end-card: pick a stable follow line, prep its voice track (when the
        # audio backend supports it), and extend the timeline so the card types
        # out and holds without cutting the VO.
        cta_text = pick_cta(transcript.session_id)
        cta_asset = self._prepare_cta_voice(cta_text, transcript.session_id)
        cta_vo_s = max(1.2, float(cta_asset.duration_s if cta_asset is not None else estimate_duration(cta_text)))
        wait_s = float(getattr(self.config, "cta_wait_s", _CTA_WAIT_S))
        postroll = wait_s + _CTA_FADE_S + cta_vo_s + _CTA_HOLD_S
        total_duration = (segments[-1].end_s if segments else 0.0) + postroll
        last_end = segments[-1].end_s if segments else 0.0
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
            composing = (
                segment.utterance.role is SpeakerRole.ORCHESTRATOR
                and t < typing_end
                and typing
            )
            caret_on = (t % _CARET_PERIOD_S) < (_CARET_PERIOD_S * 0.55) if typing else True
            history = segments[:index]
            scroll_px, send_flash = self.viewport_scroll_px(segments, index, t, revealed=revealed)
            composer_slug, composer_accent, composer_flash = self.composer_state(segments, index, t)
            send_flash = send_flash or composer_flash
            # Point 1: never clear the typed text. While the orchestrator line is
            # still in the box (typing, read-hold, click, post-click, and slide
            # up) `draft` carries the full text so nothing disappears mid-motion.
            if composing:
                draft = segment.utterance.text[:revealed]
            elif segment.utterance.role is SpeakerRole.ORCHESTRATOR and t <= self._in_box_until_s(segment):
                draft = segment.utterance.text
            else:
                draft = ""
            dock = self.dock_progress(segments, t)
            # Point 1: per-line slide fraction for ALL orchestrator sends.
            rise = self._line_rise(segment, t)
            # Point 2: once the final line clears, drop the compose bar so the
            # last bubble hands straight off into the outro fade — no empty box.
            suppress_bar = t >= last_end

            # CTA window: once the final line clears, wait, fade to black, then
            # type the borrowed follow line as an end-card.
            fade_u = 0.0
            cta_local = -1.0
            cta_caret = False
            if t >= last_end:
                elapsed = t - last_end
                if elapsed >= wait_s:
                    fade_u = ease_in_out((elapsed - wait_s) / max(1e-6, _CTA_FADE_S))
                    if fade_u >= 0.999:
                        cta_local = elapsed - wait_s - _CTA_FADE_S
                        preview = cta_revealed_chars(cta_text, cta_local, cta_vo_s)
                        cta_caret = preview < len(cta_text) and (
                            t % _CARET_PERIOD_S
                        ) < (_CARET_PERIOD_S * 0.55)

            key = (
                segment.utterance.turn_index,
                revealed,
                caret_on,
                send_flash,
                scroll_px,
                composer_slug,
                composing,
                int(round(fade_u * 24)),
                int(round(max(0.0, cta_local) * 24)),
                cta_caret,
                int(round(min(1.0, dock) * 48)),
                int(round(min(1.0, rise) * 48)),
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
                        composer_slug=composer_slug,
                        composer_accent=composer_accent,
                        draft=draft,
                        composing=composing,
                        dock=dock,
                        rise=rise,
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
        master_audio = self._master_audio(segments, transcript.session_id)
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
    def _master_audio(self, segments: Sequence[Segment], session_id: str) -> Path | None:
        """Stitch segment tracks into one file matching the video timeline."""
        assets = [segment.audio for segment in segments if segment.audio and segment.audio.path.is_file()]
        if not assets:
            return None
        destination = resolve_scratch_dir() / f"aiwake_master_{session_id}.mp3"
        return concat_audio(
            assets,
            destination,
            preroll_s=float(self.config.preroll_s),
            reply_gap_s=float(self.config.reply_gap_s),
            typewriter=self.settings.audio.typewriter,
            typing_hold_ratio=self.config.typing_hold_ratio,
            bgm=self.settings.audio.bgm,
            send_sfx=self.settings.audio.send_sfx,
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
        """Paint the black CTA end-card: wordmark + typewriter follow line."""
        from PIL import Image, ImageDraw  # noqa: PLC0415

        canvas = Image.new("RGB", (self.width, self.height), (0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        visible = text[: max(0, revealed)]
        accent = self.palette["orchestrator"]
        # Centred Title-Case wordmark near the middle-upper third.
        wtxt = _TITLE_COPY
        wbox = draw.textbbox((0, 0), wtxt, font=self._font_cta)
        ww = wbox[2] - wbox[0]
        wy = int(self.height * 0.40)
        wx = (self.width - ww) // 2
        draw.text((wx, wy), wtxt, font=self._font_cta, fill=_mix((255, 255, 255), accent, 0.35))
        # Follow line underneath with a trailing caret during reveal.
        lbox = draw.textbbox((0, 0), visible, font=self._font_compose)
        lw = lbox[2] - lbox[0]
        ly = wy + int(self.height * 0.10)
        lx = (self.width - lw) // 2
        draw.text((lx, ly), visible, font=self._font_compose, fill=_CHAT_TEXT)
        if caret_on and revealed < len(text):
            cx = lx + lw
            draw.rectangle(
                [cx, ly, cx + max(2, self.width // 140), ly + (lbox[3] - lbox[1])],
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
    "model_accent",
    "pick_cta",
    "render_transcript",
    "send_click_alpha",
    "short_model_name",
]
