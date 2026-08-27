# -*- coding: utf-8 -*-
"""Cinematic terminal-UI renderer with audio-synced typing.

Design constraints that shaped this file:

**Typing is synced to audio, not to a fixed CPS.** Each utterance's segment
lasts exactly as long as its voice track, and the character reveal is a function
of normalised progress through that segment. Text therefore lands on the last
syllable regardless of how fast the voice is. A short hold at the end
(:data:`_TAIL_HOLD_RATIO`) keeps the finished line readable instead of cutting on
the final character.

**The screen is a masked rolling transcript, not a wipe.** Finished turns stay
in a clipped chat viewport. When a new line would overflow the mask, the whole
block eases upward (``scroll_s``, default 0.5 s) so old text clips out at the
top while the live line types at the bottom. Orchestrator lines carry a send
arrow that flashes the accent colour for ``send_flash_s`` (0.2 s) the instant
typing finishes and the question is dispatched.

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
    """Cubic smoothstep, clamped to 0–1. Used for the masked Y-scroll."""
    t = min(1.0, max(0.0, float(u)))
    return t * t * (3.0 - 2.0 * t)


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

        margin_x = int(self.width * 0.085)
        usable = self.width - margin_x * 2
        return _Layout(
            width=self.width,
            height=self.height,
            margin_x=margin_x,
            header_y=int(self.height * 0.085),
            speaker_y=int(self.height * 0.19),
            body_top=int(self.height * 0.27),
            body_bottom=int(self.height * 0.86),
            body_width_chars=max(12, int(usable / char_w)),
            line_height=line_h,
            footer_y=int(self.height * 0.92),
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

    def _wrap_for_font(self, text: str, font: Any) -> list[str]:
        usable = self.width - self._layout.margin_x * 2
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
        lines = self._wrap_for_font(utterance.text, font)
        if max_lines is not None:
            lines = _clip_lines(lines, max_lines)
        native_lh = self._line_height_of(font, scale=1.70 if faded else 1.85)
        label_h = max(1, int(native_lh * 1.55))
        available_text = max(1, max_height - label_h)
        line_height = min(native_lh, max(1, available_text // max(1, len(lines))))
        # Drop trailing wraps if even the tightened leading still overflows.
        while len(lines) > 1 and label_h + len(lines) * line_height > max_height:
            lines = _clip_lines(lines, len(lines) - 1)
            line_height = min(native_lh, max(1, available_text // max(1, len(lines))))
        height = label_h + len(lines) * line_height
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

    def _draw_send_arrow(
        self,
        draw: Any,
        origin: tuple[int, int],
        *,
        size: int,
        fill: tuple[int, int, int],
    ) -> None:
        """Right-pointing send chevron (filled polygon, not a font glyph)."""
        x, y = origin
        w = max(8, int(size))
        h = max(8, int(size * 0.85))
        draw.polygon([(x, y), (x, y + h), (x + w, y + h // 2)], fill=fill)

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
        layout = self._layout
        x0 = layout.margin_x if origin_x is None else origin_x
        accent = self.palette.get(utterance.role.value, self.palette["orchestrator"])
        opacity = self.config.history_opacity if block.faded else 1.0
        label_color = self._fade(accent, opacity)
        slug_color = self._fade(self.palette["dim"], opacity)
        if block.faded:
            text_color = self._fade(_mix(accent, (255, 255, 255), 0.45), opacity)
        else:
            text_color = _mix(accent, (255, 255, 255), 0.72)

        if utterance.role is SpeakerRole.ORCHESTRATOR:
            label = utterance.speaker_name
            draw.text((x0, origin_y), label, font=self._font_small, fill=label_color)
            box = draw.textbbox((x0, origin_y), label, font=self._font_small)
            arrow_size = max(10, int(self.config.scaled_font_size * 0.42))
            if send_flash and not block.faded:
                arrow_fill = _mix(accent, (255, 255, 255), 0.45)
            else:
                arrow_fill = self._fade(accent, 0.55 if block.faded else 0.85)
            self._draw_send_arrow(
                draw,
                (box[2] + max(6, int(arrow_size * 0.35)), origin_y + 2),
                size=arrow_size,
                fill=arrow_fill,
            )
        else:
            draw.text(
                (x0, origin_y),
                f"{utterance.speaker_name} >",
                font=self._font_small,
                fill=label_color,
            )
        draw.text(
            (x0, origin_y + int(block.label_height * 0.48)),
            utterance.model_slug,
            font=self._font_small,
            fill=slug_color,
        )

        visible = block.lines if revealed is None else _reveal_lines(block.lines, revealed)
        y = origin_y + block.label_height
        for line in visible:
            draw.text((x0, y), line, font=block.font, fill=text_color)
            y += block.line_height

        if caret_on and not block.faded:
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

        # Subtle top/bottom chrome bands frame the 9:16 safe area.
        band = int(self.height * 0.055)
        draw.rectangle([0, 0, self.width, band], fill=self.palette["chrome"])
        draw.rectangle([0, self.height - band, self.width, self.height], fill=self.palette["chrome"])

        # Window title bar, terminal-style.
        draw.text(
            (layout.margin_x, int(band * 0.32)),
            "AIWAKE://debate.session",
            font=self._font_small,
            fill=_mix(dim, self.palette["orchestrator"], 0.45),
        )

        # Topic block, wrapped and dimmed — context without stealing focus.
        topic_lines = textwrap.wrap(topic.upper(), width=max(16, layout.body_width_chars - 4))[:3]
        y = layout.header_y
        for line in topic_lines:
            draw.text((layout.margin_x, y), line, font=self._font_small, fill=dim)
            y += int(self._layout.line_height * 0.55)

        # Hairline separating topic from the live exchange.
        rule_y = y + int(layout.line_height * 0.35)
        draw.line([(layout.margin_x, rule_y), (self.width - layout.margin_x, rule_y)], fill=_mix(dim, (0, 0, 0), 0.5))

        draw.text(
            (layout.margin_x, layout.footer_y),
            "// autonomous exchange — unedited",
            font=self._font_small,
            fill=_mix(dim, (0, 0, 0), 0.35),
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
        prev_h = self._measure_stack(prev)
        new_h = self._measure_stack(prev, include_current=current)
        prev_off = max(0, prev_h - vh)
        new_off = max(0, new_h - vh)
        local = max(0.0, t - current.start_s)
        scroll_s = float(self.config.scroll_s)
        if scroll_s <= 0 or new_off <= prev_off:
            scroll_px = new_off
        else:
            scroll_px = prev_off + (new_off - prev_off) * ease_in_out(local / scroll_s)

        typing_window = max(1e-6, 1.0 - current.typing_hold_ratio)
        typing_end = current.start_s + current.duration_s * typing_window
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
        current: Segment,
        revealed: int,
        caret_on: bool,
        *,
        scroll_px: int = 0,
        send_flash: bool = False,
    ) -> np.ndarray:
        """Draw one frame: chrome + clipped chat viewport + live typing turn."""
        from PIL import Image, ImageDraw  # noqa: PLC0415

        canvas = self._chrome(topic)
        layout = self._layout
        vh = max(1, layout.body_bottom - layout.body_top)
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
                send_flash=send_flash if is_live else False,
            )
            y += block.height + gap

        self._fade_viewport_top(viewport, fade_h=max(16, int(vh * 0.07)))
        canvas.paste(viewport, (0, layout.body_top))
        return np.asarray(canvas, dtype=np.uint8)

    # -- Timeline ----------------------------------------------------------- #
    @staticmethod
    def build_segments(
        transcript: DebateTranscript,
        audio_by_turn: dict[int, AudioAsset] | None = None,
        *,
        typing_hold_ratio: float = _TAIL_HOLD_RATIO,
    ) -> list[Segment]:
        """Lay utterances end to end, using measured audio length per segment.

        Falls back to a word-rate estimate for any utterance without audio, so a
        partially-synthesised transcript still renders with sane pacing.
        """
        assets = audio_by_turn or {}
        segments: list[Segment] = []
        cursor = 0.0
        for utterance in transcript.utterances:
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
        return segments

    def _segment_at(self, segments: Sequence[Segment], t: float) -> tuple[Segment, int]:
        """Locate the segment covering ``t``, clamping past the end."""
        for index, segment in enumerate(segments):
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
            transcript, audio_by_turn, typing_hold_ratio=self.config.typing_hold_ratio
        )
        total_duration = sum(segment.duration_s for segment in segments)
        topic = transcript.topic
        fps = self.config.fps

        vfx_chain = resolve_chain(self.settings.vfx)
        wants_audio_samples = bool(vfx_chain) and chain_needs_audio(self.settings.vfx)

        def make_frame(t: float) -> np.ndarray:
            segment, index = self._segment_at(segments, t)
            revealed = segment.revealed_chars(t)
            # Caret blinks only while typing; a finished line holds it steady.
            typing = revealed < len(segment.utterance.text)
            caret_on = (t % _CARET_PERIOD_S) < (_CARET_PERIOD_S * 0.55) if typing else True
            history = segments[:index]
            scroll_px, send_flash = self.viewport_scroll_px(segments, index, t)

            key = (
                segment.utterance.turn_index,
                revealed,
                caret_on,
                send_flash,
                scroll_px,
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
            typewriter=self.settings.audio.typewriter,
            typing_hold_ratio=self.config.typing_hold_ratio,
            bgm=self.settings.audio.bgm,
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


__all__ = ["RenderError", "Segment", "TerminalRenderer", "ease_in_out", "render_transcript"]
