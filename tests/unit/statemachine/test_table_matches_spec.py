"""Card P1-03: the table in code equals transitions.yaml, and checks C1-C16.

Every assertion here reads the file at test time. The table is the backbone every later
invariant rests on, and the failure that matters is not a wrong row — it is a row that was
right when it was written and quietly stopped matching the document six months later.

The structural checks (C1-C16) are the ones the Stage A1 review ran by hand. Having them as
tests means a transition added in Phase 5 cannot strand a state or create a terminal state
with a way back into the middle of the flow.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest
import yaml

from dispute_agent.infra.transitions import default_table, load_transition_table
from dispute_agent.kernel.domain.enums import CaseState, TimerKind, Trigger
from dispute_agent.kernel.domain.errors import ConfigError
from dispute_agent.kernel.statemachine.transitions import Transition, build_table

ROOT = Path(__file__).resolve().parents[3]
RAW = yaml.safe_load((ROOT / "docs" / "spec" / "transitions.yaml").read_text(encoding="utf-8"))
TABLE = default_table()

TERMINAL = {
    CaseState.RESOLVED_CREDITED,
    CaseState.RESOLVED_UPHELD,
    CaseState.WITHDRAWN,
    CaseState.CLOSED_NO_RESPONSE,
    CaseState.REJECTED_INELIGIBLE,
    CaseState.HANDED_TO_HUMAN,
}
WORKING = set(CaseState) - TERMINAL


class TestTheTableMatchesTheDocument:
    def test_the_version_matches(self) -> None:
        assert TABLE.version == str(RAW["version"])

    def test_every_state_is_present(self) -> None:
        assert [s.value for s in TABLE.states] == list(RAW["states"])

    def test_every_trigger_is_present(self) -> None:
        assert {t.value for t in TABLE.triggers} == {t.upper() for t in RAW["triggers"]}

    def test_every_timer_kind_is_present(self) -> None:
        assert {k.value for k in TABLE.timer_kinds} == set(RAW["timer_kinds"])

    def test_every_guard_is_present(self) -> None:
        assert set(TABLE.guards) == set(RAW["guards"])

    def test_every_step_is_present(self) -> None:
        assert set(TABLE.steps) == set(RAW["steps"])

    def test_the_groups_match(self) -> None:
        assert {name: [s.value for s in members] for name, members in TABLE.groups.items()} == {
            name: list(members) for name, members in RAW["groups"].items()
        }

    def test_every_declared_transition_appears(self) -> None:
        """Ids, not rows: T90 is one declaration and nine rows after expansion."""
        assert {t.id for t in TABLE.transitions} == {t["id"] for t in RAW["transitions"]}

    def test_each_transition_matches_field_for_field(self) -> None:
        by_id: dict[str, list[Transition]] = {}
        for transition in TABLE.transitions:
            by_id.setdefault(transition.id, []).append(transition)

        for row in RAW["transitions"]:
            built = by_id[row["id"]]
            assert all(t.to_state.value == row["to"] for t in built), row["id"]
            assert all(t.trigger.value == row["trigger"].upper() for t in built), row["id"]
            assert all(list(t.guards) == list(row.get("guards") or ()) for t in built), row["id"]
            if "from_group" in row:
                assert {t.from_state for t in built} == set(TABLE.groups[row["from_group"]])
            else:
                origin = row.get("from")
                expected = None if origin is None else CaseState(origin)
                assert [t.from_state for t in built] == [expected], row["id"]

    def test_the_acceptance_matrix_matches(self) -> None:
        assert {s.value: dict(row) for s, row in TABLE.accept.items()} == RAW["accept"]

    def test_the_checks_are_carried(self) -> None:
        assert dict(TABLE.checks) == RAW["checks"]


class TestFromGroupExpansion:
    """The card's Watch note: expand at load time so the legal-edge set is explicit."""

    def test_t90_becomes_one_row_per_escalable_state(self) -> None:
        t90 = [t for t in TABLE.transitions if t.id == "T90"]
        assert len(t90) == len(TABLE.groups["escalable"]) == 9
        assert {t.from_state for t in t90} == set(TABLE.groups["escalable"])
        assert all(t.to_state is CaseState.NEEDS_HUMAN_REVIEW for t in t90)

    def test_every_escalable_state_can_be_looked_up_directly(self) -> None:
        """ "Can this state escalate?" is a lookup, not a group membership test elsewhere."""
        for state in TABLE.groups["escalable"]:
            assert TABLE.find(state, Trigger.ESCALATE) is not None

    def test_a_terminal_state_cannot_escalate(self) -> None:
        assert TABLE.find(CaseState.RESOLVED_CREDITED, Trigger.ESCALATE) is None


