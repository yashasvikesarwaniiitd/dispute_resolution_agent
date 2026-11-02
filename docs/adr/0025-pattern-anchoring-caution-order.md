# ADR 0025: The pattern anchoring control is a total caution order enforced in code

Status: accepted (D44, Stage A4, after the independent review). Supersedes the step 3 wording of `agent-design.md` section 7 as first drafted.

## Decision

The Phase 7 two-pass control compares the two passes on a **total** order over the six actions, strictest first:

    escalate_to_human > request_remeasurement > request_seller_evidence > wait > uphold_charge > propose_credit

The adapter returns pass 2 only when it ranks at least as cautious as pass 1. It also keeps pass 1's `confidence` and `action_parameters` whenever pass 2 would raise the confidence or change the parameters while keeping the action. The model's own `pattern_influence.changed_recommendation` is validated for consistency but is never the control.

## Why

The first draft wrote the order as `escalate > request_* > wait > uphold/credit`, tying `uphold_charge` and `propose_credit` at one rank. The independent review found the hole: with P1 = `uphold_charge` and P2 = `propose_credit`, P2 is not *less* cautious under that order, so the adapter returns it — and a cross-case pattern has just caused a credit. I7 says patterns "can never approve, uphold, credit or lower a threshold". A partial order cannot enforce a total guarantee; the two actions that decide the case must be separated, and `propose_credit` ranks lowest because it is the only action that moves money.

The second hole was subtler. The control only ever compared the action, so a pattern could keep the action and lift `confidence` from `low` to `medium` — which removes R22's `low_confidence` escalation. That is a pattern lowering a verdict, which I7 and P5 forbid, and it would have passed every check in the original design. Hence the parameter and confidence clause.

Relying on the model's self-report was the third problem: `changed_recommendation` is a claim by the same model whose anchoring we are trying to control. It stays as a validated consistency check (a mismatch is `InvalidModelOutput`, which catches a confused or evasive answer) but the enforcement is a comparison our code performs on two values our code holds.

## Options considered

1. **Keep the partial order, document the tie.** Rejected: the tie is exactly where the money is.
2. **Forbid pattern-driven action changes entirely.** Rejected: patterns legitimately make the agent *more* cautious, which is their whole purpose (I7 allows added caution). Blocking all change would make `pattern_context` useless.
3. **Enforce through policy rather than the adapter.** Rejected: policy sees one `Proposal`. Keeping both passes inside the adapter is what lets the kernel stay unaware that two calls happened (D3: connecting cross-case changes zero lines in `kernel/`).

## Consequences

- `CautionRank` is specified in `kernel-interfaces.md` section 15.3 as an `IntEnum`, so the order is data, not scattered comparisons.
- The adapter records `pattern_relaxation_blocked` whenever it returns P1, which is an observable signal that a pattern tried to relax a case.
- Two passes cost two calls, so `max_calls_per_case` becomes 10 when `cross_case` is enabled (`config.md`). Without that change every patterned case would exhaust the budget and escalate `llm_budget_exceeded`.
- Scenarios S15 and S16 exercise the Phase 7 path; neither may credit or uphold because of a pattern.
