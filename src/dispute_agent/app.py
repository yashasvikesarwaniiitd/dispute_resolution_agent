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

DEFAULT_CONFIG_DIR = Path("config")


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
    runtime = _build_runtime(database, loaded, clock, ids) if database else None
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


def _build_runtime(database: Path, settings: Settings, clock: Clock, ids: IdGenerator) -> Runtime:
    """Wire storage, intake and the step loop. The only place any of this is assembled."""
    from datetime import timedelta

    from dispute_agent.infra.sqlite.engine import create_sqlite_engine, verify_pragmas
    from dispute_agent.infra.sqlite.unit_of_work import unit_of_work_factory
    from dispute_agent.infra.transitions import default_table
    from dispute_agent.kernel.orchestrator.intake import Intake
    from dispute_agent.kernel.orchestrator.orchestrator import (
        Orchestrator,
        phase_1_steps,
    )
    from dispute_agent.kernel.policy.config import PolicyConfig
    from dispute_agent.kernel.statemachine.machine import StateMachine
    from dispute_agent.modules.cross_case.null import (
        NullOutcomeObserver,
        NullPatternContextProvider,
    )

    engine = create_sqlite_engine(database)
    verify_pragmas(engine)
    policy = PolicyConfig()

    allowed = (
        tuple(settings.test_seller_email.split(","))
        if settings.test_seller_email
        else tuple(policy.messages.allowed_recipients)
    )
    intake = Intake(
        allowed_recipients=allowed,
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
