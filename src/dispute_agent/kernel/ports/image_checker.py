"""The ImageChecker and PhotoSource ports (ADR 0049).

`PhotoSource` hands the kernel a shipment's parcel photo; `ImageChecker` asks a vision model
to describe it. The checker returns an `ImageVerdict` - closed choices and a few checked
observations - and nothing else: it holds no store, no channel and no write port (I1).
"""

from __future__ import annotations

from typing import Protocol

from dispute_agent.kernel.domain.image import ImageAttachment, ImageCheckRequest, ImageVerdict


class PhotoSource(Protocol):
    def photo(self, shipment_ref: str) -> ImageAttachment | None:
        """The shipment's parcel photo, or None when it has none."""
        ...


class ImageChecker(Protocol):
    def check(self, req: ImageCheckRequest) -> ImageVerdict:
        """Describe the photo. Raises a KernelError (with usage) when it cannot."""
        ...
