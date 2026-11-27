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
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from dispute_agent.kernel.domain.errors import (
    InvalidModelOutput,
    KernelError,
    ModuleUnavailable,
    TransientPortError,
)
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
    #: Set when the provider says the answer is unusable before we read it (cut short at
    #: the token limit, for example). Treated as invalid, with this as the reason.
    unusable: str | None = None
    #: Provider screening notes on this call, e.g. "prompt_shield:indirect_attack" (I15).
    annotations: tuple[str, ...] = ()
    #: Of input_tokens, written to / read from the prompt cache (ADR 0072).
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0


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
        raise InvalidModelOutput("schema: " + "; ".join(_explain(e) for e in errors[:3]))
    return value


#: A field name safe to repeat in a log or a retry: an identifier, never free text.
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")


def _explain(error: Any) -> str:
    """Where and how the answer broke the schema, in terms a retry can act on. Names fields
    and the values the schema allows; never repeats a value the model wrote."""
    where = "/".join(str(p) for p in error.absolute_path) or "(root)"
    rule, expected = error.validator, error.validator_value
    if rule == "additionalProperties" and isinstance(error.instance, dict):
        allowed = sorted(error.schema.get("properties") or {})
        extra = [k for k in error.instance if k not in allowed]
        named = ", ".join(k if _NAME.match(str(k)) else "(unnamed)" for k in extra[:3])
        return f"{where}: field not allowed: {named} (allowed: {', '.join(allowed) or 'none'})"
    if rule == "enum":
        return f"{where}: not an allowed value; use one of: {', '.join(map(str, expected))}"
    if rule == "required" and isinstance(error.instance, dict):
        missing = [k for k in expected if k not in error.instance]
        return f"{where}: missing field: {', '.join(missing)}"
    if rule == "type":
        return f"{where}: must be {expected}"
    if rule in ("maxLength", "minLength", "maxItems", "minItems", "maximum", "minimum"):
        return f"{where}: {rule} is {expected}"
    return f"{where}: {rule} failed"


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
        try:
            # The retry is told what was wrong: the same call, not an extra one (ADR 0073).
            reply = call(req if last_error is None else _with_feedback(req, last_error))
        except KernelError as failed:
            usage = add_usage(_total(spent), failed.usage)
            if last_error is not None and isinstance(
                failed, TransientPortError | ModuleUnavailable
            ):
                # The model already answered badly once. A retry that then could not be
                # made does not turn that into "the model was unavailable" - which would
                # let the rules decide a case the model got wrong (review, M3).
                raise InvalidModelOutput(
                    f"invalid, then the retry failed ({failed.code})", usage=usage
                ) from failed
            failed.usage = usage
            raise
        spent.append(reply)
        try:
            if reply.unusable is not None:
                raise InvalidModelOutput(reply.unusable)
            output = parse(reply.text, req.prompt.output_schema)
        except InvalidModelOutput as invalid:
            last_error = invalid
            continue
        notes = tuple(sorted({a for r in spent for a in r.annotations}))
        return ModelResponse(
            output=output,
            model_id=reply.model_id,
            route=route,
            usage=_total(spent),
            screen_annotations=notes,
        )
    assert last_error is not None
    raise InvalidModelOutput(
        f"invalid twice; last: {last_error.args[0]}", usage=_total(spent)
    ) from last_error


RETRY_NOTE = (
    "\n\n[Your previous answer was not accepted - {reason}. Return the complete JSON object "
    "again, corrected, and nothing else.]"
)


def _with_feedback(req: ModelRequest, error: InvalidModelOutput) -> ModelRequest:
    """`req` with why its answer was refused added to the last message (a gateway that reads
    the request it is given passes it on; one that does not retries exactly as before)."""
    messages = list(req.prompt.messages)
    note = RETRY_NOTE.format(reason=str(error.args[0] if error.args else "invalid")[:600])
    messages[-1] = messages[-1].model_copy(update={"content": messages[-1].content + note})
    prompt = req.prompt.model_copy(update={"messages": tuple(messages)})
    return req.model_copy(update={"prompt": prompt})


def add_usage(base: ModelUsage, extra: object | None) -> ModelUsage:
    """`base` plus `extra` when `extra` is a ModelUsage; the spend guard reads the sum."""
    if not isinstance(extra, ModelUsage):
        return base
    return ModelUsage(
        input_tokens=base.input_tokens + extra.input_tokens,
        output_tokens=base.output_tokens + extra.output_tokens,
        latency_ms=base.latency_ms + extra.latency_ms,
        cost_usd=base.cost_usd + extra.cost_usd,
        cache_write_tokens=base.cache_write_tokens + extra.cache_write_tokens,
        cache_read_tokens=base.cache_read_tokens + extra.cache_read_tokens,
    )


def _total(replies: list[RawReply]) -> ModelUsage:
    return ModelUsage(
        input_tokens=sum(r.input_tokens for r in replies),
        output_tokens=sum(r.output_tokens for r in replies),
        latency_ms=sum(r.latency_ms for r in replies),
        cost_usd=sum((r.cost_usd for r in replies), Decimal(0)),
        cache_write_tokens=sum(r.cache_write_tokens for r in replies),
        cache_read_tokens=sum(r.cache_read_tokens for r in replies),
    )


__all__ = ["ATTEMPTS", "RawReply", "add_usage", "complete_with_one_retry", "parse"]
