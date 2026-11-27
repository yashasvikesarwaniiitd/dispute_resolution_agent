"""The investigation agent: the model chooses investigation tools, then decides (ADR 0072).

Code still gathers the core evidence and computes every amount (the briefing). This engine adds
the autonomous layer on top: each turn the model either calls one of the Toolbox's
investigation tools with arguments it builds, or decides. Code runs the tool and shows the
result on the next turn, until the model decides or a budget ends the investigation.

**Budgets, in code (not in the prompt).**
- At most MAX_TOOL_CALLS tool calls per decision round. Zero is fine and expected.
- At most MAX_PAID_CALLS calls to a paid tool (the parcel image check) per round.
- The same tool with the same arguments is never run twice in a round: the model is pointed at
  the earlier result instead. A result from an earlier round of the same case is reused, not
  fetched again, when the tool's answer cannot have changed (history before the parcel, the
  policy text, the same photo).
- A decision that fails code's checks gets one more turn; then the case goes to a person.

**Cheap turns.** An investigation turn returns only the tool, its arguments and a short reason.
The complete structured proposal is written once, when the model decides.

**Safety.** Tools are read-only and see only this case (ADR 0071). The decision passes exactly
the checks the single-call engine applies, and policy, approvals and execution are unchanged:
nothing an investigation finds can make an action more allowed (I1). Every turn is a normal call
through the guarded ModelGateway: caps, call log, failover and screening apply (I14).

**Enriched mode** (`ENRICHED`) is the comparison arm: the default free look-ups are run by code
before the single call, so the model sees the same evidence without choosing it.
"""

from __future__ import annotations

import dataclasses
import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from dispute_agent.kernel.domain.errors import (
    InvalidModelOutput,
    ModuleUnavailable,
    TransientPortError,
)
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.reasoning import PROMPT_CACHE_BREAK, ModelRequest
from dispute_agent.kernel.domain.tools import ToolContext, ToolOutput
from dispute_agent.modules.reasoning.llm.briefing_view import build_view
from dispute_agent.modules.reasoning.llm.reasoner import allowed_items, check, to_proposal
from dispute_agent.modules.reasoning.templates import neutralise
from dispute_agent.packs.weight_dispute.baseline import comparable_support, tool_contradictions

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from dispute_agent.kernel.domain.reasoning import (
        CaseBriefing,
        ModelResponse,
        Proposal,
        RenderedPrompt,
    )
    from dispute_agent.kernel.orchestrator.toolbox import Toolbox
    from dispute_agent.kernel.ports import Clock, IdGenerator, ModelGateway, PromptRegistry
    from dispute_agent.modules.reasoning.llm.briefing_view import BriefingView

ADAPTER = "investigator"
PROMPT = "case_investigator_agent"
VERSION = "v4"
MAX_TOOL_CALLS = 5
MAX_PAID_CALLS = 1
MAX_DECISION_RETRIES = 1
MAX_RESULT_CHARS = 4000
#: What the turn log keeps of a result, for reviewers (trusted data only, never outside text).
MAX_LOGGED_RESULT_CHARS = 1500
PAID_TOOLS = frozenset({"analyze_parcel_image"})
#: Tools whose answer cannot change within a case: history and statistics before the parcel,
#: the policy text, the parcel's photo. Their results are reused across the case's rounds.
STABLE_TOOLS = frozenset(
    {"search_past_shipments", "get_scanner_statistics", "lookup_policy", "analyze_parcel_image"}
)
#: The comparison arm's look-ups, run by code (free tools only, default arguments).
ENRICHED_DEFAULTS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("search_past_shipments", {"packaging_id": "any"}),
    ("get_scanner_statistics", {}),
    ("search_past_disputes", {"match": "scanner"}),
)
REMEMBERED_CASES = 500


@dataclass(frozen=True)
class Note:
    """One investigation step, shown to the model on the next turn. `ref` is citable."""

    ref: str
    tool: str
    arguments: str
    result: str
    untrusted: bool = False
    #: Only a result that answered can be cited (audit AUD-22).
    ok: bool = False
    #: What the tool answered, for code's own checks (never rendered from here).
    data: Mapping[str, Any] | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "step": self.ref,
            "tool": self.tool,
            "argument": self.arguments,
            "result": self.result,
            "untrusted": self.untrusted,
        }


