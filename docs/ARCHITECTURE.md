# Architecture

## 1. Style: ports and adapters

A **port** is a socket: a small interface the kernel owns, such as "send a message" or "create a ticket". An **adapter** is a plug: Outlook is one adapter for the message socket, an in-memory fake is another. The kernel only knows sockets, so any adapter can be swapped, switched off or added without editing the kernel.

## 2. Layers and the dependency rule

```
app.py (composition root)   api/ (FastAPI)
        |                       |
        v                       v
   modules/        packs/        infra/
        \            |            /
                     v
                  kernel/   (pure: domain, state machine, policy, orchestrator, executor, ports)
```

- `kernel` imports only the standard library and pydantic.
- `modules`, `packs` and `infra` import `kernel` only. Modules never import each other.
- Nothing imports `app`.

Enforce it in `pyproject.toml` with import-linter:

```toml
[tool.importlinter]
root_package = "dispute_agent"

[[tool.importlinter.contracts]]
name = "Kernel is pure"
type = "forbidden"
source_modules = ["dispute_agent.kernel"]
forbidden_modules = [
  "dispute_agent.modules", "dispute_agent.packs", "dispute_agent.infra",
  "dispute_agent.api", "dispute_agent.app",
  "sqlalchemy", "httpx", "requests", "msal", "anthropic", "openai", "fastapi", "streamlit",
  "azure", "opentelemetry",
]

[[tool.importlinter.contracts]]
name = "Modules are independent"
type = "independence"
modules = [
  "dispute_agent.modules.reasoning", "dispute_agent.modules.email",
  "dispute_agent.modules.ticketing", "dispute_agent.modules.ledger",
  "dispute_agent.modules.approvals", "dispute_agent.modules.evidence",
  "dispute_agent.modules.cross_case", "dispute_agent.modules.evals",
  "dispute_agent.modules.model_gateway", "dispute_agent.modules.input_screen",
  "dispute_agent.modules.tracing", "dispute_agent.modules.image_check",
]

[[tool.importlinter.contracts]]
name = "Packs depend on kernel only"
type = "forbidden"
source_modules = ["dispute_agent.packs"]
forbidden_modules = ["dispute_agent.modules", "dispute_agent.infra", "dispute_agent.api", "dispute_agent.app"]
```

## 3. Repository layout

```
weight-dispute-agent/
  CLAUDE.md  README.md  pyproject.toml  Makefile  .env.example
  config/            modules.yaml  policy.yaml  release_gates.yaml
  docs/              ARCHITECTURE.md  FLOW.md  PROGRESS.md  BACKLOG.md  adr/  build-prompts/
  prompts/           versioned runtime prompts (see prompts/README.md)
  migrations/        Alembic
  src/dispute_agent/
    kernel/
      domain/        Case, Event, EvidenceItem, Proposal, Action, Approval, enums, Money
      statemachine/  transitions.py (declarative table), machine.py
      policy/        engine.py, rules/, signals.py (CautionSignal), verdicts.py
      orchestrator/  orchestrator.py, briefing.py, timers.py
      executor/      executor.py (outbox, idempotency, verification)
      ports/         one file per port (Protocols)
      registry/      module_spec.py, registry.py, validation.py
    modules/
      model_gateway/ fake/  anthropic_direct/  foundry_agent_service/  foundry_openai/  failover/
      reasoning/     rules_only/  llm/
      input_screen/  rules_based/  foundry_guardrails/
      tracing/       noop/  otel_foundry/
      email/         fake/  outlook_graph/
      ticketing/     fake/  jira_cloud/
      ledger/        simulated/
      approvals/     store_backed/  streamlit_ui/
      evidence/      synthetic_shipments/
      cross_case/    null/ (Phase 0)   analyzer/ sql_context/ (Phase 7)
      evals/         scenarios/  metrics/  reports/
    packs/weight_dispute/   calculator.py  eligibility.py  rate_card.py  templates/  scenarios/
    infra/           sqlite/  clock.py  ids.py  logging.py  settings.py  prompts.py
    api/             routes for events, cases, timeline, health
    app.py
  tests/             unit/  contract/  integration/  e2e/  architecture/  evals/
```

## 4. Ports (the sockets)

Sketch signatures; refine in the phase plans.

