"""The transition table, as data (ADR 0005, transitions.yaml).

The rule this file exists to keep: **no `if` statement anywhere decides whether a move is
legal**. A transition is a row with an id, a trigger, guards and effects, and the state
machine looks it up. That is what makes the table reviewable by someone who does not read
Python, and what lets an event row name the T-number that caused it — so a case's history can
be read back against the rules that were in force.

`build_table` takes already-parsed data rather than reading the file. The kernel reads no
files and has no YAML dependency (ARCHITECTURE.md section 2); `infra/transitions.py` loads
the document and calls this. One interpretation of the structure, in the kernel, where the
state machine can be tested against it without a disk.

`from_group` is expanded here, at build time. `T90` is declared once against the nine
`escalable` states, and the table holds nine rows: the legal-edge set is then explicit, and a
question like "can this state escalate?" is a lookup rather than a group membership test
somewhere else in the code.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from dispute_agent.kernel.domain.enums import CaseState, TimerKind, Trigger
from dispute_agent.kernel.domain.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: The reaction an inbound event gets in a given state, before any transition is attempted.
#: "absorb" means the case takes it in its stride; "defer" means hold it until the case moves;
#: "ignore" means record that it arrived and do nothing (never drop it silently).
PLAIN_REACTIONS = frozenset({"absorb", "defer", "ignore"})


@dataclass(frozen=True)
class Effects:
    """What a transition does besides changing the state.

    Every field defaults to "nothing happens", so a transition that declares no effects is
    inert rather than ambiguous.
    """

    cancel_timers: tuple[TimerKind, ...] = ()
    cancel_all_timers: bool = False
    schedule_timers: tuple[TimerKind, ...] = ()
    increment: tuple[str, ...] = ()
    supersede_pending: bool = False
    clear_pending: bool = False
    mark_in_flight_unknown: bool = False
    set_review_reason: bool = False
    clear_review_reason: bool = False
    record_outcome: bool = False


@dataclass(frozen=True)
class Transition:
    """One row of the table.

    `from_state` is None only for T00, which opens a case: there is no state to leave.
    """

    id: str
    from_state: CaseState | None
    to_state: CaseState
    trigger: Trigger
    guards: tuple[str, ...] = ()
    effects: Effects = field(default_factory=Effects)
    note: str = ""


@dataclass(frozen=True)
class TransitionTable:
    """The whole table, plus the acceptance matrix and the vocabulary it is written in."""

    version: str
    states: tuple[CaseState, ...]
    groups: Mapping[str, tuple[CaseState, ...]]
    timer_kinds: tuple[TimerKind, ...]
    triggers: tuple[Trigger, ...]
    guards: Mapping[str, str]
    steps: Mapping[str, Mapping[str, object]]
    transitions: tuple[Transition, ...]
    accept: Mapping[CaseState, Mapping[str, str]]
    #: The keys the acceptance matrix is written against. TIMER_FIRED is split by timer kind
    #: ("TIMER_FIRED.SELLER_REMINDER"), because a case waiting on a seller treats a reminder
    #: and an approval expiry completely differently.
    input_event_types: tuple[str, ...]
    checks: Mapping[str, str]

    def candidates(self, from_state: CaseState | None, trigger: Trigger) -> tuple[Transition, ...]:
        """Every row that could fire, in declaration order.

        One trigger often has several destinations chosen by guards: VERIFYING on
        `action_verified` goes six different ways depending on what was verified. The machine
        evaluates guards and takes the first row whose guards all hold, so declaration order
        is the tie-break — which is why `_validate` refuses two rows with identical guards,
        and refuses an unguarded row placed before a guarded one.
        """
        return tuple(
            t for t in self.transitions if t.from_state == from_state and t.trigger is trigger
        )

    def find(self, from_state: CaseState | None, trigger: Trigger) -> Transition | None:
        """The first candidate, for callers that only need to know whether a move exists."""
        found = self.candidates(from_state, trigger)
        return found[0] if found else None

    def by_id(self, transition_id: str) -> Transition:
        for transition in self.transitions:
            if transition.id == transition_id:
                return transition
        raise ConfigError(f"no transition {transition_id}")

    def outgoing(self, state: CaseState) -> tuple[Transition, ...]:
        return tuple(t for t in self.transitions if t.from_state == state)

    def legal_pairs(self) -> frozenset[tuple[CaseState, CaseState]]:
        """What `legal_transitions` in schema.sql holds. Compared in the drift test."""
        return frozenset(
            (t.from_state, t.to_state) for t in self.transitions if t.from_state is not None
        )

    def reaction(self, state: CaseState, event_type: str) -> str:
        """What a case in this state does with this inbound event type.

        Falls back to the row's `*` entry, which every state has: there is no inbound event
        the matrix has no answer for, because "no answer" would mean dropping it.
        """
        row = self.accept[state]
        if event_type in row:
            return row[event_type]
        if "*" in row:
            return row["*"]
        raise ConfigError(f"the acceptance matrix has no entry for {state.value}/{event_type}")


def _timer_kinds(value: Any) -> tuple[TimerKind, ...]:
    if isinstance(value, list):
        return tuple(TimerKind(item) for item in value)
    return ()


def _effects(raw: Mapping[str, Any] | None) -> Effects:
    if not raw:
        return Effects()
    cancel = raw.get("cancel_timers")
    return Effects(
        cancel_timers=_timer_kinds(cancel),
        cancel_all_timers=cancel == "all",
        schedule_timers=_timer_kinds(raw.get("schedule_timers")),
        increment=tuple(raw.get("increment") or ()),
        supersede_pending=bool(raw.get("supersede_pending", False)),
        clear_pending=bool(raw.get("clear_pending", False)),
        mark_in_flight_unknown=bool(raw.get("mark_in_flight_unknown", False)),
        set_review_reason=bool(raw.get("set_review_reason", False)),
        clear_review_reason=bool(raw.get("clear_review_reason", False)),
        record_outcome=bool(raw.get("record_outcome", False)),
    )


def build_table(raw: Mapping[str, Any]) -> TransitionTable:
    """Turn the parsed document into frozen data, expanding `from_group` as it goes.

    `Any` because this is the boundary where an untyped YAML document becomes typed data.
    Everything it returns is frozen and precisely typed; the looseness stops here.
    """
    groups_raw: Mapping[str, Sequence[str]] = raw["groups"]
    groups = {name: tuple(CaseState(s) for s in members) for name, members in groups_raw.items()}

    transitions: list[Transition] = []
    for row in raw["transitions"]:
        trigger = Trigger(str(row["trigger"]).upper())
        effects = _effects(row.get("effects"))
        guards = tuple(row.get("guards") or ())
        note = str(row.get("note", ""))
        to_state = CaseState(row["to"])

        if "from_group" in row:
            group = str(row["from_group"])
            if group not in groups:
                raise ConfigError(f"transition {row['id']} names unknown group {group!r}")
            for state in groups[group]:
                transitions.append(
                    Transition(
                        id=str(row["id"]),
                        from_state=state,
                        to_state=to_state,
                        trigger=trigger,
                        guards=guards,
                        effects=effects,
                        note=note,
                    )
                )
            continue

        origin = row.get("from")
        transitions.append(
            Transition(
                id=str(row["id"]),
                from_state=None if origin is None else CaseState(origin),
                to_state=to_state,
                trigger=trigger,
                guards=guards,
                effects=effects,
                note=note,
            )
        )

    accept_raw: Mapping[str, Mapping[str, str]] = raw["accept"]
    accept = {CaseState(state): dict(row) for state, row in accept_raw.items()}

    table = TransitionTable(
        version=str(raw["version"]),
        states=tuple(CaseState(s) for s in raw["states"]),
        groups=groups,
        timer_kinds=tuple(TimerKind(k) for k in raw["timer_kinds"]),
        triggers=tuple(Trigger(t.upper()) for t in raw["triggers"]),
        guards=dict(raw["guards"]),
        steps=dict(raw["steps"]),
        transitions=tuple(transitions),
        accept=accept,
        input_event_types=tuple(raw["input_event_types"]),
        checks=dict(raw["checks"]),
    )
    _validate(table)
    return table


def _validate(table: TransitionTable) -> None:
    """Fail at build time on a table that could not work (CLAUDE.md section 6).

    Two of these have bitten real systems: a transition naming a guard nobody implements
    silently never fires, and two rows sharing (from, trigger) means the lookup's answer
    depends on list order.
    """
    seen: dict[tuple[CaseState | None, Trigger, tuple[str, ...]], str] = {}
    for transition in table.transitions:
        key = (transition.from_state, transition.trigger, transition.guards)
        if key in seen and seen[key] != transition.id:
            origin = "none" if transition.from_state is None else transition.from_state.value
            raise ConfigError(
                f"transitions {seen[key]} and {transition.id} leave {origin} on "
                f"{transition.trigger.value} under identical guards; nothing could choose "
                "between them"
            )
        seen[key] = transition.id
        for guard in transition.guards:
            if guard not in table.guards:
                raise ConfigError(f"transition {transition.id} names unknown guard {guard!r}")

    # An unguarded row always fires, so one declared before a guarded alternative would
    # shadow it and the guarded row would be dead. The failure is silent, which is why it is
    # checked here rather than left to be noticed.
    grouped: dict[tuple[CaseState | None, Trigger], list[Transition]] = {}
    for transition in table.transitions:
        grouped.setdefault((transition.from_state, transition.trigger), []).append(transition)
    for rows in grouped.values():
        for earlier, later in itertools.pairwise(rows):
            if not earlier.guards:
                raise ConfigError(
                    f"transition {earlier.id} has no guards but {later.id} follows it on the "
                    f"same trigger; {later.id} could never fire"
                )

    # Every state must have an answer for every inbound event type. A `*` fallback is one way
    # to get there; four states instead list all sixteen explicitly, which is equally total
    # and rather clearer about what each one does. What must not exist is a gap, because a
    # gap means an inbound event with nowhere to go — and the one thing the matrix is for is
    # that nothing is ever dropped silently.
    for state in table.states:
        if state not in table.accept:
            raise ConfigError(f"the acceptance matrix has no row for {state.value}")
        row = table.accept[state]
        if "*" in row:
            continue
        missing = set(table.input_event_types) - set(row)
        if missing:
            raise ConfigError(
                f"{state.value} has no entry for {sorted(missing)} and no '*' fallback; "
                "those events would be dropped silently"
            )
