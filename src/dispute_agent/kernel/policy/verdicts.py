"""The verdict lattice and the context rules read (ADR 0015, kernel-interfaces.md 7).

The composition law is one line — the verdict is the maximum of its inputs — and everything
safe about this system's decisions rests on it. Adding a rule can only raise a verdict.
Adding a caution signal can only raise a verdict. There is no operation here that lowers one,
which is why I7 and I15 hold no matter what order things are evaluated in.

`combine` takes `max` rather than something cleverer on purpose. A weighted scheme, or one
where two REQUIRE_APPROVALs make an ESCALATE, would be a system where adding information can
produce a surprise. Strictest-wins is boring and that is the point.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from dispute_agent.kernel.domain.actions import ActionIntent, Approval, RuleResult
from dispute_agent.kernel.domain.enums import PolicyStage, VerdictKind
from dispute_agent.kernel.domain.evidence import ChargeAssessment
from dispute_agent.kernel.domain.models import Case
from dispute_agent.kernel.domain.patterns import CautionSignal
from dispute_agent.kernel.domain.reasoning import Proposal, ReplyReading, ScreenResult
from dispute_agent.kernel.policy.config import PolicyConfig

if TYPE_CHECKING:
    from collections.abc import Iterable

_FROZEN = ConfigDict(frozen=True, extra="forbid")


def combine(verdicts: Iterable[VerdictKind]) -> VerdictKind:
    """The strictest wins. ALLOW when there is nothing to combine."""
    return max(verdicts, default=VerdictKind.ALLOW)


class PolicyContext(BaseModel):
    """Everything a rule may look at, and nothing else.

    Rules are pure functions of this. That is what makes them testable one at a time, and it
    is also P5 enforced structurally: anything a model produced reaches a rule only through a
    clearly-named field here — `proposal`, `screen_results`, `reply_readings` — so a rule
    cannot accidentally treat model output as fact without naming it in its own code.
    """

    model_config = _FROZEN

    case: Case
    stage: PolicyStage
    intent: ActionIntent | None = None
    proposal: Proposal | None = None
    assessment: ChargeAssessment | None = None
    approval: Approval | None = None
    signals: tuple[CautionSignal, ...] = ()
    modules: Mapping[str, bool] = {}
    kill_switch: bool = False
    messages_sent_total: int = 0
    messages_sent_today: int = 0
    live_credits_same_shipment_reason: int = 0
    case_has_verified_credit: bool = False
    screen_results: tuple[ScreenResult, ...] = ()
    reply_readings: tuple[ReplyReading, ...] = ()
    policy: PolicyConfig = PolicyConfig()
    now: datetime

    def module_enabled(self, name: str) -> bool:
        """Absent means disabled.

        Unknown is not the same as available, and defaulting the other way would make a
        missing module look fine until a case reached it (I6).
        """
        return self.modules.get(name, False)


def allow(rule_id: str, reason: str = "ok") -> RuleResult:
    return RuleResult(rule_id=rule_id, verdict=VerdictKind.ALLOW, reason=reason)


def deny(rule_id: str, reason: str) -> RuleResult:
    return RuleResult(rule_id=rule_id, verdict=VerdictKind.DENY, reason=reason)


def require_approval(rule_id: str, reason: str) -> RuleResult:
    return RuleResult(rule_id=rule_id, verdict=VerdictKind.REQUIRE_APPROVAL, reason=reason)


def escalate(rule_id: str, reason: str) -> RuleResult:
    return RuleResult(rule_id=rule_id, verdict=VerdictKind.ESCALATE, reason=reason)


def verdicts_of(results: Sequence[RuleResult], signals: Sequence[CautionSignal]) -> VerdictKind:
    """The composition law, in one place so there is one implementation of it."""
    return combine([*(r.verdict for r in results), *(s.min_verdict for s in signals)])
