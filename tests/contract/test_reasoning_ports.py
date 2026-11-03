"""Contract: Reasoner, ReplyInterpreter and MessageDrafter (kernel-interfaces.md 6.4, I1).

Three ports, one module, one suite — they share the adapters and the rules that matter. The
rules are about what a reasoner may *return*, because what it may *do* is already settled by
the port having no write method on it.

The case worth reading twice: a proposal whose action is not in the briefing's
allowed_actions is `invalid_model_output`, not a proposal to be argued with. The menu is the
menu.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from tests.contract.conftest import build_or_skip, params, pending

if TYPE_CHECKING:
    from dispute_agent.kernel.ports import MessageDrafter, Reasoner, ReplyInterpreter

ADAPTERS = [
    pending("rules_only", phase="Phase 2 (card P2-07)"),
    pending("llm", phase="Phase 4 (card P4-05)"),
]


@pytest.fixture(params=params(ADAPTERS))
def reasoner(request: pytest.FixtureRequest) -> Reasoner:
    return cast("Reasoner", build_or_skip(request.param))


@pytest.fixture(params=params(ADAPTERS))
def interpreter(request: pytest.FixtureRequest) -> ReplyInterpreter:
    return cast("ReplyInterpreter", build_or_skip(request.param))


@pytest.fixture(params=params(ADAPTERS))
def drafter(request: pytest.FixtureRequest) -> MessageDrafter:
    return cast("MessageDrafter", build_or_skip(request.param))


class TestReasoner:
    def test_a_proposal_names_an_action_from_the_menu(self, reasoner: Reasoner) -> None:
        """TC-MOD-03. Off the menu is invalid_model_output, not a negotiation."""

    def test_a_proposal_carries_at_least_one_finding(self, reasoner: Reasoner) -> None: ...

    def test_a_finding_citing_an_unknown_evidence_id_is_refused(self, reasoner: Reasoner) -> None:
        """TC-MOD-02: a citation that points nowhere is worse than none."""

    def test_an_llm_proposal_records_prompt_name_version_and_hash(self, reasoner: Reasoner) -> None:
        """Which instructions produced this decision, answerable months later."""

    def test_the_rules_only_proposal_records_no_prompt(self, reasoner: Reasoner) -> None: ...


class TestReplyInterpreter:
    def test_a_reading_is_data_and_changes_nothing(self, interpreter: ReplyInterpreter) -> None:
        """I5: a claim in a reply is evidence about the reply, never a fact about the case."""

    def test_a_truncated_reply_is_read_as_truncated(self, interpreter: ReplyInterpreter) -> None:
        """agent-design.md section 1: the model is told when it did not see the whole thing."""

    def test_an_instruction_attempt_is_reported_not_followed(
        self, interpreter: ReplyInterpreter
    ) -> None: ...


class TestMessageDrafter:
    def test_every_number_in_a_draft_traces_to_a_fact(self, drafter: MessageDrafter) -> None:
        """I9. A number the drafter invented is the one a seller will quote back."""

    def test_the_recipient_is_not_taken_from_the_draft(self, drafter: MessageDrafter) -> None:
        """R07: the system sets it, from case.facts.seller_contact."""

    def test_a_blocked_draft_reports_a_reason_rather_than_raising(
        self, drafter: MessageDrafter
    ) -> None:
        """The drafter refusing is a result, and the caller has to read it."""
