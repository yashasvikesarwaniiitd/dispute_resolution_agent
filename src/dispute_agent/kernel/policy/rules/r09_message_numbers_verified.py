"""R09 message_numbers_verified. I9.

The last gate before a figure reaches a seller. Every number token in the subject or body
must trace to a `Fact` the assessment produced, cited by id, and saying the same thing.

It runs at both stages on purpose. At PLANNING it refuses to build a message with a figure
nobody can account for; at EXECUTION it refuses to send one, because the text could have
been edited between the two and the approval binding (R10) proves only that the text is the
text that was approved — not that the numbers in it were ever true.

**It denies rather than escalating.** A message with an unexplained number is not a case
needing judgement; it is a message that must not go out. The case still reaches a person,
through the denial, with the token named.

Identifiers and rendered dates are masked before tokenising (policy-rules.md section 7
rules 2 and 3), because `CLM-000001` contains digits and `5 November 2026` contains two
numbers, and a rule that failed every message on its own case reference would be switched
off within a day.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import ActionType
from dispute_agent.kernel.policy.numbers import extract_numbers, justify, number_word_in
from dispute_agent.kernel.policy.verdicts import allow, deny

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.actions import RuleResult
    from dispute_agent.kernel.domain.evidence import Fact
    from dispute_agent.kernel.policy.verdicts import PolicyContext


class MessageNumbersVerified:
    """Implements PolicyRule."""

    rule_id = "R09"

    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        intent = ctx.intent
        if intent is None or intent.action_type is not ActionType.SEND_MESSAGE:
            return allow(self.rule_id, "not_a_message")

        payload = intent.payload
        subject = getattr(payload, "subject", "")
        body = getattr(payload, "body", "")
        claims = getattr(payload, "numbers_used", ())

        facts = self._facts(ctx)
        masks = self._masks(ctx)

        for text in (subject, body):
            word = number_word_in(text)
            if word is not None:
                # Invisible to the tokeniser, so uncheckable: refused, never waved through.
                return deny(self.rule_id, f"number_word: {word!r} - figures are written in digits")
            for token in extract_numbers(text, masks=masks):
                problem = justify(token, claims, facts)
                if problem is not None:
                    # The reason names the token, because "numbers_unverified" alone tells
                    # a reviewer nothing about which figure to look at.
                    return deny(self.rule_id, f"numbers_unverified: {problem}")

        return allow(self.rule_id)

    def _facts(self, ctx: PolicyContext) -> dict[str, Fact]:
        """This case's assessment facts, plus the kernel facts code established later.

        Not the proposal's, and not anything a model produced: a number is justified by
        what code computed, or it is not justified (I9). `credited_amount` arrives as a
        kernel fact, read from the verified ledger transaction (section 7 rule 1).
        """
        facts = {} if ctx.assessment is None else {f.fact_id: f for f in ctx.assessment.facts}
        facts.update({fact.fact_id: fact for fact in ctx.kernel_facts})
        return facts

    def _masks(self, ctx: PolicyContext) -> tuple[str, ...]:
        """Whole strings the system itself rendered, removed before tokenising.

        Identifiers only. Anything added here stops being checked, so the list is the case's
        own references and nothing that could carry a quantity.
        """
        facts = ctx.case.facts
        # The case reference too - policy-rules.md section 7 rule 2 names it. Leaving it out
        # meant every message whose subject named its case was refused on the digits in the
        # case id, before anyone could approve it.
        refs = (str(ctx.case.id), facts.shipment_ref, facts.claim_ref, facts.seller_ref)
        return tuple(ref for ref in refs if ref)
