"""The composition root — the only place that builds anything (ARCHITECTURE.md sections 2-3).

Everything else in this project takes its dependencies through its constructor and has no way
to reach out for one. That is what makes the kernel testable without mocks and what lets a
module be swapped by editing a YAML file rather than a call site. The cost is that one file
has to know about everything, and this is that file: it reads configuration, validates the
module combination, builds the adapters the registry chose, and hands them over.

Three rules hold here and nowhere else. This is the only module that reads the environment.
It is the only one that constructs an adapter. And nothing imports it — `tests/architecture/
test_composition_root.py` enforces that, because a module that imported the composition root
could reach any adapter in the system and the dependency rule would be decoration.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from fastapi import FastAPI

from dispute_agent.api import build_router
from dispute_agent.api.runtime import Runtime
from dispute_agent.infra.clock import SystemClock
from dispute_agent.infra.ids import UlidIdGenerator
from dispute_agent.infra.logging import configure_logging
from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.errors import ConfigError
from dispute_agent.kernel.registry.registry import ModulesFile, Registry
from dispute_agent.kernel.registry.validation import validate_modules
from dispute_agent.modules.approvals.disabled import DisabledApprovalService
from dispute_agent.modules.cross_case.null import NullOutcomeObserver, NullPatternContextProvider
from dispute_agent.modules.email.disabled import DisabledMessageChannel
from dispute_agent.modules.evidence.disabled import DisabledEvidenceSource
from dispute_agent.modules.input_screen.disabled import DisabledInputScreen
from dispute_agent.modules.input_screen.rules_based import RulesBasedInputScreen
from dispute_agent.modules.ledger.disabled import DisabledLedger
from dispute_agent.modules.model_gateway.disabled import DisabledModelGateway
from dispute_agent.modules.reasoning.disabled import (
    DisabledMessageDrafter,
    DisabledReasoner,
    DisabledReplyInterpreter,
)
from dispute_agent.modules.ticketing.disabled import DisabledTicketingSystem
from dispute_agent.modules.tracing.noop import NoopTracer

if TYPE_CHECKING:
    from collections.abc import Callable

    from dispute_agent.api.scenario_runner import ScenarioModels
    from dispute_agent.kernel.domain.reasoning import ModelRequest
    from dispute_agent.kernel.policy.config import PolicyConfig
    from dispute_agent.kernel.ports import (
        ApprovalService,
        CaseOutcomeObserver,
        Clock,
        EvidenceSource,
        IdGenerator,
        InputScreen,
        Ledger,
        MessageChannel,
        MessageDrafter,
        ModelGateway,
        PatternContextProvider,
        Reasoner,
        ReplyInterpreter,
        TicketingSystem,
        Tracer,
    )

#: The project folder, so config, prompts and the call log are found from any working
#: directory (Phase 4P safety review, LOW).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"


@dataclass(frozen=True)
class Adapters:
    """Every port, wired. Passed by constructor from here on down."""

    evidence: EvidenceSource
    model_gateway: ModelGateway
    reasoner: Reasoner
    reply_interpreter: ReplyInterpreter
    message_drafter: MessageDrafter
    input_screen: InputScreen
    tracer: Tracer
    email: MessageChannel
    ticketing: TicketingSystem
    ledger: Ledger
    approvals: ApprovalService
    patterns: PatternContextProvider
    outcomes: CaseOutcomeObserver


@dataclass(frozen=True)
class Application:
    """What `create_app` assembled, so a test can look at the wiring without HTTP."""

    registry: Registry
    adapters: Adapters
    app_env: str
    settings: Settings
    clock: Clock
    ids: IdGenerator
    #: None when no database path was given, which is how a configuration-only check
    #: (and the /healthz tests) build an app without touching storage.
    runtime: Runtime | None = None


def load_modules_file(config_dir: Path) -> ModulesFile:
    path = config_dir / "modules.yaml"
    if not path.is_file():
        raise ConfigError(f"no modules.yaml at {path}")
    return ModulesFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _not_built(module: str, adapter: str) -> ConfigError:
    """Fail fast and say so plainly (CLAUDE.md section 6).

    Phase 0 has the Disabled adapters and three real ones. Enabling a module whose adapter is
    still unwritten is a configuration mistake, and finding out at startup is much better than
    finding out when a case reaches it.
    """
    return ConfigError(
        f"module {module!r} is enabled with adapter {adapter!r}, which is not built yet"
    )


def build_adapters(registry: Registry) -> Adapters:
    """Resolve each module's chosen adapter into an object.

    The registry decided *which* adapter; this decides *how* to build it. Keeping the two
    apart is what lets the kernel validate a combination without importing a single adapter.
    """
    evidence_adapter = registry.adapter_name("evidence")
    if evidence_adapter == "disabled":
        evidence: EvidenceSource = DisabledEvidenceSource()
    else:
        raise _not_built("evidence", evidence_adapter)

    gateway_adapter = registry.adapter_name("model_gateway")
    if gateway_adapter == "disabled":
        gateway: ModelGateway = DisabledModelGateway()
    else:
        raise _not_built("model_gateway", gateway_adapter)

    reasoning_adapter = registry.adapter_name("reasoning")
    if reasoning_adapter == "disabled":
        reasoner: Reasoner = DisabledReasoner()
        interpreter: ReplyInterpreter = DisabledReplyInterpreter()
        drafter: MessageDrafter = DisabledMessageDrafter()
    else:
        raise _not_built("reasoning", reasoning_adapter)

    screen_adapter = registry.adapter_name("input_screen")
    if screen_adapter == "rules_based":
        screen: InputScreen = RulesBasedInputScreen()
    elif screen_adapter == "disabled":
        screen = DisabledInputScreen()
    else:
        raise _not_built("input_screen", screen_adapter)

    tracing_adapter = registry.adapter_name("tracing")
    if tracing_adapter == "noop":
        tracer: Tracer = NoopTracer()
    else:
        raise _not_built("tracing", tracing_adapter)

    email_adapter = registry.adapter_name("email")
    if email_adapter == "disabled":
        email: MessageChannel = DisabledMessageChannel()
    else:
        raise _not_built("email", email_adapter)

    ticketing_adapter = registry.adapter_name("ticketing")
    if ticketing_adapter == "disabled":
        ticketing: TicketingSystem = DisabledTicketingSystem()
    else:
        raise _not_built("ticketing", ticketing_adapter)

    ledger_adapter = registry.adapter_name("ledger")
    if ledger_adapter == "disabled":
        ledger: Ledger = DisabledLedger()
    else:
        raise _not_built("ledger", ledger_adapter)

    approvals_adapter = registry.adapter_name("approvals")
    if approvals_adapter == "disabled":
        approvals: ApprovalService = DisabledApprovalService()
    else:
        raise _not_built("approvals", approvals_adapter)

    cross_case_adapter = registry.adapter_name("cross_case")
    if cross_case_adapter == "null":
        patterns: PatternContextProvider = NullPatternContextProvider()
        outcomes: CaseOutcomeObserver = NullOutcomeObserver()
    else:
        raise _not_built("cross_case", cross_case_adapter)

    return Adapters(
        evidence=evidence,
        model_gateway=gateway,
        reasoner=reasoner,
        reply_interpreter=interpreter,
        message_drafter=drafter,
        input_screen=screen,
        tracer=tracer,
        email=email,
        ticketing=ticketing,
        ledger=ledger,
        approvals=approvals,
        patterns=patterns,
        outcomes=outcomes,
    )


def build_application(
    config_dir: Path | None = None,
    app_env: str | None = None,
    settings: Settings | None = None,
    database: Path | None = None,
) -> Application:
    """Read configuration, validate it, wire it. Raises ConfigError and starts nothing.

    Logging is configured here, with the loaded secrets handed to the redaction processor
    (I12). It has to happen before anything else can log, and this is the only place that
    knows what the secrets are.
    """
    directory = DEFAULT_CONFIG_DIR if config_dir is None else config_dir
    loaded = Settings() if settings is None else settings
    env = loaded.app_env if app_env is None else app_env

    configure_logging(app_env=env, secrets=loaded.secret_values())

    registry = validate_modules(load_modules_file(directory), app_env=env)
    clock = SystemClock()
    ids = UlidIdGenerator()
    runtime = _build_runtime(directory, database, loaded, clock, ids) if database else None
    return Application(
        registry=registry,
        adapters=build_adapters(registry),
        app_env=env,
        settings=loaded,
        clock=clock,
        ids=ids,
        runtime=runtime,
    )


def create_app(
    config_dir: Path | None = None,
    app_env: str | None = None,
    database: Path | None = None,
) -> FastAPI:
    """The app factory. `make run-api` calls this; a test calls it with its own config_dir.

    Taking the directory as an argument rather than reading an environment variable is what
    lets a test run two differently-configured apps in one process without touching os.environ
    and without the order of tests mattering.
    """
    application = build_application(
        config_dir=config_dir, app_env=app_env, database=database or _default_database()
    )
    app = FastAPI(title="Weight dispute resolution agent", version="0.1.0")
    app.state.application = application
    app.include_router(build_router(application.registry, application.runtime))
    return app


def _default_database() -> Path | None:
    """`data/cases.sqlite3` if the migration has been run, else nothing.

    An app with no database still starts and still answers `/healthz`, which is what makes
    "is my configuration right?" checkable before any storage exists.
    """
    path = Path("data") / "cases.sqlite3"
    return path if path.is_file() else None


def _build_runtime(
    config_dir: Path, database: Path, settings: Settings, clock: Clock, ids: IdGenerator
) -> Runtime:
    """Wire storage, intake and the step loop. The only place any of this is assembled."""
    from datetime import timedelta

    from dispute_agent.infra.policy import load_policy
    from dispute_agent.infra.sqlite.engine import create_sqlite_engine, verify_pragmas
    from dispute_agent.infra.sqlite.unit_of_work import unit_of_work_factory
    from dispute_agent.infra.transitions import default_table
    from dispute_agent.kernel.orchestrator.intake import Intake
    from dispute_agent.kernel.orchestrator.orchestrator import (
        Orchestrator,
        phase_1_steps,
    )
    from dispute_agent.kernel.statemachine.machine import StateMachine
    from dispute_agent.modules.cross_case.null import (
        NullOutcomeObserver,
        NullPatternContextProvider,
    )

    engine = create_sqlite_engine(database)
    verify_pragmas(engine)

    # One source for the allowlist. It used to be read twice - intake from the environment,
    # R06 from a PolicyConfig nobody loaded - and the two could disagree (safety review,
    # finding 3). The addresses come from TEST_SELLER_EMAIL so a checked-in file can never
    # name a mailbox (I11), and are merged into the policy here.
    from_env = tuple(settings.test_seller_email.split(",")) if settings.test_seller_email else ()
    policy = load_policy(config_dir, allowed_recipients=from_env)

    intake = Intake(
        allowed_recipients=tuple(policy.messages.allowed_recipients),
        reopen_window=timedelta(days=policy.timers.reopen_window_days),
    )
    orchestrator = Orchestrator(
        StateMachine(default_table()),
        phase_1_steps(),
        clock,
        ids,
        patterns=NullPatternContextProvider(),
        outcomes=NullOutcomeObserver(),
        max_steps=policy.cases.max_steps_per_advance,
    )
    return Runtime(unit_of_work_factory(engine), intake, orchestrator, clock, ids)


# -- the model path (Phase 4P, ADR 0029) ------------------------------------------------------

#: Anchored to the project folder, not the working directory: run from anywhere else, a
#: relative `data/` would be a fresh, empty call log, and the spending cap would read $0.
PROMPTS_DIR = PROJECT_ROOT / "prompts"
FOUNDRY_LOCK = PROJECT_ROOT / "config" / "foundry_agents.lock.json"
PRICING = PROJECT_ROOT / "config" / "pricing.yaml"
MODEL_CALL_LOG = PROJECT_ROOT / "data" / "model_calls.sqlite3"
#: Names the signed-in account and tenant; no token (tokens are in the encrypted cache).
FOUNDRY_AUTH_RECORD = PROJECT_ROOT / "data" / "foundry_auth_record.json"


def build_model_policy(config_dir: Path, settings: Settings) -> PolicyConfig:
    """policy.yaml with the environment's spending settings applied (config.md section 7)."""
    from dispute_agent.infra.policy import load_policy, with_llm_overrides

    return with_llm_overrides(
        load_policy(config_dir),
        credit_expires_on=settings.azure_credit_expires_on,
        allow_paid_usage=settings.llm_allow_paid_usage,
        spend_cap_usd=settings.llm_primary_spend_cap_usd,
        secondary_cap_usd=settings.llm_secondary_spend_cap_usd,
    )


