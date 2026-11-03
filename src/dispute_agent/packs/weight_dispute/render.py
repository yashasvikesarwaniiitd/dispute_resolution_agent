"""Rendering a message from facts, and from nothing else (P3-05, I9, I13, R09).

A template here is not a string with holes in it. It is a function from **stored facts** to
text, and the two rules that make it safe are both about what it cannot do.

**It cannot invent a number.** Every figure comes through `fact('id')`, which looks the id
up in the facts the assessment produced and renders it with the one renderer policy also
uses. The id is recorded as it is used, so the finished message arrives with its own
citations and R09 can check every token against them. A template that formatted a number
itself would be a number nobody could trace.

**It cannot quietly leave a blank.** Jinja is configured with `StrictUndefined` and `fact`
raises on an unknown id, so a missing fact stops the render. The alternative is worse than
it sounds: a message that reads "your charge of  was calculated from" does not look like a
bug to a template, and it is what a seller would receive.

Digits only, never number words (MD-F1). R09's tokeniser cannot see "four hundred", so a
template writing words would be a hole in the number check rather than a style choice.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound
from jinja2 import UndefinedError as JinjaUndefinedError

from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.reasoning import DraftedMessage, NumberUse
from dispute_agent.kernel.policy.numbers import render_fact

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dispute_agent.kernel.domain.evidence import Fact
    from dispute_agent.kernel.domain.reasoning import DraftContext

TEMPLATES = Path(__file__).parent / "templates"

#: One template per message kind this pack can send today. Upheld-charge and
#: credit-confirmation arrive in Phase 5; a kind with no template is refused rather than
#: falling back to something generic, because a generic message about money is worse than
#: no message.
TEMPLATE_FOR_KIND = {
    "evidence_request": "evidence_request.md.j2",
    "clarification_request": "evidence_request.md.j2",
    "reminder": "reminder.md.j2",
}

#: Number words R09 cannot see. Checked after rendering, so a template that ever starts
#: spelling a figure out fails here rather than at a seller's inbox (MD-F1, residual RR1).
NUMBER_WORDS = re.compile(
    r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|lakh|crore)\b",
    re.IGNORECASE,
)

_ENV = Environment(
    loader=FileSystemLoader(TEMPLATES),
    undefined=StrictUndefined,  # a missing value stops the render rather than blanking it
    autoescape=False,  # plain text, not HTML; escaping would corrupt the body
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=False,
)


class FactCiter:
    """Renders a fact and remembers that it was used.

    The citation is a by-product of writing the number, not a second step somebody has to
    remember. That is the point: a `numbers_used` list assembled by hand would drift from
    the text the moment a template changed.
    """

    def __init__(self, facts: Mapping[str, Fact]) -> None:
        self._facts = facts
        self.used: list[NumberUse] = []

    def __call__(self, fact_id: str) -> str:
        fact = self._facts.get(fact_id)
        if fact is None:
            raise DomainValidationError(
                f"the template asked for fact {fact_id!r}, which this case does not have; "
                "refusing to render rather than leaving a blank where a number goes (I9)"
            )
        text = render_fact(fact.value)
        self.used.append(NumberUse(text=text, fact_id=fact_id))
        return text


def render(ctx: DraftContext, *, shipment_ref: str) -> DraftedMessage:
    """Render the template for this message kind. Raises rather than emitting a blank.

    `shipment_ref` is passed in rather than read from a fact because it is an identifier,
    not a measurement: R09 masks identifiers before tokenising, so the digits inside
    `SHP-000001` are never treated as a number needing a citation.
    """
    name = TEMPLATE_FOR_KIND.get(ctx.kind.value)
    if name is None:
        raise DomainValidationError(
            f"no template for message kind {ctx.kind.value!r}; this pack sends only "
            f"{sorted(TEMPLATE_FOR_KIND)} until Phase 5"
        )

    facts = {fact.fact_id: fact for fact in ctx.facts}
    citer = FactCiter(facts)

    try:
        template = _ENV.get_template(name)
        text = template.render(
            case_ref=ctx.case_ref,
            shipment_ref=shipment_ref,
            requested_items=ctx.requested_items,
            next_step=ctx.next_step,
            fact=citer,
        )
    except TemplateNotFound as missing:  # pragma: no cover - caught by the registry test
        raise DomainValidationError(f"template file missing: {name}") from missing
    except JinjaUndefinedError as undefined:
        raise DomainValidationError(
            f"template {name} needs a value this case does not have: {undefined}"
        ) from undefined

    subject, body = _split(text, name)

    if NUMBER_WORDS.search(body) or NUMBER_WORDS.search(subject):
        # A number written as a word is invisible to R09's tokeniser, so it would reach a
        # seller unchecked. Refusing here is cheaper than the residual risk.
        raise DomainValidationError(
            f"template {name} rendered a number word; templates write digits only (MD-F1)"
        )

    return DraftedMessage(
        subject=subject,
        body=body,
        numbers_used=tuple(citer.used),
        template_id=name,
    )


def _split(text: str, name: str) -> tuple[str, str]:
    """Pull the `Subject:` line off the front.

    Kept in the template rather than in code so that a person editing the wording edits the
    whole message in one place, including what the seller sees first.
    """
    first, _, rest = text.partition("\n")
    if not first.lower().startswith("subject:"):
        raise DomainValidationError(f"template {name} has no Subject: line")
    return first.split(":", 1)[1].strip(), rest.strip()


def template_names() -> tuple[str, ...]:
    """Every template file the registry points at, for the drift test."""
    return tuple(sorted(set(TEMPLATE_FOR_KIND.values())))


class TemplateDrafter:
    """The rules-only drafter: the investigate step's `Drafter`, backed by these templates.

    A refusal to render becomes `blocked_reason` on the result rather than an exception, so
    the step reads "do not send" as a result - the same contract the model drafter will have.
    """

    def draft(self, ctx: DraftContext, *, shipment_ref: str) -> DraftedMessage:
        try:
            return render(ctx, shipment_ref=shipment_ref)
        except DomainValidationError as refused:
            return DraftedMessage(
                subject="(blocked)", body="(blocked)", blocked_reason=str(refused)
            )


__all__ = [
    "NUMBER_WORDS",
    "TEMPLATES",
    "TEMPLATE_FOR_KIND",
    "FactCiter",
    "TemplateDrafter",
    "render",
    "template_names",
]
