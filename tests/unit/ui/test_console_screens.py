"""The four screens, rendered by Streamlit's test runner over a real case (ADR 0044).

A case is driven through the whole line offline (the rules engine deciding) until a credit
waits for approval; its stores are copied into a temporary `data/` and each screen runs
against them, as `make run-console` would. The reviewer signs in with a token made for this
test, sees the recommendation beside the exact credit, approves, and the store records the
decision under the reviewer's name - nothing is sent from the page (I2).
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from streamlit.testing.v1 import AppTest

from dispute_agent.api.full_run import open_world
from dispute_agent.infra.clock import FakeClock, SystemClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.infra.sqlite.engine import create_sqlite_engine
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.enums import ApprovalDecision, CaseState
from dispute_agent.kernel.policy.config import PolicyConfig

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)


def _copy(source: Path, target: Path) -> None:
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)


@pytest.fixture
def desk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    world = open_world(
        "calc_error_high_value",
        policy=PolicyConfig(),
        clock=FakeClock(NOW),
        ids=FakeIdGenerator(),
        decide=lambda _a: None,
    )
    world.drive(20)
    world.close()
    data = tmp_path / "data"
    data.mkdir()
    _copy(world.folder / "cases.sqlite3", data / "cases.sqlite3")
    _copy(world.folder / "ledger.sqlite3", data / "ledger.sqlite3")
    token = secrets.token_hex(16)  # made for this test, never a real one
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REVIEW_CONSOLE_TOKEN", token)
    monkeypatch.setenv("TEST_SELLER_EMAIL", "owner+seller@example.test")
    return {"token": token, "cases": data / "cases.sqlite3"}


def _seller() -> None:
    from dispute_agent.ui.screens import seller

    seller.page()


def _trail() -> None:
    from dispute_agent.ui.screens import trail

    trail.page()


def _review() -> None:
    from dispute_agent.ui.screens import reviewer

    reviewer.page()


def _ledger() -> None:
    from dispute_agent.ui.screens import finance

    finance.page()


def _text(at: AppTest) -> str:
    parts = (
        [m.value for m in at.markdown] + [t.value for t in at.text] + [c.value for c in at.caption]
    )
    return "\n".join(str(p) for p in parts)


def test_the_seller_sees_their_shipments_and_their_ticket(desk: dict[str, Any]) -> None:
    at = AppTest.from_function(_seller, default_timeout=60).run()
    assert not at.exception
    assert at.title[0].value == "Raise a ticket"
    headers = [h.value for h in at.header]
    assert {"Your shipments", "Dispute a charge", "Your tickets"} <= set(headers)
    # ADR 0060: three demo sellers; the page lists only the first one's 3 disputed shipments
    # (the 25 past ones stay in the data as the agent's history).
    assert at.selectbox(key="persona").options == [
        "CaseKraft Accessories",
        "NorthCart Essentials",
        "ThreadLane Apparel",
    ]
    assert len(at.selectbox(key="ship").options) == 3
    assert not any(s.label == "Show" for s in at.selectbox)  # no filter
    assert "correct" not in " ".join(m.value for m in at.markdown).lower()  # brief 13.5


class TestDemoSellers:
    """ADR 0060: a selector, and one seller's disputed rows only."""

    def test_switching_seller_shows_only_that_sellers_shipments(self, desk: dict[str, Any]) -> None:
        at = AppTest.from_function(_seller, default_timeout=60).run()
        assert all(o.startswith("SHP-102") for o in at.selectbox(key="ship").options)
        at.selectbox(key="persona").set_value("ThreadLane Apparel").run()
        assert not at.exception
        refs = at.selectbox(key="ship").options
        assert sorted(refs) == ["SHP-103025", "SHP-103026", "SHP-103027", "SHP-103028"]

    def test_the_seller_never_sees_the_agents_comparison(self, desk: dict[str, Any]) -> None:
        # The comparison is evidence the agent weighs after a dispute, shown on Review only.
        at = AppTest.from_function(_seller, default_timeout=60).run()
        at.selectbox(key="ship").set_value("SHP-102025").run()
        assert not at.exception
        text = _text(at)
        assert "How the charge is worked out" in text
        assert "past shipments" not in text.lower()
        assert "Based on: SHP-" not in text

    def test_a_seller_without_a_test_address_borrows_no_one_elses(
        self, desk: dict[str, Any]
    ) -> None:
        at = AppTest.from_function(_seller, default_timeout=60).run()
        at.selectbox(key="ship").set_value("SHP-102027").run()
        assert "+casekraft" in _text(at)
        assert all(b.label != "Raise a dispute" for b in at.button)


