"""The Dispute Desk web app's API (design handoff v2, section 8 item 1): `/api/ws/...`.

Reads are the console's own view models as JSON (`views.py`). Writes go through exactly the
objects the Streamlit console uses - `ReviewConsole` for approvals (bound to the stored
payload's hash, single use) and `CaseDesk` for a person's decisions - so every check the
engine makes still applies, and the runner acts on a decision on its next tick.

**Who may act.** Every write names a reviewer, who must be on the policy's approver list
(policy.yaml, `approvals.approver_ids`); the engine ignores anyone else. When a site password is
configured (CONSOLE_PASSWORD), every call must carry it in `X-Console-Password`, compared in
constant time. Without one the API is open, as the hosted Streamlit console is today (the
owner's choice, 2026-11-25); locally it listens on loopback only.

Endpoints are plain `def`: they read SQLite, so FastAPI runs them in its thread pool and one
slow read never stalls the runner or another request.
"""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from dispute_agent.infra.clock import SystemClock
from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.enums import (
    ApprovalDecision,
    ApprovalSubject,
    CaseState,
)
from dispute_agent.kernel.domain.errors import (
    AuthPortError,
    ConfigError,
    DomainValidationError,
    NotFound,
)
from dispute_agent.kernel.domain.ids import ApprovalId, CaseId
from dispute_agent.modules.approvals.streamlit_ui.console import (
    ReviewConsole,
    reviewer_named,
    sign_in,
)
from dispute_agent.packs.weight_dispute.requested_items import EVIDENCE_ITEMS
from dispute_agent.ui import data
from dispute_agent.ui.desk import CaseDesk
from dispute_agent.ui.finance import rows, totals
from dispute_agent.ui.gate import opens
from dispute_agent.ui.journey import journey
from dispute_agent.ui.labels import local
from dispute_agent.ui.photos import photo_for
from dispute_agent.ui.seller import (
    NOTE_CHARS,
    contact_for,
    personas,
    raise_ticket,
    seller_shipments,
    tickets,
)
from dispute_agent.ui.web.views import case_detail, shipment_view, work_list

if TYPE_CHECKING:
    from collections.abc import Callable

    from dispute_agent.kernel.domain.models import Actor


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer: str
    decision: Literal["approve", "reject"]
    note: str = Field(default="", max_length=500)


class CaseDecision(BaseModel):
    """A person's decision on a case with a person (ADR 0046, 0050)."""

    model_config = ConfigDict(extra="forbid")

    reviewer: str
    choice: Literal["verdict", "take_over", "return", "withdraw"]
    note: str = Field(default="", max_length=500)
    #: For a verdict: the action code's menu offered, and its proven reason for a credit.
    action: str | None = None
    reason_code: str | None = None
    items: list[str] = []
    notify_seller: bool = False
    #: For a return: whether each action the agent could not confirm actually happened.
    resolutions: dict[str, Literal["confirmed_done", "confirmed_not_done"]] = {}


class Raise(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seller_id: str
    shipment_ref: str
    note: str = Field(default="", max_length=NOTE_CHARS)


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)


