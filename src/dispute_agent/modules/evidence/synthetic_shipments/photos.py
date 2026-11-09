"""Each synthetic shipment's parcel photo (ADR 0049; credits in `photos/CREDITS.md`).

Real, freely licensed photographs stand in for the photo a hub keeps on a shipment record.
`PhotoFolder` hands the kernel one by shipment reference; nothing else reads the files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dispute_agent.kernel.domain.image import ImageAttachment

FOLDER = Path(__file__).parent / "photos"


@dataclass
class PhotoFolder:
    """Implements PhotoSource."""

    folder: Path = field(default=FOLDER)

    def photo(self, shipment_ref: str) -> ImageAttachment | None:
        path = self.folder / f"{shipment_ref.lower()}.jpg"
        if not path.is_file():
            return None
        return ImageAttachment(media_type="image/jpeg", data=path.read_bytes())


__all__ = ["FOLDER", "PhotoFolder"]