def test_the_steps_tell_the_case_in_order(desk: dict[str, Any]) -> None:
    at = AppTest.from_function(_trail, default_timeout=60).run()
    assert not at.exception
    text = _text(at)
    for expected in (
        "Waiting for a reviewer to approve a ₹1,820.00 credit.",  # the headline
        "Approve or reject it on the Review screen",  # what happens next
        "Round 1 · First look",
        "Dispute raised on SHP-000002",
        "Charged ₹2,800.00, correct ₹980.00, overcharged ₹1,820.00.",
        "Proposed: Credit the seller",
        "a person must approve first",
        "Overcharge: ₹1,820.00",  # in the step's details
        "Its reasoning, exactly as recorded · proposed by Rules engine",  # shown openly
    ):
        assert expected in text


def test_the_steps_explain_a_shipment_closed_after_its_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = open_world(
        "not_delivered_late_scan",
        policy=PolicyConfig(),
        clock=FakeClock(NOW),
        ids=FakeIdGenerator(),
        decide=lambda _a: None,
    )
    world.drive(20)
    world.close()
    data = tmp_path / "data"
    data.mkdir()
    _copy(world.folder / "cases.sqlite3", data / "cases.sqlite3")
    monkeypatch.chdir(tmp_path)

    at = AppTest.from_function(_trail, default_timeout=60).run()
    assert not at.exception
    text = _text(at)
    assert "Closed: not eligible" in text and "scan history shows" in text
    assert "not reached" in text  # approval and action were never reached
    assert "Overcharged" not in text  # no figures for a shipment that cannot be disputed


def test_a_reviewer_signs_in_sees_the_recommendation_and_approves(desk: dict[str, Any]) -> None:
    at = AppTest.from_function(_review, default_timeout=60).run()
    assert not at.exception
    at.text_input[0].input(desk["token"])
    assert at.selectbox[0].label == "Review as" and at.selectbox[0].options == ["owner"]
    at.button[0].click().run()
    assert not at.exception
    text = _text(at)
    assert "What the agent recommends" in text and "Credit the seller" in text
    assert "₹1,820.00" in text and "Rules engine" in text
    captions = [c for image in at.image for c in image.captions]
    assert captions == ["Parcel photo"], (
        "an approval card shows the photo too, not only a person card"
    )

    approve = next(b for b in at.button if b.label == "Approve the ₹1,820.00 credit")
    approve.click().run()
    assert not at.exception
    with SqliteUnitOfWork(create_sqlite_engine(desk["cases"]), clock=SystemClock()) as work:
        case = work.cases.list_in_states(list(CaseState))[0]
        decided = list(work.approvals.for_case(case.id))
    assert [(a.decision, a.decided_by.id if a.decided_by else "") for a in decided] == [
        (ApprovalDecision.APPROVED, "human:owner")
    ]


def test_a_wrong_token_shows_no_queue(desk: dict[str, Any]) -> None:
    at = AppTest.from_function(_review, default_timeout=60).run()
    at.text_input[0].input("not-the-token")
    at.button[0].click().run()
    assert at.error and "not correct" in at.error[0].value
    assert "What the agent recommends" not in _text(at)


