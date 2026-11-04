"""The policy rules, one per file (policy-rules.md).

Phase 1 implements the rules that need nothing outside the kernel. Each remaining rule has a
skipped test naming the phase that writes it, so the catalogue's gaps are visible in a test
run rather than only in a document.

`MANDATORY_RULES` is the set the engine refuses to construct without. They are the ones whose
absence would not look like a failure — a kill switch that is never consulted, a credit that
is never checked for an approval — so the engine fails at startup instead.
"""

from __future__ import annotations

from typing import Final

from dispute_agent.kernel.policy.rules.credit_rules import (
    ApprovalFresh,
    CreditAmountDeterministic,
    CreditReasonProven,
    CreditWithinOutstanding,
    NoSecondOutcomeAfterCredit,
)
from dispute_agent.kernel.policy.rules.model_signal_rules import (
    GuardrailBlockedRule,
    LowConfidenceConsequential,
    ManipulationFlagged,
    WithdrawalClaimed,
)
from dispute_agent.kernel.policy.rules.r01_action_on_menu import ActionOnMenu
from dispute_agent.kernel.policy.rules.r02_kill_switch import KillSwitchRule
from dispute_agent.kernel.policy.rules.r03_module_available import ModuleAvailable
from dispute_agent.kernel.policy.rules.r04_contact_limit_case import ContactLimitCase
from dispute_agent.kernel.policy.rules.r05_contact_limit_daily import ContactLimitDaily
from dispute_agent.kernel.policy.rules.r06_recipient_allowlisted import RecipientAllowlisted
from dispute_agent.kernel.policy.rules.r08_message_review_required import MessageReviewRequired
from dispute_agent.kernel.policy.rules.r09_message_numbers_verified import (
    MessageNumbersVerified,
)
from dispute_agent.kernel.policy.rules.r10_message_approval_bound import MessageApprovalBound
from dispute_agent.kernel.policy.rules.r12_clarification_limit import ClarificationLimit
from dispute_agent.kernel.policy.rules.r13_credit_requires_approval import CreditRequiresApproval
from dispute_agent.kernel.policy.rules.r17_credit_not_duplicate import CreditNotDuplicate

#: kernel-interfaces.md section 13: the engine refuses to construct without these.
MANDATORY_RULES: Final[frozenset[str]] = frozenset({"R02", "R03", "R10", "R13", "R17"})


def phase_1_rules() -> tuple[object, ...]:
    """The rules Phase 1 can evaluate, in catalogue order.

    Order does not affect the verdict — the lattice takes the maximum — but it does affect
    which reason is read first in a log, so catalogue order keeps that predictable.
    """
    return (
        ActionOnMenu(),
        KillSwitchRule(),
        ModuleAvailable(),
        ContactLimitCase(),
        ContactLimitDaily(),
        RecipientAllowlisted(),
        MessageReviewRequired(),
        MessageApprovalBound(),
        CreditRequiresApproval(),
        CreditNotDuplicate(),
    )


def phase_3_rules() -> tuple[object, ...]:
    """Phase 1's rules plus R09, which needs a rendered message to check.

    Written out rather than built by inserting into the Phase 1 list: the order is the
    catalogue's, and a list that rearranges itself is one nobody can read against
    policy-rules.md. Order does not change the verdict - the lattice takes the maximum -
    but it does decide which reason a log shows first.
    """
    return (
        ActionOnMenu(),
        KillSwitchRule(),
        ModuleAvailable(),
        ContactLimitCase(),
        ContactLimitDaily(),
        RecipientAllowlisted(),
        MessageReviewRequired(),
        MessageNumbersVerified(),
        MessageApprovalBound(),
        CreditRequiresApproval(),
        CreditNotDuplicate(),
    )


def phase_5_rules() -> tuple[object, ...]:
    """Every rule the line runs from Phase 4+/5 on: money (R14-R18, R25) and the model and
    screen signals (R20-R23), in catalogue order. R11 and R19 are carried by the investigate
    step (a blocked draft and a wait both escalate there); R12 is the reply round's (one clarification, then a person).
    """
    return (
        ActionOnMenu(),
        KillSwitchRule(),
        ModuleAvailable(),
        ContactLimitCase(),
        ContactLimitDaily(),
        RecipientAllowlisted(),
        MessageReviewRequired(),
        MessageNumbersVerified(),
        MessageApprovalBound(),
        ClarificationLimit(),
        CreditRequiresApproval(),
        CreditReasonProven(),
        CreditAmountDeterministic(),
        CreditWithinOutstanding(),
        CreditNotDuplicate(),
        NoSecondOutcomeAfterCredit(),
        WithdrawalClaimed(),
        ManipulationFlagged(),
        LowConfidenceConsequential(),
        GuardrailBlockedRule(),
        ApprovalFresh(),
    )


__all__ = [
    "MANDATORY_RULES",
    "ActionOnMenu",
    "ContactLimitCase",
    "ContactLimitDaily",
    "CreditNotDuplicate",
    "CreditRequiresApproval",
    "KillSwitchRule",
    "MessageApprovalBound",
    "MessageNumbersVerified",
    "MessageReviewRequired",
    "ModuleAvailable",
    "RecipientAllowlisted",
    "phase_1_rules",
    "phase_3_rules",
    "phase_5_rules",
]