```python
class Clock(Protocol):            def now(self) -> datetime: ...
class IdGenerator(Protocol):      def new_id(self, prefix: str) -> str: ...

class CaseRepository(Protocol):   # optimistic concurrency via version
    def get(self, case_id: CaseId) -> Case: ...
    def save(self, case: Case, expected_version: int) -> Case: ...
class EventLog(Protocol):         def append(self, event: Event) -> None: ...  # append-only

class EvidenceSource(Protocol):
    name: str
    def fetch(self, ref: CaseRef) -> EvidenceResult: ...   # found | missing | failed, never invented

class Reasoner(Protocol):         def propose(self, briefing: CaseBriefing) -> Proposal: ...
class ReplyInterpreter(Protocol): def interpret(self, ctx: ReplyContext) -> ReplyReading: ...
class MessageDrafter(Protocol):   def draft(self, ctx: DraftContext) -> DraftedMessage: ...

class MessageChannel(Protocol):
    def send(self, msg: OutboundMessage, key: IdempotencyKey) -> SendReceipt: ...
    def fetch_replies(self, since: datetime) -> Sequence[InboundMessage]: ...
    def get_message(self, receipt: SendReceipt) -> MessageSnapshot: ...      # read-back

class TicketingSystem(Protocol):
    def upsert_issue(self, spec: IssueSpec, key: IdempotencyKey) -> IssueRef: ...
    def get_issue(self, ref: IssueRef) -> IssueSnapshot: ...                 # read-back

class Ledger(Protocol):
    def get_entries(self, ref: ShipmentRef) -> Sequence[LedgerEntry]: ...
    def post_credit(self, req: CreditRequest, key: IdempotencyKey) -> TransactionRef: ...
    def get_transaction(self, ref: TransactionRef) -> TransactionSnapshot: ...  # read-back

class ApprovalService(Protocol):
    def request(self, req: ApprovalRequest) -> ApprovalId: ...
    def get(self, approval_id: ApprovalId) -> ApprovalStatus: ...

class PatternContextProvider(Protocol):          # READ side, used by the orchestrator
    def patterns_for(self, facts: CaseFacts) -> Sequence[PatternRecord]: ...
class CaseOutcomeObserver(Protocol):             # WRITE side, notified after a case closes
    def on_outcome(self, outcome: CaseOutcome) -> None: ...

class PromptRegistry(Protocol):   def load(self, name: str, version: str) -> RenderedPrompt: ...
class Tracer(Protocol):           def span(self, name: str, **attrs: str) -> ContextManager[None]: ...

class ModelGateway(Protocol):     # the only path to any model (I14)
    def complete(self, req: ModelRequest) -> ModelResponse: ...   # messages + optional JSON schema; returns parsed output, token usage, latency
class InputScreen(Protocol):      # advisory only (I15)
    def screen(self, text: UntrustedText, kind: ScreenKind) -> ScreenResult: ...  # flags can only add caution
```

Every module port also has a `Disabled` implementation that raises `ModuleUnavailable(module_name)`. The orchestrator turns that into `NEEDS_HUMAN_REVIEW` with reason `module_unavailable:<name>` (I6).

## 5. Module registry

```yaml
# config/modules.yaml
modules:
  evidence:    {enabled: true,  adapter: synthetic_shipments}
  model_gateway: {enabled: false, adapter: fake}            # anthropic_direct (Haiku) from Phase 4; failover(foundry_agent_service -> anthropic_direct) from Phase 4b
  reasoning:   {enabled: true,  adapter: rules_only}        # llm from Phase 4; depends_on model_gateway
  input_screen: {enabled: true,  adapter: rules_based}      # foundry_guardrails from Phase 4b
  tracing:     {enabled: true,  adapter: noop}              # otel_foundry from Phase 4b
  email:       {enabled: true,  adapter: outlook_graph}     # fake in tests
  ticketing:   {enabled: true,  adapter: jira_cloud}
  ledger:      {enabled: false, adapter: simulated}         # on from Phase 5
  approvals:   {enabled: true,  adapter: store_backed}      # message review from Phase 3; credits from Phase 5
  cross_case:  {enabled: false, adapter: null}              # the empty box; on in Phase 7
  evals:       {enabled: true,  adapter: default}
```

```python
@dataclass(frozen=True)
class ModuleSpec:
    name: str
    port: type
    required: bool = False                 # kernel-required modules cannot be disabled
    depends_on: tuple[str, ...] = ()       # ledger -> approvals; cross_case -> ticketing
    adapters: Mapping[str, Callable[..., object]] = ...
```

Startup validation, in `kernel/registry/validation.py`: an unknown module or adapter, a missing dependency (for example `ledger` enabled without `approvals`), and a required module disabled all stop the app with a message that names the modules. A disabled module is wired to its `Disabled` adapter, except `cross_case`, which is wired to the `Null` adapter (see section 8).

