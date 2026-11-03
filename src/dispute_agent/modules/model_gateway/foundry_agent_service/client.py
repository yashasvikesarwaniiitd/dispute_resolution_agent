"""The thin layer that talks to Azure, and translates what Azure says (foundry.md 3 and 7).

Everything provider-specific lives here, behind `AgentServiceClient`: the SDK calls, and
the mapping of Azure's and OpenAI's exceptions onto the kernel's typed errors. The gateway
above it is then plain logic over a small protocol, which is what lets the contract suite
run it against a stub without a network or an Azure account.

**How the agent is called.** The project's own OpenAI client (`project.get_openai_client()`,
GA) with `extra_body={"agent_reference": {"name", "version", "type"}}`, and `store=False`.
The SDK's `get_openai_client(agent_name=...)` form needs `allow_preview=True` in
azure-ai-projects 2.10.0, so it is not used (ADR 0029 addendum). The reference names the
**exact version** from the lock file, so the call cannot silently reach a newer version.

**What is never sent:** sampling parameters (foundry.md section 6), tools (D12), or the
system prompt - the agent already holds it, and sending it again would be a second copy
that could disagree with the one the drift check verified.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from dispute_agent.kernel.domain.errors import (
    AgentDrift,
    AuthPortError,
    GuardrailBlocked,
    KernelError,
    PermanentPortError,
    TransientPortError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from dispute_agent.modules.model_gateway.foundry_agent_service.lock import AgentLockEntry


@dataclass(frozen=True)
class DeployedVersion:
    model: str
    instructions: str


@dataclass(frozen=True)
class ProviderReply:
    """One response, before anything in it has been believed."""

    text: str
    status: str
    incomplete_reason: str | None
    input_tokens: int | None
    output_tokens: int | None
    model: str
    #: Every keyword the request was sent with, for the "no sampling parameters" check.
    sent: dict[str, Any]


class AgentServiceClient(Protocol):
    def get_version(self, agent_name: str, version: str) -> DeployedVersion:
        """Raises AgentDrift if the agent or version does not exist."""
        ...

    def respond(
        self, agent: AgentLockEntry, user_input: str, max_output_tokens: int
    ) -> ProviderReply:
        """One call. Raises the kernel's typed errors, never the SDK's."""
        ...

    def create_version(self, agent_name: str, model: str, instructions: str) -> str:
        """For `make agents-sync` only; the runtime never creates or edits agents."""
        ...


class AzureAgentServiceClient:
    """Implements AgentServiceClient over azure-ai-projects 2.x. Entra ID only (I14)."""

    def __init__(self, endpoint: str, *, timeout_s: float) -> None:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        # No key, no connection string: DefaultAzureCredential uses `az login` locally.
        self._project = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
        self._timeout_s = timeout_s
        self._openai: Any = None

    def get_version(self, agent_name: str, version: str) -> DeployedVersion:
        with _translated():
            details = self._project.agents.get_version(agent_name, version)
        definition: Any = details.definition
        return DeployedVersion(
            model=str(getattr(definition, "model", "")),
            instructions=str(getattr(definition, "instructions", "") or ""),
        )

    def respond(
        self, agent: AgentLockEntry, user_input: str, max_output_tokens: int
    ) -> ProviderReply:
        sent: dict[str, Any] = {
            "input": user_input,
            "max_output_tokens": max_output_tokens,
            "store": False,
            "extra_body": {
                "agent_reference": {
                    "name": agent.agent_name,
                    "version": agent.version,
                    "type": "agent_reference",
                }
            },
        }
        with _translated():
            if self._openai is None:
                self._openai = self._project.get_openai_client(
                    timeout=self._timeout_s, max_retries=0
                )
            response = self._openai.responses.create(**sent)
        usage = getattr(response, "usage", None)
        details = getattr(response, "incomplete_details", None)
        return ProviderReply(
            text=str(getattr(response, "output_text", "") or ""),
            status=str(getattr(response, "status", "") or ""),
            incomplete_reason=str(getattr(details, "reason", "") or "") or None,
            input_tokens=_int_or_none(getattr(usage, "input_tokens", None)),
            output_tokens=_int_or_none(getattr(usage, "output_tokens", None)),
            model=str(getattr(response, "model", "") or agent.deployment),
            sent={k: v for k, v in sent.items() if k != "input"},
        )

    def create_version(self, agent_name: str, model: str, instructions: str) -> str:
        from azure.ai.projects.models import PromptAgentDefinition

        with _translated():
            created = self._project.agents.create_version(
                agent_name=agent_name,
                definition=PromptAgentDefinition(model=model, instructions=instructions),
            )
        return str(created.version)


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


@contextmanager
def _translated() -> Iterator[None]:
    """Map Azure and OpenAI exceptions onto the kernel's (foundry.md section 7).

    Messages name the kind of failure, never the request or response body (I12, I14).
    """
    try:
        yield
    except Exception as exc:
        mapped = map_error(exc)
        if mapped is None:
            raise
        raise mapped from None


def map_error(exc: BaseException) -> KernelError | None:
    """The error table, as one function, so it can be tested row by row.

    `maybe_billed` marks the failures after which Azure may still charge for a generation:
    a timeout (the server keeps generating), a 5xx, and anything unrecognised. The gateway
    charges those at the worst case (review, M1). A 429, a refused connection or an auth
    failure never reached a model.
    """
    mapped = _map(exc)
    if mapped is not None and _maybe_billed(exc):
        mapped.maybe_billed = True
    return mapped


def _maybe_billed(exc: BaseException) -> bool:
    import openai
    from azure.core import exceptions as azure_errors

    if isinstance(exc, openai.APITimeoutError):
        return True
    if isinstance(exc, openai.APIStatusError | azure_errors.HttpResponseError):
        return int(getattr(exc, "status_code", 0) or 0) >= 500
    known = (openai.APIConnectionError, openai.APIStatusError, azure_errors.AzureError)
    return isinstance(exc, openai.OpenAIError) and not isinstance(exc, known)


def _map(exc: BaseException) -> KernelError | None:
    import openai
    from azure.core import exceptions as azure_errors

    try:
        from azure.identity import CredentialUnavailableError
    except ImportError:  # pragma: no cover
        CredentialUnavailableError = ()  # type: ignore[assignment,misc]  # noqa: N806

    if isinstance(exc, CredentialUnavailableError | azure_errors.ClientAuthenticationError):
        return AuthPortError("Azure sign-in is not available; run `az login`", status=401)
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        return AuthPortError("the Foundry project refused this identity", status=exc.status_code)
    if isinstance(exc, openai.NotFoundError | azure_errors.ResourceNotFoundError):
        return AgentDrift("the pinned agent version does not exist in Foundry")
    if isinstance(exc, openai.RateLimitError):
        return TransientPortError("rate limited", retry_after_s=_retry_after(exc))
    if isinstance(exc, openai.APITimeoutError | openai.APIConnectionError):
        return TransientPortError("Foundry did not respond in time")
    if isinstance(exc, openai.BadRequestError):
        if "content_filter" in str(getattr(exc, "code", "") or ""):
            return GuardrailBlocked("the deployment's content filter blocked the input")
        return PermanentPortError("Foundry refused the request", status=400)
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code >= 500:
            return TransientPortError(f"Foundry returned {exc.status_code}")
        return PermanentPortError(f"Foundry returned {exc.status_code}", status=exc.status_code)
    if isinstance(exc, azure_errors.ServiceRequestError):
        return TransientPortError("could not reach the Foundry project")
    if isinstance(exc, azure_errors.HttpResponseError):
        status = int(getattr(exc, "status_code", 0) or 0)
        if status in (401, 403):
            return AuthPortError("the Foundry project refused this identity", status=status)
        if status == 429 or status >= 500:
            return TransientPortError(f"Foundry returned {status}")
        return PermanentPortError(f"Foundry returned {status}", status=status or None)
    if isinstance(exc, openai.OpenAIError | azure_errors.AzureError):
        # A provider failure no row above names (a malformed response, a new error type).
        # Typed, so it is recorded and the case escalates rather than crashing (review, M2).
        return PermanentPortError(f"unexpected provider error ({type(exc).__name__})")
    return None


def _retry_after(exc: Any) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    for name, scale in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
        raw = headers.get(name)
        if raw is not None:
            try:
                return float(raw) / scale
            except ValueError:
                continue
    return None


__all__ = [
    "AgentServiceClient",
    "AzureAgentServiceClient",
    "DeployedVersion",
    "ProviderReply",
    "map_error",
]
