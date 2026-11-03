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

from dispute_agent.kernel.policy.rules.r01_action_on_menu import ActionOnMenu
from dispute_agent.kernel.policy.rules.r02_kill_switch import KillSwitchRule
from dispute_agent.kernel.policy.rules.r03_module_available import ModuleAvailable
from dispute_agent.kernel.policy.rules.r04_contact_limit_case import ContactLimitCase
from dispute_agent.kernel.policy.rules.r05_contact_limit_daily import ContactLimitDaily
from dispute_agent.kernel.policy.rules.r06_recipient_allowlisted import RecipientAllowlisted
from dispute_agent.kernel.policy.rules.r08_message_review_required import MessageReviewRequired
from dispute_agent.kernel.policy.rules.r10_message_approval_bound import MessageApprovalBound
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


__all__ = [
    "MANDATORY_RULES",
    "ActionOnMenu",
    "ContactLimitCase",
    "ContactLimitDaily",
    "CreditNotDuplicate",
    "CreditRequiresApproval",
    "KillSwitchRule",
    "MessageApprovalBound",
    "MessageReviewRequired",
    "ModuleAvailable",
    "RecipientAllowlisted",
    "phase_1_rules",
]
