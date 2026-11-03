"""Shared model-side test data: a request built from the real prompt, and a valid answer.

Built from the real `case_investigator` schema rather than a toy one, so a gateway or
reasoner test that passes here passes against the schema a live model is held to.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.reasoning import ChatMessage, ModelRequest, RenderedPrompt

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
SCHEMA = json.loads(
    (PROMPTS / "case_investigator" / "v1" / "schema.json").read_text(encoding="utf-8")
)
#: A marker that must never appear in a log line (I14: content stays out of telemetry).
SECRET_MARKER = "CONTENT-MARKER-5521"


def valid_answer(**overrides: Any) -> dict[str, Any]:
    answer: dict[str, Any] = {
        "action": "request_seller_evidence",
        "confidence": "medium",
        "rationale": "The scan and the declared weight disagree and no photo is on file.",
        "findings": [
            {
                "kind": "missing_evidence",
                "statement": "No packed-parcel photo has been supplied.",
                "evidence_refs": ["f_charged"],
            }
        ],
        "action_parameters": {"requested_items": ["A photo of the packed parcel on a scale"]},
        "pattern_influence": {"considered": False, "changed_recommendation": False, "note": ""},
        "manipulation_flags": [],
    }
    answer.update(overrides)
    return answer


def request(max_output_tokens: int = 1200) -> ModelRequest:
    system = "system instructions"
    return ModelRequest(
        purpose="case_investigator",
        prompt=RenderedPrompt(
            name="case_investigator",
            version="v1",
            hash=payload_hash(system),
            messages=(
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=f"case data {SECRET_MARKER}"),
            ),
            output_schema=SCHEMA,
        ),
        max_output_tokens=max_output_tokens,
    )
