"""The operations console's logic (ADR 0044), on real cases driven through the whole line.

No browser: each screen's content is built from a case the real line produced (offline, the
rules engine deciding), and checked for what a seller, reviewer or finance person must see -
and for what they must not (the synthetic answer key; text the reviewer would misread).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from dispute_agent.api.full_run import World, open_world
from dispute_agent.infra.clock import FakeClock, SystemClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.enums import ActorKind, CaseState, ProposalAction
from dispute_agent.kernel.domain.events import INBOUND_SOURCES
from dispute_agent.kernel.domain.models import Actor as Who
from dispute_agent.kernel.policy.config import PolicyConfig
from dispute_agent.modules.approvals.streamlit_ui.console import ReviewConsole
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import fixture
from dispute_agent.ui import finance, labels, review, seller, trail

if TYPE_CHECKING:
    from collections.abc import Iterator

NOW = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
REVIEWER = Who(kind=ActorKind.HUMAN, id="human:owner")


def _world(name: str, *, approve: bool) -> World:
    world = open_world(
        name,
        policy=PolicyConfig(),
        clock=FakeClock(NOW),
        ids=FakeIdGenerator(),
        decide=(lambda _a: "APPROVED") if approve else (lambda _a: None),  # type: ignore[arg-type,return-value]
    )
    world.drive(20)
    return world


@pytest.fixture
def credited() -> Iterator[World]:
    from dispute_agent.kernel.domain.enums import ApprovalDecision

    world = open_world(
        "calc_error_high_value",
        policy=PolicyConfig(),
        clock=FakeClock(NOW),
        ids=FakeIdGenerator(),
        decide=lambda _a: ApprovalDecision.APPROVED,
    )
    world.drive(20)
    yield world
    world.close()


@pytest.fixture
def waiting() -> Iterator[World]:
    world = open_world(
        "calc_error_high_value",
        policy=PolicyConfig(),
        clock=FakeClock(NOW),
        ids=FakeIdGenerator(),
        decide=lambda _a: None,
    )
    world.drive(20)
    yield world
    world.close()


def _work(world: World) -> SqliteUnitOfWork:
    return SqliteUnitOfWork(world.engine, clock=SystemClock())


class TestLabels:
    def test_every_state_and_action_has_words(self) -> None:
        assert set(labels.STATE) == set(CaseState)
        assert set(labels.ACTION) == set(ProposalAction)

    def test_the_accent_is_spent_on_one_state_only(self) -> None:
        settled = [s for s, (_l, tone) in labels.STATE.items() if tone is labels.Tone.SETTLED]
        assert settled == [CaseState.RESOLVED_CREDITED]


class TestSellerScreen:
    def test_each_shipment_once_as_the_running_app_serves_it(self) -> None:
        shipments = seller.catalogue()
        refs = [s.ref for s in shipments]
        assert len(refs) == len(set(refs))
        first = next(s for s in shipments if s.ref == "SHP-000001")
        assert first.charged == "₹152.00"  # kurta_box_larger, first in FIXTURES order

    def test_the_answer_key_is_never_shown(self) -> None:
        key = fixture("calc_error_high_value").shipment.correct_paise
        shown = next(s for s in seller.catalogue() if s.ref == "SHP-000002")
        text = " ".join(str(v) for v in vars(shown).values())
        assert f"{key / 100:,.2f}" not in text and str(key) not in text
        assert shown.charged == "₹2,800.00"

    def test_a_credited_case_reads_as_settled(self, credited: World) -> None:
        with _work(credited) as work:
            mine = seller.tickets(work.cases.list_in_states(list(CaseState)), "SELL-0001")
        assert [(t.label, t.tone, t.is_open) for t in mine] == [
            ("Resolved: credited", labels.Tone.SETTLED, False)
        ]

    def test_an_open_ticket_blocks_a_second_one_on_the_same_shipment(self, waiting: World) -> None:
        with _work(waiting) as work:
            mine = seller.tickets(work.cases.list_in_states(list(CaseState)))
        shipment = next(s for s in seller.catalogue() if s.ref == "SHP-000002")
        assert seller.open_ticket_for(shipment, mine) is not None

    @pytest.mark.parametrize(
        ("answer", "ok", "says"),
        [
            ((200, {"case_id": "case_1", "duplicate": False}), True, "Ticket raised"),
            ((200, {"case_id": "case_1", "duplicate": True}), True, "already raised"),
            ((422, {}), False, "allowlist"),
            ((500, {}), False, "HTTP 500"),
        ],
    )
    def test_raising_a_ticket_says_what_happened(
        self, answer: tuple[int, dict[str, Any]], ok: bool, says: str
    ) -> None:
        sent: list[dict[str, str]] = []

        def post(body: dict[str, str]) -> tuple[int, dict[str, Any]]:
            sent.append(body)
            return answer

        shipment = seller.catalogue()[0]
        raised = seller.raise_ticket(post, shipment, "owner+seller@example.test", NOW, "e1")
        assert raised.ok is ok and says in raised.message
        assert sent[0]["shipment_ref"] == shipment.ref and "statement" not in sent[0]
        assert sent[0]["source"] in INBOUND_SOURCES  # an unknown source is refused at intake

    def test_no_api_and_no_address_are_explained_not_crashed(self) -> None:
        def down(_body: dict[str, str]) -> tuple[int, dict[str, Any]]:
            raise ConnectionRefusedError

        shipment = seller.catalogue()[0]
        assert (
            "not running" in seller.raise_ticket(down, shipment, "a@example.test", NOW, "e").message
        )
        assert not seller.raise_ticket(down, shipment, None, NOW, "e").ok


class TestTrailScreen:
    def test_the_whole_case_is_told_in_order_with_who_did_each_step(self, credited: World) -> None:
        with _work(credited) as work:
            case = work.cases.list_in_states(list(CaseState))[0]
            story = trail.build(work, case, usage=(0, Decimal(0)))
        actors = [s.actor for s in story.steps]
        titles = [s.title for s in story.steps]
        assert actors[0] is trail.Actor.SELLER
        assert "Checked eligibility: eligible" in titles
        assert "Recalculated the charge" in titles
        assert "Proposed: Credit the seller" in titles
        assert trail.Actor.RULES in actors and trail.Actor.PERSON in actors
        assert story.steps[-2].tone is labels.Tone.SETTLED  # the last read-back, credited
        assert [s.at for s in story.steps] == sorted(s.at for s in story.steps)

    def test_the_recalculation_shows_code_s_figures(self, credited: World) -> None:
        with _work(credited) as work:
            case = work.cases.list_in_states(list(CaseState))[0]
            story = trail.build(work, case)
        recalc = next(s for s in story.steps if s.title == "Recalculated the charge")
        assert any("₹2,800.00" in line for line in recalc.lines)
        assert any("₹980.00" in line for line in recalc.lines)

    def test_a_case_sent_to_a_person_says_why(self) -> None:
        world = _world("multi_error", approve=False)
        try:
            with _work(world) as work:
                case = work.cases.list_in_states(list(CaseState))[0]
                story = trail.build(work, case)
                people = review.with_a_person(work)
        finally:
            world.close()
        assert case.state is CaseState.NEEDS_HUMAN_REVIEW
        assert any(s.tone is labels.Tone.ATTENTION for s in story.steps)
        assert [t.case_id for t in people] == [str(case.id)]


class TestReviewScreen:
    def test_each_item_carries_the_recommendation_and_the_exact_credit(
        self, waiting: World
    ) -> None:
        with _work(waiting) as work:
            console = ReviewConsole(store=work.approvals, clock=SystemClock(), reviewer=REVIEWER)
            cards = review.queue(work, console)
        assert len(cards) == 1
        card = cards[0]
        assert card.item.is_credit and card.item.credit_amount == "₹1,820.00"
        assert card.recommendation is not None
        assert card.recommendation.action == "Credit the seller"
        assert card.case.label == "Awaiting approval"


class TestFinanceScreen:
    def test_a_credit_is_traced_to_its_case_its_approver_and_its_read_back(
        self, credited: World
    ) -> None:
        with _work(credited) as work:
            found = finance.rows(credited.ledger.get_entries("SHP-000002"), work)
            case_id = str(work.cases.list_in_states(list(CaseState))[0].id)
        credit = next(r for r in found if r.kind == "Credit")
        charge = next(r for r in found if r.kind == "Charge")
        assert credit.amount == Decimal("1820.00") and charge.amount == Decimal("2800.00")
        assert (credit.case_id, credit.approved_by, credit.proved) == (
            case_id,
            "owner",
            "Read back",
        )
        assert charge.case_id == "" and charge.approved_by == ""
        sums = finance.totals(found)
        assert (sums.charged, sums.credited, sums.net) == (
            Decimal("2800.00"),
            Decimal("1820.00"),
            Decimal("980.00"),
        )


class TestWhereTheCaseIs:
    """The headline, the next step and the five phases (ui/journey.py)."""

    def _journey(self, world: World) -> Any:
        from dispute_agent.ui.journey import journey

        with _work(world) as work:
            return journey(work, work.cases.get(world.case_id))

    def test_a_credited_case_says_how_much_and_every_phase_is_done(self, credited: World) -> None:
        from dispute_agent.ui.journey import Mark

        where = self._journey(credited)
        assert where.headline == "Resolved: the seller was credited ₹1,820.00."
        assert [m for _n, m in where.phases] == [Mark.DONE] * 5

    def test_a_case_waiting_for_approval_says_what_and_where(self, waiting: World) -> None:
        from dispute_agent.ui.journey import Mark

        where = self._journey(waiting)
        assert where.headline == "Waiting for a reviewer to approve a ₹1,820.00 credit."
        assert "Review screen" in where.next_step
        assert dict(where.phases)["Approved"] is Mark.WAITING
        assert dict(where.phases)["Acted"] is Mark.TODO

    def test_a_case_with_a_person_says_why(self) -> None:
        from dispute_agent.ui.journey import Mark

        world = _world("multi_error", approve=False)
        try:
            where = self._journey(world)
        finally:
            world.close()
        assert where.headline == "The agent stopped and handed this case to a person."
        assert where.why  # the recorded review reason, in words
        assert dict(where.phases)["Investigated"] is Mark.PERSON

    def test_an_ineligible_case_marks_what_was_never_reached(self) -> None:
        from dispute_agent.ui.journey import Mark

        world = _world("not_delivered", approve=False)
        try:
            where = self._journey(world)
        finally:
            world.close()
        assert where.headline == "Closed: this shipment could not be disputed."
        phases = dict(where.phases)
        assert phases["Closed"] is Mark.DONE and phases["Investigated"] is Mark.NOT_REACHED
        assert where.why.startswith("The shipment's status shows it was not delivered")
        assert where.figures == ()

    def test_a_shipment_proved_ineligible_by_its_evidence_says_so(self) -> None:
        """S32 (T05): intake saw "delivered", the scan history did not. The screen must say
        where it closed and why, not read "eligible" and then stop."""
        from dispute_agent.ui.journey import Mark

        world = _world("not_delivered_late_scan", approve=False)
        try:
            where = self._journey(world)
            with _work(world) as work:
                story = trail.build(work, work.cases.get(world.case_id))
        finally:
            world.close()
        assert where.headline == "Closed: this shipment could not be disputed."
        assert where.why.startswith("The scan history shows the shipment was not delivered")
        assert where.figures == ()  # an overcharge of a shipment that cannot be disputed
        phases = dict(where.phases)
        assert phases["Investigated"] is Mark.DONE
        assert phases["Approved"] is Mark.NOT_REACHED and phases["Acted"] is Mark.NOT_REACHED
        titles = [s.title for s in story.steps]
        closed = titles.index("Closed: not eligible")
        assert titles[closed - 1] == "Recalculated the charge"
        assert "scan history" in story.steps[closed].summary
        assert story.steps[closed].tone is labels.Tone.CLOSED
        assert story.steps[-1].minor  # the bookkeeping step, shown quietly


class TestTheStepsAtAGlance:
    def test_steps_are_grouped_into_named_rounds_with_one_line_summaries(
        self, credited: World
    ) -> None:
        with _work(credited) as work:
            story = trail.build(work, work.cases.get(credited.case_id))
        assert story.round_titles == {1: "First look"}
        assert {s.round for s in story.steps} == {0, 1}
        evidence = next(s for s in story.steps if s.title == "Gathered the evidence")
        assert evidence.summary.startswith("Found ") and "missing: parcel image" in evidence.summary
        recalc = next(s for s in story.steps if s.title == "Recalculated the charge")
        assert recalc.summary == (
            "Charged ₹2,800.00, correct ₹980.00, overcharged ₹1,820.00. "
            "Code proved calculation error."
        )