def build_model_gateway(
    adapter: str,
    *,
    settings: Settings,
    policy: PolicyConfig,
    clock: Clock,
    ids: IdGenerator,
    fake_answer: Callable[[ModelRequest], str | dict[str, object]] | None = None,
    log_path: Path = MODEL_CALL_LOG,
    secondary: str | None = None,
) -> ModelGateway:
    """The one guarded path to a model (I14, ADR 0017): the chosen route, inside the guard.

    No reasoner ever receives an unguarded gateway: this function has no other return.
    """
    from dispute_agent.infra.sqlite.model_call_log import open_model_call_log
    from dispute_agent.kernel.gateway.budgeted import BudgetedModelGateway

    if adapter == "fake":
        from dispute_agent.modules.model_gateway.fake.gateway import FakeModelGateway

        primary: ModelGateway = FakeModelGateway(responder=fake_answer)
        route = "fake"
    elif adapter == "foundry_agent_service":
        primary = build_foundry_gateway(settings, policy)
        route = "foundry_agent_service"
    elif adapter == "foundry_openai":
        primary = build_foundry_openai_gateway(settings, policy)
        route = "foundry_openai"
    else:
        raise _not_built("model_gateway", adapter)

    second: ModelGateway | None = None
    if secondary == "anthropic_direct":
        second = build_haiku_gateway(settings, policy)
    elif secondary is not None:
        raise ConfigError(f"the only failover route is anthropic_direct, not {secondary!r} (F01)")

    log_path.parent.mkdir(parents=True, exist_ok=True)
    return BudgetedModelGateway(
        primary=primary,
        route=route,
        ledger=open_model_call_log(log_path),
        limits=policy.llm,
        money=policy.llm_primary,
        clock=clock,
        ids=ids,
        secondary=second,
        secondary_cap_usd=policy.llm_secondary.spend_cap_usd,
    )


