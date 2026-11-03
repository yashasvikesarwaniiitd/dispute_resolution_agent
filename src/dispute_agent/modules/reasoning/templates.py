"""The `neutralise` filter and the prompt Jinja environment (prompts/README.md, threat T01, I5).

Seller-written text reaches a model only inside a delimited block. The delimiter is only as
strong as the guarantee that the text inside cannot close it, and that guarantee lives here:
`neutralise` defuses anything in untrusted text that looks like a tag, so a seller cannot
write a closing tag for the block they are in and then speak as the system.

Two things it does, both from the prompts/README contract:

1. **Every tag-like sequence loses its markup.** `<` becomes `&lt;` in any sequence shaped
   like an opening or closing tag, for every tag name, not only the reserved ones. The text
   stays readable to the model as literal text - it can still see that the seller *tried* -
   but it can no longer be mistaken for structure.
2. **Invisible characters are removed.** Zero-width and bidirectional-override characters
   are a way to hide markup from a human reviewer while the model still reads it.

This is the first mitigation for threat T01 and has a test per attack shape rather than
relying on the prompt's instructions to the model.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, StrictUndefined

if TYPE_CHECKING:
    from pathlib import Path

#: Anything shaped like a tag, opening or closing, any name (prompts/README.md).
TAG_LIKE = re.compile(r"</?[A-Za-z_][A-Za-z0-9_-]*\s*[^>]*>")

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
    text = INVISIBLE.sub("", "" if value is None else str(value))
    return TAG_LIKE.sub(lambda match: "&lt;" + match.group()[1:], text)


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