def test_the_ledger_totals_are_sums_of_its_rows(desk: dict[str, Any]) -> None:
    from dispute_agent.kernel.domain.values import Money
    from dispute_agent.modules.ledger.simulated.ledger import SimulatedLedger

    books = SimulatedLedger(desk["cases"].parent / "ledger.sqlite3")
    books.seed_debit("SHP-101028", Money.of("114.00"), "weight_charge", "TXN-SHP-101028-1")
    at = AppTest.from_function(_ledger, default_timeout=60).run()
    at.selectbox(key="ledger-seller").set_value("SELL-0101").run()  # NorthCart
    assert not at.exception
    text = _text(at)
    assert "<dt>Charged</dt><dd>₹114.00</dd>" in text
    assert "<dt>Credited back</dt><dd>\u2212 ₹0.00</dd>" in text  # nothing approved yet
    assert "<dt>Charged</dt><dd>₹" in text  # money reads the same on every screen
    assert "No credits yet" in text


def test_a_demo_seller_s_credit_shows_on_the_ledger(desk: dict[str, Any]) -> None:
    """Live, 2026-11-25: SHP-102026 was credited INR 152 and read back, but the Ledger offered
    only SELL-0001, so CaseKraft's charges and credit were nowhere to be seen."""
    from dispute_agent.kernel.domain.external import CreditRequest
    from dispute_agent.kernel.domain.ids import CaseId, IdempotencyKey
    from dispute_agent.kernel.domain.values import Money
    from dispute_agent.modules.ledger.simulated.ledger import SimulatedLedger

    books = SimulatedLedger(desk["cases"].parent / "ledger.sqlite3")
    books.seed_debit("SHP-102026", Money.of("190.00"), "weight_charge", "TXN-SHP-102026-1")
    books.post_credit(
        CreditRequest(
            shipment_ref="SHP-102026",
            amount=Money.of("152.00"),
            reason_code="remeasurement_lower",
            case_id=CaseId("case_ck"),
        ),
        IdempotencyKey("idem-ck-credit"),
    )
    at = AppTest.from_function(_ledger, default_timeout=60).run()
    # The demo sellers only, as on Raise a ticket: SELL-0001 is the old test seller.
    assert at.selectbox(key="ledger-seller").options == [
        "CaseKraft Accessories",
        "NorthCart Essentials",
        "ThreadLane Apparel",
    ]
    text = _text(at)  # CaseKraft is first, so it opens there
    assert f"<dt>Credited back</dt><dd>{chr(0x2212)} ₹152.00</dd>" in text
    assert "Credits from disputes (1)" in [h.value for h in at.header]


@pytest.fixture
def desk_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """SHP-000001 with a person (B5: image evidence), as on the hosted console."""
    world = open_world(
        "kurta_box_larger",
        policy=PolicyConfig(),
        clock=FakeClock(NOW),
        ids=FakeIdGenerator(),
        decide=lambda _a: None,
    )
    world.drive(20)
    world.close()
    data = tmp_path / "data"
    data.mkdir()
    _copy(world.folder / "cases.sqlite3", data / "cases.sqlite3")
    token = secrets.token_hex(16)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REVIEW_CONSOLE_TOKEN", token)
    return {"token": token, "cases": data / "cases.sqlite3"}


def test_a_reviewer_takes_over_a_case_with_a_person(desk_case: dict[str, Any]) -> None:
    at = AppTest.from_function(_review, default_timeout=60).run()
    at.text_input[0].input(desk_case["token"])
    at.button[0].click().run()
    assert not at.exception
    text = _text(at)
    assert "Why it is with a person" in text and "Proposal escalated" in text
    assert "image evidence" in text  # the reason's detail, shown as text
    assert [c for image in at.image for c in image.captions] == ["Parcel photo"]  # to judge
    # ADR 0050: the task, the facts side by side, and code's choices with what each does.
    assert "Decide whose measurement to trust" in text
    x = chr(0xD7)  # the multiplication sign the card writes sizes with
    assert f"20 {x} 15 {x} 10 cm · 480 g" in text and f"30 {x} 22 {x} 15 cm · 1,100 g" in text

    tiles = [c.label for c in at.checkbox if (c.key or "").startswith("pick-")]
    assert tiles[:3] == [
        "Uphold the charge",
        "Ask the hub to remeasure",
        "Ask the seller for evidence",
    ]
    # Each tile's extra stays disabled until its tile is chosen.
    notify = next(c for c in at.checkbox if (c.key or "").startswith("notify-"))
    items = next(m for m in at.multiselect if (m.key or "").startswith("items-"))
    assert notify.disabled and items.disabled
    next(c for c in at.checkbox if c.label == "Take it over").check().run()
    notify = next(c for c in at.checkbox if (c.key or "").startswith("notify-"))
    assert not notify.disabled
    assert next(m for m in at.multiselect if (m.key or "").startswith("items-")).disabled
    # One choice at a time: choosing another tile clears the first.
    next(c for c in at.checkbox if c.label == "Uphold the charge").check().run()
    assert not next(c for c in at.checkbox if c.label == "Take it over").value
    next(c for c in at.checkbox if c.label == "Take it over").check().run()
    next(b for b in at.button if b.label == "Record my decision").click().run()
    assert not at.exception
    with sqlite3.connect(desk_case["cases"]) as conn:
        rows = conn.execute(
            "SELECT event_type, source, event_json FROM inbox WHERE event_type = 'HUMAN_TOOK_CASE'"
        ).fetchall()
    assert len(rows) == 1 and rows[0][1] == "ops_console" and "human:owner" in rows[0][2]