def build_haiku_gateway(settings: Settings, policy: PolicyConfig) -> ModelGateway:
    """The secondary: Haiku only, the one provider API key, read by the settings loader."""
    from dispute_agent.modules.model_gateway.anthropic_direct.gateway import (
        ROUTE,
        AnthropicDirectGateway,
    )
    from dispute_agent.modules.model_gateway.foundry_agent_service.pricing import load_price

    model = settings.anthropic_model_haiku
    return AnthropicDirectGateway.build(
        model=model,
        api_key=settings.anthropic_api_key,
        price=load_price(PRICING, ROUTE, model or ""),
        timeout_s=policy.llm.call_timeout_s,
    )


def build_foundry_openai_gateway(settings: Settings, policy: PolicyConfig) -> ModelGateway:
    from dispute_agent.modules.model_gateway.foundry_agent_service.pricing import load_price
    from dispute_agent.modules.model_gateway.foundry_agent_service.signin import (
        runtime_credential,
    )
    from dispute_agent.modules.model_gateway.foundry_openai.gateway import (
        FoundryOpenAiGateway,
    )

    if not settings.foundry_project_endpoint or not settings.foundry_model_deployment:
        raise ConfigError("FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL_DEPLOYMENT must be set")
    return FoundryOpenAiGateway.build(
        endpoint=settings.foundry_project_endpoint,
        deployment=settings.foundry_model_deployment,
        credential=runtime_credential(settings.azure_tenant_id, FOUNDRY_AUTH_RECORD),
        # Same deployment, same price row as the agent route.
        price=load_price(PRICING, "foundry_agent_service", settings.foundry_model_deployment),
        timeout_s=policy.llm.call_timeout_s,
    )


