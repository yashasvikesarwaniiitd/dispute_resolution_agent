"""The image check's inputs and its verdict (ADR 0049).

A vision model may look at a parcel photo and describe it - how many parcels, how they are
packed, whether a label shows, whether size can be judged at all - in closed choices and a
few short observations. That is all it may say. The verdict is advice for a person: it never
approves, credits or closes anything (I1, I7), and nothing in it is a figure (I9).

The photo's bytes travel in an `ImageAttachment`, which is never written to a prompt, a log or
a store: only its media type and content hash are recorded.
"""

from __future__ import annotations

import hashlib
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.ids import CaseId

_FROZEN = ConfigDict(frozen=True, extra="forbid", strict=True)

#: The largest photo sent to a model. A shipment photo is 960 px wide, well under this.
MAX_IMAGE_BYTES = 4 * 1024 * 1024
#: Observations are short, few, and say no figures (R09 for model-written text).
MAX_OBSERVATIONS = 4
MAX_OBSERVATION_CHARS = 200
_DIGIT = re.compile(r"\d")
_MARKUP = re.compile(r"[<>`]")

ParcelCount = Literal["one", "more_than_one", "cannot_tell"]
Packaging = Literal["sealed", "open", "damaged", "cannot_tell"]
LabelVisible = Literal["yes", "no", "cannot_tell"]
SizeFromPhoto = Literal[
    "cannot_be_judged", "looks_smaller_than_measured", "looks_consistent_with_measured"
]
Confidence = Literal["low", "medium", "high"]


class ImageAttachment(BaseModel):
    """A photo for one model call. The bytes are never stored or logged; the hash is."""

    model_config = _FROZEN

    media_type: Literal["image/jpeg", "image/png"]
    data: Annotated[bytes, Field(min_length=1, max_length=MAX_IMAGE_BYTES)]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    def __repr__(self) -> str:  # never print the bytes
        return (
            f"ImageAttachment({self.media_type}, {len(self.data)} bytes, sha256={self.sha256[:12]})"
        )


class ImageCheckRequest(BaseModel):
    """What the image check is given: the photo, and code's own size facts as text."""

    model_config = _FROZEN

    case_id: CaseId
    shipment_ref: str
    photo: ImageAttachment
    #: Code's facts, rendered ("20 x 15 x 10 cm, 0.48 kg"). Trusted: they come from evidence.
    declared: str
    measured: str


class ImageVerdict(BaseModel):
    """What the image check says. Closed choices, and observations checked in code."""

    model_config = _FROZEN

    parcel_count: ParcelCount
    packaging: Packaging
    label_visible: LabelVisible
    size_from_photo: SizeFromPhoto
    observations: Annotated[tuple[str, ...], Field(max_length=MAX_OBSERVATIONS)]
    confidence: Confidence
    #: Who said it: the model and the instructions it was given (recorded on every verdict).
    model_id: str
    route: str
    prompt_name: str
    prompt_version: str
    prompt_hash: str

    @field_validator("observations")
    @classmethod
    def _plain(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for line in value:
            if not line.strip() or len(line) > MAX_OBSERVATION_CHARS:
                raise DomainValidationError("an observation is empty or too long")
            if _DIGIT.search(line):
                raise DomainValidationError("an observation states a figure (R09)")
            if _MARKUP.search(line):
                raise DomainValidationError("an observation contains markup")
        return value


__all__ = [
    "MAX_IMAGE_BYTES",
    "MAX_OBSERVATIONS",
    "Confidence",
    "ImageAttachment",
    "ImageCheckRequest",
    "ImageVerdict",
    "LabelVisible",
    "Packaging",
    "ParcelCount",
    "SizeFromPhoto",
]