def test_a_reviewer_decides_a_case_and_the_choice_is_recorded(desk_case: dict[str, Any]) -> None:
    """ADR 0050: a verdict is recorded for the runner; nothing is sent from the page (I2)."""
    at = AppTest.from_function(_review, default_timeout=60).run()
    at.text_input[0].input(desk_case["token"])
    at.button[0].click().run()
    next(c for c in at.checkbox if c.label == "Uphold the charge").check().run()
    record = next(b for b in at.button if b.label == "Record my decision")
    record.click().run()
    assert at.error and "Write why" in at.error[0].value  # a note is needed
    at.text_area[0].input("The photo shows a box the size the hub measured.")
    next(b for b in at.button if b.label == "Record my decision").click().run()
    assert not at.exception
    with sqlite3.connect(desk_case["cases"]) as conn:
        rows = conn.execute(
            "SELECT event_json FROM inbox WHERE event_type = 'HUMAN_DECIDED_CASE'"
        ).fetchall()
    assert len(rows) == 1
    assert '"action":"uphold_charge"' in rows[0][0].replace(" ", "")
    assert "human:owner" in rows[0][0]


class TestHostedReviewSignIn:
    """ADR 0047: past the hosted site password, the reviewer chooses a listed approver."""

    def test_past_the_site_password_only_listed_approvers_are_offered(
        self, desk: dict[str, Any]
    ) -> None:
        at = AppTest.from_function(_review, default_timeout=60)
        at.session_state["gate_passed"] = True  # set only by the hosted gate, after the password
        at.run()
        assert not at.text_input  # no token, and no free-text name
        assert at.selectbox[0].label == "Review as" and at.selectbox[0].options == ["owner"]
        at.button[0].click().run()
        assert not at.exception
        assert "What the agent recommends" in _text(at)
        assert at.session_state["reviewer"].id == "human:owner"

    def test_run_locally_the_token_is_still_required(self, desk: dict[str, Any]) -> None:
        at = AppTest.from_function(_review, default_timeout=60).run()
        assert at.text_input[0].label == "Review console token"
        at.button[0].click().run()  # no token typed
        assert at.error and "not correct" in at.error[0].value
        assert "What the agent recommends" not in _text(at)


