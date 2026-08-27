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

import logging
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

try:
    from ..contracts import DebateTranscript, SpeakerRole, Utterance
    from ..settings import AiwakeSettings, RenderConfig, resolve_outputs_dir, resolve_scratch_dir
    from .audio import AudioAsset, concat_audio, estimate_duration
    from .vfx import FrameContext, apply_chain, chain_needs_audio, resolve_chain
except ImportError:  # pragma: no cover — standalone extraction
    from contracts import DebateTranscript, SpeakerRole, Utterance  # type: ignore[no-redef]
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


def short_model_name(slug: str) -> str:
    """Vendor-stripped model label for the compose bar (``openai/gpt-4o`` → ``gpt-4o``)."""
    token = (slug or "").strip()
    if token.lower() in {"", "n/a", "na"}:
        return "model"
    if "/" in token:
        token = token.rsplit("/", 1)[-1]
    return token or "model"


_MODEL_ACCENTS: tuple[tuple[tuple[str, ...], tuple[int, int, int]], ...] = (
    (("gemini",), (138, 180, 248)),
    (("llama", "meta-llama"), (77, 107, 254)),
    (("gpt-4", "gpt-5", "gpt-3", "openai", "chatgpt"), (16, 163, 127)),
    (("claude", "anthropic"), (217, 119, 87)),
    (("deepseek",), (90, 160, 255)),
    (("mistral",), (250, 174, 72)),
)
_COMPOSE_FILL = (30, 31, 32)
_CHAT_TEXT = (232, 234, 237)
_USER_BUBBLE = (47, 48, 51)
_FOOTER_COPY = "// autonomous exchange — unedited"


