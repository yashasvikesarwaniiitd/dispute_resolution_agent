"""`make llm-check`: one tiny Haiku call; prints model id, latency and token counts only (P4-12).

Never prints the prompt or the answer (I14). Spends a fraction of a cent on the key in
ANTHROPIC_API_KEY, through the same guard and call log as every other model call.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from dispute_agent.app import DEFAULT_CONFIG_DIR, build_haiku_gateway, build_model_policy
from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.errors import KernelError
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.reasoning import ChatMessage, ModelRequest, RenderedPrompt

if TYPE_CHECKING:
    from dispute_agent.kernel.ports import ModelGateway

#: A fixed, content-free probe. Its schema is the smallest object, so the check is cheap.
PROBE_SYSTEM = "Reply with a JSON object only."
PROBE_USER = 'Return {"ok": true}.'
PROBE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
}


def probe(gateway: ModelGateway) -> str:
    """The one line this command prints: numbers and ids, never text."""
    request = ModelRequest(
        purpose="case_investigator",
        prompt=RenderedPrompt(
            name="llm_check",
            version="v1",
            hash=payload_hash(PROBE_SYSTEM),
            messages=(
                ChatMessage(role="system", content=PROBE_SYSTEM),
                ChatMessage(role="user", content=PROBE_USER),
            ),
            output_schema=PROBE_SCHEMA,
        ),
        max_output_tokens=256,
    )
    response = gateway.complete(request)
    usage = response.usage
    return (
        f"model={response.model_id} route={response.route} latency_ms={usage.latency_ms} "
        f"input_tokens={usage.input_tokens} output_tokens={usage.output_tokens} "
        f"cost_usd={usage.cost_usd:.6f}"
    )


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    settings = Settings()
    try:
        gateway = build_haiku_gateway(settings, build_model_policy(DEFAULT_CONFIG_DIR, settings))
        print(probe(gateway))
    except KernelError as failed:
        print(f"llm-check failed: {type(failed).__name__}: {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
