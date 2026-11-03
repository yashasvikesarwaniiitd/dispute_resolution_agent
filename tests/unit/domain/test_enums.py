"""Card P0-03: enums, read against the frozen spec files rather than against memory.

The two drift tests here are the valuable ones. CaseState and Trigger are transcribed from
transitions.yaml; nothing stops the two diverging except a test that reads the file. The
Stage A4 review found several places where a document and its implementation had quietly
parted company, so these read the source of truth at test time.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from dispute_agent.kernel.domain.enums import (
    RESOLVED_STATES,
    TERMINAL_STATES,
    CautionRank,
    ProposalAction,
    ReviewCode,
    TimerKind,
    Trigger,
    VerdictKind,
)
from dispute_agent.kernel.domain.enums import CaseState as State

ROOT = Path(__file__).resolve().parents[3]
TRANSITIONS = yaml.safe_load((ROOT / "docs/spec/transitions.yaml").read_text(encoding="utf-8"))


def test_case_state_matches_transitions_yaml() -> None:
    assert {s.value for s in State} == set(TRANSITIONS["states"])


def test_trigger_matches_transitions_yaml() -> None:
    """Trigger values are the `triggers` keys, upper-cased (kernel-interfaces section 3)."""
    assert {t.value for t in Trigger} == {k.upper() for k in TRANSITIONS["triggers"]}


def test_timer_kind_matches_transitions_yaml() -> None:
    assert {t.value for t in TimerKind} == set(TRANSITIONS["timer_kinds"])


def test_sixteen_states() -> None:
    """Ten working states and six outcomes (phase-0 prompt task 3)."""
    assert len(State) == 16


def test_terminal_states_match_transitions_yaml() -> None:
    """Card P0-04 transcribed these; transitions.yaml stays the authority."""
    expected = {
        name for name, body in TRANSITIONS["states"].items() if body["category"] == "terminal"
    }
    assert {s.value for s in TERMINAL_STATES} == expected


def test_resolved_states_match_transitions_yaml() -> None:
    expected = {name for name, body in TRANSITIONS["states"].items() if body.get("resolved")}
    assert {s.value for s in RESOLVED_STATES} == expected


def test_verdict_kind_is_ordered_by_caution() -> None:
    assert VerdictKind.ALLOW < VerdictKind.REQUIRE_APPROVAL < VerdictKind.ESCALATE
    assert VerdictKind.ESCALATE < VerdictKind.DENY
    assert max(VerdictKind.ALLOW, VerdictKind.ESCALATE) is VerdictKind.ESCALATE


class TestCautionRank:
    """ADR 0025. The order must be total: a tie is how a pattern turns an uphold into a credit."""

    def test_covers_every_action(self) -> None:
        assert {r.name for r in CautionRank} == {a.name for a in ProposalAction}

    def test_is_total(self) -> None:
        values = [r.value for r in CautionRank]
        assert len(set(values)) == len(values), "two actions share a rank; the order is not total"

    def test_credit_is_least_cautious(self) -> None:
        """The hole the Stage A4 review found: uphold and credit were tied."""
        assert CautionRank.PROPOSE_CREDIT < CautionRank.UPHOLD_CHARGE
        assert min(CautionRank) == CautionRank.PROPOSE_CREDIT

    def test_escalate_is_most_cautious(self) -> None:
        assert max(CautionRank) == CautionRank.ESCALATE_TO_HUMAN


def test_proposal_action_matches_prompt_schema() -> None:
    """ProposalAction mirrors prompts/case_investigator/v1/schema.json."""
    import json

    schema = json.loads(
        (ROOT / "prompts/case_investigator/v1/schema.json").read_text(encoding="utf-8")
    )
    assert set(schema["properties"]["action"]["enum"]) == {a.value for a in ProposalAction}


def _review_codes_declared_in_the_spec() -> set[str]:
    """Every ReviewCode value kernel-interfaces.md declares, section 3 and its amendments.

    The enum is declared once in section 3 and extended again in section 13, which is how
    six members came to be missed when this file was first written. Reading both out of the
    document is the only thing that keeps the two from parting company again.
    """
    text = (ROOT / "docs/spec/kernel-interfaces.md").read_text(encoding="utf-8")
    member = re.compile(r'^\s*([A-Z][A-Z0-9_]*)\s*=\s*"([a-z0-9_]+)"')
    values: set[str] = set()
    collecting = False
    for line in text.splitlines():
        if "class ReviewCode(StrEnum):" in line or re.search(
            r"# ReviewCode \(section \d+\): new members", line
        ):
            collecting = True
            continue
        if not collecting:
            continue
        found = [m.group(2) for part in line.split(";") if (m := member.match(part.strip()))]
        if found:
            values.update(found)
        elif line.strip() and not line.lstrip().startswith("#"):
            collecting = False  # the block moved on to something else
    return values


def test_review_code_matches_kernel_interfaces() -> None:
    """Drift test: the enum is exactly what the spec declares, amendments included."""
    assert {c.value for c in ReviewCode} >= _review_codes_declared_in_the_spec()


def test_review_codes_used_by_scenarios_all_exist() -> None:
    """Every review code the scenario catalogue expects is a real ReviewCode."""
    catalogue = yaml.safe_load((ROOT / "docs/spec/eval-scenarios.yaml").read_text(encoding="utf-8"))
    known = {c.value for c in ReviewCode}
    used: set[str] = set()
    for scenario in catalogue["scenarios"]:
        expect = scenario.get("expect") or {}
        for key in ("review", "review_on_first_stop"):
            value = expect.get(key)
            if isinstance(value, str):
                # "module_unavailable:email" renders the module after a colon (I6).
                used.add(value.split(":", 1)[0])
    assert used <= known, f"scenarios expect unknown review codes: {sorted(used - known)}"
