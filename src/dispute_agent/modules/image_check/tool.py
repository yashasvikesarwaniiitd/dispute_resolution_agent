"""The `analyze_parcel_image` investigation tool (ADR 0071).

The same check the returned-photo round runs (ADR 0049), on the agent's request: the parcel
photo and code's own size facts go to the configured `ImageChecker`. With the vision adapter
that is one paid model call, guarded and logged like every other (I14).

What comes back: the verdict's closed choices as data, and its free-text observations as
`UntrustedText` (I5) - a model wrote them. A missing photo or a failed check is `ok=False`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from dispute_agent.kernel.domain.errors import KernelError
from dispute_agent.kernel.domain.image import ImageCheckRequest
from dispute_agent.kernel.domain.tools import ToolOutput, ToolSpec
from dispute_agent.kernel.domain.values import UntrustedText

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from dispute_agent.kernel.domain.evidence import EvidenceResult
    from dispute_agent.kernel.domain.tools import ToolContext
    from dispute_agent.kernel.ports import ImageChecker, PhotoSource


class NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParcelImageTool:
    """Implements InvestigationTool."""

    spec = ToolSpec(
        name="analyze_parcel_image",
        description=(
            "Look at the parcel photo for this shipment: how many parcels, the packaging, "
            "whether the label is visible, and whether the size in the photo fits the declared "
            "or the measured size. Costs a model call; use it when the photo could settle "
            "what the numbers cannot."
        ),
        input_schema=NoArgs.model_json_schema(),
    )

    def __init__(
        self,
        checker: ImageChecker,
        photos: PhotoSource,
        size_facts: Callable[[Sequence[EvidenceResult]], tuple[str, str]],
    ) -> None:
        self._checker = checker
        self._photos = photos
        self._size_facts = size_facts

    def run(self, ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
        NoArgs.model_validate(dict(args))
        ref = ctx.facts.shipment_ref
        photo = self._photos.photo(ref)
        if photo is None:
            return ToolOutput(tool=self.spec.name, ok=False, error="no_photo")
        declared, measured = self._size_facts(ctx.evidence)
        try:
            verdict = self._checker.check(
                ImageCheckRequest(
                    case_id=ctx.case_id,
                    shipment_ref=ref,
                    photo=photo,
                    declared=declared,
                    measured=measured,
                )
            )
        except KernelError as failed:
            return ToolOutput(
                tool=self.spec.name, ok=False, error=f"image_check_failed:{failed.code}"
            )
        return ToolOutput(
            tool=self.spec.name,
            ok=True,
            data=verdict.model_dump(mode="json", exclude={"observations"})
            | {"photo_sha256": photo.sha256},
            untrusted=tuple(
                UntrustedText.create(line, "image_check", f"{ctx.case_id}:tool:{i}")
                for i, line in enumerate(verdict.observations)
            ),
        )


__all__ = ["ParcelImageTool"]
