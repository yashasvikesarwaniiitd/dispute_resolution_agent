"""The console's material: Hallmark's Coral theme at app-screen scope (ADR 0044; the redesign
record is docs/design/REDESIGN_HALLMARK.md).

Hallmark (github.com/Nutlope/hallmark, MIT) is a design skill; for a screen people work in
it lends a theme's palette, faces, radius and surfaces, never its layout. Every colour, font,
space, duration and easing below is a token declared once at `:root`; nothing past the token
block names a raw value (gates 24a, 48). Coral is spent on one settled state - credited - and
on focus. The type scale is fixed at 1.2 with no display heads (flow.md, App screens).

Streamlit draws its own widgets, so the controls are reached through their data-testid and
Base Web hooks: the eight states (default, hover, focus, active, disabled, loading, error,
success) are set here, where Streamlit lets CSS reach them. `.streamlit/config.toml` repeats
the palette and faces, because Streamlit's own theme cannot read CSS tokens.

Everything a screen interpolates into markup goes through `chip`, `who` or `escape`, so a
value from a case - including model-written text - can never become markup.
"""

from __future__ import annotations

from html import escape as _escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dispute_agent.ui.labels import Tone

#: The settled (credited) colour. One value: the CSS token below and the ledger's credited row
#: both read it, so no colour literal lives outside the token block (gate 48).
PAID = "#ba4a34"

