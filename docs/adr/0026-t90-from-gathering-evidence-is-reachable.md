# ADR 0026: T90 from GATHERING_EVIDENCE stays; it is reachable, just not by an evidence failure

Status: accepted (owner, 2026-11-03)
Date: 2026-11-03

## Decision
Keep the `T90` edge from `GATHERING_EVIDENCE` in `transitions.yaml`. It is reachable, and
`tests/unit/statemachine/test_t90_from_gathering.py` walks it. No frozen specification file
changes.

## Why
In plain terms: while the system is collecting evidence, something unrelated to the evidence
can still go wrong, and when it does the case needs a way to reach a person.

The Stage A4 review found that no scenario reaches this edge and asked whether it was dead
design. The question was framed around evidence failures, and for those the answer is clear —
a failed fetch becomes a FAILED evidence item that a later step acts on
(`orchestrator.md` section 5), never an escalation from the gathering state itself. That is
why no evidence scenario walks it.

But `T90` is not an evidence transition. It is declared against the whole `escalable` group
and fires on the `escalate` trigger, which the orchestrator raises for reasons that have
nothing to do with what a step is doing:

- the **kill switch** is engaged (I10): the case stops wherever it is, including mid-gather
- the **step limit** is reached (`orchestrator.md` section 3): a case producing work without
  resting stops and tells someone
- a **module the step needs is disabled** (I6): `module_unavailable:evidence` is raised and
  converted in the orchestrator's one conversion point
- an **internal error** leaves the case somewhere it cannot continue from

Each of those can happen while a case is in `GATHERING_EVIDENCE`, and in each the case must
reach `NEEDS_HUMAN_REVIEW`. Removing the edge would mean that a kill switch thrown at the
wrong moment leaves a case with no legal move at all — stuck, needing a migration to rescue.
That is a much worse failure than an unwalked edge in a coverage report.

The review's finding was still worth having. "No scenario reaches this" is exactly the right
thing to notice; the conclusion it suggested happened to be wrong, and the cost of checking
was one ADR.

## Options considered
- **Remove the edge** (the review's implied suggestion). Rejected: the four causes above are
  real, and three of them are already implemented in Phase 1. A case stranded with no legal
  transition cannot be recovered through the state machine at all.
- **Keep it and add an eval scenario now.** The honest scenario is "kill switch thrown while
  gathering", and `gather_evidence` is not built until Phase 2 (card P2-04). Writing a
  scenario against a step that does not exist would be writing the expectation twice and
  checking it never.
- **Keep it silently.** Rejected: an edge nobody can explain is one somebody deletes later
  for the same reason the review flagged it.

## Consequences
Easier: the escalation path out of `GATHERING_EVIDENCE` is tested, and the reasoning is
written down where the next person to notice the coverage gap will find it.

Harder: `scripts/check_scenarios.py` still lists this edge as unwalked by the scenario
catalogue, because it is walked by a unit test rather than by a scenario. That stays true
until Phase 2.

Watch: when card P2-04 builds `gather_evidence`, add the kill-switch-while-gathering scenario
to `eval-scenarios.yaml` and the edge count becomes 46 of 50. The remaining four unwalked
edges are a separate question, not this one.

## Sources
Project documents only: `docs/spec/transitions.yaml`, `docs/spec/orchestrator.md` section 5,
`docs/spec/a4-review-findings.md` (D44), `CLAUDE.md` invariants I6 and I10. No external
documentation was needed.