class TestStructuralChecks:
    """C1-C16 from transitions.yaml, as tests rather than a review someone once did."""

    def test_c1_every_state_is_reachable_from_t00(self) -> None:
        reached = {CaseState.RECEIVED}
        changed = True
        while changed:
            changed = False
            for transition in TABLE.transitions:
                if transition.from_state in reached and transition.to_state not in reached:
                    reached.add(transition.to_state)
                    changed = True
        assert reached == set(CaseState), f"unreachable: {set(CaseState) - reached}"

    def test_c2_every_working_state_reaches_a_terminal_state(self) -> None:
        for start in WORKING:
            seen = {start}
            frontier = [start]
            while frontier:
                current = frontier.pop()
                for transition in TABLE.outgoing(current):
                    if transition.to_state not in seen:
                        seen.add(transition.to_state)
                        frontier.append(transition.to_state)
            assert seen & TERMINAL, f"{start.value} cannot reach a terminal state"

    def test_c3_terminal_states_leave_only_to_gathering_or_review(self) -> None:
        for state in TERMINAL:
            targets = {t.to_state for t in TABLE.outgoing(state)}
            assert targets <= {CaseState.GATHERING_EVIDENCE, CaseState.NEEDS_HUMAN_REVIEW}, (
                f"{state.value} leaves to {targets}"
            )

    def test_c3_handed_to_human_is_final(self) -> None:
        """The one state with no way out: a case a person took over stays with them."""
        assert TABLE.outgoing(CaseState.HANDED_TO_HUMAN) == ()

    def test_no_two_rows_are_indistinguishable(self) -> None:
        """A trigger may have several destinations chosen by guards — VERIFYING on
        action_verified has six. What must not happen is two rows with the same guards,
        because nothing could choose between them."""
        keys = [(t.from_state, t.trigger, t.guards) for t in TABLE.transitions]
        assert len(keys) == len(set(keys))

    def test_an_unguarded_row_never_shadows_a_guarded_one(self) -> None:
        """An unguarded row always fires, so anything after it on the same trigger is dead
        code that no test would ever reach."""
        grouped: dict[tuple[CaseState | None, Trigger], list[object]] = {}
        for transition in TABLE.transitions:
            grouped.setdefault((transition.from_state, transition.trigger), []).append(transition)
        for rows in grouped.values():
            for earlier, later in itertools.pairwise(rows):
                assert earlier.guards, f"{earlier.id} shadows {later.id}"  # type: ignore[attr-defined]

    def test_every_guard_named_is_documented(self) -> None:
        """A transition naming a guard nobody implements silently never fires."""
        for transition in TABLE.transitions:
            for guard in transition.guards:
                assert guard in TABLE.guards, f"{transition.id} names unknown guard {guard}"

    def test_every_state_answers_every_inbound_event(self) -> None:
        """No inbound event may be dropped for want of a matrix entry.

        Most states use a `*` fallback; four list all sixteen types explicitly, which is
        equally total. What matters is coverage, not which form it takes.
        """
        for state in CaseState:
            assert state in TABLE.accept
            row = TABLE.accept[state]
            if "*" in row:
                continue
            assert set(TABLE.input_event_types) <= set(row), state.value

    def test_the_reaction_lookup_is_total(self) -> None:
        for state in CaseState:
            for event_type in TABLE.input_event_types:
                assert TABLE.reaction(state, event_type)

    def test_only_t00_has_no_origin(self) -> None:
        originless = [t.id for t in TABLE.transitions if t.from_state is None]
        assert originless == ["T00"]


class TestItMatchesTheDatabase:
    def test_the_legal_pairs_equal_schema_sql(self) -> None:
        """schema.sql's `legal_transitions` is generated from this file, and a trigger uses
        it. If the two disagree, the database refuses a move the state machine allows."""
        schema = (ROOT / "docs" / "spec" / "schema.sql").read_text(encoding="utf-8")
        block = schema.split("INSERT INTO legal_transitions (from_state, to_state) VALUES")[1]
        block = block.split(";")[0]
        in_schema = {
            tuple(part.strip().strip("()'").split("', '"))
            for part in block.split("),")
            if "'" in part
        }
        in_table = {(a.value, b.value) for a, b in TABLE.legal_pairs()}
        assert in_table == in_schema