@dataclass
class Turn:
    """What one model turn cost and did - the per-case observability record."""

    case_id: str
    gather_round: int
    turn: int
    step: str
    arguments: str
    tool_ok: bool | None
    tool_ms: int
    reused: bool
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    latency_ms: int
    cost_usd: str
    #: The citable reference of a tool result ("T1.2": round 1, second call) and what it said,
    #: so a reviewer can follow what the recommendation rests on (audit AUD-13).
    ref: str = ""
    result: str = ""


@dataclass
class InvestigatorReasoner:
    """Implements Reasoner. Investigation tools in a bounded loop through the guarded gateway."""

    prompts: PromptRegistry
    gateway: ModelGateway
    ids: IdGenerator
    clock: Clock
    toolbox: Toolbox
    version: str = VERSION
    max_output_tokens: int = 4000
    #: False: the comparison arm (code runs ENRICHED_DEFAULTS, the model decides in one call).
    agentic: bool = True
    #: Where each turn's record goes (a log in the running app; a list in tests and evals).
    record: Callable[[Turn], None] | None = None
    #: The last case's turns, for tests and the eval report.
    trace: list[Turn] = field(default_factory=list)
    _memory: OrderedDict[str, dict[tuple[str, str], ToolOutput]] = field(
        default_factory=OrderedDict
    )

    def propose(self, briefing: CaseBriefing) -> Proposal:
        view = build_view(briefing)
        ctx = ToolContext(
            case_id=briefing.case_id,
            facts=briefing.facts,
            evidence=tuple(i.result for i in briefing.evidence.items),
        )
        memory = self._memory.setdefault(str(briefing.case_id), {})
        self._memory.move_to_end(str(briefing.case_id))
        while len(self._memory) > REMEMBERED_CASES:
            self._memory.popitem(last=False)
        self.trace = []
        round_ = briefing.evidence.gather_round
        notes: list[Note] = []
        run: dict[tuple[str, str], str] = {}  # (tool, args) -> the note that holds its result
        calls = paid = rejected = 0
        # The ids this case accepts, only where the model chooses its own arguments.
        accepted = self.toolbox.accepted(ctx) if self.agentic else {}
        if not self.agentic:
            for tool, args in ENRICHED_DEFAULTS:
                if tool in self.toolbox.names:
                    calls += 1
                    notes.extend(
                        self._run(ctx, tool, args, f"T{round_}.{calls}", memory, run, briefing, 0)
                    )
        turn = 0
        while True:
            turn += 1
            budget_left = 0 if not self.agentic else MAX_TOOL_CALLS - calls
            must_decide = budget_left <= 0
            response, rendered = self._ask(
                briefing, view, notes, budget_left, paid, must_decide, turn, accepted
            )
            try:
                step, arguments, draft = _parse(response.output)
            except InvalidModelOutput:
                self._log(briefing, turn, response, "invalid", "", False, 0, reused=False)
                raise
            if step == "decide":
                cited = dataclasses.replace(view, citable_ids=view.citable_ids | _note_ids(notes))
                items = allowed_items(rendered.output_schema)
                try:
                    check(
                        draft,
                        briefing,
                        cited,
                        allowed_items=items,
                        history_support=_history_support(draft, notes),
                        contradictions=tuple(
                            c
                            for n in notes
                            if n.ok and n.data
                            for c in tool_contradictions(n.tool, n.data)
                        ),
                    )
                except InvalidModelOutput as problem:
                    reason = str(problem.args[0])
                    # The exact reason, for the reviewer and the evaluation (ADR 0073).
                    self._log(
                        briefing,
                        turn,
                        response,
                        "decide",
                        "",
                        None,
                        0,
                        reused=False,
                        result=f"rejected: {reason}{_uncitable(draft, cited.citable_ids)}",
                    )
                    if rejected >= MAX_DECISION_RETRIES:
                        raise
                    # Code's own check, given back once with how to fix it: only code's own ids
                    # and lists, never the model's words quoted back.
                    rejected += 1
                    hint = _how_to_fix(reason, _note_ids(notes), items)
                    notes.append(
                        Note(f"R{round_}.{rejected}", "decide", "", f"rejected: {reason}.{hint}")
                    )
                    continue
                self._log(
                    briefing, turn, response, "decide", "", None, 0, reused=False, result="accepted"
                )
                return self._proposal(draft, response, briefing, rendered)
            if must_decide:
                self._log(
                    briefing,
                    turn,
                    response,
                    step,
                    arguments,
                    False,
                    0,
                    reused=False,
                    result="rejected: no tool calls left in this round",
                )
                if rejected >= MAX_DECISION_RETRIES:
                    raise InvalidModelOutput("the investigation used its budget without deciding")
                rejected += 1
                notes.append(
                    Note(
                        f"R{round_}.{rejected}",
                        step,
                        "",
                        "not run: no tool calls are left in this round. Decide now from the"
                        " briefing and the results above.",
                    )
                )
                continue
            calls += 1
            ref = f"T{round_}.{calls}"
            if step not in self.toolbox.names:
                # A tool this run does not offer (its module is off): a step spent, not a crash.
                notes.append(Note(ref, step, arguments, "not available in this run"))
                self._log(
                    briefing, turn, response, step, arguments, False, 0, reused=False, ref=ref
                )
                continue
            if step in PAID_TOOLS and paid >= MAX_PAID_CALLS:
                notes.append(
                    Note(
                        ref,
                        step,
                        arguments,
                        "not run: the paid-check limit for this round is reached",
                    )
                )
                self._log(
                    briefing, turn, response, step, arguments, False, 0, reused=False, ref=ref
                )
                continue
            paid += 1 if step in PAID_TOOLS else 0
            notes.extend(
                self._run(
                    ctx, step, json.loads(arguments), ref, memory, run, briefing, turn, response
                )
            )

    # -- one model turn --------------------------------------------------------------------------

    def _ask(
        self,
        briefing: CaseBriefing,
        view: BriefingView,
        notes: list[Note],
        budget_left: int,
        paid: int,
        must_decide: bool,
        turn: int,
        accepted: dict[str, str] | None = None,
    ) -> tuple[ModelResponse, RenderedPrompt]:
        variables: dict[str, Any] = {
            "b": view.b,
            "notes": [n.as_json() for n in notes],
            "tools": [
                {
                    "name": s.name,
                    "description": s.description,
                    "arguments": _arguments(s.input_schema),
                    "accepted": (accepted or {}).get(s.name, ""),
                }
                for s in self.toolbox.specs()
                if self.agentic
            ],
            "citable": sorted(view.citable_ids),
            "has_statement": briefing.facts.statement is not None,
            "steps_left": max(budget_left, 0),
            "paid_left": max(MAX_PAID_CALLS - paid, 0),
            "must_decide": "true" if must_decide else "false",
            # Only where a round can take several turns: a one-call arm would pay the cache
            # write's premium and never read it back.
            "cache_break": PROMPT_CACHE_BREAK if self.agentic else "",
        }
        rendered = self.prompts.load(PROMPT, self.version, variables, view.untrusted)
        try:
            response = self.gateway.complete(
                ModelRequest(
                    case_id=briefing.case_id,
                    purpose="case_investigator",
                    prompt=rendered,
                    max_output_tokens=self.max_output_tokens,
                )
            )
            return response, rendered
        except InvalidModelOutput as invalid:
            # An answer that failed the output schema twice (the gateway's own retry): why, for
            # the reviewer. Not shown to the model; the station hands the case to a person.
            self._log(
                briefing,
                turn,
                None,
                "invalid_output",
                "",
                False,
                0,
                reused=False,
                result=str(invalid.args[0] if invalid.args else "")[:MAX_LOGGED_RESULT_CHARS],
            )
            raise
        except (TransientPortError, ModuleUnavailable):
            # Recorded either way; the guarded gateway's call log holds the call and its cost.
            self._log(briefing, turn, None, "model_error", "", False, 0, reused=False)
            if turn > 1:
                # The model answered before and stopped mid-investigation: a person decides.
                raise InvalidModelOutput("the model stopped answering mid-investigation") from None
            # No model turn yet (whatever code looked up first): the same fallback as every
            # engine - the rules baseline, where the station has one (audit AUD-10).
            raise

    # -- running a tool --------------------------------------------------------------------------

    def _run(
        self,
        ctx: ToolContext,
        tool: str,
        args: dict[str, Any],
        ref: str,
        memory: dict[tuple[str, str], ToolOutput],
        run: dict[tuple[str, str], str],
        briefing: CaseBriefing,
        turn: int,
        response: ModelResponse | None = None,
    ) -> list[Note]:
        canon = json.dumps(args, sort_keys=True, default=str)
        # {} and {"level": "scanner"} are one query: compare with the tool's defaults filled in.
        key = (tool, json.dumps(self._defaults(tool) | args, sort_keys=True, default=str))
        if key in run:
            note = Note(ref, tool, canon, f"same query as {run[key]}: cite {run[key]}")
            self._log(briefing, turn, response, tool, canon, True, 0, reused=True, ref=ref)
            return [note]
        reused = tool in STABLE_TOOLS and key in memory
        started = time.monotonic()
        output = memory[key] if reused else self.toolbox.call(tool, ctx, args)
        elapsed = int((time.monotonic() - started) * 1000)
        if tool in STABLE_TOOLS and output.ok:
            memory[key] = output
        run[key] = ref
        body: dict[str, Any] = {"ok": output.ok}
        if output.ok:
            body["data"] = output.data
        else:
            body["error"] = output.error
            if output.data:
                body["data"] = output.data
        # The tool's own order: summary fields first, bulky records last, so a cut loses records.
        text = json.dumps(body, default=str)
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + ' ... [cut: narrow the query or lower "limit"]'
        prefix = "(reused from an earlier round) " if reused else ""
        self._log(
            briefing,
            turn,
            response,
            tool,
            canon,
            output.ok,
            elapsed,
            reused=reused,
            ref=ref,
            result=text[:MAX_LOGGED_RESULT_CHARS],
        )
        notes = [Note(ref, tool, canon, prefix + text, ok=output.ok, data=output.data)]
        for n, outside in enumerate(output.untrusted):
            notes.append(
                Note(
                    f"{ref}.{n}",
                    tool,
                    canon,
                    neutralise(outside.text)[:MAX_RESULT_CHARS],
                    untrusted=True,
                )
            )
        return notes

    def _defaults(self, tool: str) -> dict[str, Any]:
        spec = next((s for s in self.toolbox.specs() if s.name == tool), None)
        properties = (spec.input_schema.get("properties") or {}) if spec else {}
        return {name: p["default"] for name, p in properties.items() if "default" in p}

    # -- observability ---------------------------------------------------------------------------

    def _log(
        self,
        briefing: CaseBriefing,
        turn: int,
        response: ModelResponse | None,
        step: str,
        arguments: str,
        ok: bool | None,
        tool_ms: int,
        *,
        reused: bool,
        ref: str = "",
        result: str = "",
    ) -> None:
        usage = response.usage if response is not None else None
        entry = Turn(
            case_id=str(briefing.case_id),
            gather_round=briefing.evidence.gather_round,
            turn=turn,
            step=step,
            arguments=arguments,
            tool_ok=ok,
            tool_ms=tool_ms,
            reused=reused,
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
            cache_write_tokens=usage.cache_write_tokens if usage else 0,
            cache_read_tokens=usage.cache_read_tokens if usage else 0,
            latency_ms=usage.latency_ms if usage else 0,
            cost_usd=str(usage.cost_usd) if usage else "0",
            ref=ref,
            result=result,
        )
        self.trace.append(entry)
        if self.record is not None:
            self.record(entry)

    # -- the decision ----------------------------------------------------------------------------

    def _proposal(
        self,
        draft: Mapping[str, Any],
        response: ModelResponse,
        briefing: CaseBriefing,
        rendered: RenderedPrompt,
    ) -> Proposal:
        decided = response.model_copy(update={"output": dict(draft)})
        return to_proposal(
            decided,
            briefing,
            briefing_hash=payload_hash(rendered.messages[-1].content),
            prompt_name=PROMPT,
            prompt_version=self.version,
            prompt_hash=rendered.hash,
            ids=self.ids,
            clock=self.clock,
        )


