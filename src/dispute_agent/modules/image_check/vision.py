"""The image check through the model gateway: Foundry gpt-5-mini reads the photo (ADR 0049).

The prompt (`prompts/image_check/v1`) is rendered by the registry; the photo travels on the
`ModelRequest` and nowhere else. The gateway enforces the JSON schema with one retry; this
builds the `ImageVerdict`, whose own checks (no figures, no markup, at most four observations)
are the last word. An answer that breaks one is refused as `InvalidModelOutput`, never
repaired: the case then reaches a person with "the image check failed" on the record.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import ValidationError

from dispute_agent.kernel.domain.errors import DomainValidationError, InvalidModelOutput
from dispute_agent.kernel.domain.image import ImageVerdict
from dispute_agent.kernel.domain.reasoning import ModelRequest

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.image import ImageCheckRequest
    from dispute_agent.kernel.ports import ModelGateway, PromptRegistry

PROMPT = "image_check"
FIELDS = ("parcel_count", "packaging", "label_visible", "size_from_photo", "confidence")


@dataclass
class VisionImageChecker:
    """Implements ImageChecker."""

    gateway: ModelGateway
    prompts: PromptRegistry
    version: str = "v1"
    max_output_tokens: int = 4000  # a reasoning model spends most of its budget thinking

    def check(self, req: ImageCheckRequest) -> ImageVerdict:
        prompt = self.prompts.load(
            PROMPT,
            self.version,
            {
                "ctx": {
                    "shipment_ref": req.shipment_ref,
                    "declared": req.declared,
                    "measured": req.measured,
                }
            },
            {},
        )
        answer = self.gateway.complete(
            ModelRequest(
                case_id=req.case_id,
                purpose="image_check",
                prompt=prompt,
                max_output_tokens=self.max_output_tokens,
                image=req.photo,
            )
        )
        out = dict(answer.output)
        try:
            notes = out.get("observations")
            return ImageVerdict(
                **{name: str(out[name]) for name in FIELDS},
                observations=tuple(str(n) for n in notes) if isinstance(notes, list) else (),
                model_id=answer.model_id,
                route=answer.route,
                prompt_name=prompt.name,
                prompt_version=prompt.version,
                prompt_hash=str(prompt.hash),
            )
        except (KeyError, ValidationError, DomainValidationError) as broken:
            raise InvalidModelOutput(f"the image check's answer broke a rule: {broken}") from None


__all__ = ["PROMPT", "VisionImageChecker"]
