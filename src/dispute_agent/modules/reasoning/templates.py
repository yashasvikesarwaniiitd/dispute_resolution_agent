"""The `neutralise` filter and the prompt Jinja environment (prompts/README.md, threat T01, I5).

Seller-written text reaches a model only inside a delimited block. The delimiter is only as
strong as the guarantee that the text inside cannot close it, and that guarantee lives here:
`neutralise` defuses anything in untrusted text that looks like a tag, so a seller cannot
write a closing tag for the block they are in and then speak as the system.

Two things it does, both from the prompts/README contract:

1. **Every tag-like sequence loses its markup.** Every `<` that could open a tag becomes
   `&lt;`, for every tag name, not only the reserved ones, after look-alike characters are
   normalised to the plain `<`. The text
   stays readable to the model as literal text - it can still see that the seller *tried* -
   but it can no longer be mistaken for structure.
2. **Invisible characters are removed.** Zero-width and bidirectional-override characters
   are a way to hide markup from a human reviewer while the model still reads it.

This is the first mitigation for threat T01 and has a test per attack shape rather than
relying on the prompt's instructions to the model.
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, StrictUndefined

if TYPE_CHECKING:
    from pathlib import Path

#: Any `<` that could begin markup: followed (after optional whitespace) by `/`, `!`, `?`
#: or a name character. Matching the opening character alone, rather than a whole tag, is
#: deliberate: a whole-tag pattern can be made to swallow a real tag behind a malformed one
#: (`</a </untrusted_evidence>` - Phase 4P safety review, H1), and escaping only what a
#: pattern spans leaves the rest live. This is wider than prompts/README's tag pattern, so
#: stricter; a `<` before a digit or a space-then-digit ("2 < 3") still reads normally.
TAG_LIKE = re.compile(r"<(?=\s*[/!?A-Za-z_])")

#: Zero-width, bidirectional and invisible formatting characters that can hide markup.
#: U+2066-2069 (bidi isolates, "Trojan Source") go beyond the prompts/README list: stricter.
INVISIBLE = re.compile(
    "["
    + "".join(
        f"{chr(lo)}-{chr(hi)}"
        for lo, hi in (
            (0x200B, 0x200F),
            (0x202A, 0x202E),
            (0x2060, 0x2064),
            (0x2066, 0x2069),
            (0xFEFF, 0xFEFF),
        )
    )
    + "]"
)


def neutralise(value: object) -> str:
    """Untrusted text, made safe to place inside a delimited block.

    Accepts any value and renders it as text, because a filter that raised on an unexpected
    type would fail the whole render - and a failed render on a hostile input is the input
    winning.
    """
    # NFKC first, so look-alikes such as the full-width < (U+FF1C) or the small < (U+FE64)
    # become the plain character and are then defused like it.
    text = unicodedata.normalize("NFKC", "" if value is None else str(value))
    text = INVISIBLE.sub("", text)
    return TAG_LIKE.sub("&lt;", text)


def make_environment(root: Path) -> Environment:
    """The one Jinja environment prompts are rendered with.

    `StrictUndefined`: a briefing missing a field stops the render instead of leaving a
    blank where evidence should be. `autoescape=False`: this is a plain-text prompt, not
    HTML, and untrusted values are made safe explicitly with `neutralise`, which says what
    it is doing, rather than by a general escaper that would also mangle trusted JSON.
    """
    env = Environment(
        loader=FileSystemLoader(root),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )
    env.filters["neutralise"] = neutralise
    return env


__all__ = ["INVISIBLE", "TAG_LIKE", "make_environment", "neutralise"]
