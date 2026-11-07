"""The console's material: Hallmark's Coral theme at app-screen scope (ADR 0044).

Hallmark (github.com/Nutlope/hallmark, MIT) is a design skill; for a screen people work in
it lends a theme's palette, faces, radius and surfaces, never its layout. Every colour and
font below is a token declared once at `:root`; nothing past the token block names a raw
colour (Hallmark Floor 1). Coral is spent on one settled state - credited - and on focus,
nowhere else. Labels are sentence case and untracked; motion is state only, with a
reduced-motion fallback; every interactive element shows a focus ring at once.

Everything a screen interpolates into markup goes through `chip` or `escape`, so a value
from a case - including model-written text - can never become markup.
"""

from __future__ import annotations

from html import escape as _escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dispute_agent.ui.labels import Tone

FONTS = (
    "https://api.fontshare.com/v2/css?f[]=general-sans@400,500,600&display=swap",
    "https://fonts.googleapis.com/css2?family=Geist+Mono:wght@400&display=swap",
)

CSS = """
/* Hallmark · Coral (dealt: manual) · shape: app screen · scope: app · v2.0.0
 * critique: one object per screen (ticket, trail, decision, ledger); coral only on credited
 * and focus; tables before cards; no invented figures. */
:root {
  --color-paper: #f8f4f1; --color-paper-2: #f1ece8; --color-paper-3: #e8e1dd;
  --color-ink: #1d1614; --color-ink-2: #312926; --color-muted: #615955;
  --color-rule: #d9d3cf; --color-rule-2: #c7bfba;
  --color-cotton: #fdfbf8; --color-board: #1f1917; --color-board-type: #f6f3ef;
  --color-paid: #ba4a34; --color-focus: #bd3a2b; --color-accent-2: #bb3d31;
  --font-body: "General Sans", ui-sans-serif, system-ui, sans-serif;
  --font-figure: "Geist Mono", ui-monospace, monospace;
  --radius-card: 8px; --radius-chip: 6px; --rule-hair: 1px;
  --shadow-card: 0 1px 2px rgb(40 30 25 / 0.06);
  --dur-short: 120ms; --ease-out: cubic-bezier(0.2, 0.7, 0.2, 1);
  --space-xs: 0.25rem; --space-sm: 0.5rem; --space-md: 0.75rem;
}
html, body { overflow-x: clip; }
html, body, .stApp { font-family: var(--font-body); }
/* Inherit, never override: Streamlit's icons declare their own font, and a broad selector
   here once turned every nav icon into its name. */
button, input, textarea, select { font-family: inherit; }
h1, h2, h3 { font-family: var(--font-body); font-weight: 600; letter-spacing: -0.02em; color: var(--color-ink); }
code, pre, [data-testid="stMetricValue"], .fig { font-family: var(--font-figure); font-variant-numeric: tabular-nums; }
.block-container { max-width: 76rem; }
/* A figure is read, not admired: one step above body, never truncated. */
[data-testid="stMetricValue"] { font-size: 1.5rem; overflow-wrap: anywhere; }
[data-testid="stMetricValue"] > div { overflow: visible; text-overflow: clip; white-space: normal; }
a { text-decoration-color: var(--color-rule-2); }
a:hover { text-decoration-color: var(--color-accent-2); }
button, a, input, textarea, [role="tab"], [role="button"] {
  transition: transform var(--dur-short) var(--ease-out), opacity var(--dur-short) var(--ease-out);
}
button:focus-visible, a:focus-visible, input:focus-visible, textarea:focus-visible,
[role="tab"]:focus-visible, [role="button"]:focus-visible, [tabindex]:focus-visible {
  outline: 2px solid var(--color-focus); outline-offset: 2px; transition: none;
}
.chip {
  display: inline-block; padding: 0 var(--space-sm); border-radius: var(--radius-chip);
  border: var(--rule-hair) solid var(--color-rule-2); color: var(--color-ink-2);
  font-size: 0.875rem; line-height: 1.6; white-space: nowrap;
}
.chip--settled { border-color: var(--color-paid); color: var(--color-paid); font-weight: 600; }
.chip--attention { border-color: var(--color-ink); color: var(--color-ink); font-weight: 600; }
.chip--waiting { border-style: dashed; }
.chip--closed { color: var(--color-muted); }
.who {
  display: inline-block; min-width: 4.5rem; color: var(--color-muted); font-size: 0.875rem;
}
.who--ai, .who--rules { color: var(--color-ink); font-weight: 600; }
.muted { color: var(--color-muted); font-size: 0.875rem; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition: none !important; animation: none !important; }
}
"""


def stylesheet() -> str:
    """The token block and rules. Fonts load from `.streamlit/config.toml` (`FONTS` there):
    Streamlit strips `<link>` tags from injected HTML."""
    return f"<style>{CSS}</style>"


def escape(text: object) -> str:
    return _escape(str(text), quote=True)


def chip(label: str, tone: Tone) -> str:
    return f'<span class="chip chip--{tone.value}">{escape(label)}</span>'


def who(label: str) -> str:
    return f'<span class="who who--{escape(label.lower())}">{escape(label)}</span>'


__all__ = ["CSS", "FONTS", "chip", "escape", "stylesheet", "who"]