def build_foundry_gateway(settings: Settings, policy: PolicyConfig) -> ModelGateway:
    """Fails fast, naming what is missing, rather than at the first case (CLAUDE.md 6)."""
    from dispute_agent.modules.model_gateway.foundry_agent_service.client import (
        AzureAgentServiceClient,
    )
    from dispute_agent.modules.model_gateway.foundry_agent_service.gateway import (
        ROUTE,
        FoundryAgentServiceGateway,
    )
    from dispute_agent.modules.model_gateway.foundry_agent_service.lock import read_lock
    from dispute_agent.modules.model_gateway.foundry_agent_service.pricing import load_price

    if not settings.foundry_project_endpoint or not settings.foundry_model_deployment:
        raise ConfigError(
            "FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL_DEPLOYMENT must be set in .env "
            "(docs/SETUP_FOUNDRY.md)"
        )
    if policy.llm_primary.credit_expires_on is None and not policy.llm_primary.allow_paid_usage:
        raise ConfigError(
            "AZURE_CREDIT_EXPIRES_ON must be set for the Foundry route, so calls stop when "
            "the free credit ends (D18)"
        )
    from dispute_agent.modules.model_gateway.foundry_agent_service.signin import (
        check_tenant,
        runtime_credential,
    )

    check_tenant(settings.azure_tenant_id)
    if not FOUNDRY_AUTH_RECORD.is_file():
        raise ConfigError("not signed in to Foundry yet; run `make foundry-login`")
    return FoundryAgentServiceGateway(
        client=AzureAgentServiceClient(
            settings.foundry_project_endpoint,
            timeout_s=policy.llm.call_timeout_s,
            credential=runtime_credential(settings.azure_tenant_id, FOUNDRY_AUTH_RECORD),
        ),
        lock=read_lock(FOUNDRY_LOCK),
        price=load_price(PRICING, ROUTE, settings.foundry_model_deployment),
    )


