# ADR 0005: State machine as data, with an event acceptance matrix

Status: accepted (owner, 2026-11-02)
Date: 2026-11-02

## Decision
The state machine is a declarative table (`docs/spec/transitions.yaml`, transcribed into `kernel/statemachine/transitions.py`) of transitions with named guards and effects, plus a matrix that says what every state does with every inbound event (transition, step, absorb, defer, ignore). The table reconciles FLOW.md section 2 with thirteen recorded changes (X1-X13), including a human return path from NEEDS_HUMAN_REVIEW and a bounded wait in every waiting state.

## Why
A table can be checked mechanically: reachability, no dead ends, credits only via approval, resolved only after verification. Sixteen such checks (C1-C16) were run on it and Phase 1 keeps them as tests. The matrix closes the gap a transition list leaves open: what happens to a reply that arrives while an email is being sent, or a timer that fires after the case moved on. Without it those situations get handled ad hoc in code.

## Options considered
- Transitions written as if/else in the orchestrator: unreviewable and untestable as a whole.
- A state machine library: a dependency for what a 50-row table does; the kernel may import only the standard library and pydantic.

## Consequences
Easier: the owner reviews behaviour in one file; a spec-conformance test keeps code and spec identical; the database refuses any state pair not in the table. Harder: adding a state means updating the table, the matrix, the CHECK list and the `legal_transitions` seed together (conformance tests catch a miss). Watch: guards stay pure functions of `GuardContext`.

## Sources
Project documents only (CLAUDE.md, docs/FLOW.md, docs/HANDOFF.md). No external documentation was needed.

## Implementation notes (Phase 1, 2026-11-03)

The decision held, and two of my own validation rules were wrong before the table was.

**A trigger may have several destinations.** My first loader refused two rows sharing a
from-state and trigger, and it fired immediately on T11/T12. That is not a bug in the table:
`VERIFYING` on `action_verified` goes six different ways, chosen by guards. The lookup
returns candidates in declaration order and the machine takes the first whose guards hold.
What is genuinely forbidden is two rows with *identical* guards, and an unguarded row placed
before a guarded one — which would make the guarded row dead code and fail silently.

**Exhaustive beats a fallback.** My second rule demanded a `*` entry in every acceptance row.
Four states instead list all sixteen inbound event types explicitly, which is equally total
and clearer about what each one does. The rule is coverage, not the form it takes.

**Guards are supplied, never assumed.** `apply` takes already-evaluated booleans and raises
if one it needs was not supplied. The step that calls it is what knows whether an approval
matches a pending intent, and a guard defaulting to `True` would be `intent_is_not_credit`
quietly passing — I3 defeated by an omission.

`from_group` is expanded at load time, so T90 is one declaration and nine rows and the
legal-edge set is explicit. ADR 0026 records why the `GATHERING_EVIDENCE` row stays.
