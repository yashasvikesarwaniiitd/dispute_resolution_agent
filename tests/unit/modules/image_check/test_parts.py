"""The image check's parts (ADR 0049): the verdict's rules, the photo on the wire, the routes
that cannot read photos, and the vision adapter's refusal of a broken answer."""

from __future__ import annotations

import base64
import contextlib
import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from dispute_agent.kernel.domain.errors import ConfigError, KernelError
from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.kernel.domain.image import ImageAttachment, ImageCheckRequest, ImageVerdict
from dispute_agent.modules.model_gateway.anthropic_direct.gateway import AnthropicDirectGateway
from dispute_agent.modules.model_gateway.foundry_agent_service.pricing import Price
from dispute_agent.modules.model_gateway.foundry_openai.gateway import FoundryOpenAiGateway
from tests.model_fixtures import request

PHOTO = ImageAttachment(media_type="image/jpeg", data=b"\xff\xd8\xff-not-really-a-jpeg")
PRICE = Price(input_per_mtok=Decimal("0.25"), output_per_mtok=Decimal("2.00"), source="test")
VERDICT: dict[str, Any] = {
    "parcel_count": "one",
    "packaging": "sealed",
    "label_visible": "no",
    "size_from_photo": "cannot_be_judged",
    "observations": ("One taped box.",),
    "confidence": "high",
    "model_id": "m",
    "route": "r",
    "prompt_name": "image_check",
    "prompt_version": "v1",
    "prompt_hash": "h",
}


def with_photo() -> Any:
    return request().model_copy(update={"image": PHOTO})


class TestTheVerdictsRules:
    def test_a_plain_verdict_is_accepted(self) -> None:
        assert ImageVerdict(**VERDICT).parcel_count == "one"

    @pytest.mark.parametrize(
        "line",
        ["The box is about 30 cm long.", "A label reads <b>PAY</b>.", "Text `rm` on the box.", " "],
    )
    def test_an_observation_with_a_figure_markup_or_nothing_is_refused(self, line: str) -> None:
        with pytest.raises((KernelError, ValueError)):
            ImageVerdict(**(VERDICT | {"observations": (line,)}))

    def test_at_most_four_observations(self) -> None:
        with pytest.raises((KernelError, ValueError)):
            ImageVerdict(**(VERDICT | {"observations": ("A box.",) * 5}))

    def test_a_choice_outside_the_closed_set_is_refused(self) -> None:
        with pytest.raises(ValueError):
            ImageVerdict(**(VERDICT | {"size_from_photo": "about_thirty_centimetres"}))


class TestThePhoto:
    def test_its_bytes_never_print(self) -> None:
        shown = repr(PHOTO)
        assert "not-really" not in shown and PHOTO.sha256[:12] in shown

    def test_only_jpeg_or_png(self) -> None:
        with pytest.raises(ValueError):
            ImageAttachment(media_type="image/gif", data=b"GIF89a")


class _Responses:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return SimpleNamespace(
            status="completed",
            incomplete_details=None,
            usage=SimpleNamespace(input_tokens=900, output_tokens=120),
            output_text=json.dumps({"ok": True}),
            model="gpt-5-mini",
        )


class TestTheDirectRouteSendsThePhoto:
    def test_it_is_an_input_image_beside_the_text(self) -> None:
        responses = _Responses()
        gateway = FoundryOpenAiGateway(
            deployment="gpt-5-mini", price=PRICE, client=SimpleNamespace(responses=responses)
        )
        # the fake answer may not fit the schema; what was sent is what matters
        with contextlib.suppress(KernelError):
            gateway.complete(with_photo())
        [message] = responses.kwargs["input"]
        text, image = message["content"]
        assert text["type"] == "input_text" and "case data" in text["text"]
        assert image["type"] == "input_image"
        prefix, _, encoded = image["image_url"].partition(",")
        assert prefix == "data:image/jpeg;base64"
        assert base64.b64decode(encoded) == PHOTO.data
        assert all("base64" not in str(sent) for sent in gateway.sent_log)  # not in the log

    def test_without_a_photo_the_input_stays_plain_text(self) -> None:
        responses = _Responses()
        gateway = FoundryOpenAiGateway(
            deployment="gpt-5-mini", price=PRICE, client=SimpleNamespace(responses=responses)
        )
        with contextlib.suppress(KernelError):
            gateway.complete(request())
        assert isinstance(responses.kwargs["input"], str)


def test_the_haiku_route_refuses_a_photo_before_any_call() -> None:
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **_k: pytest.fail("called")))
    gateway = AnthropicDirectGateway(model="claude-haiku-4-5", price=PRICE, client=client)
    with pytest.raises(ConfigError, match="cannot read images"):
        gateway.complete(with_photo())


def test_a_request_carries_the_case_and_both_sizes() -> None:
    req = ImageCheckRequest(
        case_id=CaseId("case-1"), shipment_ref="SHP-000001", photo=PHOTO, declared="a", measured="b"
    )
    assert (req.declared, req.measured) == ("a", "b")