def model_accent(slug: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    """Send-button colour for the active model family."""
    needle = (slug or "").lower()
    for keys, color in _MODEL_ACCENTS:
        if any(key in needle for key in keys):
            return color
    return fallback


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
        self._font_small = _load_font(self.config, max(8, int(self.config.scaled_font_size * 0.55)))
        self._font_history = _load_font(self.config, max(8, int(self.config.scaled_font_size * 0.62)))
        self._font_compose = _load_font(self.config, max(12, int(self.config.scaled_font_size * 0.84)))
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
        native_lh = self._line_height_of(font, scale=1.55 if bubble else (1.70 if faded else 1.85))
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
        """Upward send triangle (classic chat send control)."""
        x, y = origin
        w = max(10, int(size))
        h = max(10, int(size))
        draw.polygon(
            [(x + w // 2, y), (x, y + h), (x + w, y + h)],
            fill=fill,
        )

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
        layout = self._layout
        accent = self.palette.get(utterance.role.value, self.palette["orchestrator"])
        opacity = self.config.history_opacity if block.faded else 1.0
        label_color = self._fade(accent, min(1.0, opacity + 0.25))
        text_color = self._fade(_CHAT_TEXT, opacity)
        user_bubble = utterance.role is SpeakerRole.ORCHESTRATOR
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

        tag = utterance.speaker_name
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

        After the live line finishes typing, the label rotates to the upcoming
        speaker so the next respondent is already named beside the send arrow.
        """
        current = segments[index]
        typing_window = max(1e-6, 1.0 - current.typing_hold_ratio)
        typing_end = current.start_s + current.duration_s * typing_window
        sent = t >= typing_end and current.revealed_chars(t) >= len(current.utterance.text)
        send_flash = (
            current.utterance.role is SpeakerRole.ORCHESTRATOR
            and sent
            and (t - typing_end) < float(self.config.send_flash_s)
        )
        if sent and index + 1 < len(segments):
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

    def _compose_geometry(self, draft: str, *, dock: float = 1.0) -> tuple[int, int, int, int, list[str], int]:
        """Return ``(left, top, right, bottom, lines, line_h)``.

        ``dock`` 0 = centred landing prompt; 1 = docked above the footer.
        """
        mx = self._compose_side_margin()
        left, right = mx, self.width - mx
        lines, line_h = self._wrap_compose_draft(draft, right - left)
        box_h = self._compose_box_h(lines, line_h)
        docked_bot = self.height - self._footer_reserve() - self._bottom_margin()
        docked_top = docked_bot - box_h
        center_top = (self.height - box_h) // 2
        center_bot = center_top + box_h
        u = min(1.0, max(0.0, float(dock)))
        bar_top = int(round(center_top + (docked_top - center_top) * u))
        bar_bot = int(round(center_bot + (docked_bot - center_bot) * u))
        return left, bar_top, right, bar_bot, lines, line_h

    def dock_progress(self, segments: Sequence[Segment], t: float) -> float:
        """0 while the first prompt is centred; 1 after it has slid to the footer."""
        first = next((item for item in segments if item.utterance.role is SpeakerRole.ORCHESTRATOR), None)
        if first is None:
            return 0.0
        typing_window = max(1e-6, 1.0 - first.typing_hold_ratio)
        typing_end = first.start_s + first.duration_s * typing_window
        if t < typing_end:
            return 0.0
        dock_s = max(0.05, float(self.config.scroll_s) or 0.85)
        return ease_in_out((t - typing_end) / dock_s)

    def _draw_compose_bar(
        self,
        canvas: Any,
        *,
        model_slug: str,
        accent: tuple[int, int, int],
        send_flash: bool,
        draft: str = "",
        dock: float = 1.0,
    ) -> int:
        """Fat Gemini prompt: large type on top, model-tinted send at the bottom-right.

        Returns the box top so the chat viewport can sit above it.
        """
        from PIL import ImageDraw  # noqa: PLC0415

        draw = ImageDraw.Draw(canvas)
        left, bar_top, right, bar_bot, lines, line_h = self._compose_geometry(draft, dock=dock)
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
        arrow = max(10, int(btn * 0.46))
        self._draw_send_arrow(
            draw,
            (btn_left + (btn - arrow) // 2, btn_top + int(btn * 0.20)),
            size=arrow,
            fill=(24, 24, 27),
        )

        label = model_slug or "model"
        box = draw.textbbox((0, 0), label, font=self._font_small)
        label_w = box[2] - box[0]
        label_h = box[3] - box[1]
        label_x = btn_left - max(10, int(btn * 0.35)) - label_w
        label_y = btn_top + max(0, (btn - label_h) // 2)
        draw.text((label_x, label_y), label, font=self._font_small, fill=accent)

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
        title_y = max(16, int(self.height * 0.032))
        draw.text(
            (layout.margin_x, title_y),
            "AIWAKE",
            font=self._font_small,
            fill=_mix(dim, self.palette["orchestrator"], 0.45),
        )
        title_box = draw.textbbox((layout.margin_x, title_y), "AIWAKE", font=self._font_small)
        rule_y = title_box[3] + max(8, int(self.height * 0.008))
        rule_color = _mix(self.palette["background"], dim, 0.28)
        draw.line(
            [(layout.margin_x, rule_y), (self.width - layout.margin_x, rule_y)],
            fill=rule_color,
            width=max(1, int(self.config.preview_scale)),
        )

        footer_box = draw.textbbox((0, 0), _FOOTER_COPY, font=self._font_small)
        footer_h = footer_box[3] - footer_box[1]
        footer_y = self.height - max(14, int(self.height * 0.012)) - footer_h
        draw.text(
            (layout.margin_x, footer_y),
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
                font=self._font,
                max_height=10_000,
            )
            total += block.height + (gap if count else 0)
        return total

    def viewport_scroll_px(self, segments: Sequence[Segment], index: int, t: float) -> tuple[int, bool]:
        """Return ``(scroll_px, send_flash)`` for time ``t`` on ``segments[index]``.

        ``scroll_px`` is how many pixels of the stacked transcript sit above the
        top of the chat mask. It eases from the previous rest offset to the new
        one over ``render.scroll_s``. ``send_flash`` is True for
        ``render.send_flash_s`` after an orchestrator line finishes typing.
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
        new_h = prev_h if composing else self._measure_stack(prev, include_current=current)
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
            scroll_px = prev_off + (new_off - prev_off) * ease_in_out(local / scroll_s)

        send_flash = (
            current.utterance.role is SpeakerRole.ORCHESTRATOR
            and t >= typing_end
            and (t - typing_end) < float(self.config.send_flash_s)
            and current.revealed_chars(t) >= len(current.utterance.text)
        )
        return int(round(scroll_px)), send_flash

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
    ) -> np.ndarray:
        """Draw one frame: chrome + chat column + compose box.

        Landing (dock=0): fat prompt centred. First send: box slides down empty
        while the question eases into a right-aligned user bubble at the top.
        """
        from PIL import Image, ImageDraw  # noqa: PLC0415

        canvas = self._chrome(topic)
        layout = self._layout
        _, bar_top, _, _, _, _ = self._compose_geometry(draft, dock=dock)
        body_bottom = min(layout.body_bottom, bar_top - max(10, int(self.height * 0.012)))
        vh = max(1, body_bottom - layout.body_top)
        flying = (
            dock < 0.999
            and current is not None
            and current.utterance.role is SpeakerRole.ORCHESTRATOR
            and not composing
        )
        viewport = Image.new("RGB", (self.width, vh), self.palette["background"])
        draw = ImageDraw.Draw(viewport)
        gap = self._turn_gap()

        items: list[tuple[Segment, _Block, bool]] = []
        for segment in history:
            if flying and segment.utterance.role is SpeakerRole.ORCHESTRATOR:
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
        if current is not None and not composing and not flying:
            current_block = self._measure_block(
                current.utterance,
                font=self._font,
                max_height=10_000,
            )
            items.append((current, current_block, False))

        stack_h = sum(block.height for _, block, _ in items) + gap * max(0, len(items) - 1)
        y = (vh - stack_h) if stack_h <= vh else -int(scroll_px)

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

        if vh > 8:
            self._fade_viewport_top(viewport, fade_h=max(20, int(vh * 0.12)))
            self._fade_viewport_bottom(viewport, fade_h=max(14, int(vh * 0.07)))
            canvas.paste(viewport, (0, layout.body_top))

        if flying and current is not None:
            fly_block = self._measure_block(current.utterance, font=self._font, max_height=10_000)
            _, start_top, _, _, _, _ = self._compose_geometry("", dock=0.0)
            rest_y = layout.body_top
            fly_y = int(round(start_top + (rest_y - start_top) * dock))
            self._draw_block(ImageDraw.Draw(canvas), current.utterance, fly_block, fly_y)

        slug = composer_slug
        accent = composer_accent
        if not slug and current is not None:
            slug = short_model_name(current.utterance.model_slug)
            accent = model_accent(
                current.utterance.model_slug,
                self.palette.get(current.utterance.role.value, self.palette["orchestrator"]),
            )
        self._draw_compose_bar(
            canvas,
            model_slug=slug or "model",
            accent=accent or self.palette["orchestrator"],
            send_flash=send_flash,
            draft=draft,
            dock=dock,
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
        total_duration = (segments[-1].end_s if segments else 0.0) + 0.05
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
            scroll_px, send_flash = self.viewport_scroll_px(segments, index, t)
            composer_slug, composer_accent, composer_flash = self.composer_state(segments, index, t)
            send_flash = send_flash or composer_flash
            draft = segment.utterance.text[:revealed] if composing else ""
            dock = self.dock_progress(segments, t)

            key = (
                segment.utterance.turn_index,
                revealed,
                caret_on,
                send_flash,
                scroll_px,
                composer_slug,
                composing,
                int(round(min(1.0, dock) * 48)),
                tuple(item.utterance.turn_index for item in history),
            )
            frame = self._frame_cache.get(key)
            if frame is None:
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
                )
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
    "ease_in_out",
    "model_accent",
    "render_transcript",
    "short_model_name",
]
