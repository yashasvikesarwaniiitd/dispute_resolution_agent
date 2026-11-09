"""Recorded answers, for offline runs and tests (ADR 0049).

Replays a verdict per shipment - from a live evaluation run's saved answers, or written by a
test - so the whole Return-then-check path runs with no model and no spend. A shipment with no
recorded answer fails like a model that could not answer, which is what the case must survive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from dispute_agent.kernel.domain.errors import InvalidModelOutput
from dispute_agent.kernel.domain.image import ImageVerdict

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dispute_agent.kernel.domain.image import ImageCheckRequest


@dataclass
class RecordedImageChecker:
    """Implements ImageChecker."""

    answers: Mapping[str, Mapping[str, Any]]
    calls: list[str] = field(default_factory=list)

    def check(self, req: ImageCheckRequest) -> ImageVerdict:
        self.calls.append(req.shipment_ref)
        answer = self.answers.get(req.shipment_ref)
        if answer is None:
            raise InvalidModelOutput(f"no recorded answer for {req.shipment_ref}")
        return ImageVerdict(
            parcel_count=answer["parcel_count"],
            packaging=answer["packaging"],
            label_visible=answer["label_visible"],
            size_from_photo=answer["size_from_photo"],
            observations=tuple(answer.get("observations", ())),
            confidence=answer["confidence"],
            model_id=str(answer.get("model_id", "recorded")),
            # Said on the screen: a replayed verdict is not a live look (ADR 0049).
            route=f"recorded:{answer.get('route', 'none')}",
            prompt_name=str(answer.get("prompt_name", "image_check")),
            prompt_version=str(answer.get("prompt_version", "v1")),
            prompt_hash=str(answer.get("prompt_hash", "recorded")),
        )


__all__ = ["RecordedImageChecker"]
