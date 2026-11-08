"""The console's material and its smallest parts (design handoff; ADR 0044, ADR 0048).

The stylesheet lives in `assets/console.css`: the handoff's tokens on `:root`, then one class
per component (chip, actor pill, stepper, timeline step, recorded text, card, filter). This
module loads it and builds the small parts every screen shares.

**Everything interpolated goes through `escape`.** A value from a case - a shipment
reference, a seller's words, the investigator's rationale - can never become markup (I5).
Recorded text is shown as escaped plain text in a quote block labelled "exactly as recorded";
it is never rendered as markdown or HTML.
"""

from __future__ import annotations

from html import escape as _escape
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from dispute_agent.ui.labels import Tone

#: The credited colour. One value: the CSS token and the ledger's credit cells both read it.
PAID = "#ba4a34"
CSS_FILE = Path(__file__).parent / "assets" / "console.css"

#: Who did a step, as a reader names them, and the pill style for each (HANDOFF 3.2).
ACTOR_STYLE = {
    "Code": "code",
    "AI": "ai",
    "Rules engine": "rules",
    "Reviewer": "person",
    "Seller": "seller",
    "Hub": "hub",
    "System": "system",
}


def stylesheet() -> str:
    """The whole stylesheet as a style element. Fonts load from `.streamlit/config.toml`:
    Streamlit strips `<link>` tags from injected HTML."""
    return "<style>" + CSS_FILE.read_text(encoding="utf-8").replace("__PAID__", PAID) + "</style>"


def escape(text: object) -> str:
    return _escape(str(text), quote=True)


def text(value: str) -> str:
    """Escaped, with its line breaks kept: recorded text shown as written, never as markup."""
    return escape(value).replace("\n", "<br>")


def chip(label: str, tone: Tone) -> str:
    """A status chip: always the state's words, styled by its tone (HANDOFF 3.1)."""
    return f'<span class="chip chip--{tone.value}">{escape(label)}</span>'


def actor(name: str) -> str:
    """Who did a step: a pill with a shape, so the actor reads without colour (HANDOFF 3.2)."""
    return f'<span class="actor actor--{ACTOR_STYLE.get(name, "system")}">{escape(name)}</span>'


def pairs(rows: Iterable[tuple[str, str]], *, numeric: bool = False) -> str:
    """A definition list: label on the left, value on the right (right-aligned figures)."""
    cell = ' class="num"' if numeric else ""
    body = "".join(f"<dt>{escape(k)}</dt><dd{cell}>{escape(v)}</dd>" for k, v in rows)
    return f'<dl class="pairs">{body}</dl>'


def recorded(caption: str, value: str) -> str:
    """Recorded text - the investigator's reasoning, a seller's words, a review detail - as a
    captioned quote of escaped plain text (HANDOFF 3.5, I5)."""
    # pre-wrap keeps the text's own line breaks; written as entities, because a blank line
    # inside Markdown would end the HTML block and let the rest be read as Markdown.
    kept = escape(value).replace("\r\n", "\n").replace("\n", "&#10;")
    return (
        f'<figure class="recorded"><figcaption>{escape(caption)}</figcaption>'
        f"<blockquote>{kept}</blockquote></figure>"
    )


def line(message: str, *, error: bool = False) -> str:
    """A result said in one line, not a boxed alert; read out politely to screen readers."""
    css = "line line--error" if error else "line"
    return f'<p class="{css}" role="status" aria-live="polite">{escape(message)}</p>'


__all__ = [
    "ACTOR_STYLE",
    "PAID",
    "actor",
    "chip",
    "escape",
    "line",
    "pairs",
    "recorded",
    "stylesheet",
    "text",
]
