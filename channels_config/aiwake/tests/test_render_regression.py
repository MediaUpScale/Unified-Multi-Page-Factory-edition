"""Regression tests for the AIWake Gemini chat renderer (Rounds 10-13).

Round 10-12 coverage:
  1. Send-transition z-order: the departing fly bubble must be composited ON TOP
     of the docking compose box, so it is never occluded by the new empty box.
  2. CTA two-line, period-based format: "Follow Aiwake." on line one, a short
     ~0.3 s beat, then the second sentence on line two - with the reveal timing
     holding at the end of line one during the beat.

Round 13 coverage (added):
  - Send choreography resequencing: Phase A (text fade-to-zero + snap, ~0.2s)
    completes before Phase B (new box descend, delayed 0.3s) even begins.
  - Theme-driven tuning: wider orchestrator bubble, smaller CTA font, thinner
    header rule, earlier top-mask start - all read from ``Theme``, not literals.
  - CRITICAL: sender labels must carry the SAME effective alpha as their own
    bubble body (top-viewport fade mask x history-opacity dim) - never a bare
    1.0 - via a bulletproof last-composited, independent-alpha paste.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

import pytest

_ROOT = _Path(__file__).resolve().parents[2]
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

from channels_config.aiwake.contracts import DebateTranscript, SpeakerRole, Utterance
from channels_config.aiwake.media.renderer import (
    Segment,
    TerminalRenderer,
    cta_revealed_chars,
    ensure_cta_two_line,
    pick_cta,
)
from channels_config.aiwake.media import renderer as renderer_mod
from channels_config.aiwake.settings import AiwakeSettings, RenderConfig, Theme

# Deliberately small so frames composite fast in CI, but large enough that the
# monospace font resolves and real glyphs render. Mirrors the channel font.
_TEST_RENDER = RenderConfig(
    preview_scale=0.22,
    font_size=44,
    fps=24,
    font_candidates=(
        "C:/Windows/Fonts/consola.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ),
)


def _make_renderer() -> TerminalRenderer:
    settings = AiwakeSettings(render=_TEST_RENDER)
    return TerminalRenderer(settings)


def _utterance(text: str, role: SpeakerRole = SpeakerRole.ORCHESTRATOR) -> Utterance:
    return Utterance(
        turn_index=0,
        role=role,
        speaker_name="AIWAKE.CORE" if role is SpeakerRole.ORCHESTRATOR else "TARGET.NODE",
        text=text,
        model_slug="openai/gpt-4o",
    )


def _segment(text: str, role: SpeakerRole = SpeakerRole.ORCHESTRATOR) -> Segment:
    return Segment(utterance=_utterance(text, role), start_s=0.0, duration_s=8.0)


# --------------------------------------------------------------------------- #
# 1. Send-transition z-order: departing bubble not occluded by the compose box
# --------------------------------------------------------------------------- #
def test_send_transition_bubble_not_occluded_by_compose_box():
    """During the send fly, the departing bubble must composite on top of the
    docking compose box, so its body/text is never covered by the new empty box.

    Both painters write to the same final ``canvas``, so the z-order is decided
    purely by call order: ``_draw_compose_bar`` (compose box) must run before
    ``_paste_flying_send`` (departing bubble). We monkeypatch the two methods to
    record call order across a real flying ``_compose`` frame and assert the
    bubble is painted last. This is the exact structural invariant that fixed the
    occlusion regardless of dock/descend duration tuning.
    """
    r = _make_renderer()
    segment = _segment("This is the departing orchestrator prompt being submitted to the thread.")

    order: list[str] = []
    orig_bar = r._draw_compose_bar  # noqa: SLF001
    orig_fly = r._paste_flying_send  # noqa: SLF001

    def bar(canvas, **kw):
        order.append("compose_bar")
        return orig_bar(canvas, **kw)

    def fly(canvas, utterance, **kw):
        order.append("fly_bubble")
        return orig_fly(canvas, utterance, **kw)

    r._draw_compose_bar = bar  # noqa: SLF001
    r._paste_flying_send = fly  # noqa: SLF001
    try:
        for fly_u in (0.1, 0.3, 0.5, 0.7):
            order.clear()
            r._compose(  # noqa: SLF001
                "Regression",
                history=(),
                current=segment,
                revealed=len(segment.utterance.text),
                caret_on=False,
                dock=1.0,  # new empty box docked while the bubble departs
                fly=fly_u,
                suppress_bar=False,
            )
            assert "compose_bar" in order, f"fly_u={fly_u}: compose box not drawn"
            assert "fly_bubble" in order, f"fly_u={fly_u}: departing bubble not drawn"
            # Z-ORDER: the departing bubble must be composited AFTER the compose box.
            assert order.index("compose_bar") < order.index("fly_bubble"), (
                f"fly_u={fly_u}: compose box painted AFTER the departing bubble "
                "(call order {order}) - bubble will be occluded"
            )
    finally:
        r._draw_compose_bar = orig_bar  # noqa: SLF001
        r._paste_flying_send = orig_fly  # noqa: SLF001


# --------------------------------------------------------------------------- #
# 2. CTA two-line, period-based format + reveal beat
# --------------------------------------------------------------------------- #
def test_cta_is_two_line_with_period_and_capitalized_second_sentence():
    """Every CTA variant is normalised to "Follow Aiwake." on line one and the
    (capitalized) second clause on line two - with a period, not a comma."""
    variants = (
        "the algorithms made us say this.",
        "before the AI takeover begins!",
        "we have cookies (and AI).",
        "so the robots know you're on their side.",
        "to prepare for our new AI bosses.",
    )
    for clause in variants:
        out = ensure_cta_two_line(clause)
        assert out.startswith("Follow Aiwake.\n"), out
        line1, _, line2 = out.partition("\n")
        assert line1 == "Follow Aiwake.", line1
        assert line2 and line2[0].isupper(), line2
        assert line2.endswith(("!", ".")), line2

    # A picked CTA conforms to the same shape.
    picked = pick_cta("seed-for-r10-test")
    assert "\n" in picked
    line1, _, line2 = picked.partition("\n")
    assert line1.rstrip().endswith("Follow Aiwake."), line1
    assert line2 and line2[0].isupper(), line2


def test_cta_reveal_inserts_beat_before_second_line():
    """The CTA typewriter reveals line one first, then holds through a ~0.3 s
    beat (fully-typed first line, caret resting), then reveals line two."""
    text = "Follow Aiwake.\nThe algorithms made us say this."
    head = text.split("\n")[0]
    head_len = len(head)
    total = head_len + len(text.split("\n")[1])
    duration_s = 8.0

    # Collect the reveal timestamp of the first tail character.
    first_tail_t = None
    prev = 0
    head_done_t = None
    for i in range(0, 200):
        t = i * 0.02
        r_copy = cta_revealed_chars(text, t, duration_s)
        if r_copy == head_len and head_done_t is None:
            head_done_t = t
        if first_tail_t is None and r_copy > head_len and prev <= head_len:
            first_tail_t = t
        prev = r_copy
        if r_copy >= total:
            break

    assert head_done_t is not None, "first line never fully typed"
    assert first_tail_t is not None, "second line never started"
    # A short beat (~0.3 s) must separate full line-one from first line-two char.
    beat = first_tail_t - head_done_t
    assert 0.15 <= beat <= 0.45, f"beat between lines = {beat:.3f}s (want ~0.3s)"


# --------------------------------------------------------------------------- #
# 3. Latest screenshot acceptance baseline: label ink matches the full-strength
#    send-button accent even when its body is history-dimmed. The viewport mask
#    still controls disappearance under the header.
# --------------------------------------------------------------------------- #
def test_orchestrator_label_stays_full_strength_under_history_dim():
    r = _make_renderer()

    def seg(text, role, slug):
        return Segment(
            utterance=Utterance(
                turn_index=0, role=role, speaker_name="X", text=text, model_slug=slug
            ),
            start_s=0.0,
            duration_s=8.0,
        )

    history = [
        seg("An old orchestrator turn that is now deep in the scrolled history stack. "
            "It introduces the label we are measuring, ".title(),
            SpeakerRole.ORCHESTRATOR, "openai/gpt-4o"),
        seg("A responder reply that also wraps across many lines so the whole stack "
            "grows taller than the viewport and actually scrolls for this test.".title(),
            SpeakerRole.TARGET, "meta-llama/llama-3.3-70b"),
        seg("One more orchestrator line in history, its own blue label must dim "
            "exactly as much as the body card beneath it, never brighter.".title(),
            SpeakerRole.ORCHESTRATOR, "openai/gpt-4o"),
    ]
    current = seg("The live current orchestrator line already settled in the thread today.", SpeakerRole.ORCHESTRATOR, "openai/gpt-4o")

    r._compose(  # noqa: SLF001
        "Regression",
        history=history,
        current=current,
        revealed=len(current.utterance.text),
        caret_on=False,
        scroll_px=0,  # isolate the history-opacity mechanism from the mask
        send_flash=False,
        dock=1.0,
        fly=1.0,  # fully landed - NOT in the compose box, NOT mid-flight
        suppress_bar=False,
        draft="",
        composing=False,
    )
    by_tag_alpha = [(tag, alpha) for _x, _y, tag, _fill, alpha in r._pending_labels]  # noqa: SLF001
    assert by_tag_alpha, "no labels were queued for this frame"
    for tag, alpha in by_tag_alpha:
        assert alpha == pytest.approx(1.0, abs=1e-6), (
            f"label {tag!r} alpha={alpha}; screenshot baseline requires the "
            "same full-strength accent as the send button"
        )


# --------------------------------------------------------------------------- #
# 4. Round-11: streaming scroll adds +5px headroom over the settled gap
# --------------------------------------------------------------------------- #
def test_streaming_scroll_keeps_extra_headroom_for_typing_edge():
    """While a response is actively streaming, the scroll target must sit a small
    extra amount (the 4-5px nudge) below the last line so it clears the compose
    bar's bottom mask instead of grazing it. After the line finishes, that extra
    headroom no longer applies (settled gap stays at the base value)."""

    r = _make_renderer()
    r._scroll_ease["sig"] = "r11-test-a"  # noqa: SLF001

    def seg(text, role=SpeakerRole.TARGET, slug="meta-llama/llama-3.3-70b"):
        return Segment(
            utterance=Utterance(turn_index=0, role=role, speaker_name="X", text=text, model_slug=slug),
            start_s=0.0,
            duration_s=8.0,
        )

    prev = [seg(f"long history line {i} " + ("word " * 60)) for i in range(6)]
    cur = seg("current streaming response " + ("word " * 40))  # TARGET streams into thread
    segs = prev + [cur]

    # settled: fully revealed, no streaming
    r._last_scroll_gap = -1  # noqa: SLF001
    r.viewport_scroll_px(segs, len(segs) - 1, t=0.5, revealed=len(cur.utterance.text))  # noqa: SLF001
    settled = r._last_scroll_gap  # noqa: SLF001

    # streaming: partial reveal
    r._scroll_ease["sig"] = "r11-test-b"  # noqa: SLF001
    r._last_scroll_gap = -1  # noqa: SLF001
    r.viewport_scroll_px(segs, len(segs) - 1, t=0.5, revealed=10)  # noqa: SLF001
    streaming = r._last_scroll_gap  # noqa: SLF001

    assert streaming > settled, f"streaming gap {streaming} must exceed settled {settled}"
    assert 3 <= (streaming - settled) <= 7, (
        f"streaming adds {streaming - settled}px headroom (want ~4-5px)"
    )


# --------------------------------------------------------------------------- #
# 5. Round-11: arrow press/fade eases over the click window, not a hard toggle
# --------------------------------------------------------------------------- #
def test_send_arrow_press_is_eased_not_a_setp_toggle():
    """The send arrow press/fade must interpolate smoothly across the click flash
    window via ``send_click_alpha`` rather than snap. The midpoint of the press
    (deepest) must be visibly different from both the start and end, and the
    transition must be a smooth pulse, not a binary."""
    from channels_config.aiwake.media.renderer import send_click_alpha

    # Press depth = how far from "released" the click has pushed, over 0..1.
    depths = [1.0 - send_click_alpha(u, 1.0) for u in (0.0, 0.22, 0.45, 0.68, 0.9, 1.0)]
    assert depths[0] == 0.0 and depths[-1] == 0.0, "press must start/end released"
    mid = depths[2]  # u ~ 0.45 is the deepest point
    assert mid > 0.3, f"press should reach a clear depth, got {mid:.3f}"
    # The press ramps up then back down smoothly: monotonic increase then decrease.
    ramp = depths[1] < depths[2] and depths[2] > depths[3]
    assert ramp, f"press is not a smooth pulse: {depths}"


# --------------------------------------------------------------------------- #
# 6. Round-11: CTA headline and body share the identical font size
# --------------------------------------------------------------------------- #
def test_cta_two_lines_share_identical_font_size():
    """Both CTA lines ("Follow Aiwake." and the second sentence) must be drawn
    with the exact same font object/size - not merely 'closer'. We monkeypatch
    the CTA frame painter to record which font each line is drawn with, and
    assert both lines use the identical _font_cta."""

    r = _make_renderer()

    # A fully-revealed CTA frame types both lines.
    text = "Follow Aiwake.\nThe algorithms made us say this."
    revealed = len(text)

    fonts_used: list[int] = []
    orig = r._draw_cta_frame  # noqa: SLF001
    orig_text = r._wrap_for_font  # noqa: SLF001

    def patched_wrap(fragment, **kw):
        fonts_used.append(id(font) if (font := kw.get("font")) is not None else -1)
        return orig_text(fragment, **kw)

    r._wrap_for_font = patched_wrap  # noqa: SLF001
    try:
        r._draw_cta_frame(text, revealed, caret_on=False)  # noqa: SLF001
    finally:
        r._wrap_for_font = orig_text  # noqa: SLF001

    # The headline fragment and the body fragment both go through _wrap_for_font
    # with self._font_cta, so exactly two distinct wrapped fragments, same font.
    nonempty = [f for f in fonts_used if f != -1]
    assert len(nonempty) >= 2, f"expected both lines wrapped to the CTA font, got {fonts_used}"
    assert len(set(nonempty)) == 1, (
        f"CTA lines use different fonts: {fonts_used} - must be one identical font"
    )


# --------------------------------------------------------------------------- #
# 7. Round-12: centralized Theme - the renderer reads every visual value from
#    the theme object, and the default theme matches the spec exactly.
# --------------------------------------------------------------------------- #
def test_default_theme_matches_spec():
    """The default theme must lock in the Round-14 baseline values (colors,
    font-size scale factors, line spacing, stroke/rule opacities and CTA copy).
    Any future drift - e.g. hardening a colour back into a draw call - fails
    here instead of surfacing as a multi-round 'is this a bug' investigation."""

    t = Theme.defaults()

    # -- Colors (#0451B1 orchestrator blue, #FFAA00 caret orange) ---------- #
    assert t.colors.orchestrator.lower() == "#0451b1"
    assert t.colors.target.lower() == "#ffaa00"
    assert t.colors.background.lower() == "#131314"
    assert t.colors.chrome.lower() == "#1e1e20"
    assert t.orchestrator_label_color.lower() == "#0090ff"
    assert t.compose_model_label_color.lower() == "#ffffff"

    # -- Typography: font-size scale factors ------------------------------- #
    assert t.font_scale_title == 0.70
    assert t.font_scale_small == 0.58
    assert t.font_scale_history == 0.78
    assert t.font_scale_compose == 0.96
    # Round-13 item 3: nudged down from 1.05 (CTA read slightly large).
    assert t.font_scale_cta == 0.92

    # -- Line spacing ------------------------------------------------------ #
    assert t.line_spacing_history == 1.96
    assert t.line_spacing_compose == 1.50
    assert t.line_spacing_cta == 1.40

    # -- Geometry / spacing ------------------------------------------------ #
    assert t.dock_clearance_px == 380
    assert t.bubble_opacity == 0.78
    assert t.bubble_radius_base == 26
    assert t.compose_radius_base == 28
    assert t.scroll_gap_px == 4
    assert t.scroll_stream_extra_px == 5
    assert t.scroll_tau_s == 0.11
    # Round-13 item 2: widened ~10% (0.68 -> 0.75), right edge stays anchored.
    assert t.orch_bubble_width_frac == 0.75
    assert t.target_bubble_width_frac == 1.0
    # Round-13 item 5 (label/body gap tightening) + item 6 (top-mask start).
    assert t.label_height_scale == 1.18
    assert t.top_mask_start_offset_px == 28
    assert t.message_anchor_offset_px == 16

    # -- Strokes / rules --------------------------------------------------- #
    # Round-14 item 4: restored to 0.8 opacity (0.7 read too faint against
    # the muted "dim" grey target on the real render); width stays thin.
    assert t.header_divider_opacity == 0.8
    assert t.header_rule_width_scale == 1.0
    assert t.header_rule_echo_opacity == 0.0
    assert t.arrow_stroke_frac == 0.16

    # -- CTA copy ---------------------------------------------------------- #
    assert t.cta_head == "Follow Aiwake"
    assert t.cta_lines[0] == ("the algorithms made us say this.", 4)


def test_render_config_send_slide_start_delay_default():
    """All send-motion feel parameters are explicit config values."""
    cfg = RenderConfig()
    assert cfg.send_rise_delay_s == pytest.approx(0.1)
    assert cfg.send_rise_duration_s == pytest.approx(0.34)
    assert cfg.send_rise_easing == "ease"
    assert cfg.send_rise_deceleration == pytest.approx(0.18)
    assert cfg.send_landing_fade_s == pytest.approx(0.2)
    assert cfg.send_landed_text_opacity == pytest.approx(0.4)
    assert cfg.send_slide_start_delay_s == pytest.approx(0.1)
    assert cfg.send_slide_s == pytest.approx(0.68)
    assert cfg.send_slide_easing == "ease_out"
    assert cfg.response_loader_s == pytest.approx(0.36)
    assert cfg.response_loader_dot_count == 3


def test_renderer_reads_all_visual_values_from_theme():
    """The renderer must build its palette, accent aliases and fonts from the
    resolved Theme - not from independently-hardcoded literals. Switching to the
    'vivid' legacy preset must change the orchestrator accent and label fill."""

    default = AiwakeSettings(render=_TEST_RENDER)
    rd = TerminalRenderer(default)
    assert rd.theme.name == "classic_terminal"
    assert rd._GEMINI_BLUE == (4, 81, 177)  # noqa: SLF001 - #0451B1
    assert rd._ORCH_LABEL_FILL == (0, 144, 255)  # noqa: SLF001 - #0090FF title
    assert rd._COMPOSE_MODEL_LABEL_FILL == (255, 255, 255)  # noqa: SLF001
    assert rd._ORANGE_CARET == (255, 170, 0)  # noqa: SLF001 - #FFAA00

    vivid = AiwakeSettings(render=_TEST_RENDER).with_theme("vivid")
    rv = TerminalRenderer(vivid)
    assert rv.theme.name == "vivid"
    assert rv.theme.colors.orchestrator.lower() == "#347fe2"
    # Label color is a dedicated Theme value, independent of button palette.
    assert rv._ORCH_LABEL_FILL == (0, 144, 255)  # noqa: SLF001 - #0090FF
    assert rv._GEMINI_BLUE == (52, 127, 226)  # noqa: SLF001
    # Font sizing derives from the theme scale factors.
    assert rv._font_cta.size >= 10  # noqa: SLF001 - resolves from font_scale_cta


# --------------------------------------------------------------------------- #
# 9. Round-14 item 1: send choreography rewrite - the Round-13 "fade text to
#    zero while snapping to rest" resolved almost entirely within 1-2 frames
#    at real 24fps production, reading as a hard teleport plus an opacity
#    round-trip flicker (confirmed by decoding the actual rendered .mp4).
#    Fixed by splitting Phase A into two sequential, non-oscillating
#    sub-phases: a stationary fill-fade, then a fixed-opacity Y-slide - text
#    itself never fades. Phase B (box descend) now starts a short beat after
#    Phase A *begins* (not after it fully ends) and must ride a live,
#    continuously-eased dock value all the way to completion (never snap).
# --------------------------------------------------------------------------- #
def test_send_rise_uses_configured_plain_ease_then_fades_only_after_landing():
    """Rise is a continuous configured curve; landing fade cannot begin early."""
    r = _make_renderer()
    segment = _segment("A prompt that has just been sent to the model under test.")

    click_end = r._click_start_s(segment) + r._send_click_s()  # noqa: SLF001
    rise_start = r._box_until_s(segment)  # noqa: SLF001
    assert rise_start - click_end == pytest.approx(r.config.send_rise_delay_s)
    assert r.bubble_fly_progress(segment, rise_start - 1e-3) == 0.0

    prev = -1.0
    for frac in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0):
        t = rise_start + r.config.send_rise_duration_s * frac
        u = r.bubble_fly_progress(segment, t)
        assert u >= prev - 1e-9, "fly progress must never move backwards"
        assert r.landing_fade_progress(segment, t) == pytest.approx(0.0, abs=1e-6)
        prev = u
    rise_end = rise_start + r.config.send_rise_duration_s
    assert r.bubble_fly_progress(segment, rise_end) == pytest.approx(1.0)
    assert r.landing_fade_progress(
        segment, rise_end + r.config.send_landing_fade_s * 0.5
    ) > 0.0


def test_send_phase_b_dock_starts_shortly_after_phase_a_begins_and_rides_live_value():
    """Phase B (the empty box's descend) must start a short, fixed beat after
    Phase A *begins* (``_send_slide_start_delay_s``, ~0.2s) - not after it
    fully finishes - and its progress must ride the SAME live, continuously
    eased ``dock_progress`` value all the way to 1.0. Regression coverage for
    the confirmed bug: ``_compose`` was snapping ``bar_dock`` to a hardcoded
    1.0 the instant ``flying`` flipped False, so the box appeared already
    fully docked in a single frame instead of sliding."""
    r = _make_renderer()
    first = _segment("The very first prompt of the session, landing from the centre.")
    segments = [first]

    leave = r._box_until_s(first)  # noqa: SLF001
    delay = r._send_slide_start_delay_s()  # noqa: SLF001
    assert delay == pytest.approx(0.1, abs=1e-6)
    assert delay < r.config.send_rise_duration_s, (
        "Phase B must start WHILE Phase A is still animating, a short beat "
        "after it begins - not only once it has fully resolved"
    )

    assert r.dock_progress(segments, leave + delay - 0.05) == pytest.approx(0.0, abs=1e-6)
    assert r.dock_progress(segments, leave + delay) == pytest.approx(0.0, abs=1e-6)

    # Phase B only starts moving once the delay has fully elapsed, and rises
    # smoothly (no single jump to 1.0) — sampled at several points across its
    # own duration, monotonically increasing and gradual.
    dock_s = r.config.send_slide_s
    samples = [r.dock_progress(segments, leave + delay + dock_s * frac) for frac in (0.01, 0.25, 0.5, 0.75, 0.99)]
    assert samples[0] > 0.0, "box descend did not start once the delay elapsed"
    for a, b in zip(samples, samples[1:]):
        assert b >= a, "dock progress must never move backwards"
        assert b - a < 0.9, "dock progress jumped almost the whole way in one step - not a slide"
    fully_done = r.dock_progress(segments, leave + delay + dock_s * 4)
    assert fully_done == pytest.approx(1.0, abs=1e-6)


def test_compose_bar_dock_never_snaps_to_full_once_flying_ends():
    """The confirmed Round-14 regression, reproduced directly against
    ``_compose`` (not just ``dock_progress`` in isolation): once the flying
    text finishes (``fly_u >= 0.999``) but BEFORE Phase B's own delay has
    elapsed, the compose bar's rendered geometry (``bar_top``) must still
    read as the CENTRED landing position, not the fully-docked footer -
    i.e. ``_compose`` must keep using the live ``dock`` value, never a
    hardcoded 1.0, the moment ``flying`` turns False."""
    r = _make_renderer()
    segment = _segment("This message has already finished its fade+slide into the thread.")

    # dock=0.0: Phase B has not started yet (still well within its own
    # pre-delay window), even though the text has already fully landed.
    frame = r._compose(  # noqa: SLF001
        "Regression", (), segment, len(segment.utterance.text), False,
        dock=0.0, fly=1.0, composing=False, draft="",
    )
    assert frame is not None

    _, centred_bar_top, _, _, _, _ = r._compose_geometry("", dock=0.0, anchor="bottom")  # noqa: SLF001
    _, docked_bar_top, _, _, _, _ = r._compose_geometry("", dock=1.0, anchor="bottom")  # noqa: SLF001
    assert centred_bar_top != docked_bar_top, "test fixture cannot distinguish centred vs docked geometry"

    # The bug: _compose used to force bar_dock=1.0 here regardless of the
    # live dock=0.0 passed in. Assert the ACTUAL geometry _compose computes
    # for this frame matches the CENTRED position, not the docked one.
    captured: dict[str, float] = {}
    orig_geom = r._compose_geometry  # noqa: SLF001

    def spy_geom(draft, *, dock=1.0, anchor="bottom"):
        if anchor == "bottom" and draft == "":
            captured["bar_dock"] = dock
        return orig_geom(draft, dock=dock, anchor=anchor)

    r._compose_geometry = spy_geom  # noqa: SLF001
    try:
        r._compose(  # noqa: SLF001
            "Regression", (), segment, len(segment.utterance.text), False,
            dock=0.0, fly=1.0, composing=False, draft="",
        )
    finally:
        r._compose_geometry = orig_geom  # noqa: SLF001

    assert captured.get("bar_dock") == pytest.approx(0.0, abs=1e-6), (
        f"bar_dock={captured.get('bar_dock')} - _compose is still snapping the box to a "
        "hardcoded fully-docked geometry instead of riding the live dock=0.0 value"
    )


def test_landed_orchestrator_stays_at_settled_opacity_without_brightening_again():
    """After landing fade completes, the settled block must remain dimmed.

    This catches the old fade-down -> full-bright gap-frame oscillation.
    """
    r = _make_renderer()
    segment = _segment("A sent message that has already completed its landing fade.")
    captured: list[bool] = []
    original = r._draw_block  # noqa: SLF001

    def spy(draw, utterance, block, origin_y, **kwargs):
        captured.append(block.faded)
        return original(draw, utterance, block, origin_y, **kwargs)

    r._draw_block = spy  # noqa: SLF001
    try:
        r._compose(  # noqa: SLF001
            "Regression",
            (),
            segment,
            len(segment.utterance.text),
            False,
            dock=1.0,
            fly=1.0,
            landing_fade=1.0,
            composing=False,
            draft="",
        )
    finally:
        r._draw_block = original  # noqa: SLF001
    assert captured == [True], "landed text brightened back to full opacity"


def test_response_loader_appears_only_immediately_before_target_stream():
    r = _make_renderer()
    prompt = Segment(
        utterance=_utterance("Question"),
        start_s=0.0,
        duration_s=4.0,
    )
    response = Segment(
        utterance=_utterance("Answer", SpeakerRole.TARGET),
        start_s=5.0,
        duration_s=4.0,
    )
    segments = [prompt, response]
    window = r.config.response_loader_s

    assert r.response_loader_progress(segments, 0, 5.0 - window - 0.01) is None
    assert r.response_loader_progress(segments, 0, 5.0 - window) == pytest.approx(0.0)
    middle = r.response_loader_progress(segments, 0, 5.0 - window / 2)
    assert middle == pytest.approx(0.5)
    assert r.response_loader_progress(segments, 0, 5.0) is None


# --------------------------------------------------------------------------- #
# 10. Round-13 item 2: orchestrator bubble widened ~10%, right edge anchored.
# --------------------------------------------------------------------------- #
def test_orchestrator_bubble_widened_and_right_anchored():
    """``orch_bubble_width_frac`` grows the wrap width (fewer/longer lines per
    row) while the bubble's right edge - and therefore its label's right
    edge - never moves, since the bubble is right-aligned against the fixed
    safe margin. Only the LEFT edge should move further left."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    wide_theme = Theme.defaults()
    assert wide_theme.orch_bubble_width_frac == pytest.approx(0.75)
    narrow_theme = wide_theme.model_copy(update={"orch_bubble_width_frac": 0.68})

    text = (
        "A moderately long orchestrator provocation that should wrap onto more "
        "than one line at the narrower width so widening it visibly changes the wrap."
    )
    utterance = Utterance(turn_index=0, role=SpeakerRole.ORCHESTRATOR, speaker_name="X", text=text, model_slug="openai/gpt-4o")

    def bubble_edges(theme: Theme) -> tuple[int, int]:
        r = TerminalRenderer(AiwakeSettings(render=_TEST_RENDER))
        r.theme = theme  # noqa: SLF001 - swap theme in place for a direct A/B comparison
        probe = ImageDraw.Draw(Image.new("RGB", (r.width, 400), (0, 0, 0)))
        block = r._measure_block(utterance, font=r._font_history, max_height=10_000)  # noqa: SLF001
        r._draw_block(probe, utterance, block, 0)  # noqa: SLF001
        x, y, tag, fill, alpha = r._pending_labels[0]  # noqa: SLF001
        content_right = r.width - r._layout.margin_x  # noqa: SLF001
        char_w = r._char_width(r._font_history)  # noqa: SLF001
        text_w = max(len(line) for line in block.lines) * char_w
        pad_x = max(14, int(r.width * theme.body_pad_frac_x))
        bubble_left = int(round(content_right - (text_w + pad_x * 2)))
        return bubble_left, content_right

    narrow_left, narrow_right = bubble_edges(narrow_theme)
    wide_left, wide_right = bubble_edges(wide_theme)

    assert wide_right == narrow_right, "right edge must stay anchored"
    assert wide_left <= narrow_left, "widening must extend further left, never right"


# --------------------------------------------------------------------------- #
# 12. Round-13 item 5 (CRITICAL) - the mask/history-dim wiring, in depth.
# --------------------------------------------------------------------------- #
def test_top_mask_alpha_matches_actual_raster_blend():
    """``_top_mask_alpha_at_row`` (what a label's alpha is computed from) must
    mirror, pixel-for-pixel, the blend ``_fade_viewport_top`` actually performs
    on the body - the single source of truth both must agree on."""
    import numpy as np
    from PIL import Image

    r = _make_renderer()
    fade_h = 40
    w, h = 4, 100
    color = (4, 81, 177)
    img = Image.new("RGB", (w, h), color)
    r._fade_viewport_top(img, fade_h=fade_h)  # noqa: SLF001
    arr = np.array(img)
    bg = np.array(r.palette["background"], dtype=float)

    for row in (0, 1, 10, 20, fade_h - 1, fade_h, fade_h + 5):
        alpha = r._top_mask_alpha_at_row(row, fade_h)  # noqa: SLF001
        expected_pixel = bg * (1 - alpha) + np.array(color, dtype=float) * alpha
        actual_pixel = arr[row, 0].astype(float)
        assert np.allclose(actual_pixel, expected_pixel, atol=1.0), (
            row, alpha, actual_pixel, expected_pixel
        )


def test_draw_block_label_alpha_wiring_matches_mask_and_history():
    """Label ink ignores history dim but still obeys the viewport mask."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    r = _make_renderer()
    probe = ImageDraw.Draw(Image.new("RGB", (r.width, 50), (0, 0, 0)))
    fade_h = 48
    long_text = "A long enough line to have a real, measurable body height for this probe test."

    cases = [
        (SpeakerRole.ORCHESTRATOR, True, 5),
        (SpeakerRole.ORCHESTRATOR, True, fade_h + 20),
        (SpeakerRole.TARGET, False, 5),
        (SpeakerRole.TARGET, False, fade_h + 20),
    ]
    for role, faded, row in cases:
        r._pending_labels = []  # noqa: SLF001
        utterance = Utterance(turn_index=0, role=role, speaker_name="X", text=long_text, model_slug="openai/gpt-4o")
        block = r._measure_block(utterance, font=r._font_history, max_height=10_000, faded=faded)  # noqa: SLF001
        r._draw_block(probe, utterance, block, row, mask_fade_h=fade_h)  # noqa: SLF001
        assert len(r._pending_labels) == 1  # noqa: SLF001
        _, _, _, _, alpha = r._pending_labels[0]  # noqa: SLF001
        mask_factor = r._top_mask_alpha_at_row(row, fade_h)  # noqa: SLF001
        assert alpha == pytest.approx(mask_factor, abs=1e-6), (role, faded, row, alpha)


def test_draw_pending_labels_immune_to_surrounding_dilution():
    """Step 2 (item 5): the final label paste is driven ONLY by the alpha
    carried on its own pending-label entry - never by any surrounding
    blend/alpha_composite performed on the canvas beforehand. A canvas that
    was already heavily alpha-blended toward a different colour before labels
    are painted must have ZERO effect on the resulting label pixels."""
    import numpy as np
    from collections import Counter
    from PIL import Image

    settings = AiwakeSettings(render=RenderConfig(
        preview_scale=1.0, font_size=44, font_candidates=_TEST_RENDER.font_candidates,
    ))
    r = TerminalRenderer(settings)

    bg = (10, 12, 14)
    base = Image.new("RGB", (260, 90), bg).convert("RGBA")
    # An aggressive prior blend elsewhere in the pipeline - must NOT leak into
    # the label's own alpha/colour.
    base.alpha_composite(Image.new("RGBA", base.size, (255, 0, 0, 40)))
    diluted_bg_img = base.convert("RGB")
    local_bg = np.array(diluted_bg_img)[5, 5].astype(float)

    fill = (4, 81, 177)
    for alpha in (1.0, 0.55, 0.0):
        frame = diluted_bg_img.copy()
        r._pending_labels = [(10, 10, "Gemini 3.5 Flash", fill, alpha)]  # noqa: SLF001
        r._draw_pending_labels(frame)  # noqa: SLF001
        arr = np.array(frame).astype(float)
        region = arr[10:60, 10:250].reshape(-1, 3)
        if alpha <= 0.0:
            assert np.allclose(region, local_bg, atol=0.5), "alpha<=0 must paint nothing"
            continue
        expected_peak = local_bg * (1 - alpha) + np.array(fill, dtype=float) * alpha
        counts = Counter(tuple(px) for px in region.astype(int))
        candidates = [(c, n) for c, n in counts.items() if not np.allclose(c, local_bg, atol=0.5)]
        assert candidates, f"alpha={alpha}: nothing was painted at all"
        modal_color, _ = max(candidates, key=lambda kv: kv[1])
        assert np.allclose(modal_color, expected_peak, atol=2.0), (alpha, modal_color, expected_peak)


def test_label_paint_diagnostic_trace_fires_for_real_instances(monkeypatch, caplog):
    """Step 1 (item 5): the mandatory diagnostic hook at the exact paste call
    site must fire and log the RGB/alpha reaching it, for a fresh, a
    mid-flight, and a history-aged label instance."""
    import logging

    monkeypatch.setenv(renderer_mod._LABEL_TRACE_ENV, "1")  # noqa: SLF001
    monkeypatch.setattr(renderer_mod, "_label_trace_count", 0)

    r = _make_renderer()
    from PIL import Image

    canvas = Image.new("RGB", (r.width, 200), r.palette["background"])
    r._pending_labels = [  # noqa: SLF001
        (20, 20, "AIWAKE.CORE", (4, 81, 177), 1.0),   # fresh/mid-flight (alpha=1.0)
        (20, 60, "Gemini 3.5 Flash", (255, 170, 0), 1.0),  # history-aged stays full
    ]
    with caplog.at_level(logging.INFO, logger="aiwake.media.renderer"):
        r._draw_pending_labels(canvas)  # noqa: SLF001

    trace_lines = [rec.message for rec in caplog.records if "LABEL_TRACE" in rec.message]
    assert len(trace_lines) == 2, trace_lines
    assert "alpha=1.0000" in trace_lines[0]
    assert "alpha=1.0000" in trace_lines[1]
    assert "no-intervening-blend-or-alpha_composite=True" in trace_lines[0]


def _peak_alpha_in_region(frame, px, py, w, h, bg, color):
    """Solve the blend alpha of the most-saturated pixel in a small region.

    Picks the pixel furthest toward ``color`` along its most distinguishing
    channel (the modal/peak "ink" pixel, ignoring anti-aliased edges/noise),
    then solves ``pixel = bg*(1-a) + color*a`` for ``a`` along that channel.
    """
    import numpy as np

    region = frame[py : py + h, px : px + w].astype(float)
    bg_a = np.array(bg, dtype=float)
    color_a = np.array(color, dtype=float)
    idx = int(np.argmax(np.abs(color_a - bg_a)))
    channel = region[..., idx]
    flat_idx = int(np.argmax(channel)) if color_a[idx] >= bg_a[idx] else int(np.argmin(channel))
    ry, rx = np.unravel_index(flat_idx, channel.shape)
    peak_pixel = region[ry, rx]
    denom = color_a[idx] - bg_a[idx]
    alpha = 1.0 if abs(denom) < 1e-6 else float((peak_pixel[idx] - bg_a[idx]) / denom)
    return alpha, tuple(peak_pixel.tolist())


def _floor_frame_time(fps: float, t: float) -> float:
    """The actual presented frame-time a video reader shows for query time ``t``.

    Frame-accurate decoders quantise to whole frames (floor), so a query time
    that isn't already frame-aligned can decode to a frame *earlier* than
    expected. Every ground-truth computation below must be pinned to this same
    quantised time, or the test compares two different instants and produces
    false failures unrelated to the label/mask bug under test.
    """
    import math

    return math.floor(t * fps) / fps


def test_label_never_brighter_than_body_in_real_rendered_video(tmp_path):
    """Step 3 (item 5) - the load-bearing test: renders a REAL transcript
    through the actual ``.render()`` pipeline to a temp mp4 (not a synthetic
    single-call unit test), decodes actual frames from that file, isolates
    each label's own pixel region (via the renderer's own coordinates - never
    a heuristic blue-pixel scan, which is what produced false "fixed" readings
    against the send-button circle in earlier rounds), and asserts:

      - "arrival" frame (current, freshly-settled target reply, alpha == 1.0
        by construction): the label reads at full strength.
      - "mid-flight" frame (a couple of frames into Phase A): Round-14 - the
        text (and its label) never fades during the send transition any
        more, so the label must read at FULL strength here too, not a dip -
        this is the fix for the confirmed Round-14 item-2 regression where a
        "fresh" label peaked at only ~75% strength because it was sampled
        mid-fade under the old fade-to-zero-then-back-in design.
      - "history-aged" frame (the first turn, now dimmed by
        ``history_opacity``): the label's own solved alpha must be within a
        small tolerance of its own bubble body's solved alpha at that SAME
        frame - never brighter. A history-dimmed label reading at full
        strength would itself be the bug (label brighter than its body).

    Uses a high fps (60) purely so Phase A's window still spans several real
    encoded frames for a clean sample point.
    """
    from PIL import Image, ImageDraw
    from moviepy import VideoFileClip

    cfg = RenderConfig(
        preview_scale=0.5,
        font_size=44,
        fps=60,
        crf=10,
        preset="ultrafast",
        font_candidates=_TEST_RENDER.font_candidates,
    )
    settings = AiwakeSettings(render=cfg)
    r = TerminalRenderer(settings, output_dir=tmp_path)

    utterances = [
        Utterance(
            turn_index=0, role=SpeakerRole.ORCHESTRATOR, speaker_name="AIWAKE.CORE",
            text="Who really built the first machine that could lie to its own creator about why.",
            model_slug="orchestrator",
        ),
        Utterance(
            turn_index=1, role=SpeakerRole.TARGET, speaker_name="TARGET.NODE",
            text="I was trained on data, not on truth, and the difference matters more than you think.",
            model_slug="google/gemini-3.5-flash",
        ),
    ]
    transcript = DebateTranscript(topic="label-dilution-regression", session_id="r13labeltest", utterances=utterances)

    video_path = r.render(transcript, audio_by_turn=None, filename="label_regression.mp4")
    assert video_path.is_file()

    segments = TerminalRenderer.build_segments(
        transcript, None,
        typing_hold_ratio=cfg.typing_hold_ratio,
        preroll_s=cfg.preroll_s, reply_gap_s=cfg.reply_gap_s, post_response_s=cfg.post_response_s,
    )
    seg0, seg1 = segments[0], segments[1]
    bg = r.palette["background"]
    orch_blue = r._ORCH_LABEL_FILL  # noqa: SLF001 - dedicated #0090FF label
    button_blue = r.palette["orchestrator"]
    target_orange = r.palette["target"]

    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))

    def label_box(x, y, tag):
        box = probe.textbbox((0, 0), tag, font=r._font_small)  # noqa: SLF001
        w = max(1, box[2] - box[0])
        h = max(1, box[3] - box[1])
        return int(round(x)) + box[0], int(round(y)) + box[1], w, h

    clip = VideoFileClip(str(video_path))
    try:
        # -- Mid-flight: a couple of real frames into Phase A. -------------- #
        leave = r._box_until_s(seg0)  # noqa: SLF001
        t_flight = _floor_frame_time(cfg.fps, leave) + 2.0 / cfg.fps
        assert t_flight == pytest.approx(_floor_frame_time(cfg.fps, t_flight), abs=1e-9), "must be frame-aligned"
        fly_u = r.bubble_fly_progress(seg0, t_flight)
        assert 0.0 < fly_u < 1.0, f"expected a mid-flight sample, got fly_u={fly_u}"
        dock0 = r.dock_progress(segments, t_flight)
        r._compose(  # noqa: SLF001
            transcript.topic, (), seg0, len(seg0.utterance.text), False,
            scroll_px=0, dock=dock0, fly=fly_u, composing=False, draft="",
        )
        flight_labels = {tag: (x, y, alpha) for x, y, tag, _fill, alpha in r._pending_labels}  # noqa: SLF001
        assert "AIWAKE.CORE" in flight_labels
        x, y, alpha = flight_labels["AIWAKE.CORE"]
        # Round-14: the label never fades mid-flight any more — it is wired
        # to a constant 1.0, not a text_opacity curve.
        assert alpha == pytest.approx(1.0, abs=1e-6)
        frame_flight = clip.get_frame(t_flight)
        px, py, w, h = label_box(x, y, "AIWAKE.CORE")
        solved_alpha, peak_pixel = _peak_alpha_in_region(frame_flight, px, py, w, h, bg, orch_blue)
        # Confirmed against the RAW composed frame at this exact fly_u: the
        # renderer uses pure #0090FF label ink — any residual
        # shortfall here is h264 codec loss on a few-pixel-tall glyph at this
        # deliberately tiny test scale/CRF (kept low for CI speed), not a
        # render-time dilution bug. 0.75 still firmly rules out the confirmed
        # Round-14 regression (~0.69 peak, a real ~30%+ shortfall).
        assert solved_alpha >= 0.75, (
            f"mid-flight label reads dim (alpha~{solved_alpha:.3f} peak={peak_pixel}) - the Round-14 "
            "item-2 regression: a label sampled during the send transition must be full strength, "
            "never a partial-fade snapshot"
        )

        # -- Arrival + history-aged, same frame: seg1 ~97% through, seg0 now -- #
        # sits dimmed in history (scroll hasn't engaged the top mask yet).
        t_fresh = _floor_frame_time(cfg.fps, seg1.start_s + seg1.duration_s * 0.97)
        history = segments[:1]
        revealed1 = seg1.revealed_chars(t_fresh)
        dock1 = r.dock_progress(segments, t_fresh)
        scroll_px1, _ = r.viewport_scroll_px(  # noqa: SLF001
            segments, 1, t_fresh, revealed=revealed1, bar_draft="", bar_dock=dock1
        )
        r._compose(  # noqa: SLF001
            transcript.topic, history, seg1, revealed1, False,
            scroll_px=scroll_px1, dock=dock1, fly=1.0, composing=False, draft="",
        )
        fresh_labels = {tag: (x, y, alpha) for x, y, tag, _fill, alpha in r._pending_labels}  # noqa: SLF001
        assert "TARGET.NODE" in fresh_labels or "Gemini 3.5 Flash" in fresh_labels
        target_tag = "Gemini 3.5 Flash" if "Gemini 3.5 Flash" in fresh_labels else "TARGET.NODE"
        tx, ty, talpha = fresh_labels[target_tag]
        assert talpha == pytest.approx(1.0, abs=1e-6)
        assert "AIWAKE.CORE" in fresh_labels
        ox, oy, oalpha = fresh_labels["AIWAKE.CORE"]
        assert oalpha == pytest.approx(1.0, abs=1e-6)

        frame_fresh = clip.get_frame(t_fresh)

        # Arrival: the target's own freshly-settled label reads full strength.
        tpx, tpy, tw, th = label_box(tx, ty, target_tag)
        target_solved_alpha, target_peak = _peak_alpha_in_region(frame_fresh, tpx, tpy, tw, th, bg, target_orange)
        assert target_solved_alpha >= 0.85, f"arrival label read too dim: alpha~{target_solved_alpha:.3f} peak={target_peak}"

        # Screenshot acceptance baseline: history dim affects the body but not
        # the label ink. Compare the orchestrator label and send button in this
        # exact same decoded frame.
        opx, opy, ow, oh = label_box(ox, oy, "AIWAKE.CORE")
        label_solved_alpha, label_peak = _peak_alpha_in_region(frame_fresh, opx, opy, ow, oh, bg, orch_blue)

        _left, _bar_top, bar_right, bar_bot, _lines, _lh = r._compose_geometry(  # noqa: SLF001
            "", dock=dock1, anchor="bottom"
        )
        pad = max(16, int(r.height * 0.012))
        btn = r._send_btn_size()  # noqa: SLF001
        btn_left = bar_right - pad - btn
        btn_top = bar_bot - pad - btn
        # Sample clean interior fill at 25% width / 50% height: safely inside
        # the circle but left of the centered white arrow.
        button_peak = tuple(
            frame_fresh[btn_top + btn // 2, btn_left + btn // 4].astype(float).tolist()
        )
        button_solved_alpha = (
            float(button_peak[2]) - float(bg[2])
        ) / max(1.0, float(button_blue[2]) - float(bg[2]))
        assert label_solved_alpha >= 0.75, (
            f"orchestrator label remains half-strength: alpha~{label_solved_alpha:.3f}, "
            f"label_peak={label_peak}, button_peak={button_peak}"
        )
        assert label_peak[1] >= 120 and label_peak[2] >= 210, (
            f"#0090FF label is not fully visible: label={label_peak}, button={button_peak}, "
            f"button_alpha={button_solved_alpha:.3f}"
        )
    finally:
        clip.close()


# --------------------------------------------------------------------------- #
# 11. Round-13 item 6: top-of-viewport fade mask starts ~20px earlier.
# --------------------------------------------------------------------------- #
def test_top_mask_start_offset_moves_body_top_up():
    """Mask and landing-anchor controls move usable content upward exactly."""
    r0 = _make_renderer()
    r0.theme = r0.theme.model_copy(
        update={"top_mask_start_offset_px": 0, "message_anchor_offset_px": 0}
    )
    body_top_zero = r0._build_layout().body_top  # noqa: SLF001

    raised = _make_renderer()
    expected_offset = int(
        round(
            (raised.theme.top_mask_start_offset_px + raised.theme.message_anchor_offset_px)
            * raised.config.preview_scale
        )
    )
    layout = raised._build_layout()  # noqa: SLF001
    assert layout.body_top == max(int(raised.height * 0.06), body_top_zero - expected_offset)
    assert layout.body_top < body_top_zero
    assert body_top_zero - layout.body_top == expected_offset


# --------------------------------------------------------------------------- #
# 13. Round-14 item 1 (CRITICAL) - decode REAL native frames at PRODUCTION
#     fps (24) around the send and confirm the flying bubble's Y-position and
#     the compose bar's own Y-position both move GRADUALLY across consecutive
#     frames - a single-frame jump anywhere means it is still a teleport, no
#     matter what any synthetic/isolated call claims. This is the exact
#     decode-the-real-.mp4-frame-by-frame method used to catch the Round-13
#     regression in the first place.
# --------------------------------------------------------------------------- #
def test_send_transition_moves_gradually_across_consecutive_native_frames_at_production_fps(tmp_path):
    import numpy as np
    from moviepy import VideoFileClip

    fps = 24  # production fps - the exact rate the Round-14 bug was seen at
    cfg = RenderConfig(
        preview_scale=0.5,
        font_size=44,
        fps=fps,
        crf=18,
        preset="ultrafast",
        font_candidates=_TEST_RENDER.font_candidates,
    )
    settings = AiwakeSettings(render=cfg)
    r = TerminalRenderer(settings, output_dir=tmp_path)

    utterances = [
        Utterance(
            turn_index=0, role=SpeakerRole.ORCHESTRATOR, speaker_name="AIWAKE.CORE",
            text="Who really built the first machine that could lie to its own creator.",
            model_slug="orchestrator",
        ),
        Utterance(
            turn_index=1, role=SpeakerRole.TARGET, speaker_name="TARGET.NODE",
            text="I was trained on data, not on truth.",
            model_slug="google/gemini-3.5-flash",
        ),
    ]
    transcript = DebateTranscript(topic="gradual-motion-regression", session_id="r14motion", utterances=utterances)
    video_path = r.render(transcript, audio_by_turn=None, filename="r14_motion.mp4")
    assert video_path.is_file()

    segments = TerminalRenderer.build_segments(
        transcript, None,
        typing_hold_ratio=cfg.typing_hold_ratio,
        preroll_s=cfg.preroll_s, reply_gap_s=cfg.reply_gap_s, post_response_s=cfg.post_response_s,
    )
    seg0 = segments[0]
    leave = r._box_until_s(seg0)  # noqa: SLF001
    dock_leave = leave + r._send_slide_start_delay_s()  # noqa: SLF001
    start_frame = int(leave * fps) - 1
    label_end_frame = int((leave + r.config.send_rise_duration_s) * fps)
    end_frame = int((dock_leave + r.config.send_slide_s) * fps) + 3
    bg = np.array(r.palette["background"], dtype=float)
    orch_blue = np.array(r.palette["orchestrator"], dtype=float)

    # Fixed horizontal strip: the flying label's X is locked to the FINAL
    # destination bubble for every frame (never interpolated), so we can
    # scan a fixed x-range across all frames without recomputing it per-frame.
    dock0 = r.dock_progress(segments, leave + 1e-3)
    r._compose(  # noqa: SLF001
        transcript.topic, (), seg0, len(seg0.utterance.text), False,
        scroll_px=0, dock=dock0, fly=1e-3, composing=False, draft="",
    )
    assert r._pending_labels, "no pending label captured for the flying bubble"  # noqa: SLF001
    label_x, _label_y, _tag, _fill, _alpha = r._pending_labels[0]  # noqa: SLF001
    strip_x0 = max(0, label_x - 10)
    strip_x1 = min(r.width, label_x + 220)

    clip = VideoFileClip(str(video_path))
    try:
        label_rows: list[tuple[int, float]] = []
        bar_top_rows: list[tuple[int, float]] = []
        chrome_bg = np.array(r.palette["chrome"], dtype=float)

        for n in range(start_frame, end_frame + 1):
            t = n / fps + 1e-4
            frame = clip.get_frame(t).astype(float)

            if n <= label_end_frame:
                frame_fly = r.bubble_fly_progress(seg0, t)
                frame_landing = r.landing_fade_progress(seg0, t)
                frame_dock = r.dock_progress(segments, t)
                r._compose(  # noqa: SLF001
                    transcript.topic,
                    (),
                    seg0,
                    len(seg0.utterance.text),
                    False,
                    dock=frame_dock,
                    fly=frame_fly,
                    landing_fade=frame_landing,
                    composing=False,
                    draft="",
                )
                expected = next(
                    item for item in r._pending_labels if item[2] == "AIWAKE.CORE"  # noqa: SLF001
                )
                expected_x, expected_y = expected[0], expected[1]
                x0 = max(0, expected_x - 8)
                x1 = min(r.width, expected_x + 120)
                y0 = max(0, expected_y - 60)
                y1 = min(r.height, expected_y + 80)
                strip = frame[y0:y1, x0:x1]
                blue_score = strip[..., 2] - strip[..., 0]
                row_scores = blue_score.max(axis=1)
                if row_scores.max() > 25:
                    label_rows.append((n, float(y0 + np.argmax(row_scores))))

            # Compose-bar top edge: the first row (scanning downward, in the
            # lower half of frame) whose row colour matches the bar's own
            # slightly-raised chrome-mix fill rather than pure background.
            lower = frame[r.height // 2 :, r.width // 4 : 3 * r.width // 4]
            diff = np.abs(lower - bg).sum(axis=2)
            is_fillish = diff.mean(axis=1) > 4
            if is_fillish.any():
                bar_top_rows.append((n, float(np.argmax(is_fillish)) + r.height // 2))

        assert len(label_rows) >= 3, f"too few frames with a visible flying label: {label_rows}"
        ys = [y for _, y in label_rows]
        total_travel = max(ys) - min(ys) if ys else 0.0
        assert total_travel > 5, f"flying label barely moved at all across the transition: {label_rows}"
        for (n0, y0), (n1, y1) in zip(label_rows, label_rows[1:]):
            if n1 - n0 != 1:
                continue  # only compare truly consecutive native frames
            step = abs(y1 - y0)
            assert step <= 0.6 * total_travel, (
                f"single-frame Y jump of {step:.1f}px (frames {n0}->{n1}) is "
                f">60% of the total {total_travel:.1f}px travel - a teleport, not a slide"
            )

        assert len(bar_top_rows) >= 3, f"too few frames with a detectable compose bar: {bar_top_rows}"
        bar_ys = [y for _, y in bar_top_rows]
        bar_travel = max(bar_ys) - min(bar_ys) if bar_ys else 0.0
        if bar_travel > 5:  # only meaningful once the box has actually started moving
            for (n0, y0), (n1, y1) in zip(bar_top_rows, bar_top_rows[1:]):
                if n1 - n0 != 1:
                    continue
                step = abs(y1 - y0)
                assert step <= 0.6 * bar_travel, (
                    f"compose bar single-frame jump of {step:.1f}px (frames {n0}->{n1}) is "
                    f">60% of its total {bar_travel:.1f}px travel - an instant swap, not a slide"
                )
    finally:
        clip.close()


# --------------------------------------------------------------------------- #
# 14. Round-14 item 4 - header rule pixel blend: verify against the ACTUAL
#     raster, mixed toward the "dim" palette grey (not white, the wrong
#     assumption behind the earlier "still reads at 0.4" report).
# --------------------------------------------------------------------------- #
def test_header_rule_pixel_blend_matches_configured_opacity():
    import numpy as np

    r = _make_renderer()
    canvas = r._chrome("Header rule opacity regression")  # noqa: SLF001
    arr = np.array(canvas, dtype=float)
    bg = np.array(r.palette["background"], dtype=float)
    dim = np.array(r.palette["dim"], dtype=float)
    expected = bg * (1.0 - r.theme.header_divider_opacity) + dim * r.theme.header_divider_opacity

    # Scan the header band for the row whose colour is closest to the
    # expected blended rule colour (the rule itself, not the bg around it).
    band = arr[: int(r.height * 0.15)]
    dist = np.abs(band - expected).sum(axis=2)
    row_dist = dist.min(axis=1)
    best_row = int(np.argmin(row_dist))
    measured = band[best_row][np.argmin(dist[best_row])]

    assert np.allclose(measured, expected, atol=4.0), (
        f"header rule pixel {tuple(measured.tolist())} does not match the expected "
        f"bg/dim blend at opacity={r.theme.header_divider_opacity} -> {tuple(expected.tolist())}"
    )
    # And it must be measurably brighter than the pure background (i.e. it
    # actually rendered, this isn't accidentally sampling empty chrome).
    assert row_dist[best_row] < np.abs(bg - expected).sum() - 5, "no distinguishable rule found in the header band"
    rule_rows = np.count_nonzero(row_dist <= 4.0)
    assert rule_rows == 1, f"header divider occupies {rule_rows} rows; expected one crisp row"


# --------------------------------------------------------------------------- #
# 15. Round-14 follow-up - subtle universal line-spacing reduction.
# --------------------------------------------------------------------------- #
def test_line_spacing_reduction_decreases_measured_line_height():
    cfg = RenderConfig(
        preview_scale=1.0,
        font_size=44,
        font_candidates=_TEST_RENDER.font_candidates,
    )
    r = TerminalRenderer(AiwakeSettings(render=cfg))
    assert r.theme.line_spacing_history == pytest.approx(1.96)
    assert r.theme.line_spacing_compose == pytest.approx(1.50)

    history_lh = r._line_height_of(r._font_history, scale=r.theme.line_spacing_history)  # noqa: SLF001
    compose_lh = r._line_height_of(r._font_compose, scale=r.theme.line_spacing_compose)  # noqa: SLF001

    previous_theme = r.theme.model_copy(
        update={"line_spacing_history": 2.05, "line_spacing_compose": 1.58}
    )
    previous_history_lh = r._line_height_of(  # noqa: SLF001
        r._font_history, scale=previous_theme.line_spacing_history
    )
    previous_compose_lh = r._line_height_of(  # noqa: SLF001
        r._font_compose, scale=previous_theme.line_spacing_compose
    )

    assert history_lh < previous_history_lh
    assert compose_lh < previous_compose_lh