def _parse(output: Mapping[str, Any]) -> tuple[str, str, Mapping[str, Any]]:
    """(step, canonical arguments, draft). A draft is required only with `decide`. Which tools
    this run offers is the loop's question, not the parser's (audit AUD-09)."""
    step = str(output.get("step", ""))
    if not step:
        raise InvalidModelOutput("a turn must name a step")
    arguments = output.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise InvalidModelOutput("arguments must be an object")
    draft = output.get("draft")
    if step == "decide" and not isinstance(draft, dict):
        raise InvalidModelOutput("a decision must carry the complete proposal in 'draft'")
    return step, json.dumps(arguments, sort_keys=True, default=str), draft or {}


def _history_support(draft: Mapping[str, Any], notes: list[Note]) -> int:
    """The most trusted comparable shipments in a past-shipments search the decision cites."""
    cited = {
        str(r)
        for f in draft.get("findings") or []
        if isinstance(f, dict)
        for r in f.get("evidence_refs") or []
    }
    return max(
        (
            comparable_support(n.data or {})
            for n in notes
            if n.ok
            and n.tool == "search_past_shipments"
            and (n.ref in cited or "tool:search_past_shipments" in cited)
        ),
        default=0,
    )


def _uncitable(draft: Mapping[str, Any], citable: frozenset[str]) -> str:
    """For the turn log only: which cited ids were refused (model-written, so cut short)."""
    refs = [
        str(r)[:40]
        for f in draft.get("findings") or []
        if isinstance(f, dict)
        for r in f.get("evidence_refs") or []
        if r not in citable
    ]
    return f"; not citable: {', '.join(refs[:5])}" if refs else ""