def build_workspace_router(
    post: Callable[[dict[str, str]], tuple[int, dict[str, Any]]],
    explain: Callable[[str, str], Any] | None = None,
    *,
    prefix: str = "/api/ws",
    open_site: bool = False,
) -> APIRouter:
    """`post` raises a dispute the way `POST /events` does; `explain` is the read-only case
    explainer (ADR 0058), or None when this site has none. `prefix` is "" when the router is
    mounted under /api/ws by the hosted entry.

    `open_site` is the hosted console's choice, carried over: no site password, and a listed
    approver decides without the review token, exactly as the Streamlit console has been since
    2026-11-25 (ui/app.py). shortcut: anyone with the link can approve as a listed approver on
    synthetic data; pass open_site=False (and set CONSOLE_PASSWORD) to close it again.
    """

    def gate(x_console_password: str | None = Header(default=None)) -> None:
        expected = None if open_site else Settings().console_password
        if expected is not None and not opens(x_console_password, expected.get_secret_value()):
            raise HTTPException(status_code=401, detail="The site password is needed.")

    def decider(name: str, token: str | None) -> Actor:
        if open_site:
            return _listed(name)
        return _reviewer(name, token)

    router = APIRouter(prefix=prefix, dependencies=[Depends(gate)])

    @router.get("/session")
    def session() -> dict[str, Any]:
        settings = Settings()
        return {
            "approvers": data.approvers(),
            "kill_switch": settings.kill_switch,
            # Behind a site password the password is the credential; otherwise the token.
            "needs_token": not open_site and settings.console_password is None,
            "ask": _ask_unavailable(explain) is None,
            "ask_unavailable": _ask_unavailable(explain),
            "sellers": personas(),
            "evidence_items": list(EVIDENCE_ITEMS),
            "store": data.has_store(),
        }

    @router.get("/work")
    def work() -> dict[str, Any]:
        if not data.has_store():
            return {"views": [], "items": [], "no_store": data.NO_STORE}
        with data.reader() as uow:
            return work_list(uow, datetime.now(UTC))

    @router.get("/cases/{case_id}")
    def case(case_id: str) -> dict[str, Any]:
        with data.reader() as uow:
            try:
                found = uow.cases.get(CaseId(case_id))
            except NotFound as missing:
                raise HTTPException(status_code=404, detail="No such case.") from missing
            return case_detail(uow, found, datetime.now(UTC))

    @router.post("/approvals/{approval_id}")
    def decide_approval(
        approval_id: str, body: Decision, x_review_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        reviewer = decider(body.reviewer, x_review_token)
        if body.decision == "approve" and Settings().kill_switch:
            raise HTTPException(
                status_code=409, detail="The kill switch is on: approvals are paused."
            )
        if body.decision == "reject" and not body.note.strip():
            raise HTTPException(status_code=422, detail="Write your reason for rejecting.")
        with data.writer() as uow:
            try:
                current = uow.approvals.get(ApprovalId(approval_id))
            except NotFound as missing:
                raise HTTPException(status_code=404, detail="No such approval.") from missing
            if current.decision is not ApprovalDecision.PENDING:
                who = current.decided_by.id.removeprefix("human:") if current.decided_by else ""
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Already {current.decision.value.lower()}"
                        + (f" by {who}" if who else "")
                        + ". Your decision was not recorded and nothing was done twice."
                    ),
                )
            console = ReviewConsole(store=uow.approvals, clock=SystemClock(), reviewer=reviewer)
            try:
                if body.decision == "approve":
                    decided = console.approve(ApprovalId(approval_id), body.note)
                else:
                    decided = console.reject(ApprovalId(approval_id), body.note)
            except DomainValidationError as refused:
                raise HTTPException(status_code=409, detail=str(refused)) from refused
        return {"approval_id": approval_id, "decision": decided.decision.value}

    @router.post("/cases/{case_id}/decide")
    def decide_case(
        case_id: str, body: CaseDecision, x_review_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        reviewer = decider(body.reviewer, x_review_token)
        with data.writer() as uow:
            try:
                found = uow.cases.get(CaseId(case_id))
            except NotFound as missing:
                raise HTTPException(status_code=404, detail="No such case.") from missing
            desk = CaseDesk(inbox=uow.inbox, clock=SystemClock(), reviewer=reviewer)
            try:
                _apply(desk, uow, found, body)
            except DomainValidationError as refused:
                raise HTTPException(status_code=422, detail=str(refused)) from refused
        return {"case_id": case_id, "recorded": body.choice}

    @router.get("/sellers/{seller_id}")
    def seller(seller_id: str) -> dict[str, Any]:
        persona = next((p for p in personas() if p["seller_id"] == seller_id), None)
        if persona is None:
            raise HTTPException(status_code=404, detail="No such demo seller.")
        shipments = [s for s in seller_shipments(seller_id) if s.disputed]
        mine: list[dict[str, Any]] = []
        if data.has_store():
            with data.reader() as uow:
                cases = uow.cases.list_in_states(list(CaseState))
                ours = {str(c.id): c for c in cases}
                for t in tickets(cases, seller_id):
                    mine.append(
                        {
                            "case_id": t.case_id,
                            "shipment_ref": t.shipment_ref,
                            "status": t.label,
                            "tone": t.tone.value,
                            "is_open": t.is_open,
                            "opened_at": t.opened_at.isoformat(),
                            "updated_at": t.updated_at.isoformat(),
                            "headline": journey(uow, ours[t.case_id]).headline,
                        }
                    )
        contact = contact_for(persona["contact_tag"], Settings().test_seller_email)
        return {
            "persona": persona,
            "can_raise": contact is not None and data.has_store(),
            "cannot_raise": None
            if contact is not None
            else f"This demo seller has no test address: add the +{persona['contact_tag']} "
            "address to TEST_SELLER_EMAIL.",
            "shipments": [shipment_view(s) for s in shipments],
            "tickets": mine,
        }

    @router.post("/sellers/raise")
    def raise_(body: Raise) -> dict[str, Any]:
        persona = next((p for p in personas() if p["seller_id"] == body.seller_id), None)
        if persona is None:
            raise HTTPException(status_code=404, detail="No such demo seller.")
        shipment = next(
            (s for s in seller_shipments(body.seller_id) if s.ref == body.shipment_ref), None
        )
        if shipment is None:
            raise HTTPException(status_code=404, detail="Not one of this seller's shipments.")
        contact = contact_for(persona["contact_tag"], Settings().test_seller_email)
        now = datetime.now(UTC)
        raised = raise_ticket(post, shipment, contact, now, f"ws-{uuid.uuid4().hex}", body.note)
        return {"ok": raised.ok, "message": raised.message, "case_id": raised.case_id}

    @router.get("/ledger/{seller_id}")
    def ledger(seller_id: str) -> dict[str, Any]:
        refs = [s.ref for s in seller_shipments(seller_id)]
        entries = data.ledger_entries(refs)
        proposed: list[dict[str, Any]] = []
        if data.has_store():
            with data.reader() as uow:
                found = rows(entries, uow)
                for c in uow.cases.list_in_states(list(CaseState)):
                    if c.facts.shipment_ref not in refs:
                        continue
                    for a in uow.approvals.for_case(c.id):
                        if a.request.subject is not ApprovalSubject.CREDIT:
                            continue
                        posted = a.used_by_action_id is not None and any(
                            r.case_id == str(c.id) for r in found if r.kind == "Credit"
                        )
                        if a.decision is ApprovalDecision.PENDING or (
                            a.decision is ApprovalDecision.APPROVED and not posted
                        ):
                            proposed.append(
                                {
                                    "case_id": str(c.id),
                                    "shipment_ref": c.facts.shipment_ref,
                                    "amount": str(a.request.amount.amount)
                                    if a.request.amount
                                    else None,
                                    "stage": "Waiting for approval"
                                    if a.decision is ApprovalDecision.PENDING
                                    else "Approved, not yet read back",
                                }
                            )
        else:
            found = rows(entries, None)
        sums = totals(found)
        return {
            "rows": [
                {
                    "posted_at": r.posted_at.isoformat(),
                    "shipment_ref": r.shipment_ref,
                    "kind": r.kind,
                    "amount": str(r.amount),
                    "reason": r.reason,
                    "transaction_id": r.transaction_id,
                    "case_id": r.case_id,
                    "approved_by": r.approved_by,
                    "proved": r.proved,
                }
                for r in found
            ],
            "totals": {
                "charged": str(sums.charged),
                "credited": str(sums.credited),
                "net": str(sums.net),
            },
            "proposed": proposed,
        }

    @router.get("/evals")
    def evals() -> dict[str, Any]:
        from dispute_agent.api.online_judge import CAP_USD, FILE, JUDGE_LOG, OnlineEvals, spent_usd
        from dispute_agent.modules.evals.decision_quality import (
            load_golden,
            load_runs,
            run_summary,
        )

        records = load_runs()
        summaries = [run_summary(r) for r in records]

        def latest(wanted: Callable[[dict[str, Any]], bool]) -> int | None:
            hits = [i for i, s in enumerate(summaries) if wanted(s)]
            return hits[-1] if hits else None

        ai = latest(lambda s: s["kind"] == "decisions" and s["decider"] != "rules")
        golden = load_golden() if ai is not None else None
        misses = []
        if ai is not None and golden is not None:
            for p in records[ai].get("points", []):
                if p.get("grade") in (None, "acceptable"):
                    continue
                point = golden.point(p["id"])
                misses.append(
                    {
                        "id": p["id"],
                        "shipment": p.get("shipment"),
                        "situation": point.situation,
                        "chose": p.get("action"),
                        "grade": p.get("grade"),
                        "acceptable": list(point.acceptable),
                    }
                )
        online = OnlineEvals(data.DATA / FILE)
        live = online.rows()
        return {
            "ai": summaries[ai] if ai is not None else None,
            "rules": _at(summaries, latest(lambda s: s["decider"] == "rules")),
            "judge": _at(summaries, latest(lambda s: s["kind"] == "judge")),
            "misses": misses,
            "runs": list(reversed(summaries)),
            "online": {
                "on": online.is_on(),
                "spent_usd": str(spent_usd(data.DATA / JUDGE_LOG)),
                "cap_usd": str(CAP_USD),
                "judged": sum(1 for r in live if r["status"] == "judged"),
                "flagged": sum(
                    1 for r in live if r["status"] == "judged" and r["verdict"] == "fail"
                ),
                "waiting": sum(1 for r in live if r["status"] == "pending"),
                "rows": len(live),
            },
        }

    @router.get("/team")
    def team() -> dict[str, Any]:
        """Flow counts and where work is stuck, from the case store (design 1.2, Team page).
        Only what the store records: there is no assignment or per-person queue yet."""
        if not data.has_store():
            return {"flow": [], "stuck": [], "approvers": []}
        now = datetime.now(UTC)
        hub_hours = data.policy().timers.ops_timeout_hours
        with data.reader() as uow:
            listed = work_list(uow, now)
            waiting_long = hub_overdue = 0
            decided: dict[str, int] = {}
            for c in uow.cases.list_in_states(list(CaseState)):
                if (
                    c.state is CaseState.AWAITING_OPS
                    and (now - c.state_entered_at).total_seconds() > hub_hours * 3600
                ):
                    hub_overdue += 1
                for a in uow.approvals.for_case(c.id):
                    if (
                        a.decision is ApprovalDecision.PENDING
                        and (now - a.request.requested_at).total_seconds() > 24 * 3600
                    ):
                        waiting_long += 1
                    if a.decided_by is not None and a.decided_at is not None:
                        who = a.decided_by.id.removeprefix("human:")
                        if local(a.decided_at).date() == local(now).date():  # IST day
                            decided[who] = decided.get(who, 0) + 1
        return {
            "flow": listed["views"],
            "stuck": [
                {"label": "Approvals waiting more than 24 h", "count": waiting_long},
                {"label": f"Hub replies overdue (over {hub_hours} h)", "count": hub_overdue},
            ],
            "approvers": [
                {"name": name, "decided_today": decided.get(name, 0)} for name in data.approvers()
            ],
        }

    @router.get("/photos/{shipment_ref}")
    def photo(shipment_ref: str) -> FileResponse:
        found = photo_for(shipment_ref)
        if found is None:
            raise HTTPException(status_code=404, detail="No photo for this shipment.")
        return FileResponse(found, media_type="image/jpeg")

    @router.post("/cases/{case_id}/ask")
    def ask(case_id: str, body: Question) -> dict[str, Any]:
        why = _ask_unavailable(explain)
        if why is not None or explain is None:
            raise HTTPException(status_code=503, detail=why or "The assistant is off.")
        from dispute_agent.modules.reasoning.explainer.agent import ExplainerBudgetError
        from dispute_agent.ui.ask import prompt, sources

        with data.reader() as uow:
            try:
                ref = uow.cases.get(CaseId(case_id)).facts.shipment_ref
            except NotFound as missing:
                raise HTTPException(status_code=404, detail="No such case.") from missing
        try:
            answer = explain(case_id, prompt(case_id, ref, [], body.question))
        except ExplainerBudgetError as capped:
            raise HTTPException(
                status_code=503, detail="The assistant has reached its spending limit."
            ) from capped
        except ConfigError as refused:
            raise HTTPException(
                status_code=503, detail=f"The assistant cannot start: {refused}"
            ) from refused
        except Exception as failed:  # a network or SDK failure must not look like an answer
            raise HTTPException(
                status_code=502,
                detail=f"The assistant could not answer just now ({type(failed).__name__}).",
            ) from failed
        return {"answer": answer.text, "sources": sources(answer.tools_used)}

    return router


def _at(summaries: list[dict[str, Any]], index: int | None) -> dict[str, Any] | None:
    return summaries[index] if index is not None else None


def _listed(name: str) -> Actor:
    """A listed approver on a site whose gate already decided who may be here (ADR 0047)."""
    if name not in data.approvers():
        raise HTTPException(status_code=403, detail="Only a listed approver can decide.")
    try:
        return reviewer_named(name)
    except DomainValidationError as refused:
        raise HTTPException(status_code=403, detail=str(refused)) from refused


def _reviewer(name: str, token: str | None) -> Actor:
    """A listed approver, or a refusal: the engine ignores anyone else (T09, R10, R13).

    Behind a site password (hosted), the password stands in for the review token (ADR 0047).
    Without one, the review console token is required, exactly as on the Streamlit Review
    screen: an API that approves credits never opens to whoever can reach the port.
    """
    if name not in data.approvers():
        raise HTTPException(status_code=403, detail="Only a listed approver can decide.")
    settings = Settings()
    try:
        if settings.console_password is not None:  # the gate already checked the password
            return reviewer_named(name)
        expected = settings.review_console_token
        return sign_in(token, expected.get_secret_value() if expected else None, name)
    except AuthPortError as wrong:
        raise HTTPException(status_code=401, detail="The review token is not correct.") from wrong
    except (ConfigError, DomainValidationError) as refused:
        raise HTTPException(status_code=403, detail=str(refused)) from refused


def _apply(desk: CaseDesk, uow: Any, case: Any, body: CaseDecision) -> None:
    from dispute_agent.ui.case_sheet import build as sheet_of
    from dispute_agent.ui.review import unknown_actions

    if body.choice == "take_over":
        desk.take_over(case, notify_seller=body.notify_seller, note=body.note)
    elif body.choice == "return":
        unknown = [u.action_id for u in unknown_actions(uow, case.id)]
        desk.return_to_agent(
            case,
            body.note,
            credit_verified=uow.actions.has_verified_credit(case.id),
            unknown=unknown,
            resolutions={
                a: body.resolutions[str(a)] for a in unknown if str(a) in body.resolutions
            },
        )
    elif body.choice == "withdraw":
        desk.confirm_withdrawal(case, body.note)
    else:
        offered = sheet_of(uow, case).verdicts
        verdict = next(
            (
                v
                for v in offered
                if v.action == body.action and (v.reason_code or None) == (body.reason_code or None)
            ),
            None,
        )
        if verdict is None:  # only what code's menu offers now; the engine checks it again
            raise DomainValidationError("That decision is not offered on this case any more.")
        asking = verdict.action == "request_seller_evidence"
        desk.decide(
            case,
            verdict.action,
            body.note,
            reason_code=verdict.reason_code,
            requested_items=tuple(body.items) if asking else (),
        )


def _ask_unavailable(explain: Callable[[str, str], Any] | None) -> str | None:
    if explain is None:
        return "This site was started without the case assistant."
    if importlib.util.find_spec("claude_agent_sdk") is None:
        return "Its library is not installed on this site."
    settings = Settings()
    if settings.anthropic_api_key is None or not settings.anthropic_model_haiku:
        return "No Anthropic key or Haiku model is configured."
    return None


__all__ = ["build_workspace_router"]
