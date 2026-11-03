"""The case timeline: what happened, in order, and why (P3-09, I5, I8).

A page rather than JSON, because its audience is a person deciding whether the system did
something sensible. The events table is the audit trail; this is the readable face of it,
and every row says what happened, when, and what caused it.

**The security point of this file is that it renders text a seller wrote.** A dispute
statement, a reply, an ops note — all of it arrives as `UntrustedText`, and the review
screen is one of the few places it is legitimately shown to a person (agent-design.md
section 6). So two rules, and the second is the one that bites:

1. **Escaped.** Seller text is HTML-escaped before it reaches the page. Without that, a
   seller who writes `<script>` runs it in the reviewer's browser, on an internal console
   that has a review token in it. That is the real attack here, not prompt injection.
2. **Boxed and labelled.** It is shown inside a marked block that says where it came from,
   so a reader can tell at a glance which words are the system's and which are somebody
   else's. An injection that reads like an instruction from the system is only convincing
   if it is not visibly quoted.
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from dispute_agent.kernel.domain.errors import NotFound
from dispute_agent.kernel.domain.events import StateChangedPayload

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dispute_agent.api.runtime import Runtime
    from dispute_agent.kernel.domain.events import Event
    from dispute_agent.kernel.domain.models import Case
    from dispute_agent.kernel.domain.values import UntrustedText

#: Minimal, and deliberately not a template file. The page has one job, and a template
#: engine here would be a second place for an unescaped interpolation to hide.
STYLE = """
body { font-family: system-ui, sans-serif; margin: 2rem; max-width: 60rem; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 0.4rem 0.8rem; border-bottom: 1px solid #ddd; }
th { background: #f4f4f4; }
.untrusted { border: 2px dashed #c47; background: #fff8fb; padding: 0.8rem; margin: 1rem 0; }
.untrusted .label { color: #a03; font-weight: 600; font-size: 0.85rem; }
.untrusted pre { white-space: pre-wrap; margin: 0.5rem 0 0; font-family: inherit; }
"""


def build_timeline_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()

    @router.get("/cases/{case_id}/timeline", response_class=HTMLResponse)
    def timeline(case_id: str) -> HTMLResponse:
        try:
            case, events = runtime.read_case(case_id)
        except NotFound as missing:
            raise HTTPException(status_code=404, detail=str(missing)) from missing
        return HTMLResponse(render_timeline(case, events))

    return router


def render_timeline(case: Case, events: Sequence[Event]) -> str:
    """The whole page, as a string. Pure, so it can be tested without a server."""
    statement = case.facts.statement
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Case {escape(str(case.id))}</title>
<style>{STYLE}</style></head>
<body>
<h1>Case {escape(str(case.id))}</h1>
<p>Shipment {escape(case.facts.shipment_ref)} &middot; claim
   {escape(case.facts.claim_ref)} &middot; state <strong>{escape(case.state.value)}</strong></p>
{untrusted_block(statement, "the seller's dispute statement") if statement else ""}
<h2>What happened</h2>
{events_table(events)}
</body>
</html>"""


def untrusted_block(text: UntrustedText, what: str) -> str:
    """Somebody else's words, escaped and visibly quoted.

    The label names the origin, so a reader can see whose words these are. An instruction
    hidden in a seller's reply is only persuasive if it does not look like a quotation.
    """
    note = " (truncated)" if text.truncated else ""
    return f"""<div class="untrusted">
  <div class="label">Not written by this system &mdash; {escape(what)},
       from {escape(text.origin)}{note}</div>
  <pre>{escape(text.text)}</pre>
</div>"""


def events_table(events: Sequence[Event]) -> str:
    """Every event in sequence order, with its cause (I8).

    The cause column is the point. "The state changed" is a log line; "the state changed
    because timer tmr_01 fired" is an audit trail.
    """
    if not events:
        return "<p>No events recorded.</p>"

    rows = "\n".join(_row(event) for event in sorted(events, key=lambda e: e.seq))
    return f"""<table>
<thead><tr><th>#</th><th>When</th><th>What</th><th>Change</th><th>Because</th></tr></thead>
<tbody>
{rows}
</tbody></table>"""


def _row(event: Event) -> str:
    payload = event.payload
    change = payload if isinstance(payload, StateChangedPayload) else None
    movement = (
        f"{escape(change.from_state.value)} &rarr; {escape(change.to_state.value)}"
        f" ({escape(change.transition_id)})"
        if change is not None
        else ""
    )
    # Every value escaped, including the cause ref: it can be an external id, and an
    # external id is a string somebody else chose.
    return (
        f"<tr><td>{event.seq}</td>"
        f"<td>{escape(event.occurred_at.isoformat())}</td>"
        f"<td>{escape(event.type.value)}</td>"
        f"<td>{movement}</td>"
        f"<td>{escape(event.cause.kind.value)}: {escape(event.cause.ref)}</td></tr>"
    )


__all__ = ["STYLE", "build_timeline_router", "events_table", "render_timeline", "untrusted_block"]
