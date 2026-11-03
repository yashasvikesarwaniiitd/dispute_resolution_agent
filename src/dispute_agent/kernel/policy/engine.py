"""The policy engine (kernel-interfaces.md 7, ADR 0015, I3, I6, I7, I10, I13, I15).

Composition is `max` over every rule result and every signal floor. That is the entire
decision procedure, and keeping it that simple is what makes the safety argument checkable:
adding a rule can only raise a verdict, adding a signal can only raise a verdict, and no
code path here lowers one.

Two defensive choices are worth naming.

A rule that raises is not allowed to take the system down, nor to be ignored. It becomes
`DENY rule_error:<id>` — fail safe. A buggy rule stopping an action is recoverable; a buggy
rule being skipped means the system acted without a check it believed it had.

The engine refuses to construct without the mandatory rules. Those are the ones whose absence
would not look like a failure: a kill switch nobody consults, a credit nobody checks for an
approval. Everything would appear to work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.actions import PolicyVerdict, RuleResult
from dispute_agent.kernel.domain.enums import ReviewCode, VerdictKind
from dispute_agent.kernel.domain.errors import ConfigError
from dispute_agent.kernel.domain.models import ReviewReason
from dispute_agent.kernel.policy.rules import MANDATORY_RULES
from dispute_agent.kernel.policy.verdicts import PolicyContext, verdicts_of

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dispute_agent.kernel.domain.ids import VerdictId
    from dispute_agent.kernel.ports import Clock, IdGenerator

#: Reasons that map to a review code when the verdict escalates. Anything not listed becomes
#: POLICY_ESCALATED, which is honest rather than inventing a code that no document defines.
REASON_TO_REVIEW_CODE: dict[str, ReviewCode] = {
    "kill_switch_active": ReviewCode.KILL_SWITCH_ACTIVE,
    "manipulation_suspected": ReviewCode.MANIPULATION_SUSPECTED,
    "low_confidence": ReviewCode.LOW_CONFIDENCE,
    "multiple_definite_errors": ReviewCode.MULTIPLE_DEFINITE_ERRORS,
    "ledger_state_unknown": ReviewCode.LEDGER_STATE_UNKNOWN,
    "sender_unauthenticated": ReviewCode.SENDER_UNAUTHENTICATED,
    "seller_withdrawal_claimed": ReviewCode.SELLER_WITHDRAWAL_CLAIMED,
    "guardrail_blocked": ReviewCode.GUARDRAIL_BLOCKED,
    "reply_unresolved": ReviewCode.REPLY_UNRESOLVED,
    "wait_without_expectation": ReviewCode.WAIT_WITHOUT_EXPECTATION,
    "draft_blocked": ReviewCode.ACTION_FAILED,
}


class PolicyRuleProtocolError(ConfigError):
    """A rule that does not look like a rule. Caught at construction, not at decision time."""


class PolicyEngine:
    """Composes rules into one verdict. Holds no state; safe to share."""

    def __init__(
        self,
        rules: Sequence[object],
        clock: Clock,
        ids: IdGenerator,
        *,
        rules_version: str = "",
    ) -> None:
        self._rules = tuple(rules)
        self._clock = clock
        self._ids = ids
        self._rules_version = rules_version
        self._check_mandatory()

    def _check_mandatory(self) -> None:
        present = {getattr(rule, "rule_id", "") for rule in self._rules}
        for rule in self._rules:
            if not hasattr(rule, "evaluate") or not getattr(rule, "rule_id", ""):
                raise PolicyRuleProtocolError(f"{rule!r} is not a policy rule")
        missing = MANDATORY_RULES - present
        if missing:
            raise ConfigError(
                f"the policy engine refuses to run without {sorted(missing)}; their absence "
                "would not look like a failure, which is exactly why it is checked here"
            )

    @property
    def rule_ids(self) -> tuple[str, ...]:
        return tuple(getattr(rule, "rule_id", "") for rule in self._rules)

    def evaluate(self, ctx: PolicyContext) -> PolicyVerdict:
        """Run every rule, take the strictest answer, and record all of them.

        Every rule runs even after one denies. A verdict that listed only the first failure
        would hide the other three reasons a person needs when deciding what to do.
        """
        results: list[RuleResult] = []
        for rule in self._rules:
            results.append(self._run(rule, ctx))

        verdict = verdicts_of(results, ctx.signals)
        reason = self._review_reason(verdict, results, ctx)

        return PolicyVerdict(
            id=self._new_verdict_id(),
            case_id=ctx.case.id,
            intent_id=None if ctx.intent is None else ctx.intent.id,
            stage=ctx.stage,
            verdict=verdict,
            results=tuple(results),
            signals=ctx.signals,
            review_reason=reason,
            rules_version=self._rules_version or "unversioned",
            evaluated_at=ctx.now,
        )

    def _run(self, rule: object, ctx: PolicyContext) -> RuleResult:
        rule_id = getattr(rule, "rule_id", "unknown")
        try:
            evaluate = rule.evaluate  # type: ignore[attr-defined]
            result = evaluate(ctx)
            if not isinstance(result, RuleResult):
                # A rule that *returns* nonsense would otherwise blow up later, outside this
                # handler. A crash is safe, but it escapes the "a bad rule becomes DENY"
                # story, and the story is what the next reader will rely on.
                raise TypeError(f"{rule_id} returned {type(result).__name__}")
            return result
        except Exception:
            # Fail safe (kernel-interfaces.md section 7). A buggy rule that stops an action
            # is recoverable; a buggy rule that is skipped means the system acted without a
            # check it believed it had.
            return RuleResult(
                rule_id=rule_id,
                verdict=VerdictKind.DENY,
                reason=f"rule_error:{rule_id}",
            )

    def _review_reason(
        self, verdict: VerdictKind, results: Sequence[RuleResult], ctx: PolicyContext
    ) -> ReviewReason | None:
        """Set exactly when the verdict is ESCALATE or DENY (schema.sql policy_verdicts)."""
        if verdict < VerdictKind.ESCALATE:
            return None

        strictest = [r for r in results if r.verdict is verdict]
        reason_text = strictest[0].reason if strictest else ""

        if reason_text.startswith("module_unavailable:"):
            return ReviewReason(
                code=ReviewCode.MODULE_UNAVAILABLE,
                module=reason_text.split(":", 1)[1],
                detail=reason_text,
            )

        code = REASON_TO_REVIEW_CODE.get(reason_text)
        if code is not None:
            return ReviewReason(code=code, detail=reason_text)

        if not strictest and ctx.signals:
            # The verdict came from a signal floor rather than any rule.
            return ReviewReason(
                code=ReviewCode.POLICY_ESCALATED, detail=ctx.signals[0].reason[:300]
            )

        fallback = (
            ReviewCode.POLICY_DENIED if verdict is VerdictKind.DENY else ReviewCode.POLICY_ESCALATED
        )
        return ReviewReason(code=fallback, detail=reason_text[:300])

    def _new_verdict_id(self) -> VerdictId:
        from dispute_agent.kernel.domain.ids import VerdictId as _VerdictId

        return _VerdictId(self._ids.new_id("vrd"))
