"""`make try-agent [case] [--offline]`: watch the agent decide one synthetic dispute.

Sends nothing (see `dispute_agent.api.agent_trial`). With `--offline` the model is the
scripted fake, so the whole path can be tried before any Azure resource exists.

Cases: scanner_fault (default), kurta_box_larger, calc_error_wrong_slab, ambiguous_image,
conflicting_history, injection_in_statement, not_delivered.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from typing import TYPE_CHECKING

from dispute_agent.api.agent_trial import run_trial
from dispute_agent.app import (
    DEFAULT_CONFIG_DIR,
    MODEL_CALL_LOG,
    build_llm_reasoner,
    build_model_gateway,
    build_model_policy,
)
from dispute_agent.infra.clock import SystemClock
from dispute_agent.infra.ids import UlidIdGenerator
from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.errors import KernelError

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.reasoning import ModelRequest


def offline_answer(req: ModelRequest) -> dict[str, object]:
    """A plausible answer, so the offline run exercises the real checking and policy."""
    menu = req.prompt.messages[-1].content
    asks = "- request_seller_evidence" in menu
    return {
        "action": "request_seller_evidence" if asks else "escalate_to_human",
        "confidence": "medium",
        "rationale": "Offline stand-in answer: the scripted fake, not a real model.",
        "findings": [
            {
                "kind": "missing_evidence",
                "statement": "No parcel photo on file.",
                "evidence_refs": [],
            }
        ],
        "action_parameters": (
            {"requested_items": ["A photo of the packed parcel beside a tape measure"]}
            if asks
            else {}
        ),
        "pattern_influence": {"considered": False, "changed_recommendation": False, "note": ""},
        "manipulation_flags": [],
    }


def main() -> int:
    # Windows consoles default to cp1252, which has no rupee sign.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(prog="make try-agent")
    parser.add_argument("case", nargs="?", default="scanner_fault")
    parser.add_argument("--offline", action="store_true", help="use the scripted fake model")
    parser.add_argument(
        "--with-haiku",
        action="store_true",
        help="fail over to Haiku if Foundry is unavailable (spends on ANTHROPIC_API_KEY)",
    )
    args = parser.parse_args()

    settings = Settings()
    clock, ids = SystemClock(), UlidIdGenerator()
    route = "fake" if args.offline else "foundry_agent_service"
    try:
        policy = build_model_policy(DEFAULT_CONFIG_DIR, settings)
        gateway = build_model_gateway(
            route,
            settings=settings,
            policy=policy,
            clock=clock,
            ids=ids,
            fake_answer=offline_answer,
        )
        reasoner = build_llm_reasoner(
            gateway, settings=settings, policy=policy, clock=clock, ids=ids
        )
        from dispute_agent.infra.sqlite.model_call_log import open_model_call_log

        log = open_model_call_log(MODEL_CALL_LOG)
        before = log.spend_usd(route)
        report = run_trial(args.case, reasoner, policy=policy, clock=clock, ids=ids)
        spent = log.spend_usd(route) - before
    except KernelError as failed:
        print(f"try-agent stopped before running the case: {failed}", file=sys.stderr)
        print("Run `make foundry-check` to see which setup step is missing.", file=sys.stderr)
        return 2

    print(
        f"\nCase: {report.fixture}   (model: {'offline fake' if args.offline else 'Foundry agent'})"
    )
    print("=" * 72)
    p = report.proposal
    if p is None and spent > 0:
        print("The agent was asked, but its answer was rejected (see the reason below).")
    elif p is None:
        print("The agent was not asked: the case stopped before the decide station.")
    else:
        who = "RULES (fallback - the model could not be reached)" if report.fell_back else "AGENT"
        print(f"Proposed by:   {who}")
        print(f"Action:        {p.action.value}   (confidence {p.confidence.value})")
        print("Why:")
        print(textwrap.indent(textwrap.fill(p.rationale, 68), "  "))
        for f in p.findings:
            refs = ", ".join(f.evidence_refs) or "-"
            print(f"  - [{f.kind.value}] {f.statement}  (cites: {refs})")
        if p.manipulation_flags:
            print(f"Flagged as manipulation attempts: {', '.join(p.manipulation_flags)}")
    print("-" * 72)
    case = report.case
    if case is None:
        print(f"Refused at intake, before anything was stored or asked: {report.refused}")
        return 0
    print(f"What the system decided: case is now {case.state.value}")
    if case.review_reason is not None:
        reason = case.review_reason
        print(
            f"  Handed to a person because: {reason.code.value}"
            + (f" ({reason.module})" if reason.module else "")
        )
        if reason.detail:
            print(f"  Detail: {reason.detail}")
    if report.email_body:
        print("\nDrafted email, waiting in your approval queue (NOT sent):")
        print(f"  Subject: {report.email_subject}")
        print(textwrap.indent(report.email_body, "  | "))
    print("-" * 72)
    print(f"Model cost for this run: ${spent:.6f}   (log: {MODEL_CALL_LOG})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