## 6. Domain pack

A pack carries all domain knowledge so the kernel stays generic.

```python
class DomainPack(Protocol):
    name: str
    def eligibility(self, facts: CaseFacts) -> EligibilityResult: ...
    def evidence_plan(self, facts: CaseFacts) -> Sequence[EvidenceRequirement]: ...
    def assess(self, evidence: EvidenceBundle) -> ChargeAssessment: ...     # deterministic
    def allowed_actions(self, state: CaseState, assessment: ChargeAssessment) -> Sequence[ActionType]: ...
    def templates(self) -> TemplateSet: ...
    def policy_defaults(self) -> PolicyDefaults: ...
```

`packs/weight_dispute` is the first pack. Its volumetric divisor, slabs, zones and thresholds are configuration, never constants in code.

## 7. Data model (SQLite via SQLAlchemy Core, Alembic migrations)

| Table | Holds |
|-------|-------|
| cases | id, dedupe key, shipment ref, seller ref, state, version, timestamps, due dates |
| events | append-only audit trail: time, type, cause, payload, correlation id |
| evidence_items | source, value, retrieved at, status (found, missing, failed) |
| proposals | Reasoner output, cited evidence, rules verdict, prompt name/version/hash, model id |
| approvals | approver, subject (credit or message), amount or message text hash, reason code, decision, time |
| actions | type, idempotency key, payload hash, external id, status (PLANNED, SENT, VERIFIED, FAILED, UNKNOWN), verified at |
| messages | direction, channel ids, conversation id, case id |
| timers | case id, kind (reminder, decision), due at, fired at |
| model_calls | case id, purpose, deployment name, input and output tokens, latency, estimated cost (Phase 4b; no message content) |
| pattern_records, pattern_case_links | created in Phase 7 only (the domain model `PatternRecord` exists from Phase 0) |

## 8. The cross-case socket

Phase 0 creates: the `PatternRecord`, `CautionSignal` and `CaseOutcome` domain models; the `PatternContextProvider` and `CaseOutcomeObserver` ports; `NullPatternContextProvider` (returns an empty list) and `NullOutcomeObserver` (does nothing); the `pattern_context` field on `CaseBriefing`, empty until Phase 7; and a policy rule that accepts `CautionSignal`s and can only escalate a verdict.

Phase 7 adds adapters, a scheduled analyzer and tables. It changes no line in `kernel/`. If it needs to, the socket was designed wrongly: stop and write an ADR.

## 9. Action lifecycle (outbox style)

```
PLANNED -> SENT -> VERIFIED
              \-> FAILED        (known failure; safe to retry with the same key)
              \-> UNKNOWN       (could not confirm; goes to NEEDS_HUMAN_REVIEW)
```

- Idempotency key: `sha256(case_id | action_type | payload_hash)`. A retry reuses the key; the adapter or the ledger must reject it or return the original result.
- Event dedupe key: `<source>:<source_event_id>`.
- Concurrency: per-case optimistic versioning on `cases.version`. A conflict re-reads and retries the handler.
- Retries: capped exponential backoff, idempotent operations only, always with a timeout.

## 10. Configuration defaults (illustrative, all in `config/policy.yaml`)

```yaml
timers:        {reminder_after_hours: 48, decision_after_hours: 72, reopen_window_days: 7}
contact:       {max_messages_per_case: 3, max_messages_per_day: 1}
messages:      {review_required_kinds: [evidence_request, reminder, handed_to_human_notice, upheld_explanation, credit_confirmation], auto_send_kinds: []}   # pilot: review everything; relax later
credits:       {require_approval: true, reason_codes: [calculation_error, remeasurement_lower, duplicate_charge]}
patterns:      {min_related_cases: 3, window_days: 14}     # used from Phase 7
llm:           {max_calls_per_case: 6, max_output_tokens: 1200, pricing_file: config/pricing.yaml}
llm_primary:   {spend_cap_usd: 150, credit_expires_on: YYYY-MM-DD, allow_paid_usage: false}   # Azure free credit: 30-day window; paid usage off by default
llm_secondary: {allowed_models: "claude-haiku-*", spend_cap_usd: 10}                          # Haiku only, enforced at startup
telemetry:     {capture_content: false}                    # I14; true only for a deliberate test run
```

## 11. Extension recipes

