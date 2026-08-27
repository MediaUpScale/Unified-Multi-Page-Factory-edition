# -*- coding: utf-8 -*-
"""Input contract for the freeform script brain.

Two entry points produce the same brief, so everything downstream of the writer
(judge gate, atmosphere, image generation, assemble) is identical either way:

  * ``WriterBrief.from_theme(...)``  — theme / subtheme, as today.
  * ``WriterBrief.from_quote(...)``  — a seed quote to develop into a script.

Nothing here decides line count, cadence, or shape. Those belong to the writer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

BriefMode = Literal["theme", "quote"]


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
        if self.mode == "quote":
            parts.append(
                "SEED IDEA — one line from another writer:\n"
                f"  \u201c{self.seed_quote}\u201d\n"
                "Develop what is true in it into your own piece. Do not quote it, "
                "do not paraphrase it as a line, do not name or gesture at whoever "
                "said it. It is the thought you are arguing with or extending, not "
                "material to reuse. If the seed is abstract, find the specific human "
                "situation underneath it and write that instead."
            )
            if self.theme:
                parts.append(f"It should land somewhere near: {self.theme.replace('_', ' ')}")
        else:
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
