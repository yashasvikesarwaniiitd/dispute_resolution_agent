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