class TestAnApprovalIsActedOn:
    """The step the first ADR 0047 tests skipped: after the click, the engine must act.

    The engine accepts decisions only from `approvals.approver_ids` (T09, R13). Review once
    let a reviewer type any name, and a credit approved as "asha" was recorded and then not
    acted on - the case went to a person. Driven here through the real line to the ledger.
    """

    @pytest.fixture
    def world(self, monkeypatch: pytest.MonkeyPatch) -> Any:
        from dispute_agent.ui import data as console_data

        # Today, not the fixed NOW: the Review screen stamps its decision with the real clock,
        # and an approval expires 72 h after it is requested. With a fixed date the decision
        # landed after the expiry once that date was 3 days old, so the case correctly went to
        # a person and the test failed from 2026-11-10 on for a reason that was not the product.
        today = datetime.now(UTC).replace(microsecond=0)
        world = open_world(
            "calc_error_high_value",
            policy=PolicyConfig(),
            clock=FakeClock(today),
            ids=FakeIdGenerator(),
            decide=lambda _a: None,  # the decision comes from the Review screen
        )
        world.drive(20)
        monkeypatch.setattr(console_data, "CASES", world.folder / "cases.sqlite3")
        monkeypatch.setenv("REVIEW_CONSOLE_TOKEN", "t-" + secrets.token_hex(8))
        yield world
        world.close()

    def _state(self, world: Any) -> CaseState:
        with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
            return work.cases.get(world.case_id).state

    def test_a_credit_approved_on_the_review_screen_reaches_the_ledger(self, world: Any) -> None:
        import os

        at = AppTest.from_function(_review, default_timeout=60).run()
        at.text_input[0].input(os.environ["REVIEW_CONSOLE_TOKEN"])
        at.button[0].click().run()
        next(b for b in at.button if b.label.startswith("Approve the")).click().run()
        assert not at.exception

        world.drive(20)  # the runner picks the decision up, as the hosted service does
        posted = [e for e in world.ledger.get_entries("SHP-000002") if str(e.type) == "CREDIT"]
        assert [e.amount.amount for e in posted] == [Decimal("1820.00")]  # posted, read back
        # Next, the email telling the seller waits for its own approval (I13).
        assert self._state(world) is CaseState.AWAITING_APPROVAL

    def test_a_decision_by_an_unlisted_name_is_not_acted_on(self, world: Any) -> None:
        """Why Review offers no other name: the engine would set the decision aside."""
        from dispute_agent.modules.approvals.streamlit_ui.console import (
            ReviewConsole,
            reviewer_named,
        )

        with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
            console = ReviewConsole(
                store=work.approvals, clock=world.clock, reviewer=reviewer_named("asha")
            )
            console.approve(console.queue()[0].id, "")
        world.drive(20)
        assert self._state(world) is CaseState.NEEDS_HUMAN_REVIEW
        assert not [e for e in world.ledger.get_entries("SHP-000002") if str(e.type) == "CREDIT"]


@pytest.mark.parametrize("name", ["", "   ", "<b>x</b>", "a" * 41])
def test_an_odd_approver_name_is_refused(name: str) -> None:
    from dispute_agent.kernel.domain.errors import DomainValidationError
    from dispute_agent.modules.approvals.streamlit_ui.console import reviewer_named

    with pytest.raises(DomainValidationError):
        reviewer_named(name)