class TestBuildTimeValidation:
    def test_two_rows_with_identical_guards_are_refused(self) -> None:
        raw = yaml.safe_load(
            (ROOT / "docs" / "spec" / "transitions.yaml").read_text(encoding="utf-8")
        )
        first = next(t for t in raw["transitions"] if t.get("from") and t.get("guards"))
        raw["transitions"].append(
            {
                "id": "TXX",
                "from": first["from"],
                "to": "NEEDS_HUMAN_REVIEW",
                "trigger": first["trigger"],
                "guards": list(first["guards"]),
            }
        )
        with pytest.raises(ConfigError, match="identical guards"):
            build_table(raw)

    def test_an_unguarded_row_before_a_guarded_one_is_refused(self) -> None:
        """The failure this catches is silent: the guarded row simply never fires."""
        raw = yaml.safe_load(
            (ROOT / "docs" / "spec" / "transitions.yaml").read_text(encoding="utf-8")
        )
        guarded = next(t for t in raw["transitions"] if t.get("from") and t.get("guards"))
        index = raw["transitions"].index(guarded)
        raw["transitions"].insert(
            index,
            {
                "id": "TXX",
                "from": guarded["from"],
                "to": "NEEDS_HUMAN_REVIEW",
                "trigger": guarded["trigger"],
            },
        )
        with pytest.raises(ConfigError, match="could never fire"):
            build_table(raw)

    def test_an_unknown_guard_is_refused(self) -> None:
        raw = yaml.safe_load(
            (ROOT / "docs" / "spec" / "transitions.yaml").read_text(encoding="utf-8")
        )
        raw["transitions"][1]["guards"] = ["a_guard_nobody_wrote"]
        with pytest.raises(ConfigError, match="unknown guard"):
            build_table(raw)

    def test_a_gap_in_the_acceptance_matrix_is_refused(self) -> None:
        """Removing the fallback from a row that relied on it leaves events with nowhere
        to go, which is the one thing the matrix exists to prevent."""
        raw = yaml.safe_load(
            (ROOT / "docs" / "spec" / "transitions.yaml").read_text(encoding="utf-8")
        )
        del raw["accept"]["RECEIVED"]["*"]
        with pytest.raises(ConfigError, match="dropped silently"):
            build_table(raw)

    def test_an_exhaustive_row_needs_no_fallback(self) -> None:
        """The positive control: four shipped rows have no `*` and are perfectly valid."""
        exhaustive = [state.value for state, row in TABLE.accept.items() if "*" not in row]
        assert sorted(exhaustive) == [
            "AWAITING_APPROVAL",
            "AWAITING_OPS",
            "AWAITING_SELLER",
            "NEEDS_HUMAN_REVIEW",
        ]

    def test_an_unknown_group_is_refused(self) -> None:
        raw = yaml.safe_load(
            (ROOT / "docs" / "spec" / "transitions.yaml").read_text(encoding="utf-8")
        )
        for row in raw["transitions"]:
            if "from_group" in row:
                row["from_group"] = "not_a_group"
                break
        with pytest.raises(ConfigError, match="unknown group"):
            build_table(raw)


class TestLookup:
    def test_find_returns_the_transition(self) -> None:
        found = TABLE.find(CaseState.RECEIVED, Trigger.ELIGIBILITY_PASSED)
        assert found is not None
        assert found.to_state is CaseState.GATHERING_EVIDENCE

    def test_a_guarded_trigger_returns_every_candidate(self) -> None:
        """VERIFYING on action_verified goes six different ways; the guards decide which."""
        candidates = TABLE.candidates(CaseState.VERIFYING, Trigger.ACTION_VERIFIED)
        assert {c.id for c in candidates} == {"T18", "T19", "T20", "T21", "T24", "T25"}
        assert all(c.guards for c in candidates), "a guardless row here would shadow the rest"

    def test_find_returns_none_for_an_illegal_move(self) -> None:
        assert TABLE.find(CaseState.RECEIVED, Trigger.ACTION_VERIFIED) is None
        assert TABLE.candidates(CaseState.RECEIVED, Trigger.ACTION_VERIFIED) == ()

    def test_effects_are_parsed(self) -> None:
        t90 = TABLE.by_id("T90")
        assert t90.effects.cancel_all_timers is True
        assert t90.effects.mark_in_flight_unknown is True
        assert t90.effects.set_review_reason is True

    def test_named_timer_cancellations_are_parsed(self) -> None:
        named = [t for t in TABLE.transitions if t.effects.cancel_timers]
        assert named, "no transition cancels a named timer; the parser may be dropping them"
        assert all(isinstance(kind, TimerKind) for t in named for kind in t.effects.cancel_timers)

    def test_the_table_is_parsed_once(self) -> None:
        assert default_table() is TABLE

    def test_loading_from_an_explicit_path_works(self) -> None:
        other = load_transition_table(ROOT / "docs" / "spec" / "transitions.yaml")
        assert other.version == TABLE.version
