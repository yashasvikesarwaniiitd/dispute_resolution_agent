"""Each synthetic shipment's parcel photo, for the screens to show (ADR 0049).

Real, freely licensed photographs (credits in the photos folder) stand in for the photo a
seller or hub attaches. The console only shows them; what a photo means for a case is decided
by code and people, with the image check's verdict as advice (ADR 0049). A shipment added on
the console may carry an uploaded photo (ADR 0056), kept beside the case database.
"""

from __future__ import annotations

from pathlib import Path

from dispute_agent.modules.evidence.synthetic_shipments.added import photo_in

PHOTOS = (
    Path(__file__).resolve().parents[1] / "modules" / "evidence" / "synthetic_shipments" / "photos"
)
#: Photos uploaded with added shipments: `data/photos`, beside the case database.
ADDED = Path("data") / "photos"
CREDIT = "Photo: Wikimedia Commons, free licence (credits in the project's photos folder)."


def photo_for(shipment_ref: str) -> Path | None:
    """The shipment's photo, or None when it has none."""
    path = PHOTOS / f"{shipment_ref.lower()}.jpg"
    if path.is_file():
        return path
    found = photo_in(ADDED, shipment_ref)
    return found[0] if found else None


__all__ = ["ADDED", "CREDIT", "PHOTOS", "photo_for"]
