"""The model-backed message drafter (P4-07, I9, I13, R09).

It writes the words of a message. It may not write a number of its own: every figure in
the subject or body must be listed in `numbers_used` against a fact we hold, and must read
exactly as that fact renders. The kernel's R09 checks this again at planning and at sending -
the module is the thing being checked, so the module's own check is a first filter, not the
guarantee.

**Refusing is a result.** The prompt tells the model to set `blocked_reason` when a fact it
needs is missing (a credit confirmation with no credited amount). That comes back as a
`DraftedMessage` with `blocked_reason` set, which the caller reads as "do not send" - never
as an error to retry past.

The recipient is not in the context at all (R07): who receives a message is the system's
decision, not the drafter's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from dispute_agent.kernel.domain.errors import InvalidModelOutput
from dispute_agent.kernel.domain.reasoning import DraftedMessage, ModelRequest
from dispute_agent.kernel.domain.values import Money, Weight
from dispute_agent.kernel.policy.numbers import extract_numbers, number_word_in, render_fact
from dispute_agent.modules.reasoning.llm.calls import ask_checked

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dispute_agent.kernel.domain.evidence import Fact
    from dispute_agent.kernel.domain.json_values import JsonValue
    from dispute_agent.kernel.domain.reasoning import DraftContext
    from dispute_agent.kernel.ports import ModelGateway, PromptRegistry

PROMPT = "seller_message_drafter"


def fact_kind(fact: Fact) -> str:
    """The prompt's four kinds. Strings are identifiers; everything numeric is a quantity."""
    if isinstance(fact.value, Money | Weight) or not isinstance(fact.value, str):
        return "quantity"
    return "identifier"


@dataclass
class LlmMessageDrafter:
    """Implements MessageDrafter."""

    prompts: PromptRegistry
    gateway: ModelGateway
    version: str = "v1"
    max_output_tokens: int = 4000

    def draft(self, ctx: DraftContext) -> DraftedMessage:
        facts = [
            {"fact_id": f.fact_id, "kind": fact_kind(f), "rendered": render_fact(f.value)}
            for f in ctx.facts
        ]
        facts.append({"fact_id": "case_ref", "kind": "identifier", "rendered": ctx.case_ref})
        variables: dict[str, Any] = {
            "ctx": {
                "kind": ctx.kind.value,
                "case_ref": ctx.case_ref,
                "facts": facts,
                # Model- and seller-derived; the template renders both as untrusted blocks.
                "requested_items": list(ctx.requested_items),
                "open_points": list(ctx.open_points),
                "next_step": ctx.next_step,
                "style": ctx.style.model_dump(mode="json"),
            }
        }
        rendered = self.prompts.load(PROMPT, self.version, variables, {})
        response = ask_checked(
            self.gateway,
            ModelRequest(
                case_id=ctx.case_id,
                purpose="seller_message_drafter",
                prompt=rendered,
                max_output_tokens=self.max_output_tokens,
            ),
            lambda output: check_numbers(output, ctx),
        )
        out = dict(response.output)
        try:
            return DraftedMessage.model_validate(
                {
                    **out,
                    "prompt_name": PROMPT,
                    "prompt_version": self.version,
                    "prompt_hash": rendered.hash,
                },
                strict=False,
            )
        except ValidationError as bad:
            fields = sorted({".".join(str(p) for p in e["loc"]) for e in bad.errors()})
            raise InvalidModelOutput(f"not a valid draft at {fields}") from None


def check_numbers(output: Mapping[str, JsonValue], ctx: DraftContext) -> None:
    """Every figure cited, every citation real and exact, no number words (I9, MD-F1)."""
    if output.get("blocked_reason"):
        return  # A refusal carries no text to check.
    by_id = {f.fact_id: render_fact(f.value) for f in ctx.facts}
    cited: Any = output.get("numbers_used") or []
    cited_texts: set[str] = set()
    for use in cited:
        fact_id, text = str(use.get("fact_id")), str(use.get("text"))
        if fact_id not in by_id:
            raise InvalidModelOutput("a number cites a fact that is not in the context")
        if text != by_id[fact_id]:
            raise InvalidModelOutput(f"a number does not read exactly as fact {fact_id!r}")
        cited_texts.add(text)
    for field in ("subject", "body"):
        text = str(output.get(field) or "")
        if number_word_in(text) is not None:
            raise InvalidModelOutput(f"the {field} writes a number as a word")
        masks = (ctx.case_ref, ctx.seller_ref)
        for token in extract_numbers(text, masks=masks):
            if not any(token.text in c or c in token.text for c in cited_texts):
                raise InvalidModelOutput(f"the {field} has a figure no fact accounts for")


__all__ = ["PROMPT", "LlmMessageDrafter", "check_numbers", "fact_kind"]