class TestAskAboutThisShipment:
    """ADR 0058: the Review screen lists shipments, and the AI answers about the chosen one."""

    def _signed_in(self, desk: dict[str, Any]) -> AppTest:
        at = AppTest.from_function(_review, default_timeout=60).run()
        at.text_input[0].input(desk["token"])
        next(b for b in at.button if b.label == "Sign in").click().run()
        assert not at.exception
        return at

    def test_a_starter_question_is_answered_about_the_chosen_shipment_only(
        self, desk: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dispute_agent.modules.reasoning.explainer.agent import Answer
        from dispute_agent.ui import ask

        asked: list[tuple[str, str]] = []

        def fake(case_id: str, question: str) -> Answer:  # no model, no cost
            asked.append((case_id, question))
            return Answer(
                text="A credit of 182000 paise waits for you <b>(R13)</b>.",
                cost_usd=0.001,
                turns=3,
                tools_used=("mcp__case__case_overview", "mcp__case__policy_lookup"),
            )

        monkeypatch.setattr(ask, "unavailable", lambda: None)
        monkeypatch.setattr(ask, "_ask", fake)
        at = self._signed_in(desk)
        assert "Ask about" in _text(at) and "Shipments" in _text(at)
        next(b for b in at.button if b.label == "Why does this need my approval?").click().run()
        assert not at.exception
        with SqliteUnitOfWork(create_sqlite_engine(desk["cases"]), clock=SystemClock()) as work:
            case_id = str(work.cases.list_in_states(list(CaseState))[0].id)
        assert [c for c, _ in asked] == [case_id]
        assert asked[0][1].startswith(f"Case {case_id} (shipment ")
        text = _text(at)
        assert "&lt;b&gt;(R13)&lt;/b&gt;" in text  # the model's text is shown, never run as markup
        assert "Read: Case overview · Policy rules" in text
        assert f"{ask.ASK_LIMIT - 1} of {ask.ASK_LIMIT} questions left" in text
        # The asked starter leaves the row; the others stay one click away.
        assert all(b.label != "Why does this need my approval?" for b in at.button)

    def test_a_follow_up_carries_the_earlier_turn(
        self, desk: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dispute_agent.modules.reasoning.explainer.agent import Answer
        from dispute_agent.ui import ask

        prompts: list[str] = []

        def fake(_case_id: str, question: str) -> Answer:
            prompts.append(question)
            return Answer("Because of R13.", 0.001, 2, ())

        monkeypatch.setattr(ask, "unavailable", lambda: None)
        monkeypatch.setattr(ask, "_ask", fake)
        at = self._signed_in(desk)
        next(b for b in at.button if b.label == "Why does this need my approval?").click().run()
        at.chat_input[0].set_value("and who set that rule?").run()
        assert not at.exception
        assert "Reviewer: Why does this need my approval?" in prompts[1]
        assert prompts[1].endswith("Question: and who set that rule?")

    def test_a_failed_answer_names_its_cause_and_keeps_the_page(
        self, desk: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dispute_agent.ui import ask

        def broken(_case_id: str, _question: str) -> Any:
            raise OSError("helper did not start\nsecond line not shown")

        monkeypatch.setattr(ask, "unavailable", lambda: None)
        monkeypatch.setattr(ask, "_ask", broken)
        at = self._signed_in(desk)
        next(b for b in at.button if b.label == "Why does this need my approval?").click().run()
        assert not at.exception
        text = _text(at)
        assert "could not answer just now (OSError: helper did not start)" in text
        assert "second line" not in text
        assert f"{ask.ASK_LIMIT} of {ask.ASK_LIMIT} questions left" in text  # not counted

    def test_the_assistant_is_off_without_a_key_and_after_the_session_limit(
        self, desk: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dispute_agent.ui import ask

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        at = self._signed_in(desk)
        assert "The assistant is off here" in _text(at)
        assert at.chat_input[0].disabled
        monkeypatch.setattr(ask, "unavailable", lambda: None)
        at.session_state[ask.ASKED] = ask.ASK_LIMIT
        at.run()
        assert f"You have used all {ask.ASK_LIMIT} questions" in _text(at)
        assert all(b.disabled for b in at.button if b.label in ask.STARTERS["waiting"])


def test_the_assistant_prompt_and_sources() -> None:
    from dispute_agent.ui import ask

    turns = [{"q": f"q{i}", "a": f"a{i}"} for i in range(4)]
    text = ask.prompt("case_9", "SHP-9", turns, "  why?  ")
    assert text.startswith("Case case_9 (shipment SHP-9).")
    assert "q0" not in text and "Reviewer: q3" in text  # only the last KEEP turns
    assert text.endswith("Question: why?")
    assert ask.sources(
        ["mcp__case__case_timeline", "mcp__case__case_overview", "mcp__case__case_timeline", "Bash"]
    ) == ["Timeline", "Case overview"]


def test_an_answer_renders_its_list_and_bold_but_nothing_else() -> None:
    from dispute_agent.ui import ask

    html = ask.answer_html(
        "It waits for you <script>x</script>.\n- **Amount:** 182000 paise. [case_overview]\n"
        "- **Rule:** R13 <b>bold</b>.\n\nThe end."
    )
    assert html == (
        "<p>It waits for you &lt;script&gt;x&lt;/script&gt;.</p>"
        "<ul><li><strong>Amount:</strong> 182000 paise. [case_overview]</li>"
        "<li><strong>Rule:</strong> R13 &lt;b&gt;bold&lt;/b&gt;.</li></ul><p>The end.</p>"
    )
