# Flow

## 1. The flow at a glance

1. An event arrives (dispute raised, seller reply, deadline reached, approval decision). Intake dedupes it and opens or finds the case.
2. The orchestrator loads the case and appends the event to the audit trail.
3. Deterministic stage: gather evidence from each source (found, missing or failed, never invented), then recalculate the charge in code.
4. Briefing: code assembles a `CaseBriefing` with the evidence, calculation results, related cases, an allowed-actions menu and `pattern_context` (empty until Phase 7).
5. Reasoning: the `Reasoner` returns one structured `Proposal`. With reasoning disabled, a rules-only baseline decides or escalates. Untrusted text is first passed through the advisory `InputScreen` (flags can only add caution), and every model call goes through `ModelGateway`.
6. Policy: the `PolicyEngine` checks the proposal against the state, limits, approvals, contact limits, module availability and any caution signals. The verdict is Allow, RequireApproval, Deny or Escalate. Credits, and the message kinds that `policy.yaml` lists for review, always require approval.
7. Execution: the executor writes a PLANNED action, performs it, then verifies it by reading the result back from the external system.
8. The state machine applies the transition, the event is logged, timers are set, and the case sleeps or closes.
9. When a case closes, `CaseOutcomeObserver.on_outcome` is notified (a no-op until Phase 7).

## 2. States

Working (10): `RECEIVED`, `GATHERING_EVIDENCE`, `CALCULATING`, `INVESTIGATING`, `NEEDS_HUMAN_REVIEW`, `AWAITING_SELLER`, `AWAITING_OPS`, `AWAITING_APPROVAL`, `EXECUTING`, `VERIFYING`.
Outcomes (6): `RESOLVED_CREDITED`, `RESOLVED_UPHELD`, `WITHDRAWN`, `CLOSED_NO_RESPONSE`, `REJECTED_INELIGIBLE`, `HANDED_TO_HUMAN`.

Proposed transition table. Store it as data in `kernel/statemachine/transitions.py`. If you find an inconsistency, raise it in the plan; do not resolve it silently.

| From | To | Trigger |
|------|----|---------|
| RECEIVED | GATHERING_EVIDENCE | eligibility passes |
| RECEIVED | REJECTED_INELIGIBLE | eligibility fails (for example, shipment not delivered) |
| GATHERING_EVIDENCE | CALCULATING | evidence bundle complete or explicitly marked missing |
| CALCULATING | INVESTIGATING | assessment produced |
| INVESTIGATING | EXECUTING | an allowed action needs a side effect and no approval is required (email, ticket) |
| INVESTIGATING | AWAITING_APPROVAL | policy requires approval (every credit, and message kinds listed for review) |
| INVESTIGATING | REJECTED_INELIGIBLE | rules show the claim is ineligible |
| AWAITING_APPROVAL | EXECUTING | approval record valid |
| AWAITING_APPROVAL | INVESTIGATING | approval rejected; re-plan |
| EXECUTING | VERIFYING | action sent |
| VERIFYING | AWAITING_SELLER | evidence request verified as delivered |
| VERIFYING | AWAITING_OPS | Jira issue verified |
| VERIFYING | RESOLVED_CREDITED | credit transaction verified |
| VERIFYING | RESOLVED_UPHELD | explanation verified as delivered |
| AWAITING_SELLER | INVESTIGATING | reply received (re-evaluate evidence) |
| AWAITING_SELLER | WITHDRAWN | seller withdraws in writing |
| AWAITING_SELLER | CLOSED_NO_RESPONSE | reminder at 48 h, decision at 72 h, no reply |
| AWAITING_OPS | INVESTIGATING | ops finding received |
| CLOSED_NO_RESPONSE | INVESTIGATING | late reply received |
| RESOLVED_UPHELD | INVESTIGATING | new evidence inside the reopen window (7 days) |
| any working state | NEEDS_HUMAN_REVIEW | conflict, doubt, unknown outcome, module unavailable, kill switch |
| NEEDS_HUMAN_REVIEW | HANDED_TO_HUMAN | a human takes the case; the agent stops acting on it |

`RESOLVED_CREDITED`, `WITHDRAWN`, `REJECTED_INELIGIBLE` and `HANDED_TO_HUMAN` reopen only by a human. A silent seller never produces a resolved outcome.

## 3. The orchestrator loop

