"""Each synthetic shipment's parcel photo, for the screens to show (ADR 0049).

Real, freely licensed photographs (credits in the photos folder) stand in for the photo a
seller or hub attaches. The console only shows them; what a photo means for a case is decided
by code and people, with the image check's verdict as advice (ADR 0049).
"""

from __future__ import annotations

from pathlib import Path

PHOTOS = (
    Path(__file__).resolve().parents[1] / "modules" / "evidence" / "synthetic_shipments" / "photos"
)
CREDIT = "Photo: Wikimedia Commons, free licence (credits in the project's photos folder)."


def photo_for(shipment_ref: str) -> Path | None:
    """The shipment's photo, or None when it has none."""
    path = PHOTOS / f"{shipment_ref.lower()}.jpg"
    return path if path.is_file() else None


__all__ = ["CREDIT", "PHOTOS", "photo_for"]
