"""structlog setup and the redaction processor (observability.md sections 1 and 2, I12, I14).

The card's warning shaped this file: write the processor so a sentinel scan finds nothing,
not so a test passes. Scenario S30 runs the suite with sentinel secrets and content and then
greps the logs, so a redactor that only handles the keys somebody remembered would fail the
moment a token turned up inside an exception message, a URL or a nested dictionary.

So it walks everything. Every string value anywhere in the event — nested in mappings, inside
lists, inside a rendered traceback — is scanned for known secret values and for token shapes,
and every email is masked. Keys that carry outside or model-written text are *dropped* rather
than masked (I5, I14), and replaced by a hash and a length, which is what a debugger actually
needs: whether two things were the same, and how long they were.

Redaction is the last processor before rendering, so it sees the line exactly as it would be
written.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sys
from collections.abc import Collection, Mapping, MutableMapping
from typing import Any, Final

import structlog

#: Outside or model-written text. Dropped, never masked: there is no safe way to show part of
#: a seller's email body or an AI's rationale in a log line (observability.md section 2).
DROPPED_KEYS: Final[frozenset[str]] = frozenset(
    {"body", "text", "subject", "note", "rationale", "content"}
)

#: Personal names: masked entirely, since a partial name is still a name.
NAME_KEYS: Final[frozenset[str]] = frozenset({"name", "seller_name", "from_name", "to_name"})

#: A long opaque string under one of these keys is a credential, whatever it looks like.
SECRET_KEY_PARTS: Final[tuple[str, ...]] = ("token", "secret", "key", "password")

#: Keys that contain one of the parts above but are explicitly not secrets. observability.md
#: section 1 lists the idempotency key as a field to log, and the executor's logs are close to
#: unreadable without it; the hashes are how I13 is debugged at all. Checked before
#: SECRET_KEY_PARTS, because "idempotency_key" ends in "key" and the naive rule masked it —
#: which the tests caught, and which is exactly the kind of over-redaction that gets a
#: redactor switched off.
NOT_SECRET_KEYS: Final[frozenset[str]] = frozenset(
    {
        "idempotency_key",
        "dedupe_key",
        "payload_hash",
        "content_hash",
        "briefing_hash",
        "prompt_hash",
        "requirement_key",
        "claim_ref",
        "fact_id",
    }
)

SECRET_MASK: Final = "***SECRET***"
TOKEN_MASK: Final = "***REDACTED***"
NAME_MASK: Final = "***"

_EMAIL = re.compile(r"\b([^\s@,;<>()\[\]])([^\s@,;<>()\[\]]*)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")

#: Shapes that are credentials wherever they appear, under any key.
_TOKEN_SHAPES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"\bsk-ant-[A-Za-z0-9._-]+"),
    re.compile(r"\bATATT[A-Za-z0-9._=-]+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
)

#: 32+ base64url characters. Only applied under a secret-ish key, because at that length an
#: idempotency key or a payload hash would otherwise be mangled, and those are not secrets.
_OPAQUE = re.compile(r"\b[A-Za-z0-9_-]{32,}\b")

_MAX_DEPTH: Final = 12


def _looks_secret(key: str) -> bool:
    lowered = key.lower()
    if lowered in NOT_SECRET_KEYS:
        return False
    return any(part in lowered for part in SECRET_KEY_PARTS)


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


class Redactor:
    """A structlog processor. Last before rendering.

    Built with the secret values currently loaded, so it can find a token that escaped into
    a place nobody anticipated — which is how secrets actually leak.
    """

    def __init__(self, secrets: Collection[str] = ()) -> None:
        # Longest first, so a secret that contains another is masked whole.
        self._secrets = sorted({s for s in secrets if len(s) >= 8}, key=len, reverse=True)

    def __call__(
        self, logger: object, method_name: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        return self._mapping(event_dict, depth=0)

    def redact_text(self, value: str, key: str = "") -> str:
        """Public so a test — and the Phase 4 sentinel scan — can use the same code path."""
        return self._string(value, key)

    def _mapping(self, source: Mapping[str, Any], depth: int) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in source.items():
            if key in DROPPED_KEYS and isinstance(value, str):
                out[f"{key}_hash"] = _digest(value)
                out[f"{key}_len"] = len(value)
                continue
            if key in NAME_KEYS:
                out[key] = NAME_MASK
                continue
            out[key] = self._value(value, key, depth + 1)
        return out

    def _value(self, value: Any, key: str, depth: int) -> Any:
        if depth > _MAX_DEPTH:
            return "***DEPTH***"
        if isinstance(value, str):
            return self._string(value, key)
        if isinstance(value, BaseException):
            # A library exception's message is exactly where a token or an address turns up.
            return self._string(f"{type(value).__name__}: {value}", key)
        if isinstance(value, Mapping):
            return self._mapping(value, depth)
        if isinstance(value, list | tuple | set | frozenset):
            return [self._value(item, key, depth + 1) for item in value]
        return value

    def _string(self, value: str, key: str) -> str:
        for secret in self._secrets:
            if secret in value:
                value = value.replace(secret, SECRET_MASK)
        for shape in _TOKEN_SHAPES:
            value = shape.sub(TOKEN_MASK, value)
        if _looks_secret(key):
            value = _OPAQUE.sub(TOKEN_MASK, value)
        return _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(3)}", value)


def add_correlation_id(
    logger: object, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Every line carries one, so a chain can be followed end to end.

    `corr_none` rather than a missing key: a line outside any chain is a fact worth seeing,
    and a grep for a field that is sometimes absent finds less than you think.
    """
    event_dict.setdefault("correlation_id", "corr_none")
    return event_dict


def configure_logging(
    app_env: str = "local",
    secrets: Collection[str] = (),
    level: int = logging.INFO,
    json_output: bool = True,
) -> Redactor:
    """Configure structlog for the process and return the redactor it installed.

    Called by the composition root with the loaded secrets. Returning the redactor lets a
    caller reuse the same masking for anything it writes outside the logger.
    """
    redactor = Redactor(secrets)
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _app_env_processor(app_env),
            add_correlation_id,
            # Last before rendering: it sees the line exactly as it will be written,
            # the formatted traceback included.
            redactor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    return redactor


def _app_env_processor(app_env: str) -> Any:
    def processor(
        logger: object, method_name: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        event_dict.setdefault("app_env", app_env)
        return event_dict

    return processor