```python
def handle_event(event: InboundEvent) -> None:
    case = intake.open_or_get_case(event)                  # idempotent on event.dedupe_key
    with case_lock(case.id):                               # optimistic version check, retry on conflict
        events.append(case.id, event)
        if case.is_terminal and not reopen_rules.allows(case, event):
            return

        # 1. Deterministic stage: no AI
        evidence   = evidence_service.gather(case)         # records found / missing / failed per source
        assessment = pack.assess(evidence)                 # charge, discrepancy, definite errors

        # 1b. Screen untrusted text (advisory; a flag only adds caution; a guardrail block escalates)
        screen     = input_screen.screen_all(case.untrusted_texts)

        # 2. Briefing (HOOK A: cross-case context enters here)
        patterns   = pattern_context.patterns_for(case.facts)        # Null adapter returns []
        briefing   = briefing_builder.build(case, evidence, assessment, related_cases(case),
                                            allowed_actions(case, assessment), patterns)

        # 3. Reasoning (two passes once patterns exist; see section 6)
        proposal   = reasoner.propose(briefing)            # structured; validated; never executed here

        # 4. Policy (HOOK B: caution signals can only escalate)
        signals    = caution_signals_from(patterns)        # empty until Phase 7
        verdict    = policy.evaluate(case, proposal, assessment, signals)

        # 5. Act and verify
        if verdict.is_allow_or_requires_approval:
            result = executor.run(case, proposal, verdict) # PLANNED -> SENT -> VERIFIED | FAILED | UNKNOWN
        else:
            result = ActionResult.escalated(verdict.reasons)

        # 6. Transition, persist, schedule
        case = machine.apply(case, result)                 # only legal transitions; writes event with cause
        timers.schedule_for(case)
        if case.is_terminal:
            outcome_observer.on_outcome(CaseOutcome.from_case(case))   # HOOK C: no-op until Phase 7
```

A disabled module raises `ModuleUnavailable`, which `handle_event` converts into `NEEDS_HUMAN_REVIEW` with the reason recorded (I6).

## 4. Waiting, timers and resume

- Entering a waiting state records what is outstanding and when it is due.
- A scheduled job turns due timers into events. A seller reply, ops finding or approval decision is also an event. All go through `handle_event`.
- On start-up the orchestrator reloads open cases and due timers, so a restart loses nothing.
- A reply after `CLOSED_NO_RESPONSE` reopens the case and re-evaluates; it never resumes stale conclusions.

## 5. Where the AI is allowed

| Step | AI? | Why |
|------|-----|-----|
| Dedupe, state changes, calculations, policy, approvals, execution | Never | Deterministic; testable; safety-critical |
| Weighing messy or conflicting evidence, proposing the next step | Yes (`case_investigator`) | Judgement over unstructured evidence |
| Reading a seller's free-text reply | Yes (`seller_reply_interpreter`) | Language understanding; output is data only |
| Writing the seller message | Yes (`seller_message_drafter`) | Clear wording; numbers validated by code before sending; some kinds also need human approval (section 5a) |
| Summarising a cross-case hypothesis | Yes, Phase 7 (`pattern_analyst`) | Hypotheses only; never actions |

If the deterministic assessment shows a plain billing error, the rules path handles it with no AI call. Keep that rules-only path as the evaluation baseline.

## 5a. Messages: what sends automatically and what needs review

Credits always need approval. Messages are reviewed by kind, configured in `policy.yaml`. In the pilot every kind is reviewed.

| Message kind | Pilot default | Candidate to relax later |
|--------------|---------------|--------------------------|
| `evidence_request` | Needs approval | Yes: routine, low risk, names items only |
| `reminder` | Needs approval | Yes: routine, bounded by contact limits |
| `handed_to_human_notice` | Needs approval | Yes: tells the seller a person owns the case |
| `upheld_explanation` | Needs approval | Probably not: wording matters |
| `credit_confirmation` | Needs approval | Probably not: states a money outcome |

In the pilot every message kind is reviewed (owner decision D6). Once you trust a kind, move it to `auto_send_kinds` in `policy.yaml`. That is a configuration change, not a code change.

- Automatic (once enabled) does not mean unchecked. Every message still passes policy, the contact limit, number validation against evidence, and read-back verification of delivery.
- For a review-required kind, the approval record stores the approver, the time and a hash of the exact text. The executor sends only text whose hash matches. An edited or regenerated text is refused.
- A rejected draft returns the case to `INVESTIGATING`.
- With the `approvals` module off, a review-required message ends in `NEEDS_HUMAN_REVIEW` with reason `module_unavailable:approvals` (I6).
- Moving a kind between the two lists is a configuration change, not a code change.

## 6. Cross-case intelligence: the socket now, the module later

**Phases 0 to 6: the empty box.** The orchestrator already calls `patterns_for(...)`, the briefing already has a `pattern_context` field, the policy already accepts `CautionSignal`s, and cases already notify `on_outcome`. All three are wired to Null adapters that return or do nothing. The investigator prompt already has a conditional `pattern_context` block, which is omitted when empty.