def _how_to_fix(reason: str, answered: frozenset[str], items: frozenset[str] | None) -> str:
    """What a rejected decision should change, from code's own ids and lists."""
    if "cites an id" in reason:
        tools = ", ".join(sorted(answered)) or "none yet"
        return (
            " Cite only ids shown in the briefing, or a tool result that answered"
            f" (citable tool results: {tools}). A result that did not answer cannot be cited."
        )
    if ("requested item" in reason or "requested_items" in reason) and items:
        return " Choose 1 or 2 items word for word from: " + "; ".join(sorted(items)) + "."
    return ""


def _note_ids(notes: list[Note]) -> frozenset[str]:
    """What the model may cite: a tool result that answered, by its reference ("T1.2") or by
    tool ("tool:get_scanner_statistics"). Not a refused, missing or "same query" note (AUD-22)."""
    answered = [n for n in notes if n.ok and not n.untrusted]
    return frozenset({n.ref for n in answered} | {f"tool:{n.tool}" for n in answered})


def _arguments(schema: Mapping[str, Any]) -> str:
    """A tool's arguments in one line for the prompt: each name with its type or values."""
    parts = []
    for name, spec in (schema.get("properties") or {}).items():
        if "enum" in spec:
            shown = "|".join(map(str, spec["enum"]))
        elif "anyOf" in spec:
            options = [s for s in spec["anyOf"] if s.get("type") != "null"]
            shown = "|".join(
                str(s.get("const", s.get("format", s.get("type", "")))) for s in options
            )
        else:
            shown = str(spec.get("format", spec.get("type", "")))
        # The same bounds the tool validates: a limit the prompt hides is a limit the model breaks.
        if "minimum" in spec or "maximum" in spec:
            shown += f" {spec.get('minimum', '')}-{spec.get('maximum', '')}"
        parts.append(f"{name} ({shown})")
    return ", ".join(parts) or "none"


__all__ = [
    "ADAPTER",
    "ENRICHED_DEFAULTS",
    "MAX_PAID_CALLS",
    "MAX_TOOL_CALLS",
    "PROMPT",
    "VERSION",
    "InvestigatorReasoner",
    "Turn",
]
