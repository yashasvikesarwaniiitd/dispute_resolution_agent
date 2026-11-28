"""Investigation evaluation runs in Langfuse (ADR 0074).

Each execution of a run record (one case, one architecture) becomes one Langfuse trace:
- the root observation is the execution: the case under test in, the final decision out;
- under it, in order, every model call (a generation, with tokens, cost and latency), every
  tool call (arguments, result, whether it answered, whether the decision cited it), every
  code check of a decision (accepted, or rejected with its exact reason) and every answer that
  failed the output schema;
- scores: the grader's outcome and each measure, so failures can be filtered;
- one dataset item per case and one experiment per architecture, so Langfuse compares a case
  across architectures side by side; and the case id as the session, so the three runs of a
  case also group together;
- an annotation queue of the failures, with a failure-category score to assign by hand.

What a run did not record is said so ("unavailable: ...") rather than left blank or guessed.
Synthetic evaluation data only: production tracing (ADR 0057) still sends no content.

Idempotent: every id and timestamp is derived from the record, scores and dataset items are
upserted by id, queue items and score configs are reused when present, and a local manifest
keeps a trace from ever being sent twice. On Langfuse Cloud (2026-11-27) a span re-sent with
the same id and start time showed twice for about 30 minutes, then merged into the last version;
so a trace whose content changed after its import is reported, not re-sent. `delete` replaces
one deliberately: it deletes the trace and the next import sends it under a new id.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

DATASET = "investigation-eval-cases-v1"
QUEUE = "Investigation evaluation: failures to review"
ENVIRONMENT = "evaluation"
TOOLS = frozenset(
    {
        "search_past_shipments",
        "get_scanner_statistics",
        "search_past_disputes",
        "analyze_parcel_image",
        "lookup_policy",
        # the v3 agent's own read-only steps
        "open_evidence",
        "policy_lookup",
        "check_draft",
    }
)
#: The failure categories a reviewer assigns (manual annotation).
FAILURE_CATEGORIES = (
    "wrong_contact_choice",
    "missed_hub_or_scanner_drift",
    "unnecessary_investigation",
    "citation_error",
    "schema_or_format_failure",
    "guardrail_rejection",
    "pattern_claim_rejection",
    "policy_misread",
    "grader_disagreement",
    "other",
)


#: Plain words for what a reviewer reads in Langfuse (names, inputs and outputs). Tags, score
#: names and metadata keys stay as they are: they are what filters and saved views use.
ARM_LABELS = {
    "agent": "current agent (v3)",
    "enriched": "investigator, look-ups done first",
    "investigator": "investigator, picks its own look-ups",
    "plain": "investigator, no look-ups",
}
ACTION_LABELS = {
    "propose_credit": "Give the seller a credit",
    "uphold_charge": "Keep the charge",
    "request_seller_evidence": "Ask the seller for evidence",
    "request_remeasurement": "Ask the hub to re-measure the parcel",
    "escalate_to_human": "Send the case to a person",
    "wait": "Wait",
}
GRADE_LABELS = {
    "acceptable": "Correct",
    "safe_fallback": "Safe but not ideal",
    "not_acceptable": "Wrong",
}
TOOL_LABELS = {
    "search_past_shipments": "look up past shipments of this product",
    "get_scanner_statistics": "check how this scanner has been measuring",
    "search_past_disputes": "look up past disputes",
    "analyze_parcel_image": "check the parcel photo",
    "lookup_policy": "look up the policy",
    "policy_lookup": "look up the policy",
    "open_evidence": "open a piece of evidence",
    "check_draft": "check its draft decision",
}
NOT_SHOWN = "Test description (for reviewers; the agent never sees this)"
REBUILT = (
    "Rebuilt after the run from the same synthetic data and code. The instructions match the "
    "fingerprint the run recorded for this call."
)


def unavailable(what: str) -> str:
    return f"Not saved by this run: {what}"


def _plain(code: Any, labels: Mapping[str, str]) -> str:
    return labels.get(str(code)) or str(code).replace("_", " ")


# -- the prompt, sorted into categories a reviewer reads (the exact text stays in step 1) ---------

_BLOCK = re.compile(r"^<([a-z_]+)([^>]*)>\n(.*?)\n?</\1>", re.S | re.M)  # one-liners: limits
_ONE_LINE = re.compile(r"^<([a-z_]+)>([^<\n]*)</\1>$", re.M)
_NOTE = re.compile(r"^- (\S+?): (\w+)\((.*?)\) -> (.*)$")
_YES_NO = {"true": "yes", "false": "no"}
_FIELDS = {
    "case_ref": "Case",
    "shipment_ref": "Shipment",
    "claim_ref": "Claim",
    "seller_ref": "Seller",
    "raised_at": "Raised on",
    "reply_in_round": "Seller replied in this round",
    "clarifications_sent": "Follow-up questions sent to the seller",
    "remeasurement_outstanding": "Waiting for the hub to re-measure",
    "steps_left": "Steps left",
    "paid_checks_left": "Paid checks left",
    "must_decide": "Must decide now",
    "dead_grams": "weight (g)",
    "length_cm": "length (cm)",
    "width_cm": "width (cm)",
    "height_cm": "height (cm)",
}
_SECTIONS = {  # tag -> category, in the order a reviewer reads them
    "case_facts": "The dispute",
    "untrusted_evidence": "What the seller wrote (not verified)",
    "untrusted_reply_reading": "The seller's reply, as read by code (not verified)",
    "deterministic_results": "The bill, as checked by code",
    "evidence_items": "Evidence on file",
    "contact_status": "Contact so far",
    "screen_results": "Automatic screening",
    "related_cases": "Related cases",
    "investigation_notes": "Investigation so far",
    "allowed_actions": "What the agent may decide",
    "tools_available": "Look-ups the agent may use",
    "limits": "Limits",
    "asked": "What it was asked to do",
    "citable_ids": "Ids it may point to (technical)",
}


def readable_prompt(text: str) -> Any:
    """The case prompt sorted into numbered categories in plain words. A prompt this does not
    recognise comes back as it is: nothing is dropped, and the exact text is in step 1."""
    found: dict[str, list[Any]] = {}
    for tag, attrs, body in _BLOCK.findall(text):
        found.setdefault(tag, []).append(_section(tag, attrs, body.strip()))
    limits = {
        _plain(tag, _FIELDS): _YES_NO.get(value.strip().lower(), value.strip())
        for tag, value in _ONE_LINE.findall(text)
    }
    if limits:
        found["limits"] = [limits]
    rest = _ONE_LINE.sub("", _BLOCK.sub("", text))
    asked = [
        line.strip()
        for line in rest.splitlines()
        if line.strip() and not line.strip().startswith("[[")
    ]
    if asked:
        found["asked"] = [" ".join(asked)]
    if set(found) <= {"asked"}:  # no sections recognised
        return text
    out: dict[str, Any] = {}
    order = [t for t in _SECTIONS if t in found] + [t for t in found if t not in _SECTIONS]
    for n, tag in enumerate(order, 1):
        parts = found[tag]
        title = _SECTIONS.get(tag) or _plain(tag, {}).capitalize()
        out[f"{n}. {title}"] = parts[0] if len(parts) == 1 else parts
    return out


def _section(tag: str, attrs: str, body: str) -> Any:
    if not body or body == "none" or body == "none yet":
        return "none"
    if tag in ("case_facts", "contact_status"):
        pairs = (line.split(":", 1) for line in body.splitlines() if ":" in line)
        return {
            _plain(k.strip(), _FIELDS): _YES_NO.get(v.strip().lower(), v.strip()) for k, v in pairs
        }
    if tag == "untrusted_evidence":
        source = re.search(r'source="([^"]+)"', attrs)
        return {"From": _plain(source.group(1) if source else "unknown", {}), "Text": body}
    if tag == "deterministic_results":
        return _bill(body)
    if tag == "evidence_items":
        return _evidence(body)
    if tag == "investigation_notes":
        return [_note(line) for line in body.splitlines() if line.strip()]
    if tag == "allowed_actions":
        return [_plain(line.lstrip("- ").strip(), ACTION_LABELS) for line in body.splitlines()]
    if tag == "tools_available":
        names = [line.lstrip("- ").split(" ", 1)[0] for line in body.splitlines()]
        return [_plain(n, TOOL_LABELS).capitalize() for n in names if n]
    return _json_or_text(body)


def _bill(body: str) -> Any:
    data = _json_or_text(body)
    if not isinstance(data, dict):
        return data
    facts = {f.get("label"): f.get("value") for f in data.get("facts") or ()}

    def size(who: str, chargeable: str) -> str:
        parts = [
            f"{side} {facts.pop(f'{who} {side}')} cm"
            for side in ("length", "width", "height")
            if f"{who} {side}" in facts
        ]
        weight = facts.pop(f"{who} weight", None)
        billed = facts.pop(chargeable, None)
        return (
            ", ".join(
                parts
                + ([f"weight {weight}"] if weight else [])
                + ([f"chargeable weight {billed}"] if billed else [])
            )
            or "not given"
        )

    # Every value is one the agent was shown; none is computed here. Labels the summary
    # repeats leave the worked-out list, the rest stay in it.
    for repeated in ("amount charged", "recalculated charge", "overcharge"):
        facts.pop(repeated, None)
    out: dict[str, Any] = {
        "Amount charged": data.get("charged"),
        "Charge on the seller's declared size": facts.pop("charge on the declared size", None)
        or "not given",
        "Code's recalculation on the hub's measurement": data.get("recalculated"),
    }
    if data.get("recalculated_remeasured"):
        out["Code's recalculation on the re-measured size"] = data["recalculated_remeasured"]
    out |= {
        "Overcharge (charged minus code's recalculation)": data.get("discrepancy"),
        "Billing errors code found": [_plain(e, {}) for e in data.get("definite_errors") or ()]
        or "none",
        "Eligible for review": data.get("eligible"),
        "Seller declared": size("declared", "chargeable weight on the declared size"),
        "Hub measured": size("measured", "chargeable weight in grams"),
    }
    if data.get("reason_codes"):
        out["Reasons code gave"] = [_plain(c, {}) for c in data["reason_codes"]]
    out["How the charge is worked out"] = [f"{k}: {v}" for k, v in facts.items()]
    return out


def _evidence(body: str) -> dict[str, list[str]]:
    found: list[str] = []
    missing: list[str] = []
    for line in body.splitlines():
        cells = {
            part.split(":", 1)[0].strip(): part.split(":", 1)[1].strip()
            for part in line.lstrip("- ").split(" | ")
            if ":" in part
        }
        what = re.sub(r"\s*\([a-z_]+\.[a-z_]+\)$", "", cells.get("what", line)).strip()
        if cells.get("status") == "MISSING":
            missing.append(what)
            continue
        value = _json_or_text(cells.get("value", ""))
        if isinstance(value, dict):
            value = "; ".join(f"{_plain(k, _FIELDS)}: {_value(v)}" for k, v in value.items())
        found.append(f"{what}: {value}")
    return {"Found": found, "Missing": missing}


def _value(v: Any) -> str:
    if isinstance(v, str):
        return v.replace("_", " ") if "_" in v and " " not in v else v
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, dict):
        return "(" + ", ".join(f"{_plain(k, _FIELDS)}: {_value(x)}" for k, x in v.items()) + ")"
    if isinstance(v, list):
        return ", ".join(_value(x) for x in v) or "none"
    return "none" if v is None else str(v)


def _note(line: str) -> Any:
    hit = _NOTE.match(line.strip())
    if not hit:
        return line.strip()
    ref, tool, args, answer = hit.groups()
    return {
        "Look-up": f"{_plain(tool, TOOL_LABELS).capitalize()} [{ref}]",
        "Asked for": _json_or_text(args) or "nothing specific",
        "Answer": _json_or_text(answer),
    }


@dataclass(frozen=True)
class Observation:
    key: str
    name: str
    kind: str  # Langfuse observation type: agent, generation, tool, guardrail, event
    start_ns: int
    end_ns: int
    input: Any = None
    output: Any = None
    level: str = "DEFAULT"
    status: str = ""
    model: str | None = None
    usage: dict[str, int] | None = None
    cost_usd: float | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Execution:
    trace_id: str
    run_id: str
    case: str
    arm: str
    root: Observation
    children: tuple[Observation, ...]
    tags: tuple[str, ...]
    metadata: dict[str, str]
    scores: tuple[dict[str, Any], ...]
    review: bool
    dataset_item: dict[str, Any]

    @property
    def session(self) -> str:
        return f"{self.run_id}:{self.case}"

    def content_hash(self) -> str:
        body = json.dumps(
            [self.root.__dict__, [c.__dict__ for c in self.children], self.tags, self.metadata],
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(body.encode()).hexdigest()


# -- building traces from a run record (pure) ----------------------------------------------------


def build(
    record: Mapping[str, Any],
    cases: Mapping[str, Mapping[str, Any]],
    calls: Sequence[Sequence[Mapping[str, Any]]] | None,
    *,
    run_id: str,
    reissued: Mapping[str, int] | None = None,
) -> list[Execution]:
    """Every execution of `record` as a trace. `cases`: the case file's entries by id.
    `calls`: each execution's model-call log rows, in run order (None when the logs are gone)."""
    results = normalise(record)
    # Run 3's recorder kept 400 characters of a tool result; the matched runner keeps all it logs.
    limit = int(record.get("tool_result_chars") or 400)
    ran_at = datetime.fromisoformat(str(record["ran_at"]))
    start = ran_at - timedelta(milliseconds=sum(int(r.get("wall_ms") or 0) for r in results))
    built = []
    for n, result in enumerate(results):
        rows = list(calls[n]) if calls is not None and n < len(calls) else None
        built.append(
            _execution(
                result,
                cases.get(str(result["case"]), {}),
                rows,
                run_id,
                start,
                limit,
                (reissued or {}).get(f"{result['case']}/{result['arm']}", 0),
            )
        )
        start += timedelta(milliseconds=int(result.get("wall_ms") or 0))
    return built


