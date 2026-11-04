"""The model-backed reply interpreter (P4-06, agent-design.md section 1, R20, R21).

It reads a seller's reply and says what the reply *appears* to say: one intent, the status
of each item we asked for, any figures the seller claims (labelled as claims), attachments,
manipulation flags, a short summary and the language. It decides nothing. Policy reads the
intent (R20 withdraws, R21 manipulation) and code decides what happens.

The reply itself is outside text from first to last: it reaches the prompt only through the
registry's `untrusted` argument (`ctx.reply_text`), and it never comes back out as anything
but a labelled reading.

**The check it adds to the schema:** the requested items must come back word for word,
each exactly once. An interpreter that paraphrased "Photo of the packed box beside a tape
measure" into "box photo" would make the evidence round match the wrong thing.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from dispute_agent.kernel.domain.errors import InvalidModelOutput
from dispute_agent.kernel.domain.reasoning import ModelRequest, ReplyReading
from dispute_agent.modules.reasoning.llm.calls import ask_checked

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dispute_agent.kernel.domain.json_values import JsonValue
    from dispute_agent.kernel.domain.reasoning import ReplyContext
    from dispute_agent.kernel.ports import ModelGateway, PromptRegistry

PROMPT = "seller_reply_interpreter"


@dataclass
class LlmReplyInterpreter:
    """Implements ReplyInterpreter."""

    prompts: PromptRegistry
    gateway: ModelGateway
    version: str = "v1"
    max_output_tokens: int = 4000

    def interpret(self, ctx: ReplyContext) -> ReplyReading:
        variables: dict[str, Any] = {
            "ctx": {
                "case_ref": ctx.case_ref,
                "request_sent_at": (
                    ctx.request_sent_at.isoformat() if ctx.request_sent_at else "unknown"
                ),
                "requested_items": list(ctx.requested_items),
                "reply_truncated": ctx.reply_truncated or ctx.reply.truncated,
                "attachments": [
                    {"name": a.name, "content_type": a.content_type} for a in ctx.attachments
                ],
            }
        }
        rendered = self.prompts.load(PROMPT, self.version, variables, {"ctx.reply_text": ctx.reply})
        response = ask_checked(
            self.gateway,
            ModelRequest(
                case_id=ctx.case_id,
                purpose="seller_reply_interpreter",
                prompt=rendered,
                max_output_tokens=self.max_output_tokens,
            ),
            lambda output: check_items_echoed(output, ctx.requested_items),
        )
        try:
            return ReplyReading.model_validate(
                {
                    **dict(response.output),
                    "prompt_name": PROMPT,
                    "prompt_version": self.version,
                    "prompt_hash": rendered.hash,
                    "model_id": response.model_id,
                },
                strict=False,
            )
        except ValidationError as bad:
            fields = sorted({".".join(str(p) for p in e["loc"]) for e in bad.errors()})
            raise InvalidModelOutput(f"not a valid reading at {fields}") from None


def check_items_echoed(output: Mapping[str, JsonValue], asked: tuple[str, ...]) -> None:
    statuses: Any = output.get("requested_items_status") or []
    returned = Counter(str(s.get("item", "")) for s in statuses if isinstance(s, dict))
    if returned != Counter(asked):
        # Counts only: the items are model text and may echo the seller (I12).
        raise InvalidModelOutput(
            f"requested items not echoed exactly ({sum(returned.values())} back, "
            f"{len(asked)} asked)"
        )


__all__ = ["PROMPT", "LlmReplyInterpreter", "check_items_echoed"]