def build_scenario_models(
    answer: Callable[[ModelRequest], str | dict[str, object]],
    policy: PolicyConfig,
    clock: Clock,
    ids: IdGenerator,
) -> ScenarioModels:
    """The scenario suite's model path: recorded answers behind the real spending guard.

    Built here because this is the only file allowed to construct a route to a model
    (tests/architecture/test_guarded_model_path.py). `answer` is the scripted model; a fault
    it names ("unavailable") on the primary makes a second, healthy fake the failover route,
    so F01 is exercised for real (S23a).
    """
    import tempfile

    from dispute_agent.infra.sqlite.model_call_log import open_model_call_log
    from dispute_agent.kernel.gateway.budgeted import BudgetedModelGateway
    from dispute_agent.modules.model_gateway.fake.gateway import FakeModelGateway
    from dispute_agent.modules.reasoning.llm.interpreter import LlmReplyInterpreter
    from dispute_agent.modules.reasoning.llm.reasoner import LlmReasoner
    from dispute_agent.modules.reasoning.prompt_registry import FilePromptRegistry

    down = getattr(answer, "fault", None) == "unavailable"
    primary = FakeModelGateway(responder=(lambda _req: "unavailable") if down else answer)
    secondary = FakeModelGateway(responder=answer) if down else None
    log = Path(tempfile.mkdtemp(prefix="scenario-calls-")) / "calls.sqlite3"
    gateway = BudgetedModelGateway(
        primary=primary,
        route="fake",
        ledger=open_model_call_log(log),
        limits=policy.llm,
        money=policy.llm_primary,
        clock=clock,
        ids=ids,
        secondary=secondary,
        secondary_cap_usd=policy.llm_secondary.spend_cap_usd,
    )
    registry = FilePromptRegistry(PROMPTS_DIR, allow_inactive=True)
    from dispute_agent.api.scenario_runner import ScenarioModels

    return ScenarioModels(
        gateway=gateway,
        reasoner=LlmReasoner(prompts=registry, gateway=gateway, ids=ids, clock=clock),
        interpreter=LlmReplyInterpreter(prompts=registry, gateway=gateway),
        secondary=secondary,
    )


def build_llm_reasoner(
    gateway: ModelGateway,
    *,
    settings: Settings,
    policy: PolicyConfig,
    clock: Clock,
    ids: IdGenerator,
) -> Reasoner:
    from dispute_agent.modules.reasoning.llm.reasoner import PROMPT, LlmReasoner
    from dispute_agent.modules.reasoning.prompt_registry import FilePromptRegistry

    registry = FilePromptRegistry(PROMPTS_DIR, allow_inactive=settings.prompts_allow_inactive)
    status = str(registry.meta(PROMPT, "v1").get("status", "inactive")).split()[0]
    if status != "active" and not settings.prompts_allow_inactive:
        # Fail at start-up, not on the first case (review, M4).
        raise ConfigError(
            f"prompt {PROMPT}/v1 is {status}: set PROMPTS_ALLOW_INACTIVE=true only to "
            "knowingly test an un-evaluated prompt (docs/SETUP_FOUNDRY.md)"
        )
    return LlmReasoner(
        prompts=registry,
        gateway=gateway,
        ids=ids,
        clock=clock,
        max_output_tokens=policy.llm.max_output_tokens,
    )
