"""Caution signals (card P1-07, I7, I15, ADR 0015).

A signal is a floor, not a vote. It can push a verdict up and has no way to pull one down,
and that is true at three levels at once:

- the type: `CautionSignal.min_verdict` cannot be `ALLOW`, so "this pattern says go ahead" is
  not a sentence that can be written (P0-04)
- the composition: the engine takes `max`, and max over a set is independent of order, so a
  signal cannot be bypassed by evaluating it earlier or later
- here: the helpers below only ever raise, and there is deliberately no `lower` or `clear`

The ordering point matters more than it sounds. A scheme where signals were applied in
sequence — each one adjusting the running verdict — could be gamed by the order they arrived
in. Taking the maximum means a set of signals has one answer regardless.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import VerdictKind
from dispute_agent.kernel.domain.patterns import CautionSignal
from dispute_agent.kernel.policy.verdicts import combine

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


def floor_of(signals: Sequence[CautionSignal]) -> VerdictKind:
    """The strictest floor the signals impose. ALLOW when there are none."""
    return combine(signal.min_verdict for signal in signals)


def raise_to_floor(verdict: VerdictKind, signals: Sequence[CautionSignal]) -> VerdictKind:
    """Apply signals to a verdict. Never returns anything lower than it was given."""
    return max(verdict, floor_of(signals))


def repeat_dispute_signal(prior_case_id: str, already_credited: bool) -> CautionSignal:
    """R24: a dispute linked to an earlier case on the same shipment and claim.

    Always at least REQUIRE_APPROVAL; ESCALATE when the shipment has any non-FAILED credit,
    in any case and whatever that case's outcome (policy-rules.md R24, I7). A signal is a
    floor: it can make the verdict stricter, never looser.
    """
    return CautionSignal(
        kind="repeat_dispute",
        min_verdict=VerdictKind.ESCALATE if already_credited else VerdictKind.REQUIRE_APPROVAL,
        reason=(
            "repeat_dispute: the shipment was already credited"
            if already_credited
            else "repeat_dispute: an earlier case exists for this shipment and claim"
        ),
        source_ref=prior_case_id,
    )


def sender_unauthenticated_signal(source_ref: str) -> CautionSignal:
    """T03: a reply whose sender failed SPF/DKIM/DMARC. It escalates; it never blocks
    reading the reply as evidence, and a pass never relaxes anything (I15)."""
    return CautionSignal(
        kind="screen_flag",
        min_verdict=VerdictKind.ESCALATE,
        reason="sender_unauthenticated: a reply failed sender authentication",
        source_ref=source_ref,
    )


def screen_flag_signal(reason: str, source_ref: str) -> CautionSignal:
    """The screen is unavailable or flagged something (I15).

    orchestrator.md section 5: losing the screen adds caution rather than removing it, so
    this is the same signal in both cases and the reason says which.
    """
    return CautionSignal(
        kind="screen_flag",
        min_verdict=VerdictKind.REQUIRE_APPROVAL,
        reason=reason[:200],
        source_ref=source_ref,
    )


def pattern_context_unavailable_signal(source_ref: str = "cross_case") -> CautionSignal:
    """HOOK A failed. Not knowing whether a pattern applies is a reason to be careful."""
    return CautionSignal(
        kind="pattern_context_unavailable",
        min_verdict=VerdictKind.REQUIRE_APPROVAL,
        reason="pattern context could not be read",
        source_ref=source_ref,
    )


def dedupe(signals: Iterable[CautionSignal]) -> tuple[CautionSignal, ...]:
    """Collapse identical signals, keeping the first of each.

    Only exact duplicates: two signals of the same kind from different sources are two
    reasons to be careful, and merging them would quietly lose one.
    """
    seen: set[tuple[str, int, str, str]] = set()
    out: list[CautionSignal] = []
    for signal in signals:
        key = (signal.kind, int(signal.min_verdict), signal.reason, signal.source_ref)
        if key in seen:
            continue
        seen.add(key)
        out.append(signal)
    return tuple(out)
