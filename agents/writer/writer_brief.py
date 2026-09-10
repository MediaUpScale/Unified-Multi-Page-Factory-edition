# -*- coding: utf-8 -*-
"""Input contract for the freeform script brain.

Two entry points produce the same brief, so everything downstream of the writer
(judge gate, atmosphere, image generation, assemble) is identical either way:

  * ``WriterBrief.from_theme(...)``  — theme / subtheme, as today.
  * ``WriterBrief.from_quote(...)``  — a seed quote to develop into a script.

Nothing here decides line count, cadence, or shape. Those belong to the writer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

BriefMode = Literal["emotional", "theme", "quote", "paraphrase"]


@dataclass(frozen=True)
class WriterBrief:
    """What the writer is being asked to make. Not how to make it."""

    mode: BriefMode
    module: str = "relationship"
    theme: str = ""
    subtheme: str = ""
    seed_quote: str = ""
    seed_attribution: str = ""
    # Free text the caller wants in front of the writer (concrete details from
    # the theme bank, a producer note, a subject the episode must touch).
    context_notes: tuple[str, ...] = ()
    # Openings / images already used this batch, so drafts don't converge.
    avoid: tuple[str, ...] = ()
    # Judge feedback from a rejected attempt. Empty on the first pass.
    revision_note: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_theme(
        cls,
        *,
        theme: str,
        subtheme: str = "",
        module: str = "relationship",
        context_notes: list[str] | tuple[str, ...] | None = None,
        avoid: list[str] | tuple[str, ...] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> WriterBrief:
        t = str(theme or "").strip()
        if not t:
            raise ValueError("from_theme requires a theme")
        return cls(
            mode="theme",
            module=str(module or "relationship").strip() or "relationship",
            theme=t,
            subtheme=str(subtheme or "").strip(),
            context_notes=tuple(str(x).strip() for x in (context_notes or ()) if str(x).strip()),
            avoid=tuple(str(x).strip() for x in (avoid or ()) if str(x).strip()),
            meta=dict(meta or {}),
        )

    @classmethod
    def from_quote(
        cls,
        *,
        quote: str,
        attribution: str = "",
        theme: str = "",
        module: str = "relationship",
        context_notes: list[str] | tuple[str, ...] | None = None,
        avoid: list[str] | tuple[str, ...] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> WriterBrief:
        """Seed a script from one line by a writer or philosopher.

        The quote is a starting point to develop, not something to recite. The
        attribution is carried in metadata only — the script never names it.
        """
        q = str(quote or "").strip()
        if not q:
            raise ValueError("from_quote requires a quote")
        return cls(
            mode="quote",
            module=str(module or "relationship").strip() or "relationship",
            theme=str(theme or "").strip(),
            seed_quote=q,
            seed_attribution=str(attribution or "").strip(),
            context_notes=tuple(str(x).strip() for x in (context_notes or ()) if str(x).strip()),
            avoid=tuple(str(x).strip() for x in (avoid or ()) if str(x).strip()),
            meta=dict(meta or {}),
        )

    @classmethod
    def from_emotional(
        cls,
        *,
        theme: str,
        subtheme: str = "",
        module: str = "relationship",
        context_notes: list[str] | tuple[str, ...] | None = None,
        avoid: list[str] | tuple[str, ...] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> WriterBrief:
        """Request an original emotional reflection, unconstrained by literal visuals."""
        t = str(theme or "").strip() or "emotional maturity"
        return cls(
            mode="emotional",
            module=str(module or "relationship").strip() or "relationship",
            theme=t,
            subtheme=str(subtheme or "").strip(),
            context_notes=tuple(str(x).strip() for x in (context_notes or ()) if str(x).strip()),
            avoid=tuple(str(x).strip() for x in (avoid or ()) if str(x).strip()),
            meta=dict(meta or {}),
        )

    @classmethod
    def from_paraphrase(
        cls,
        *,
        aphorism: str,
        theme: str = "",
        module: str = "relationship",
        source_id: str = "",
        meta: dict[str, Any] | None = None,
    ) -> WriterBrief:
        """Request a light structural paraphrase of one complete aphorism."""
        text = str(aphorism or "").strip()
        if not text:
            raise ValueError("from_paraphrase requires an aphorism")
        details = dict(meta or {})
        if source_id:
            details["aphorism_id"] = source_id
        return cls(
            mode="paraphrase",
            module=str(module or "relationship").strip() or "relationship",
            theme=str(theme or "").strip(),
            seed_quote=text,
            meta=details,
        )

    def with_revision(self, note: str) -> WriterBrief:
        """Same assignment, carrying the judge's reason the last draft failed."""
        from dataclasses import replace

        return replace(self, revision_note=str(note or "").strip())

    @property
    def label(self) -> str:
        if self.mode == "quote":
            head = self.seed_quote[:48].rstrip()
            return f"quote:{head}…" if len(self.seed_quote) > 48 else f"quote:{head}"
        return f"{self.theme}/{self.subtheme}" if self.subtheme else self.theme

    def assignment_block(self) -> str:
        """The part of the writer prompt that changes per episode."""
        parts: list[str] = []
        from core.economic_reel_lofi import config as lofi_cfg

        if self.mode == "quote":
            parts.append(lofi_cfg.hook_line_brevity_clause())
            parts.append(
                "SEED IDEA — one line from another writer:\n"
                f"  \u201c{self.seed_quote}\u201d\n"
                "Develop what is true in it into your own piece. Do not quote it, "
                "do not paraphrase it as a line, do not name or gesture at whoever "
                "said it. It is the thought you are arguing with or extending, not "
                "material to reuse. If the seed is abstract, find the specific human "
                "situation underneath it and write that instead.\n"
                "PARABLE ARC: carry one concrete parable through four ordered "
                "movements across the beats: (1) concept/setup, (2) contact creates "
                "conflict or pain, (3) retreat creates loneliness or cost, "
                "(4) a workable equilibrium that preserves connection without "
                "repeating the harm. Each movement must change the situation; do "
                "not flatten the seed into repeated commentary. End with a direct, "
                "usable instruction the listener can act on; never end with "
                "\"that's what love/healing/lasting looks like.\""
            )
            if self.theme:
                parts.append(f"It should land somewhere near: {self.theme.replace('_', ' ')}")
        elif self.mode == "paraphrase":
            source_structure = str(
                (self.meta or {}).get("source_structure") or ""
            ).lower()
            starts = [
                word.lower()
                for word in re.findall(
                    r"(?:^|[.!?]\s+)([A-Za-z']+)",
                    str(self.seed_quote or ""),
                )
            ]
            repeated_opening = (
                max((starts.count(word) for word in set(starts)), default=0) >= 3
            )
            if "anaphora" in source_structure or repeated_opening:
                variation_rule = (
                    "This source is ANAPHORA: its repeated opening and repeated "
                    "closing refrain are protected. Keep every parallel repeat "
                    "as its own sentence; do not merge or split those sentences. "
                    "Preserve the repeated opening and refrain count. Create "
                    "structural variation inside exactly 1–2 sentences by "
                    "reordering clause elements or changing word order/tense. "
                    "Never join two repeats with 'or'."
                )
            else:
                variation_rule = (
                    "Make exactly one sentence-level structural change: either "
                    "merge one adjacent pair of short sentences into one, OR split "
                    "one sentence into two. Do not do both. Do not keep a 1:1 "
                    "sentence mirror."
                )
            parts.append(lofi_cfg.hook_line_brevity_clause(paraphrase=True))
            parts.append(
                "SOURCE APHORISM:\n"
                f"  \u201c{self.seed_quote}\u201d\n"
                "Make one light rewording only. Preserve the progression, repeated "
                "refrain, approximate total word count, rhythm, and meaning. "
                f"Substitute roughly 20–30% of the words. {variation_rule} "
                "Do not expand "
                "it into a story, add an arc, add examples, explain it, or improve "
                "its argument. Return a genuine variation, not a new composition."
            )
        elif self.mode == "emotional":
            parts.append(lofi_cfg.hook_line_brevity_clause())
            parts.append(
                "SCENE 1 RETENTION HOOK: strictly 5–7 punchy, high-retention "
                "words, fewer than 45 characters, written to finish speaking "
                "under 2.8 seconds."
            )
            niche = str(self.module or "relationship").strip().lower()
            if niche == "parenting":
                reflection = (
                    "Write intimate micro-philosophical prose about presence, "
                    "childhood time slipping away, quiet love, and the ordinary "
                    "moments a parent only recognizes as sacred later. Use "
                    "psychological depth without lectures, shame, or productivity "
                    "advice. Let each beat feel discovered, not announced. "
                    "Concrete details may ground the reflection, but do not force "
                    "an object merely to supply an image. Beat 1 may use a verified "
                    "public literary quote anchor or a striking behavioral truth; "
                    "never invent an attribution. Move toward a mature truth the "
                    "listener can carry, without reducing the ending to advice."
                )
            else:
                reflection = (
                    "Write intimate micro-philosophical prose about what remains "
                    "unspoken: silence, boundaries, heartbreak, detachment, longing, "
                    "or emotional maturity. Use psychological depth, moral tension, "
                    "and lucid existential observation. Let each beat feel discovered, "
                    "not announced. Concrete details may ground the reflection, but "
                    "do not force an object, room, or action merely to supply an image. "
                    "Avoid therapy slogans, motivational certainty, melodrama, ornate "
                    "purple prose, and imitation of any named author. Beat 1 may use "
                    "a verified public literary quote anchor or a striking behavioral "
                    "truth; never invent an attribution. "
                    "Move from wound or contradiction toward a mature truth the "
                    "listener can carry, without reducing the ending to advice. "
                    "Visual beats follow the vintage risograph formula: intimate "
                    "golden-hour interior, doorway silhouette against a massive "
                    "sunset disc, one graphic isolated object, ink-hatched profile, "
                    "warm hallway, rain through amber lamplight, dusk street or "
                    "tracks, then a couple walking a narrow alley or a figure "
                    "stepping into morning sun."
                )
            parts.append(
                "EMOTIONAL REFLECTION:\n"
                f"  Niche: {niche}\n"
                f"  Theme: {self.theme.replace('_', ' ')}\n"
                + (
                    f"  Angle: {self.subtheme.replace('_', ' ')}\n"
                    if self.subtheme
                    else ""
                )
                + reflection
            )
        else:
            parts.append(lofi_cfg.hook_line_brevity_clause())
            parts.append(f"SUBJECT: {self.theme.replace('_', ' ')}")
            if self.subtheme:
                parts.append(f"NARROWER ANGLE: {self.subtheme.replace('_', ' ')}")
        if self.context_notes:
            joined = "\n".join(f"  - {n}" for n in self.context_notes)
            parts.append(
                "CONTEXT (raw material you may ignore entirely — never quote it "
                f"verbatim):\n{joined}"
            )
        if self.avoid:
            joined = "\n".join(f"  - {n}" for n in self.avoid)
            parts.append(
                "ALREADY USED in this batch — do not repeat these openings, images, "
                f"or moves:\n{joined}"
            )
        if self.revision_note:
            parts.append(
                "THE LAST DRAFT WAS REJECTED. Do not patch it — throw it out and "
                "write a different piece. Here is why it failed:\n"
                f"{self.revision_note}"
            )
        return "\n\n".join(parts)
