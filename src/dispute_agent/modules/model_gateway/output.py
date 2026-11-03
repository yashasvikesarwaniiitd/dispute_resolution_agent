"""Checking a model's answer, shared by every gateway adapter (kernel-interfaces.md 6.4).

The port promises that output is validated against the prompt's schema with one retry, on
every route. Putting that promise in one function is what keeps it the same on every route:
a Foundry agent cannot do structured output (ADR 0029), so for Foundry this is the *only*
check, and a second copy of it in that adapter would be a second thing to keep in step.

What counts as a valid answer is deliberately narrow: a single JSON object, matching the
schema exactly, with no extra properties anywhere the schema forbids them. A model that
wraps its JSON in prose or a code fence has not followed the output contract, and the
contract says to return only the object - so that is invalid, and gets its one retry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from dispute_agent.kernel.domain.errors import InvalidModelOutput
from dispute_agent.kernel.domain.reasoning import ModelResponse, ModelUsage

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from dispute_agent.kernel.domain.json_values import JsonValue
    from dispute_agent.kernel.domain.reasoning import ModelRequest

#: The port: one retry on invalid output, then InvalidModelOutput (TC-MOD-01).
ATTEMPTS = 2


@dataclass(frozen=True)
class RawReply:
    """What one provider call produced, before anything has been believed."""

    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: Decimal
    model_id: str


def parse(text: str, schema: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """The answer as a JSON object that matches `schema`, or InvalidModelOutput.

    The error message names *where* the answer went wrong but never quotes it: the answer
    may echo case data, and this message ends up in logs (I12, I14).
    """
    try:
        value: Any = json.loads(text)
    except (json.JSONDecodeError, TypeError) as bad:
        raise InvalidModelOutput(f"not JSON (line {getattr(bad, 'lineno', '?')})") from None
    if not isinstance(value, dict):
        raise InvalidModelOutput(f"not a JSON object but {type(value).__name__}")

    try:
        validator = Draft202012Validator(dict(schema))
    except SchemaError as broken:  # pragma: no cover - the prompt's own tests catch this
        raise InvalidModelOutput("the prompt's schema is itself invalid") from broken
    errors = sorted(validator.iter_errors(value), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.absolute_path) or "(root)"
        raise InvalidModelOutput(f"schema: {first.validator} failed at {where}")
    return value


def complete_with_one_retry(
    req: ModelRequest, call: Callable[[ModelRequest], RawReply], *, route: str
) -> ModelResponse:
    """Call, check, and on an invalid answer call exactly once more.

    Usage is summed over both attempts: the retry was paid for, and the spending cap has
    to see what was actually spent, not what the successful attempt cost.
    """
    spent: list[RawReply] = []
    last_error: InvalidModelOutput | None = None
    for _ in range(ATTEMPTS):
        reply = call(req)
        spent.append(reply)
        try:
            output = parse(reply.text, req.prompt.output_schema)
        except InvalidModelOutput as invalid:
            last_error = invalid
            continue
        return ModelResponse(
            output=output, model_id=reply.model_id, route=route, usage=_total(spent)
        )
    assert last_error is not None
    raise InvalidModelOutput(f"invalid twice; last: {last_error.args[0]}") from last_error


def _total(replies: list[RawReply]) -> ModelUsage:
    return ModelUsage(
        input_tokens=sum(r.input_tokens for r in replies),
        output_tokens=sum(r.output_tokens for r in replies),
        latency_ms=sum(r.latency_ms for r in replies),
        cost_usd=sum((r.cost_usd for r in replies), Decimal(0)),
    )


__all__ = ["ATTEMPTS", "RawReply", "complete_with_one_retry", "parse"]
