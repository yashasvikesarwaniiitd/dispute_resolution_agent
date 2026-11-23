"""Each synthetic shipment's parcel photo (ADR 0049; credits in `photos/CREDITS.md`).

Real, freely licensed photographs stand in for the photo a hub keeps on a shipment record.
`PhotoFolder` hands the kernel one by shipment reference; nothing else reads the files. A
shipment added on the console (ADR 0056) may carry an uploaded photo, kept in `added`; the
built-in photos win on a shared reference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dispute_agent.kernel.domain.image import ImageAttachment
from dispute_agent.modules.evidence.synthetic_shipments.added import photo_in

FOLDER = Path(__file__).parent / "photos"


@dataclass
class PhotoFolder:
    """Implements PhotoSource."""

    folder: Path = field(default=FOLDER)
    added: Path | None = None

    def photo(self, shipment_ref: str) -> ImageAttachment | None:
        path = self.folder / f"{shipment_ref.lower()}.jpg"
        if path.is_file():
            return ImageAttachment(media_type="image/jpeg", data=path.read_bytes())
        found = photo_in(self.added, shipment_ref) if self.added is not None else None
        if found is None:
            return None
        uploaded, media_type = found
        return ImageAttachment(media_type=media_type, data=uploaded.read_bytes())


__all__ = ["FOLDER", "PhotoFolder"]