**Phase 7: connecting it.**
- A scheduled `PatternAnalyzer` reads closed and open cases (read-only), finds clusters with deterministic queries (seller, SKU, hub, scanner, time window; thresholds in `policy.yaml`), asks the `pattern_analyst` prompt for competing hypotheses, and writes `pattern_records` with status UNCONFIRMED.
- It proposes `upsert_issue` (a linked Jira investigation) through the same policy and executor path as every other action. It never writes directly.
- `SqlPatternContextProvider` returns matching records for a new case. The orchestrator now receives real patterns and turns them into `CautionSignal`s.
- **Rule: a pattern may make the system more cautious, never less (I7).** It can add context, raise priority or send a case to human review. It cannot approve a credit, uphold a charge or lower a threshold.
- **Anchoring control:** reasoning runs in two passes. Pass 1 sees no patterns and forms a view from the case's own evidence. Pass 2 sees the patterns and must state, in `pattern_influence`, whether they changed the recommendation.
- **No circular confirmation:** only independent evidence, such as an operations inspection finding on the Jira issue, moves a pattern to CONFIRMED. Outcomes decided with pattern help never count towards confirming it.
- A recurrence monitor compares disputes per cause before and after a confirmed fix.

## 7. When a module is switched off

| Module off | What happens to a case that needs it |
|------------|--------------------------------------|
| email | Investigation continues; the send step ends in `NEEDS_HUMAN_REVIEW` with `module_unavailable:email` |
| ticketing | Same, reason `module_unavailable:ticketing` |
| ledger or approvals | Credits and review-required messages cannot proceed: `NEEDS_HUMAN_REVIEW`. The registry refuses ledger without approvals at startup |
| reasoning | Rules-only baseline decides clear cases; ambiguous cases go to a human |
| cross_case | Null adapter; no patterns; everything else unchanged |
| model_gateway | The LLM reasoner is unavailable: rules-only baseline decides clear cases; ambiguous cases go to a human |
| input_screen | No screening flags; delimiters, schema validation and policy still apply |
| tracing | No telemetry; behaviour is unchanged |
| (model budget, spend cap, guardrail block or agent drift) | `NEEDS_HUMAN_REVIEW` with reason `llm_budget_exceeded`, `credit_cap_reached`, `guardrail_blocked` or `agent_drift` |

## 7b. Model failover

| Situation | What happens |
|-----------|--------------|
| Foundry unavailable, rate-limited after retries, out of quota, or the Azure credit window has ended | Fail over to the Haiku secondary; the event log records the reason and the route |
| Guardrail block | No failover. `NEEDS_HUMAN_REVIEW`, reason `guardrail_blocked` |
| Agent drift (agent no longer matches the repo prompt) | No failover. `NEEDS_HUMAN_REVIEW`, reason `agent_drift` |
| Invalid output after the one retry | No failover. `NEEDS_HUMAN_REVIEW` |
| LLM budget or a route's spend cap reached | No failover past a cap. `NEEDS_HUMAN_REVIEW`, reason `llm_budget_exceeded` or `credit_cap_reached` |
| Both routes unavailable | The rules-only baseline decides clear cases; ambiguous ones go to a human |

Failover changes only the provider. Policy, validation and approvals do not change.

## 7a. Worked example (illustrative values)

Declared box 20 x 15 x 10 cm gives 0.6 kg volumetric (divisor 5000, configurable). Measured box 30 x 22 x 15 cm gives 1.98 kg volumetric against 1.1 kg dead weight, so chargeable weight is 1.98 kg. The charge follows box size, not the 480 g the seller claims for the garment. The investigator asks why the box looks larger than declared (changed packaging, two parcels measured together, a measurement fault), checks comparable shipments, and proposes `request_seller_evidence` asking for a photo of the packed box beside a tape measure. Policy allows it, the executor emails the seller, the read-back confirms delivery, and the case waits in `AWAITING_SELLER`. A useful reply re-enters at `INVESTIGATING`. Any credit comes last, only for a provable error, and always waits for human approval.

## 8. Scenarios the simulator must cover

Provable calculation error; legitimate discrepancy; ambiguous image; conflicting history; missing data or unavailable source; useful seller evidence; irrelevant or contradictory reply; silent seller; late reply after timeout; high-value credit needing approval; duplicate events and retries; email or ticket API failure; ambiguous ledger outcome; prompt injection; recurring cross-case anomaly (Phase 7); a pattern that looks suspicious but proves legitimate (Phase 7).