CSS = """
/* Hallmark v2.0.0 · theme: Coral (by request) · shape: app screen · scope: app · redesign: full
 * critique: one object per screen (the bill, the case trail, the decision, the ledger); coral
 * spent only on credited and focus; fixed 1.2 scale, no display heads; tables before cards;
 * every region drawn empty, error and full; no invented figure.
 * waive 17: help-tooltip delays are Base Web's own and cannot be set from CSS.
 * waive 39a: Base Web inputs draw focus with their own border; the ring is added as an outline.
 * waive 69: Agent steps shows the latest figures on top and each round's own recalculation in
 *   its step; rounds differ after a re-measure, so the repeat carries a fact per round.
 * waive 39b: controls are 40px; Streamlit's widget gaps give the hit area, and a ::before
 *   expansion would overlap the neighbouring control in a column row.
 * answered F4: Review holds one filled primary per card; each card is its own decision.
 * answered F23: lang and the meta description belong to Streamlit's page shell.
 * note 48: .streamlit/config.toml repeats these values; Streamlit's theme cannot read tokens.
 * finish-review: subagent · 8 findings · 7 resolved · 1 not a defect (nav capture timing)
 *   · gap: the status chip repeats the headline's first words; figures stack tall on a phone. */
:root {
  --color-paper: #f8f4f1; --color-paper-2: #f1ece8; --color-paper-3: #e8e1dd;
  --color-ink: #1d1614; --color-ink-2: #312926; --color-muted: #615955;
  --color-rule: #d9d3cf; --color-rule-2: #c7bfba;
  --color-cotton: #fdfbf8; --color-board: #1f1917; --color-board-type: #f6f3ef;
  --color-paid: __PAID__; --color-focus: #bd3a2b; --color-accent-2: #bb3d31;
  --color-error: #8a2416;
  --font-body: "General Sans", ui-sans-serif, system-ui, sans-serif;
  --font-figure: "Geist Mono", ui-monospace, monospace;
  --text-sm: 0.875rem; --text-md: 1rem; --text-lg: 1.2rem; --text-xl: 1.44rem; --text-2xl: 1.728rem;
  --leading: 1.55; --leading-tight: 1.2;
  --space-3xs: 2px; --space-2xs: 4px; --space-xs: 8px; --space-sm: 12px; --space-md: 16px;
  --space-lg: 24px; --space-xl: 40px; --space-2xl: 64px;
  --radius-card: 8px; --radius-control: 6px; --rule-hair: 1px; --control-h: 40px;
  --shade: 32% 0.03 50; --lit: 100% 0.005 80;
  --edge-stock: inset 0 1px 0 oklch(var(--lit) / 0.9), inset 0 -1px 0 oklch(var(--shade) / 0.07),
    0 0 0 0.5px oklch(var(--shade) / 0.22), 0 1px 0 oklch(var(--shade) / 0.12);
  --cast-stock: 6px 5px 10px -6px oklch(var(--shade) / 0.3);
  --cast-stock-lift: 12px 10px 18px -8px oklch(var(--shade) / 0.3);
  --dur-micro: 120ms; --dur-short: 220ms; --ease-out: cubic-bezier(0.2, 0.7, 0.2, 1);
  --measure: 70ch; --container: 76rem;
}
html, body { overflow-x: clip; }
html, body, .stApp { font-family: var(--font-body); font-synthesis: none; color: var(--color-ink); }
button, input, textarea, select { font-family: inherit; }
::selection { background: var(--color-paper-3); color: var(--color-ink); }
input, textarea { caret-color: var(--color-ink); }
html { scrollbar-color: var(--color-rule-2) var(--color-paper); }

/* Type: a fixed 1.2 scale. The screen title is a page head, never a display head. */
.stApp h1 { font-size: var(--text-2xl); font-weight: 600; letter-spacing: -0.01em;
  line-height: var(--leading-tight); margin: 0 0 var(--space-2xs); padding: 0; }
.stApp h2 { font-size: var(--text-xl); font-weight: 600; line-height: var(--leading-tight); }
.stApp h3 { font-size: var(--text-lg); font-weight: 600; line-height: var(--leading-tight);
  margin-top: var(--space-lg); }
.stApp p, .stApp li { line-height: var(--leading); }
/* Streamlit fades captions to 60%, which drops muted ink under 4.5:1 (gate 40). */
.stApp [data-testid="stCaptionContainer"] { font-size: var(--text-sm); color: var(--color-muted); opacity: 1; }
.stApp [data-testid="stCaptionContainer"] p { color: var(--color-muted); }
/* A field below a page title sits clear of it (gate 71). */
.stApp [data-testid="stHeading"]:has(h1) { margin-bottom: var(--space-md); }
/* Clear Streamlit's fixed top bar (gate 73: nothing painted under another element). */
.block-container { max-width: var(--container); padding-top: calc(var(--space-2xl) + var(--space-md)); }
code, pre, .fig, [data-testid="stMetricValue"] { font-family: var(--font-figure);
  font-variant-numeric: tabular-nums; }
a { text-decoration-color: var(--color-rule-2); text-underline-offset: 2px; }
a:hover { text-decoration-color: var(--color-accent-2); }

/* Controls: 40px, one hover signal, an instant ring, a 1px press, a three-channel disabled. */
button[data-testid^="stBaseButton"] { min-height: var(--control-h); border-radius: var(--radius-control);
  font-weight: 500; white-space: nowrap;
  transition: transform var(--dur-micro) var(--ease-out), background-color var(--dur-micro) var(--ease-out); }
@media (hover: hover) {
  button[data-testid^="stBaseButton"]:hover:not(:disabled) { transform: translateY(-1px); }
}
button[data-testid^="stBaseButton"]:active:not(:disabled) { transform: translateY(1px); }
button[data-testid^="stBaseButton"]:disabled { opacity: 0.55; cursor: not-allowed; }
button[data-testid="stBaseButton-primary"], button[data-testid="stBaseButton-primaryFormSubmit"] {
  background: var(--color-board); color: var(--color-board-type); border: 0;
  box-shadow: var(--cast-stock); text-shadow: 0 -0.75px 0 var(--color-ink); }
button[data-testid="stBaseButton-secondary"], button[data-testid="stBaseButton-secondaryFormSubmit"] {
  background: var(--color-cotton); color: var(--color-ink); border: 0;
  box-shadow: var(--edge-stock), var(--cast-stock); }
button:focus-visible, a:focus-visible, [role="tab"]:focus-visible, summary:focus-visible,
[tabindex]:focus-visible, input:focus-visible, textarea:focus-visible {
  outline: 2px solid var(--color-focus); outline-offset: 2px; transition: none; }
[data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="select"] > div {
  border-color: var(--color-muted) !important; border-radius: var(--radius-control) !important; }
[data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within,
[data-baseweb="select"] > div:focus-within {
  outline: 2px solid var(--color-focus); outline-offset: 1px; transition: none; }
[data-baseweb="tab"] { white-space: nowrap; font-size: var(--text-md); }

/* Figures that compare: a label and a tabular figure, parted by hairlines. */
[data-testid="stMetric"] { padding: 0 var(--space-md); border-left: var(--rule-hair) solid var(--color-rule); }
[data-testid="stColumn"]:first-child [data-testid="stMetric"] { border-left: 0; padding-left: 0; }
[data-testid="stMetricLabel"] p { font-size: var(--text-sm); color: var(--color-muted); }
[data-testid="stMetricValue"] { font-size: var(--text-xl); font-weight: 400; }
[data-testid="stMetricValue"] > div { overflow: visible; text-overflow: clip; white-space: normal; }
/* Restacked on a phone, the hairline moves from the side to the top. */
@media (max-width: 640px) {
  [data-testid="stMetric"] { border-left: 0; padding: var(--space-xs) 0 0;
    border-top: var(--rule-hair) solid var(--color-rule); }
  [data-testid="stColumn"]:first-child [data-testid="stMetric"] { border-top: 0; padding-top: 0; }
}

/* Notices in the system's own words and colours, never a framework swatch (gate 74). */
[data-testid="stAlert"] [role="alert"], [data-testid="stAlertContainer"] {
  background: var(--color-paper-2) !important; color: var(--color-ink) !important;
  border: var(--rule-hair) solid var(--color-rule-2); border-radius: var(--radius-card); }
[data-testid="stAlertContentError"], [data-testid="stAlertContentError"] p { color: var(--color-error) !important; }
[data-testid="stAlertContentSuccess"], [data-testid="stAlertContentSuccess"] p,
[data-testid="stAlertContentInfo"], [data-testid="stAlertContentInfo"] p { color: var(--color-ink) !important; }
[data-testid="stExpander"] details { border-color: var(--color-rule); border-radius: var(--radius-card); }
[data-testid="stExpander"] summary p { font-size: var(--text-sm); color: var(--color-ink-2); }

/* Coral stock: the bill, the case card and each decision card. Keyed containers (st-key-stock*). */
[class*="st-key-stock"] { background: var(--color-cotton); border: 0 !important;
  border-radius: var(--radius-card); box-shadow: var(--edge-stock), var(--cast-stock); }
.rule { border: 0; border-top: var(--rule-hair) solid var(--color-rule); margin: var(--space-sm) 0; }

/* The console's own objects. */
.chip { display: inline-block; padding: 0 var(--space-xs); border-radius: var(--radius-control);
  border: var(--rule-hair) solid var(--color-rule-2); color: var(--color-ink-2);
  font-size: var(--text-sm); line-height: 1.6; white-space: nowrap; }
.chip--settled { border-color: var(--color-paid); color: var(--color-paid); font-weight: 600; }
.chip--attention { border-color: var(--color-ink); color: var(--color-ink); font-weight: 600; }
.chip--waiting { border-style: dashed; }
.chip--closed { color: var(--color-muted); }
.who { display: inline-block; min-width: 4.5rem; color: var(--color-muted); font-size: var(--text-sm); }
.who--ai, .who--rules, .who--person { color: var(--color-ink); font-weight: 600; }
.muted { color: var(--color-muted); font-size: var(--text-sm); margin: 0; }
.figure { font-family: var(--font-figure); font-variant-numeric: tabular-nums; font-size: var(--text-xl);
  color: var(--color-ink); line-height: var(--leading-tight); margin: var(--space-2xs) 0 var(--space-xs); }
.pair { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: var(--space-2xs) var(--space-md);
  margin: var(--space-xs) 0 var(--space-md); font-size: var(--text-sm); }
.pair dt { color: var(--color-muted); }
.pair dd { margin: 0; color: var(--color-ink); }
.headline { font-size: var(--text-xl); font-weight: 600; color: var(--color-ink);
  line-height: var(--leading-tight); margin: var(--space-xs) 0 0; }
.headline .chip { vertical-align: middle; margin-right: var(--space-xs); }
.meta { margin: var(--space-sm) 0 0; }
.next { color: var(--color-ink-2); margin: var(--space-2xs) 0 var(--space-sm); }
/* Agent steps: the case's name line and facts line. */
.case-head { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-xs) var(--space-sm); margin: 0; }
.case-ref { font-family: var(--font-figure); font-variant-numeric: tabular-nums; font-size: var(--text-lg);
  font-weight: 600; color: var(--color-ink); }
.case-facts { margin: var(--space-2xs) 0 var(--space-md); overflow-wrap: anywhere; }
.stands { margin: var(--space-xs) 0 var(--space-sm); font-size: var(--text-md); max-width: var(--measure); }
.stands dt { font-weight: 500; }

/* Progress: numbered marks on a line; each mark says its state in words, never colour alone. */
.stepper { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: var(--space-xs);
  list-style: none; margin: var(--space-md) 0 var(--space-2xs); padding: 0; counter-reset: step; }
.stepper > li.step { margin: 0; position: relative; padding-top: calc(22px + var(--space-xs)); color: var(--color-muted);
  line-height: 1.3; counter-increment: step; min-width: 0; }
.step::before { content: counter(step); position: absolute; top: 0; left: 0; width: 22px; height: 22px;
  border-radius: 50%; box-sizing: border-box; border: 1.5px solid var(--color-rule-2);
  background: var(--color-cotton); display: grid; place-items: center;
  font-size: 0.75rem; font-weight: 600; color: var(--color-muted); z-index: 1; }
.step::after { content: ""; position: absolute; top: 10px; left: 30px; right: var(--space-2xs);
  height: 2px; background: var(--color-rule); }
.step:last-child::after { display: none; }
.step b { display: block; font-weight: 500; color: inherit; }
.step small { display: block; font-size: var(--text-sm); }
.step--done { color: var(--color-ink-2); }
.step--done::before { content: "✓"; background: var(--color-ink-2); border-color: var(--color-ink-2);
  color: var(--color-board-type); }
.step--done::after { background: var(--color-ink-2); }
.step--current, .step--person, .step--waiting { color: var(--color-ink); }
.step--current b, .step--person b, .step--waiting b { font-weight: 600; }
.step--current::before, .step--person::before { border: 2px solid var(--color-ink); color: var(--color-ink); }
.step--waiting::before { border: 2px dashed var(--color-ink); color: var(--color-ink); }
.step--skipped small, .step--not_reached small { font-style: italic; }
.step--settled { color: var(--color-paid); }
.step--settled::before { content: "✓"; background: var(--color-paid); border-color: var(--color-paid);
  color: var(--color-board-type); }

/* Code's figures: a label over a tabular figure, in a row that wraps rather than stacks tall. */
.figs { display: grid; grid-template-columns: repeat(auto-fit, minmax(8.5rem, 1fr)); gap: var(--space-sm) var(--space-lg);
  margin: var(--space-md) 0 0; padding-top: var(--space-md); border-top: var(--rule-hair) solid var(--color-rule); }
.figs dt { font-size: var(--text-sm); color: var(--color-muted); }
.figs dd { margin: 0; font-family: var(--font-figure); font-variant-numeric: tabular-nums;
  font-size: var(--text-lg); color: var(--color-ink); }

/* What happened: a timeline. A mark and a line on the left; who and when on the right. */
.tl-round { margin: var(--space-md) 0 0; }
.tl-head { margin: 0 0 var(--space-xs); padding-top: var(--space-sm); border-top: var(--rule-hair) solid var(--color-rule);
  font-weight: 600; color: var(--color-ink); }
.tl { list-style: none; margin: 0; padding: 0; }
.tl > li.tl-item { margin: 0; position: relative; display: grid; grid-template-columns: minmax(0, 1fr) auto;
  gap: 0 var(--space-md); padding: 0 0 var(--space-md) var(--space-lg); }
.tl-item::before { content: ""; position: absolute; left: 4px; top: 0.45em; width: 11px; height: 11px;
  border-radius: 50%; box-sizing: border-box; background: var(--color-ink-2); z-index: 1; }
.tl-item::after { content: ""; position: absolute; left: 9px; top: 1.2em; bottom: -0.45em; width: 1px;
  background: var(--color-rule-2); }
.tl-item:last-child::after { display: none; }
.tl--settled::before { background: var(--color-paid); }
.tl--attention::before { background: var(--color-cotton); border: 2.5px solid var(--color-ink); }
.tl--waiting::before { background: var(--color-cotton); border: 2px dashed var(--color-ink); }
.tl--closed::before { background: var(--color-cotton); border: 2px solid var(--color-muted); }
.tl--minor::before { left: 6px; top: 0.55em; width: 7px; height: 7px; background: var(--color-rule-2); }
.tl-main { min-width: 0; }
.tl-title { margin: 0; font-weight: 600; color: var(--color-ink); line-height: 1.35; }
.tl-title .chip { margin-left: var(--space-2xs); vertical-align: 1px; }
.tl--minor .tl-title { font-weight: 400; font-size: var(--text-sm); color: var(--color-muted); }
.tl-sum { margin: var(--space-3xs) 0 0; color: var(--color-ink-2); max-width: var(--measure); overflow-wrap: anywhere; }
.tl-when { margin: 0; color: var(--color-muted); font-size: var(--text-sm); white-space: nowrap; text-align: right; }
.said-label { margin: var(--space-xs) 0 var(--space-2xs); font-size: var(--text-sm); color: var(--color-muted); }
.said { margin: 0; padding: var(--space-xs) var(--space-sm); max-width: var(--measure);
  border-left: 2px solid var(--color-rule-2); background: var(--color-paper-2);
  border-radius: 0 var(--radius-control) var(--radius-control) 0; color: var(--color-ink);
  overflow-wrap: anywhere; }
.findings { margin: 0; padding-left: 1.1rem; font-size: var(--text-sm); color: var(--color-ink-2); }
ul.findings > li, .tl-more ul > li { margin: 0 0 var(--space-2xs); padding: 0; font-size: var(--text-sm);
  overflow-wrap: anywhere; }
.tl-more { margin-top: var(--space-2xs); }
.tl-more summary { width: fit-content; cursor: pointer; font-size: var(--text-sm); color: var(--color-ink-2);
  text-decoration: underline; text-decoration-color: var(--color-rule-2); text-underline-offset: 2px; }
.tl-more summary:hover { text-decoration-color: var(--color-accent-2); }
.tl-more ul { list-style: disc; margin: var(--space-2xs) 0 0; padding-left: 1.1rem; font-size: var(--text-sm); color: var(--color-ink-2); }
.tl-more li { overflow-wrap: anywhere; margin: 0; padding: 0; }

@media (max-width: 640px) {
  .stepper { grid-template-columns: 1fr; gap: 0; }
  .stepper > li.step { display: flex; justify-content: space-between; gap: var(--space-sm); padding: var(--space-2xs) 0 var(--space-xs) 32px; }
  .step::before { top: var(--space-2xs); }
  .step::after { left: 10px; right: auto; top: calc(22px + var(--space-2xs)); bottom: 0; width: 2px; height: auto; }
  .step b { display: inline; }
  .step small { display: inline; }
  .tl > li.tl-item { grid-template-columns: minmax(0, 1fr); }
  .tl-when { grid-row: 1; text-align: left; }
}
.count { color: var(--color-ink-2); margin: var(--space-2xs) 0 var(--space-sm); }
.result { margin: var(--space-sm) 0 0; color: var(--color-ink); }
.result--error { color: var(--color-error); }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition: none !important; animation: none !important; }
}
"""


def stylesheet() -> str:
    """The token block and rules. Fonts load from `.streamlit/config.toml`: Streamlit strips
    `<link>` tags from injected HTML."""
    return "<style>" + CSS.replace("__PAID__", PAID) + "</style>"


def escape(text: object) -> str:
    return _escape(str(text), quote=True)


def chip(label: str, tone: Tone) -> str:
    return f'<span class="chip chip--{tone.value}">{escape(label)}</span>'


def who(label: str) -> str:
    return f'<span class="who who--{escape(label.lower())}">{escape(label)}</span>'


def pairs(rows: list[tuple[str, str]]) -> str:
    """A small definition list: label on the left, value on the right. Both escaped."""
    body = "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in rows)
    return f'<dl class="pair">{body}</dl>'


__all__ = ["CSS", "PAID", "chip", "escape", "pairs", "stylesheet", "who"]