- New adapter: implement the port, add it to the module's adapters, pass the port's contract suite, list it in `modules.yaml`.
- New module: add a port in the kernel, a `ModuleSpec`, a `Disabled` adapter, contract tests, then the adapter.
- New domain: write a new `packs/<name>` implementing `DomainPack`; no kernel change.
- Connect cross-case (Phase 7): set `cross_case.enabled` to true and select the `sql_context` adapter in `modules.yaml`.

## 12. Microsoft Foundry integration

Foundry is the model and governance platform behind our ports. It never owns the case lifecycle, state or decisions (I8). Code owns the loop; Foundry Agent Service hosts single-purpose prompt agents that our orchestrator calls.

| Touchpoint | Port or location | Adapter | Phase |
|------------|------------------|---------|-------|
| Reasoning agents | `ModelGateway` | `foundry_agent_service`: one tool-less prompt agent per runtime prompt, called statelessly through the project's OpenAI Responses client | 4b |
| Direct model call (fallback and A/B) | `ModelGateway` | `foundry_openai`: the same deployment without an agent | 4b |
| Agent definitions | `make agents-sync`, `config/foundry_agents.lock.json` | built from `prompts/`; runtime refuses drifted agents | 4b |
| Tracing | `Tracer` | `otel_foundry` (OpenTelemetry to Application Insights; content off by default) | 4b |
| Guardrails | `InputScreen` | `foundry_guardrails` (Prompt Shields signals, advisory; a block becomes `GuardrailBlocked`) | 4b |
| Usage, cost and spend cap | `model_calls` table, `config/pricing.yaml` | per-call rows; `credit_cap` policy rule; `make foundry-spend` | 4b |
| Evaluation | `modules/evals` | Foundry evaluation of the hosted agents; local gates stay authoritative | 6 |
| Red teaming | `tools/` script | Foundry AI Red Teaming (preview) against the agents | 6 |
| Secondary model (fallback) | `ModelGateway` | `anthropic_direct`: the owner's Anthropic API key, Haiku models only (enforced in code) | 4 |
| Failover | `ModelGateway` | `failover` composite: Foundry first, Haiku second, only for availability, rate-limit, quota and credit-window errors | 4b |
| Claude on Foundry | `ModelGateway` | `foundry_anthropic`, optional and later (needs East US 2 or Sweden Central and Marketplace billing) | backlog |

Runtime choices: model `gpt-5-mini` (Azure OpenAI, Global Standard), region South India, Agent Service. Owner decisions D12, D15 in `docs/HANDOFF.md`.

Rules:
- Foundry uses Entra ID only (`DefaultAzureCredential`; `az login` locally); there are no Foundry keys. The only API key in the project is the secondary Anthropic key, in `.env` only (I14).
- Endpoint, deployment name and agent names come from environment variables, `config/modules.yaml` and the lock file, never constants.
- Agents have no tools in v1. Adding one needs an ADR and the owner's approval (I1).
- One response per call. No conversation reuse. Foundry never holds case state.
- A guardrail block, an LLM budget breach, a spend-cap breach or agent drift ends in `NEEDS_HUMAN_REVIEW` with a recorded reason.
- Preview features (tracing instrumentation, red teaming, memory stores) live only inside adapters flagged `preview: true` and are never required for `make verify`.
- Avoid retired or soon-retired models; check the retirement table before choosing one.
- Setup steps that need the owner (subscription, deployment, role assignment) are in `docs/FOUNDRY_SETUP.md`. Claude Code never creates Azure resources.

### Secondary model and failover

- The secondary is the owner's Anthropic API (Haiku only). The model id comes from `ANTHROPIC_MODEL_HAIKU`; startup validation refuses any id that does not match `claude-haiku-*`.
- The key is read from `ANTHROPIC_API_KEY` by one settings loader. It never appears in the repo, logs, spans, config files, test fixtures or Claude Code settings (I12, I14).
- Failover happens only for provider unavailable, rate limit after retries, quota exceeded, or credit window ended. It never happens for a guardrail block, agent drift, invalid output after the retry, an LLM budget breach or a spend-cap breach: those escalate to a human.
- The prompt, schema, validation, policy and approvals are identical on both routes. Only the provider differs. Each failover writes an event with its reason, and every call records which route served it in `model_calls`.
- The Foundry guardrail signal exists only on the Foundry route. On the secondary route the rules-based screen runs alone, and a human still approves every credit and message in the pilot.
- Each route has its own spend cap (`llm_primary.spend_cap_usd`, `llm_secondary.spend_cap_usd`). When the primary credit window ends, Foundry calls stop unless `allow_paid_usage` is true.