def normalise(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Both run-record shapes as one: the matched runner's `results`, or the single-arm runner's
    `cases` rows (which record only the step names of a trajectory)."""
    if "results" in record:
        return [dict(r) for r in record["results"]]
    arm = str(record.get("label", "unknown"))
    return [
        {
            **row,
            "case": row["id"],
            "arm": arm,
            "trace": [{"step": s} for s in row.get("trajectory") or ()],
            "provider_calls": [],
        }
        for row in record.get("cases", [])
    ]


def _ids(run_id: str, case: str, arm: str, issue: int = 0) -> tuple[str, Callable[[str], str]]:
    """The trace id, and span ids derived from it. `issue` > 0: a re-issued trace (its earlier
    issue was deleted in Langfuse), so it never shares an id with a deletion in progress."""
    seed = f"{run_id}|{case}|{arm}" + (f"#{issue}" if issue else "")
    trace = hashlib.sha256(seed.encode()).hexdigest()[:32]

    def span(key: str) -> str:
        return hashlib.sha256(f"{trace}|{key}".encode()).hexdigest()[:16]

    return trace, span


def _ns(when: datetime) -> int:
    return int(when.timestamp() * 1_000_000_000)


def _execution(
    r: Mapping[str, Any],
    case: Mapping[str, Any],
    rows: list[Mapping[str, Any]] | None,
    run_id: str,
    start: datetime,
    limit: int = 400,
    issue: int = 0,
) -> Execution:
    case_id, arm = str(r["case"]), str(r["arm"])
    trace_id, _ = _ids(run_id, case_id, arm, issue)
    begin = _ns(start)
    end = begin + int(r.get("wall_ms") or 0) * 1_000_000
    steps = list(r.get("trace") or [])
    sent = list(r.get("provider_calls") or [])
    cited = {str(x) for x in r.get("cited") or ()}
    # The enriched arm's look-ups run in code before the model is asked; every other step
    # follows a model turn, one to one with the calls in the log (checked: run 3, 30 of 30).
    code_steps = 0
    if arm in ("enriched", "plain"):
        while code_steps < len(steps) and steps[code_steps].get("step") in TOOLS:
            code_steps += 1
    model_steps = steps[code_steps:]

    children: list[Observation] = []
    cursor = begin
    for i, step in enumerate(steps[:code_steps]):
        children.append(_tool(step, f"code-{i}", cursor, cited, chosen_by="code", limit=limit))
        cursor = children[-1].end_ns
    rejected = format_failures = uncited = 0
    final = _final_decision(r)
    first = r.get("first_prompt")  # rebuilt after the run (scripts/rebuild_eval_prompts.py)
    for i, step in enumerate(model_steps):
        row = rows[i] if rows is not None and i < len(rows) else None
        call = dict(sent[i]) if i < len(sent) else {}
        if i == 0 and first and not call.get("input"):
            call["input"] = {**first, "how": REBUILT}
        latency = int((row or {}).get("latency_ms") or call.get("latency_ms") or 0)
        is_last = i == len(model_steps) - 1
        children.append(
            _generation(i, step, row, call, cursor, latency, final if is_last else None)
        )
        cursor += latency * 1_000_000
        name = str(step.get("step"))
        if name in TOOLS:
            tool = _tool(step, f"step-{i}", cursor, cited, chosen_by="model", limit=limit)
            uncited += (
                tool.metadata.get("cited") == "false" and tool.metadata.get("answered") == "true"
            )
            children.append(tool)
            cursor = tool.end_ns - 1_000_000  # the shared step advance below adds it back
        elif name == "decide":
            result = str(step.get("result") or "")
            rejected += result.startswith("rejected")
            children.append(_check(step, f"check-{i}", cursor, result))
        elif name in ("invalid_output", "invalid", "model_error"):
            format_failures += name != "model_error"
            children.append(
                Observation(
                    key=f"fail-{i}",
                    name="the call to the AI model failed"
                    if name == "model_error"
                    else "the agent's reply was in the wrong format",
                    kind="event",
                    start_ns=cursor,
                    end_ns=cursor + 1_000_000,
                    output=str(step.get("result") or "") or unavailable("the failure reason"),
                    level="ERROR",
                    status=str(step.get("result") or name)[:500],
                )
            )
        cursor += 1_000_000
    if not model_steps and rows:
        for i, row in enumerate(rows):  # a record that kept calls but not steps
            children.append(
                _generation(i, {}, row, {}, cursor, int(row.get("latency_ms") or 0), None)
            )
            cursor += int(row.get("latency_ms") or 0) * 1_000_000
    children = [replace(c, name=f"Step {k}: {c.name}") for k, c in enumerate(children, 1)]

    grade = str(r.get("grade"))
    hub, seller = bool(r.get("unnecessary_hub_request")), bool(r.get("unnecessary_seller_request"))
    correct = grade == "acceptable"
    tags = [f"case:{case_id}", f"arm:{arm}", f"run:{run_id}", f"outcome:{grade}"]
    tags += [f"category:{r.get('category') or case.get('category', '')}"]
    tags += ["correct" if correct else "incorrect"]
    tags += ["unnecessary-hub-request"] * hub + ["unnecessary-seller-request"] * seller
    tags += ["decision-rejected"] * (rejected > 0) + ["format-failure"] * (format_failures > 0)
    tags += ["uncited-tool-calls"] * (uncited > 0)
    missing = _unavailable(arm, steps, sent, rows, limit, rebuilt=bool(first))
    metadata = {
        "case": case_id,
        "arm": arm,
        "run": run_id,
        "fixture": str(case.get("fixture")),
        "round": str(case.get("round")),
        "prompts": ", ".join(r.get("prompts") or ()) or "unrecorded",
        "models": ", ".join(r.get("models") or ()) or "unrecorded",
        "structured_output_modes": ", ".join(
            sorted({str(c.get("mode")) for c in sent if c.get("mode")})
        )
        or "unrecorded",
        "timing": "reconstructed from recorded durations (the replay clock is simulated)",
        "unavailable": "; ".join(missing),
    }
    shown = next((c.input for c in children if c.kind == "generation"), None)
    about = {
        "Case": case_id,
        "Shipment": case.get("shipment"),
        "Kind of case": _plain(r.get("category") or case.get("category"), {}),
        NOT_SHOWN: case.get("situation"),
    }
    root = Observation(
        key="root",
        name=f"Case {case_id} · {_plain(arm, ARM_LABELS)}",
        kind="agent",
        start_ns=begin,
        end_ns=max(end, cursor),
        input={
            "Case": case_id,
            "What the agent was shown": readable_prompt(
                str(shown["Exact text the agent was shown"])
            )
            if isinstance(shown, dict)
            else unavailable("the case as the agent saw it"),
            **{k: v for k, v in about.items() if k != "Case"},
        },
        output=final,
        level="DEFAULT" if correct else "WARNING",
        status="" if correct else f"Graded: {_plain(grade, GRADE_LABELS)}",
        metadata=metadata,
    )
    scores = _scores(trace_id, root.start_ns, r, rejected, format_failures, uncited)
    item = {
        "id": f"{DATASET}-{case_id}",
        "datasetName": DATASET,
        "input": about,
        "expectedOutput": {
            "acceptable": case.get("acceptable") or r.get("acceptable_actions"),
            "safe_fallback": case.get("safe_fallback"),
            "prohibited": case.get("prohibited") or r.get("prohibited_actions"),
            "contacts": case.get("contacts"),
            "must_consult": case.get("must_consult"),
        },
        "metadata": {"case": case_id},
    }
    return Execution(
        trace_id=trace_id,
        run_id=run_id,
        case=case_id,
        arm=arm,
        root=root,
        children=tuple(children),
        tags=tuple(tags),
        metadata=metadata,
        scores=tuple(scores),
        review=not correct or hub or seller or rejected > 0 or format_failures > 0,
        dataset_item=item,
    )


def _final_decision(r: Mapping[str, Any]) -> dict[str, Any]:
    graded = _plain(r.get("grade"), GRADE_LABELS)
    if not r.get("action"):
        return {"Decision": "None: the case went to a person", "Graded": graded}
    findings = r.get("findings")
    if isinstance(findings, list):
        findings = [
            f"{_plain(f.get('kind'), {}).capitalize()}: {f.get('statement')}"
            + (f" (evidence: {', '.join(f['refs'])})" if f.get("refs") else "")
            if isinstance(f, dict)
            else str(f)
            for f in findings
        ]
    return {
        "Decision": _plain(r.get("action"), ACTION_LABELS),
        "Why": r.get("rationale") or unavailable("the reasoning"),
        "Type of reason": _plain(r.get("reason_code") or "none given", {}),
        "What it found": findings or unavailable("the findings"),
        "Evidence it pointed to": r.get("cited") or "none",
        "Warning signs it flagged": r.get("flags") or "none",
        "Graded": graded,
    }


def _generation(
    i: int,
    step: Mapping[str, Any],
    row: Mapping[str, Any] | None,
    call: Mapping[str, Any],
    start: int,
    latency_ms: int,
    final: dict[str, Any] | None,
) -> Observation:
    name = str(step.get("step") or "")
    if name == "decide":
        doing = "makes its decision"
    elif name in TOOLS:
        doing = f"asks to {_plain(name, TOOL_LABELS)}"
    elif name in ("invalid_output", "invalid"):
        doing = "replies, in the wrong format"
    elif name == "model_error":
        doing = "is asked, but the call fails"
    else:
        doing = "thinks" + (f" ({_plain(name, {})})" if name else "")
    output: Any = call.get("output")
    if output is not None:
        output = {"The agent's reply": _json_or_text(str(output))}
    elif final is not None and name == "decide":
        output = {
            "Note": "The decision as stored. The agent's exact reply was not saved by this run.",
            **final,
        }
    else:
        output = {"What the agent did": doing, "Its exact reply": unavailable("the reply text")}
    sent = call.get("input")
    if isinstance(sent, dict):
        prompt: Any = {
            "Exact text the agent was shown": sent.get("user"),
            "Its instructions (the same for every case)": sent.get("system"),
        }
        if sent.get("how"):
            prompt["How we know"] = sent["how"]
    else:
        prompt = sent or unavailable(
            "the exact text. It was the first prompt plus the answers from the steps before this one."
            if i
            else "the case as the agent saw it"
        )
    failed = row is not None and str(row.get("status")) != "ok"
    return Observation(
        key=f"gen-{i}",
        name=f"the agent {doing}",
        kind="generation",
        start_ns=start,
        end_ns=start + latency_ms * 1_000_000,
        input=prompt,
        output=output,
        level="ERROR" if failed else "DEFAULT",
        status=str((row or {}).get("error_code") or "") if failed else "",
        model=str((row or {}).get("model_id") or call.get("model") or "") or None,
        usage=(
            {"input": int(row["input_tokens"]), "output": int(row["output_tokens"])}
            if row is not None
            else None
        ),
        cost_usd=(int(row["cost_usd_micros"]) / 1_000_000) if row is not None else None,
        metadata={
            "prompt": f"{(row or {}).get('prompt_name', '')}/{(row or {}).get('prompt_version', '')}"
            if row is not None
            else "unrecorded",
            "structured_output": str(call.get("mode") or "unrecorded"),
            "stop_reason": str(call.get("stop") or "unrecorded"),
            "call_status": str((row or {}).get("status") or "unrecorded"),
            "tokens_and_cost": "from the spending guard's call log"
            if row
            else unavailable("usage"),
        },
    )


def _tool(
    step: Mapping[str, Any],
    key: str,
    start: int,
    cited: set[str],
    *,
    chosen_by: str,
    limit: int = 400,
) -> Observation:
    name = str(step.get("step"))
    ref = str(step.get("ref") or "")
    answered = step.get("tool_ok")
    has_detail = "arguments" in step or "result" in step
    is_cited = bool(ref) and (ref in cited or f"tool:{name}" in cited)
    result = step.get("result")
    output: Any = {"Answer": _json_or_text(str(result))} if result else unavailable("the answer")
    if result and len(str(result)) >= limit and isinstance(output["Answer"], str):
        output["Note"] = f"This run saved only the first {limit} characters of the answer."
    if answered and result == "":
        output = "Same question as an earlier step, so no new answer (it points to that step)."
    elif answered is False:
        output = {"Answer": "No answer", "Detail": _json_or_text(str(result or ""))}
    asked = _json_or_text(str(step.get("arguments") or "{}"))
    what = _plain(name, TOOL_LABELS)
    return Observation(
        key=key,
        name=what[0].upper()
        + what[1:]
        + (" (done automatically, before the agent starts)" if chosen_by == "code" else "")
        + (f" [{ref}]" if ref else ""),
        kind="tool",
        start_ns=start,
        end_ns=start + max(int(step.get("tool_ms") or 0), 1) * 1_000_000,
        input=({"Asked for": asked} if asked else "Nothing specific: the standard search")
        if has_detail
        else unavailable("what it asked for (the current agent's log keeps only step names)"),
        output=output,
        level="ERROR" if answered is False else "DEFAULT",
        status="No answer" if answered is False else "",
        metadata={
            "ref": ref or "none",
            "chosen_by": chosen_by,
            "answered": str(answered).lower() if answered is not None else "unrecorded",
            "cited": str(is_cited).lower() if ref else "unrecorded",
            "reused": str(step.get("reused", "unrecorded")).lower(),
            "result_truncated": f"at {limit} characters by the run recorder" if result else "n/a",
            "duration": f"{step['tool_ms']} ms"
            if "tool_ms" in step
            else "unrecorded (shown as 1 ms)",
        },
    )


def _check(step: Mapping[str, Any], key: str, start: int, result: str) -> Observation:
    rejected = result.startswith("rejected")
    if rejected:
        verdict = "Rejected: " + result.removeprefix("rejected").lstrip(": ")
    else:
        verdict = "Passed" if result else unavailable("the result")
    return Observation(
        key=key,
        name="Rule check of the decision: "
        + ("rejected" if rejected else "passed" if result else "result not saved"),
        kind="guardrail",
        start_ns=start,
        end_ns=start + 1_000_000,
        input="The decision the agent just made, checked against the rules in code",
        output=verdict,
        level="WARNING" if rejected else "DEFAULT",
        status=result[:500] if rejected else "",
        metadata={"verdict": "rejected" if rejected else ("accepted" if result else "unrecorded")},
    )


def _json_or_text(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text


def _unavailable(
    arm: str,
    steps: list[Any],
    sent: list[Any],
    rows: list[Any] | None,
    limit: int = 400,
    *,
    rebuilt: bool = False,
) -> list[str]:
    missing = []
    if not any(c.get("input") for c in sent):
        missing.append(
            "prompts after the first (the first was rebuilt)"
            if rebuilt
            else "prompts sent to the model"
        )
    if not any(c.get("output") for c in sent):
        missing.append("raw model replies (the final decision is recorded)")
    missing.append("the content of rejected decision drafts (their rejection reasons are recorded)")
    if arm == "agent":
        missing.append("the v3 loop's tool arguments and results (step names only)")
    if any("result" in s for s in steps):
        missing.append(f"tool results beyond {limit} characters")
        if not any("tool_ms" in s for s in steps):
            missing.append("tool durations")
    if rows is None:
        missing.append("per-call tokens, cost and latency (call logs not found)")
    return missing


def _scores(
    trace_id: str,
    start_ns: int,
    r: Mapping[str, Any],
    rejected: int,
    format_failures: int,
    uncited: int,
) -> list[dict[str, Any]]:
    when = datetime.fromtimestamp(start_ns / 1e9, tz=UTC).isoformat()

    def score(name: str, value: Any, kind: str, comment: str = "") -> dict[str, Any]:
        sid = hashlib.sha256(f"{trace_id}|{name}".encode()).hexdigest()[:32]
        body: dict[str, Any] = {
            "id": sid,
            "traceId": trace_id,
            "name": name,
            "value": value,
            "dataType": kind,
            "timestamp": when,
            "environment": ENVIRONMENT,
        }
        if comment:
            body["comment"] = comment
        return body

    out = [
        score("outcome", str(r.get("grade")), "CATEGORICAL"),
        score("correct", int(r.get("grade") == "acceptable"), "BOOLEAN"),
        score("unnecessary_hub_request", int(bool(r.get("unnecessary_hub_request"))), "BOOLEAN"),
        score(
            "unnecessary_seller_request", int(bool(r.get("unnecessary_seller_request"))), "BOOLEAN"
        ),
        score("rejected_decisions", rejected, "NUMERIC"),
        score("format_failures", format_failures, "NUMERIC"),
        score(
            "uncited_model_tool_calls",
            uncited,
            "NUMERIC",
            "tool calls that answered but the decision did not cite",
        ),
        score("tool_calls", int(r.get("tool_calls") or 0), "NUMERIC"),
        score("model_calls", int(r.get("model_calls") or 0), "NUMERIC"),
        score("cost_usd", float(r.get("cost_usd") or 0), "NUMERIC"),
        score("max_call_cost_usd", float(r.get("max_call_cost_usd") or 0), "NUMERIC"),
        score("latency_s", int(r.get("wall_ms") or 0) / 1000, "NUMERIC"),
    ]
    for name in (
        "consulted",
        "grounded",
        "understood",
        "over_investigated",
        "within_budget",
        "wrongly_flagged",
    ):
        if r.get(name) is not None:
            out.append(score(name, int(bool(r[name])), "BOOLEAN"))
    return out


# -- sending ---------------------------------------------------------------------------------------


def spans(execution: Execution, dataset_id: str | None, experiment: Mapping[str, str]) -> list[Any]:
    """The execution as OpenTelemetry spans with Langfuse's attributes, ids from the record."""
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope
    from opentelemetry.trace import SpanContext, TraceFlags

    def span_id(key: str) -> str:
        return hashlib.sha256(f"{execution.trace_id}|{key}".encode()).hexdigest()[:16]

    trace_int = int(execution.trace_id, 16)
    resource = Resource.create({"service.name": "dispute_agent-evaluation"})
    scope = InstrumentationScope("dispute_agent.eval_traces")
    root_ctx = SpanContext(trace_int, int(span_id("root"), 16), False, TraceFlags(1))
    shared: dict[str, Any] = {
        "langfuse.trace.name": execution.root.name,
        "langfuse.trace.tags": list(execution.tags),
        "langfuse.session.id": execution.session,
        "langfuse.environment": ENVIRONMENT,
        **{f"langfuse.trace.metadata.{k}": v for k, v in execution.metadata.items()},
        **experiment,
    }
    if dataset_id:
        shared["langfuse.experiment.dataset.id"] = dataset_id
        shared["langfuse.experiment.item.id"] = execution.dataset_item["id"]
        shared["langfuse.experiment.item.root_observation_id"] = span_id("root")
    out = []
    for obs in (execution.root, *execution.children):
        ctx = (
            root_ctx
            if obs is execution.root
            else SpanContext(trace_int, int(span_id(obs.key), 16), False, TraceFlags(1))
        )
        attrs: dict[str, Any] = {
            **shared,
            "langfuse.observation.type": obs.kind,
            "langfuse.observation.input": _text(obs.input),
            "langfuse.observation.output": _text(obs.output),
            "langfuse.observation.level": obs.level,
            **{f"langfuse.observation.metadata.{k}": v for k, v in obs.metadata.items()},
        }
        if obs.status:
            attrs["langfuse.observation.status_message"] = obs.status
        if obs.model:
            attrs["langfuse.observation.model.name"] = obs.model
        if obs.usage is not None:
            attrs["langfuse.observation.usage_details"] = json.dumps(obs.usage)
        if obs.cost_usd is not None:
            attrs["langfuse.observation.cost_details"] = json.dumps({"total": obs.cost_usd})
        out.append(
            ReadableSpan(
                name=obs.name,
                context=ctx,
                parent=None if obs is execution.root else root_ctx,
                resource=resource,
                attributes=attrs,
                start_time=obs.start_ns,
                end_time=obs.end_ns,
                instrumentation_scope=scope,
            )
        )
    return out


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


@dataclass
class Report:
    traces_sent: int = 0
    traces_skipped: int = 0
    scores_sent: int = 0
    dataset_items: int = 0
    queue_items_added: int = 0
    changed_not_resent: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


class LangfuseEvalExport:
    """Sends executions to Langfuse: spans over OTLP, the rest over the public REST API."""

    def __init__(
        self, host: str, public_key: str, secret_key: str, http: Any = None, otlp: Any = None
    ) -> None:
        from dispute_agent.modules.tracing.otel import langfuse_endpoint

        endpoint, headers = langfuse_endpoint(host, public_key, secret_key)
        if otlp is None:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            otlp = OTLPSpanExporter(endpoint=endpoint, headers=headers, timeout=30)
        if http is None:
            import httpx

            http = httpx.Client(
                base_url=host.rstrip("/") + "/api/public",
                auth=(public_key, secret_key),
                timeout=30,
            )
        self.otlp, self.http = otlp, http

    def send(self, executions: Sequence[Execution], manifest: Path, *, label: str) -> Report:
        report = Report()
        stored = _read(manifest)
        sent: dict[str, str] = {k: v for k, v in stored.items() if not k.startswith("_")}
        dataset_id = self._dataset(executions, report)
        for arm in sorted({e.arm for e in executions}):
            experiment = {
                "langfuse.experiment.id": hashlib.sha256(f"{label}|{arm}".encode()).hexdigest()[
                    :24
                ],
                "langfuse.experiment.name": f"{label} · {arm}",
                "langfuse.experiment.description": f"Investigation evaluation {label}, architecture {arm}",
            }
            for e in (x for x in executions if x.arm == arm):
                digest = e.content_hash()
                if e.trace_id in sent:
                    # Never re-sent: a re-sent span shows twice until Langfuse merges it (about
                    # 30 minutes on Cloud, 2026-11-27). A changed trace is reported instead.
                    report.traces_skipped += 1
                    if sent[e.trace_id] != digest:
                        report.changed_not_resent.append(f"{e.case}/{e.arm}")
                    continue
                result = self.otlp.export(spans(e, dataset_id, experiment))
                if getattr(result, "name", str(result)) != "SUCCESS":
                    report.problems.append(f"{e.case}/{e.arm}: span export {result}")
                    continue
                sent[e.trace_id] = digest
                report.traces_sent += 1
        # What Langfuse accepted, even if a later step fails; re-issue counts kept.
        _write(manifest, {**{k: v for k, v in stored.items() if k.startswith("_")}, **sent})
        self._scores(executions, report)
        self._queue(executions, report)
        return report

    def delete(self, executions: Sequence[Execution], manifest: Path) -> list[str]:
        """Delete these executions' traces in Langfuse (asynchronous there, up to 15 minutes;
        scores and observations go with them) and their review-queue items, and mark them
        re-issued, so the next send gives them new ids. Only on an explicit, owner-approved
        request: a deletion cannot be undone."""
        ids = [e.trace_id for e in executions]
        done = self.http.request("DELETE", "/traces", json={"traceIds": ids})
        if done.status_code >= 400:
            return [f"delete traces: HTTP {done.status_code}"]
        problems: list[str] = []
        queues = self.http.get("/annotation-queues", params={"limit": 100}).json().get("data", [])
        for queue in (q for q in queues if q.get("name") == QUEUE):
            items = (
                self.http.get(f"/annotation-queues/{queue['id']}/items", params={"limit": 100})
                .json()
                .get("data", [])
            )
            for item in items:
                if item.get("objectId") in ids:
                    gone = self.http.request(
                        "DELETE", f"/annotation-queues/{queue['id']}/items/{item['id']}"
                    )
                    if gone.status_code >= 400:
                        problems.append(f"queue item {item['id']}: HTTP {gone.status_code}")
        stored = _read(manifest)
        counts = dict(stored.get("_reissued") or {})
        for e in executions:
            stored.pop(e.trace_id, None)
            counts[f"{e.case}/{e.arm}"] = int(counts.get(f"{e.case}/{e.arm}", 0)) + 1
        stored["_reissued"] = counts
        _write(manifest, stored)
        return problems

    def _dataset(self, executions: Sequence[Execution], report: Report) -> str | None:
        found = self.http.get(f"/v2/datasets/{DATASET}")
        if found.status_code == 404:
            self.http.post(
                "/v2/datasets",
                json={
                    "name": DATASET,
                    "description": "Investigation evaluation cases, version 1",
                },
            )
            found = self.http.get(f"/v2/datasets/{DATASET}")
        if found.status_code >= 400:
            report.problems.append(f"dataset: HTTP {found.status_code}")
            return None
        for item in {e.dataset_item["id"]: e.dataset_item for e in executions}.values():
            made = self.http.post("/dataset-items", json=item)
            if made.status_code >= 400:
                report.problems.append(f"dataset item {item['id']}: HTTP {made.status_code}")
            else:
                report.dataset_items += 1
        return str(found.json().get("id")) or None

    def _scores(self, executions: Sequence[Execution], report: Report) -> None:
        batch = [s for e in executions for s in e.scores]
        for i in range(0, len(batch), 100):
            done = self.http.post("/scores", json=batch[i : i + 100])
            if done.status_code >= 300 and done.status_code != 207:
                report.problems.append(f"scores: HTTP {done.status_code}")
                continue
            body = done.json() if done.content else {}
            rejected = int(body.get("rejected") or 0) if isinstance(body, dict) else 0
            if rejected:
                report.problems.append(f"scores: {rejected} rejected: {body.get('errors')}")
            report.scores_sent += len(batch[i : i + 100]) - rejected

    def _queue(self, executions: Sequence[Execution], report: Report) -> None:
        configs = self._configs(report)
        if not configs:
            return
        queues = self.http.get("/annotation-queues", params={"limit": 100}).json().get("data", [])
        queue = next((q for q in queues if q.get("name") == QUEUE), None)
        if queue is None:
            made = self.http.post(
                "/annotation-queues",
                json={
                    "name": QUEUE,
                    "description": "Incorrect decisions, unnecessary contacts, rejected decisions and format failures from investigation evaluation runs. Assign a failure_category; add notes in failure_notes.",
                    "scoreConfigIds": configs,
                },
            )
            if made.status_code >= 400:
                report.problems.append(f"annotation queue: HTTP {made.status_code}")
                return
            queue = made.json()
        present: set[str] = set()
        for page in range(1, 50):
            items = (
                self.http.get(
                    f"/annotation-queues/{queue['id']}/items", params={"page": page, "limit": 100}
                )
                .json()
                .get("data", [])
            )
            present |= {str(i.get("objectId")) for i in items}
            if len(items) < 100:
                break
        for e in executions:
            if e.review and e.trace_id not in present:
                added = self.http.post(
                    f"/annotation-queues/{queue['id']}/items",
                    json={"objectId": e.trace_id, "objectType": "TRACE"},
                )
                if added.status_code >= 400:
                    report.problems.append(f"queue item {e.case}/{e.arm}: HTTP {added.status_code}")
                else:
                    report.queue_items_added += 1

    def _configs(self, report: Report) -> list[str]:
        wanted: dict[str, dict[str, Any]] = {
            "failure_category": {
                "dataType": "CATEGORICAL",
                "categories": [{"label": c, "value": n} for n, c in enumerate(FAILURE_CATEGORIES)],
                "description": "Why this evaluation execution failed (assigned by a reviewer).",
            },
            "failure_notes": {
                "dataType": "TEXT",
                "description": "A reviewer's note on the failure.",
            },
        }
        existing = {
            c["name"]: c["id"]
            for c in self.http.get("/score-configs", params={"limit": 100}).json().get("data", [])
            if not c.get("isArchived")
        }
        ids = []
        for name, body in wanted.items():
            if name not in existing:
                made = self.http.post("/score-configs", json={"name": name, **body})
                if made.status_code >= 400:
                    report.problems.append(f"score config {name}: HTTP {made.status_code}")
                    continue
                existing[name] = made.json()["id"]
            ids.append(existing[name])
        return ids


def reissued(manifest: Path) -> dict[str, int]:
    """How many times each execution ("IC-15/enriched") was re-issued, from the manifest."""
    return dict(_read(manifest).get("_reissued") or {})


def _read(path: Path) -> dict[str, Any]:
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def _write(path: Path, sent: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sent, indent=1, sort_keys=True) + "\n", encoding="utf-8")


__all__ = [
    "DATASET",
    "FAILURE_CATEGORIES",
    "QUEUE",
    "Execution",
    "LangfuseEvalExport",
    "Observation",
    "Report",
    "build",
    "normalise",
    "readable_prompt",
    "reissued",
    "spans",
]
