"""ADR 0071: the five investigation tools, each in the module that owns its data, behind one
port, called through the Toolbox - which never raises."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest

from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.errors import KernelError
from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.kernel.domain.image import ImageAttachment, ImageVerdict
from dispute_agent.kernel.domain.tools import ToolContext, ToolOutput, ToolSpec
from dispute_agent.kernel.orchestrator.toolbox import Toolbox
from dispute_agent.modules.cross_case.sql_context.repository import CaseRow
from dispute_agent.modules.cross_case.sql_context.tools import PastDisputesTool
from dispute_agent.modules.evidence.synthetic_shipments.catalogue import catalogue
from dispute_agent.modules.evidence.synthetic_shipments.tools import (
    PastShipmentsTool,
    ScannerStatisticsTool,
)
from dispute_agent.modules.image_check.tool import ParcelImageTool
from dispute_agent.modules.reasoning.explainer.policy_index import Passage
from dispute_agent.modules.reasoning.policy_tool import PolicyLookupTool
from tests.unit.modules.image_check.test_parts import VERDICT

DISPUTE = "SHP-102026"  # a catalogue dispute: CaseKraft's tray, the scanner anomaly
NOW = datetime(2026, 11, 27, tzinfo=UTC)


def ctx(ref: str = DISPUTE, seller: str | None = None) -> ToolContext:
    built = catalogue().by_ref.get(ref)
    return ToolContext(
        case_id=CaseId("case_1"),
        facts=CaseFacts(
            shipment_ref=ref,
            claim_ref=ref.replace("SHP", "CLM"),
            seller_ref=seller or (built.shipment.seller_id if built else "SELL-0001"),
            seller_contact="seller@example.invalid",
            raised_at=NOW,
        ),
    )


def rows() -> dict[str, Any]:
    return {r.ref: r for s in catalogue().sellers for r in s.shipments}


class TestTheToolbox:
    def test_unknown_tools_bad_arguments_and_crashes_are_answers_not_exceptions(self) -> None:
        class Broken:
            spec = ToolSpec(name="broken_tool", description="x", input_schema={})

            def run(self, ctx: ToolContext, args: Any) -> ToolOutput:
                raise RuntimeError("boom")

        box = Toolbox([Broken(), PolicyLookupTool(lambda q, k: [])])
        assert box.call("nope", ctx()).error == "unknown_tool"
        assert box.call("broken_tool", ctx()).error == "tool_failed:RuntimeError"
        assert box.call("lookup_policy", ctx(), {"query": ""}).error == "bad_arguments"
        assert box.call("lookup_policy", ctx(), {"query": "R16", "extra": 1}).error == (
            "bad_arguments"
        )

    def test_bad_arguments_say_which_argument_and_why_not_the_value(self) -> None:
        out = Toolbox([PastShipmentsTool()]).call(
            "search_past_shipments", ctx(PACKAGING_CHANGE), {"limit": 20, "colour": "red"}
        )
        assert out.error == "bad_arguments"
        assert out.data["problems"] == [
            "limit: Input should be less than or equal to 10",
            "colour: Extra inputs are not permitted",
        ]

    def test_accepted_values_are_shown_where_a_tool_gives_them_and_never_break_a_case(
        self,
    ) -> None:
        class Failing:
            spec = ToolSpec(name="failing_hint", description="x", input_schema={})

            def accepted_values(self, ctx: ToolContext) -> str:
                raise RuntimeError("boom")

            def run(self, ctx: ToolContext, args: Any) -> ToolOutput:
                return ToolOutput(tool="failing_hint", ok=True)

        hints = Toolbox(
            [PastShipmentsTool(), Failing(), PolicyLookupTool(lambda q, k: [])]
        ).accepted(ctx(PACKAGING_CHANGE))
        assert set(hints) == {"search_past_shipments"}

    def test_two_tools_cannot_share_a_name(self) -> None:
        with pytest.raises(ValueError, match="two tools"):
            Toolbox([PastShipmentsTool(), PastShipmentsTool()])

    def test_every_tool_tells_a_model_its_arguments(self) -> None:
        for tool in (PastShipmentsTool(), ScannerStatisticsTool(), PolicyLookupTool()):
            assert tool.spec.input_schema["type"] == "object"


PACKAGING_CHANGE = "SHP-105903"  # IC-09: booked the old tube, shipped in the new box
OTHER_SELLER_REFS = {
    r.ref for s in catalogue().sellers if s.seller.seller_id != "SELL-0105" for r in s.shipments
}


class TestPastShipments:
    def test_the_accepted_ids_shown_are_exactly_the_ids_the_search_accepts(self) -> None:
        """Description, schema and validation agree: every id offered works, a name does not,
        and a wrong id is answered with the right ones."""
        tool, case = PastShipmentsTool(), ctx(PACKAGING_CHANGE)
        line = tool.accepted_values(case)
        assert "limit: 1-10" in line and "= this parcel" in line
        boxes = [p.split(" ")[0] for p in line.split("packaging_id: ")[1].split(";")[0].split(", ")]
        assert boxes[0] == "any" and len(boxes) > 2
        for box in boxes:
            assert tool.run(case, {"packaging_id": box}).ok, box
        wrong = tool.run(case, {"packaging_id": "mat box"})
        assert wrong.error == "unknown_packaging_id"
        assert [a.split(" ")[0] for a in wrong.data["accepted"]] == boxes
        assert PastShipmentsTool.spec.input_schema["properties"]["limit"]["maximum"] == 10

    def test_only_this_seller_s_earlier_records_and_never_another_seller_s(self) -> None:
        out = PastShipmentsTool().run(ctx(PACKAGING_CHANGE), {"packaging_id": "any", "limit": 10})
        assert out.ok
        every, current = rows(), rows()[PACKAGING_CHANGE]
        assert out.data["records"]
        for record in out.data["records"]:
            assert record["ref"] not in OTHER_SELLER_REFS and record["ref"] != PACKAGING_CHANGE
            assert every[record["ref"]].measured_at < current.measured_at  # never the future
        assert not ({"demo_label", "role", "fixture"} & set(out.data["records"][0]))

    def test_the_authoritative_basis_is_named_and_everything_else_is_supplementary(self) -> None:
        current = rows()[PACKAGING_CHANGE]
        same = {"packaging_id": current.packaging_id, "quantity": current.quantity}
        assert (
            PastShipmentsTool().run(ctx(PACKAGING_CHANGE), same).data["basis"]
            == "authoritative_basis"
        )
        wider = PastShipmentsTool().run(ctx(PACKAGING_CHANGE), {"packaging_id": "any"}).data
        assert wider["basis"] == "supplementary"
        assert any("packaging" in d for d in wider["differs_from_authoritative"])
        assert any("not the authoritative baseline" in n for n in wider["limitations"])
        assert "verdict" not in str(wider["stats"])  # no verdict words outside the comparison

    def test_a_refined_query_finds_the_new_packaging_and_its_statistics(self) -> None:
        first = PastShipmentsTool().run(ctx(PACKAGING_CHANGE), {"packaging_id": "any"}).data
        boxes = {p["packaging_id"] for p in first["available_filters"]["packagings"]}
        assert {"PKG-PS-TUBE-L", "PKG-PS-BOX-MAT"} <= boxes
        refined = (
            PastShipmentsTool()
            .run(
                ctx(PACKAGING_CHANGE), {"packaging_id": "PKG-PS-BOX-MAT", "date_from": "2026-09-01"}
            )
            .data
        )
        assert refined["stats"]["sample_size"] >= 5
        assert set(refined["stats"]["by_packaging"]) == {"PKG-PS-BOX-MAT"}
        # The parcel is the size of the new box: about 1x its typical volume.
        assert 0.85 <= float(refined["stats"]["this_parcel_volume_to_typical"]) <= 1.15

    def test_an_unknown_filter_value_comes_back_with_the_values_that_exist(self) -> None:
        out = PastShipmentsTool().run(ctx(PACKAGING_CHANGE), {"scanner_id": "SCN-NOPE"})
        assert not out.ok and out.error == "unknown_scanner_id"
        assert "SCN-DEL-01-1" in out.data["accepted"]  # the right ids, not every filter

    def test_few_matches_give_a_sample_size_and_a_limitation_not_a_typical_value(self) -> None:
        out = (
            PastShipmentsTool().run(ctx("SHP-106902"), {"quantity": 4}).data
        )  # 2 earlier 4-tin rows
        assert out["stats"] == {"sample_size": 2}
        assert any("too few" in n for n in out["limitations"])

    def test_a_shipment_nobody_describes_is_a_recorded_miss(self) -> None:
        assert (
            PastShipmentsTool().run(ctx("SHP-000001"), {}).error == "no_history_for_this_shipment"
        )

    def test_another_seller_s_shipment_is_never_this_parcel(self) -> None:
        out = PastShipmentsTool().run(ctx(PACKAGING_CHANGE, seller="SELL-0101"), {})
        assert out.error == "no_history_for_this_shipment"


class TestScannerStatistics:
    def test_the_drifting_scanner_stands_out_against_its_neighbour(self) -> None:
        out = ScannerStatisticsTool().run(ctx("SHP-110901"), {})  # IC-06, SCN-DEL-01-2 in its drift
        assert out.ok
        recent = out.data["windows"]["last_60_days"]
        mine, others = recent["this_scanner"], recent["other_scanners_at_hub"]
        assert float(mine["share_over_1_25x_declared"]) > float(others["share_over_1_25x_declared"])
        assert isinstance(mine["readings"], int) and "SHP-" not in str(out.data)  # counts only

    def test_the_hub_level_shows_a_hub_wide_drift(self) -> None:
        out = ScannerStatisticsTool().run(ctx("SHP-106903"), {"level": "hub"})  # IC-13, HUB-HYD-04
        recent = out.data["windows"]["last_60_days"]
        assert float(recent["this_hub"]["share_over_1_25x_declared"]) > float(
            recent["other_hubs"]["share_over_1_25x_declared"]
        )

    @pytest.mark.usefixtures("approved_catalogue")
    def test_a_group_too_small_to_share_is_insufficient_data(self) -> None:
        # Frozen environment: each approved scanner serves one seller, below MIN_SELLERS.
        out = ScannerStatisticsTool().run(ctx(DISPUTE), {})
        assert out.data["windows"]["last_365_days"]["this_scanner"] == "insufficient_data"


class FakeCases:
    def __init__(self, found: list[tuple[CaseRow, str, str | None]]) -> None:
        self.found = found

    def recent_outcomes(self, limit: int) -> list[tuple[CaseRow, str, str | None]]:
        return self.found[:limit]


def case(
    ref: str, seller: str, state: str, opened: datetime = NOW - timedelta(days=3)
) -> tuple[CaseRow, str, str | None]:
    return CaseRow(f"case_{ref}", ref, ref.replace("SHP", "CLM"), seller, opened), state, None


class TestPastDisputes:
    KEYS: ClassVar[dict[str, dict[str, str]]] = {
        "SHP-1": {"scanner": "SCN-A"},
        "SHP-2": {"scanner": "SCN-A"},
        "SHP-3": {"scanner": "SCN-A"},
        "SHP-4": {"scanner": "SCN-B"},
    }

    def tool(self) -> PastDisputesTool:
        return PastDisputesTool(
            FakeCases(
                [
                    case("SHP-1", "SELL-1", "RESOLVED_CREDITED"),  # this dispute itself
                    case("SHP-2", "SELL-1", "CLOSED_UPHELD"),
                    case("SHP-3", "SELL-2", "RESOLVED_CREDITED"),
                    case("SHP-4", "SELL-2", "RESOLVED_CREDITED"),
                ]
            ),
            lambda ref: self.KEYS[ref],
        )

    def test_this_seller_s_are_listed_and_too_few_others_are_insufficient_data(self) -> None:
        out = self.tool().run(ctx("SHP-1", "SELL-1"), {"match": "scanner"})
        assert [d["shipment_ref"] for d in out.data["this_seller"]] == ["SHP-2"]
        assert out.data["other_sellers_by_outcome"] == "insufficient_data"  # 1 case, 1 seller
        assert "SHP-3" not in str(out.data) and "SELL-2" not in str(out.data)

    def test_other_sellers_are_counted_only_above_both_thresholds(self) -> None:
        others = [case(f"SHP-{n}", f"SELL-{10 + n % 3}", "RESOLVED_CREDITED") for n in range(5, 10)]
        tool = PastDisputesTool(FakeCases(others), lambda ref: {"scanner": "SCN-A"})
        out = tool.run(ctx("SHP-1", "SELL-1"), {"match": "scanner"})
        assert out.data["other_sellers_by_outcome"] == {
            "RESOLVED_CREDITED": 5
        }  # 5 cases, 3 sellers
        assert "SELL-1" not in str(out.data["other_sellers_by_outcome"]) and "SHP-5" not in str(
            out.data
        )

    def test_by_seller_by_default(self) -> None:
        out = self.tool().run(ctx("SHP-1", "SELL-1"), {})
        assert out.data["this_seller_total"] == 1

    def test_a_dispute_opened_after_this_one_is_not_a_past_dispute(self) -> None:
        later = case("SHP-2", "SELL-1", "CLOSED_UPHELD", opened=NOW)  # AUD-25: not before it
        tool = PastDisputesTool(FakeCases([later]), lambda ref: {"scanner": "SCN-A"})
        assert tool.run(ctx("SHP-1", "SELL-1"), {"match": "scanner"}).data["this_seller_total"] == 0


class Photos:
    def __init__(self, photo: ImageAttachment | None) -> None:
        self._photo = photo

    def photo(self, shipment_ref: str) -> ImageAttachment | None:
        return self._photo


class Checker:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.asked: list[Any] = []

    def check(self, req: Any) -> ImageVerdict:
        self.asked.append(req)
        if self.fail:
            raise KernelError("down")
        return ImageVerdict(**VERDICT)


PHOTO = ImageAttachment(media_type="image/png", data=b"\x89PNG fake")


class TestParcelImage:
    def test_the_verdict_is_data_and_its_words_are_untrusted(self) -> None:
        checker = Checker()
        out = ParcelImageTool(
            checker, Photos(PHOTO), lambda ev: ("1 x 1 x 1 cm", "2 x 2 x 2 cm")
        ).run(ctx(), {})
        assert out.ok and out.data["parcel_count"] == "one"
        assert [u.text for u in out.untrusted] == ["One taped box."]
        assert out.untrusted[0].origin == "image_check"
        assert checker.asked[0].measured == "2 x 2 x 2 cm"

    def test_no_photo_and_a_failed_check_are_recorded(self) -> None:
        sizes = lambda ev: ("", "")  # noqa: E731
        assert ParcelImageTool(Checker(), Photos(None), sizes).run(ctx(), {}).error == "no_photo"
        failed = ParcelImageTool(Checker(fail=True), Photos(PHOTO), sizes).run(ctx(), {})
        assert failed.error == "image_check_failed:kernel_error"


@pytest.mark.parametrize("rule", ["R03", "R16", "R21"])
def test_a_rule_asked_for_by_id_comes_first(rule: str) -> None:
    passages = PolicyLookupTool().run(ctx(), {"query": f"{rule} what does it say"}).data["passages"]
    assert f"### {rule} " in passages[0]["text"]


def test_policy_lookup_returns_passages_with_their_source() -> None:
    asked: list[tuple[str, int]] = []

    def search(query: str, top_k: int) -> list[Passage]:
        asked.append((query, top_k))
        return [Passage("docs/spec/policy-rules.md", "R16 ...", 1.0)]

    out = PolicyLookupTool(search).run(ctx(), {"query": "R16"})
    assert asked == [("R16", 3), ("R16", 8)]  # the query as asked, then the rule id alone
    assert out.data == {"passages": [{"source": "docs/spec/policy-rules.md", "text": "R16 ..."}]}
