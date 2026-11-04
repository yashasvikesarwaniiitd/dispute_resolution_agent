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
from typing import TYPE_CHECKING, Any

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
    from collections.abc import AsyncIterator, Callable, Mapping

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


#: An owner-only override of modules.yaml, git-ignored: lets one machine run a real channel
#: (for example `ticketing: jira_cloud`) while the shared file - and CI, which has no
#: accounts - keeps the fakes. docs/MODULES.md.
LOCAL_MODULES = "modules.local.yaml"


def load_modules_file(config_dir: Path) -> ModulesFile:
    local = config_dir / LOCAL_MODULES
    path = local if local.is_file() else config_dir / "modules.yaml"
    if not path.is_file():
        raise ConfigError(f"no modules.yaml at {path}")
    return ModulesFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _seed_charges(ledger: Any) -> None:
    """The synthetic world's charges, so the simulated ledger has what was billed (R16).

    Idempotent (INSERT OR IGNORE on the transaction id): a restart seeds nothing twice.
    """
    from dispute_agent.kernel.domain.values import Money
    from dispute_agent.modules.evidence.synthetic_shipments.fixtures import FIXTURES, fixture

    for name in FIXTURES:
        for entry in fixture(name).shipment.ledger:
            if entry.kind == "DEBIT":
                ledger.seed_debit(
                    entry.shipment_ref,
                    Money.from_paise(entry.amount_paise),
                    entry.reason_code or "weight_charge",
                    entry.transaction_id,
                )


def _needs_accounts(module: str, adapter: str) -> ConfigError:
    """A real channel needs the owner's own test accounts, never built from defaults (I11)."""
    return ConfigError(
        f"module {module!r} with adapter {adapter!r} needs the owner's test account; follow "
        "docs/SETUP_CHANNELS.md, or use the fake adapter"
    )


def _not_built(module: str, adapter: str) -> ConfigError:
    """Fail fast and say so plainly (CLAUDE.md section 6).

    Phase 0 has the Disabled adapters and three real ones. Enabling a module whose adapter is
    still unwritten is a configuration mistake, and finding out at startup is much better than
    finding out when a case reaches it.
    """
    return ConfigError(
        f"module {module!r} is enabled with adapter {adapter!r}, which is not built yet"
    )


def _build_gmail(settings: Settings | None) -> MessageChannel:
    """The owner's pilot Gmail (ADR 0038). Refuses without every setting and a sign-in (I11)."""
    from dispute_agent.modules.email.gmail_api.auth import GmailAuth
    from dispute_agent.modules.email.gmail_api.channel import GmailChannel

    secret = None if settings is None else settings.gmail_client_secret
    if settings is None or not (settings.gmail_client_id and secret and settings.gmail_mailbox):
        raise _needs_accounts("email", "gmail_api")
    auth = GmailAuth(
        client_id=settings.gmail_client_id,
        client_secret=secret.get_secret_value(),
        cache_path=settings.gmail_token_cache_path,
        timeout_s=settings.http_timeout_s,
    )
    # Not signed in still builds: sends then fail as reauth_required and cases reach a person,
    # exactly as config.md describes for outlook_graph. `make gmail-login` fixes it.
    return GmailChannel(
        auth=auth, mailbox=settings.gmail_mailbox, timeout_s=settings.http_timeout_s
    )


def _build_jira(settings: Settings | None) -> TicketingSystem:
    """The owner's real Jira test project (ADR 0020). Refuses without every setting (I11)."""
    from dispute_agent.modules.ticketing.jira_cloud.ticketing import JiraCloudTicketing

    token = None if settings is None else settings.jira_api_token
    if settings is None or not (
        settings.jira_base_url and settings.jira_email and token and settings.jira_project_key
    ):
        raise _needs_accounts("ticketing", "jira_cloud")
    return JiraCloudTicketing(
        base_url=settings.jira_base_url,
        email=settings.jira_email,
        api_token=token.get_secret_value(),
        project_key=settings.jira_project_key,
        timeout_s=settings.http_timeout_s,
    )


def shipment_keys(evidence: Any) -> Callable[[str], Mapping[str, str]]:
    """A shipment's cluster keys (scanner, hub, SKU), from the evidence adapter (P7-02).

    Modules never import each other, so the bridge is built here. An evidence adapter that
    cannot name a shipment's keys raises LookupError for every shipment: the pattern provider
    turns that into caution, never into "no patterns".
    """
    keys_for = getattr(evidence, "keys_for", None)
    if callable(keys_for):
        return keys_for  # type: ignore[no-any-return]

    def unknown(shipment_ref: str) -> Mapping[str, str]:
        raise LookupError("the evidence adapter gives no shipment keys")

    return unknown


def pattern_engine(database: Path | None, *, reader: bool = False) -> Any:
    """The case database, with the cross-case tables (migration 0002, idempotent).

    `reader=True`: a second engine whose transactions take no write lock, for the pattern
    lookup that runs inside a case's open transaction (see `create_sqlite_engine`).
    """
    import tempfile

    from dispute_agent.infra.sqlite.engine import (
        apply_schema,
        create_sqlite_engine,
        schema_sql_path,
    )
    from dispute_agent.infra.sqlite.patterns import apply_pattern_schema

    if database is None:
        # A configuration check: a throwaway database, never data/.
        path = Path(tempfile.mkdtemp(prefix="patterns-")) / "cases.sqlite3"
        engine = create_sqlite_engine(path)
        apply_schema(engine, schema_sql_path().read_text(encoding="utf-8"))
    else:
        engine = create_sqlite_engine(database)
    apply_pattern_schema(engine)
    if reader:
        return create_sqlite_engine(str(engine.url.database), deferred=True)
    return engine


def _build_cross_case(
    evidence: Any, database: Path | None, clock: Clock, *, min_related_cases: int
) -> tuple[PatternContextProvider, CaseOutcomeObserver]:
    from dispute_agent.modules.cross_case.analyzer.observer import SqlOutcomeObserver
    from dispute_agent.modules.cross_case.sql_context import (
        PatternRepository,
        SqlPatternContextProvider,
    )

    keys = shipment_keys(evidence)
    reads = PatternRepository(pattern_engine(database, reader=True))
    writes = PatternRepository(pattern_engine(database))
    return (
        SqlPatternContextProvider(reads, keys, min_related_cases=min_related_cases),
        SqlOutcomeObserver(writes, keys, clock),
    )


def _pattern_analyst(
    registry: Registry,
    modules: Mapping[str, bool],
    settings: Settings,
    policy: PolicyConfig,
    clock: Clock,
    ids: IdGenerator,
) -> Any:
    """The analyst when the model route is on (D57); None otherwise - patterns then keep
    their code-written summary, and nothing else changes."""
    if not modules.get("model_gateway"):
        return None
    route = registry.adapter_name("model_gateway")
    if route == "foundry_agent_service":
        route = "foundry_openai"  # the analyst calls the deployment; it has no Foundry agent
    return build_pattern_analyst(settings, policy, clock, ids, route=route)


def build_cross_case_job(
    *,
    engine: Any,
    adapters: Adapters,
    policy: PolicyConfig,
    clock: Clock,
    ids: IdGenerator,
    modules: Mapping[str, bool],
    kill_switch: Any,
    analyst: Any = None,
) -> Any:
    """The scheduled cross-case analysis (P7-03 to P7-09). The analyzer holds no write port;
    the investigation goes through the kernel's PolicyEngine and ActionExecutor (I2)."""
    from dispute_agent.kernel.executor.executor import ActionExecutor
    from dispute_agent.kernel.policy.engine import PolicyEngine
    from dispute_agent.kernel.policy.rules import phase_5_rules
    from dispute_agent.modules.cross_case.analyzer import PatternAnalyzer, Thresholds
    from dispute_agent.modules.cross_case.analyzer.confirmation import (
        ConfirmationRule,
        FindingsReader,
    )
    from dispute_agent.modules.cross_case.analyzer.investigations import (
        Investigations,
        PatternActionStore,
    )
    from dispute_agent.modules.cross_case.analyzer.job import CrossCaseJob
    from dispute_agent.modules.cross_case.analyzer.recurrence import RecurrenceMonitor
    from dispute_agent.modules.cross_case.sql_context import PatternRepository
    from dispute_agent.modules.ticketing.write_port import IssueWritePort

    repository = PatternRepository(engine)
    keys = shipment_keys(adapters.evidence)
    thresholds = Thresholds(
        min_related_cases=policy.patterns.min_related_cases,
        window_days=policy.patterns.window_days,
    )
    store = PatternActionStore(engine)
    project = getattr(adapters.ticketing, "project_key", "WD")
    investigations = Investigations(
        engine=engine,
        repository=repository,
        executor=ActionExecutor(PolicyEngine(phase_5_rules(), clock, ids), store, kill_switch),
        actions=store,
        port=lambda subject: IssueWritePort(adapters.ticketing, subject, project),
        ids=ids,
        policy=policy,
        modules=modules,
    )
    rule = ConfirmationRule(repository)
    return CrossCaseJob(
        analyzer=PatternAnalyzer(repository, keys, ids, thresholds),
        investigations=investigations,
        confirmation=rule,
        findings=FindingsReader(rule, adapters.ticketing.fetch_findings, investigations.issue_key),
        recurrence=RecurrenceMonitor(repository, keys, thresholds),
        analyst=analyst,
    )


def build_adapters(
    registry: Registry,
    *,
    clock: Clock | None = None,
    ids: IdGenerator | None = None,
    data_dir: Path | None = None,
    database: Path | None = None,
    min_related_cases: int = 3,
    settings: Settings | None = None,
) -> Adapters:
    """Resolve each module's chosen adapter into an object.

    The registry decided *which* adapter; this decides *how* to build it. Keeping the two
    apart is what lets the kernel validate a combination without importing a single adapter.
    """
    clock = clock or SystemClock()
    ids = ids or UlidIdGenerator()
    # Without a database (a configuration check) the simulated ledger goes to a fresh temp
    # folder, never under data/, so a check cannot touch the real files.
    if data_dir is None:
        import tempfile

        data = Path(tempfile.mkdtemp(prefix="adapters-"))
    else:
        data = data_dir

    evidence_adapter = registry.adapter_name("evidence")
    if evidence_adapter == "disabled":
        evidence: EvidenceSource = DisabledEvidenceSource()
    elif evidence_adapter == "synthetic_shipments":
        from dispute_agent.modules.evidence.synthetic_shipments.library import (
            SyntheticLibrarySource,
        )

        evidence = SyntheticLibrarySource(clock=clock)
    else:
        raise _not_built("evidence", evidence_adapter)

    gateway_adapter = registry.adapter_name("model_gateway")
    if gateway_adapter == "disabled":
        gateway: ModelGateway = DisabledModelGateway()
    else:
        raise _not_built("model_gateway", gateway_adapter)

    reasoning_adapter = registry.adapter_name("reasoning")
    interpreter: ReplyInterpreter = DisabledReplyInterpreter()
    drafter: MessageDrafter = DisabledMessageDrafter()
    if reasoning_adapter == "disabled":
        reasoner: Reasoner = DisabledReasoner()
    elif reasoning_adapter == "rules_only":
        from dispute_agent.modules.reasoning.rules_only.reasoner import RulesOnlyReasoner
        from dispute_agent.packs.weight_dispute.pack import WeightDisputePack

        reasoner = RulesOnlyReasoner(pack=WeightDisputePack(), ids=ids, clock=clock)
    else:
        # `llm` needs the model route built with settings and policy; the runtime builds it
        # (build_model_gateway / build_llm_reasoner), never an unguarded one here (I14).
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
    elif email_adapter == "fake":
        from dispute_agent.modules.email.fake.channel import FakeMessageChannel

        email = FakeMessageChannel()
    elif email_adapter == "gmail_api":
        email = _build_gmail(settings)
    else:
        raise _needs_accounts("email", email_adapter)

    ticketing_adapter = registry.adapter_name("ticketing")
    if ticketing_adapter == "disabled":
        ticketing: TicketingSystem = DisabledTicketingSystem()
    elif ticketing_adapter == "fake":
        from dispute_agent.modules.ticketing.fake.ticketing import FakeTicketingSystem

        ticketing = FakeTicketingSystem()
    elif ticketing_adapter == "jira_cloud":
        ticketing = _build_jira(settings)
    else:
        raise _needs_accounts("ticketing", ticketing_adapter)

    ledger_adapter = registry.adapter_name("ledger")
    if ledger_adapter == "disabled":
        ledger: Ledger = DisabledLedger()
    elif ledger_adapter == "simulated":
        from dispute_agent.modules.ledger.simulated.ledger import SimulatedLedger

        ledger = SimulatedLedger(data / "ledger.sqlite3", clock=clock.now)
        if evidence_adapter == "synthetic_shipments":
            _seed_charges(ledger)
    else:
        raise _not_built("ledger", ledger_adapter)

    approvals_adapter = registry.adapter_name("approvals")
    if approvals_adapter not in ("disabled", "store_backed"):
        raise _not_built("approvals", approvals_adapter)
    # store_backed: approvals live in the case database and the review console decides them
    # there (ADR 0018); the line writes them through its unit of work. The notify/withdraw
    # port is not called by the line yet (BACKLOG), so nothing is wired behind it.
    approvals: ApprovalService = DisabledApprovalService()

    cross_case_adapter = registry.adapter_name("cross_case")
    if cross_case_adapter == "null":
        patterns: PatternContextProvider = NullPatternContextProvider()
        outcomes: CaseOutcomeObserver = NullOutcomeObserver()
    elif cross_case_adapter == "sql_context":
        patterns, outcomes = _build_cross_case(
            evidence, database, clock, min_related_cases=min_related_cases
        )
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
    data_dir = database.parent if database else None
    min_related = 3
    if (
        registry.adapter_name("cross_case") == "sql_context"
        and (directory / "policy.yaml").is_file()
    ):
        from dispute_agent.infra.policy import load_policy

        min_related = load_policy(directory).patterns.min_related_cases
    adapters = build_adapters(
        registry,
        clock=clock,
        ids=ids,
        data_dir=data_dir,
        database=database,
        min_related_cases=min_related,
        settings=loaded,
    )
    runtime = (
        _build_runtime(directory, database, loaded, clock, ids, adapters, registry)
        if database
        else None
    )
    return Application(
        registry=registry,
        adapters=adapters,
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
    app = FastAPI(
        title="Weight dispute resolution agent",
        version="0.1.0",
        lifespan=_runner_lifespan(application),
    )
    app.state.application = application
    app.include_router(build_router(application.registry, application.runtime))
    return app


#: Seconds between runner ticks (config.md section 8: runner.tick_seconds).
TICK_SECONDS = 5.0
#: Consecutive failed ticks after which the runner is reported stalled (about a minute).
STALLED_AFTER = 12


def tick_once(runner: Any, failures: int) -> int:
    """One tick; returns the count of consecutive failures (0 after a good tick).

    A tick that raises is logged by exception type (never its text, I12) and counted: a
    runner that keeps failing fires no timer and advances no case, so it must be loud
    (Phase 6 safety review, MED-1) rather than silently retried forever.
    """
    import structlog

    log = structlog.get_logger(__name__)
    try:
        runner.tick()
    except Exception as failed:
        failures += 1
        log.error("runner_tick_failed", error=type(failed).__name__, consecutive=failures)
        if failures == STALLED_AFTER:
            log.critical("runner_stalled", consecutive=failures)
        return failures
    return 0


def _runner_lifespan(application: Application) -> Any:
    """Start-up recovery, then a tick every few seconds, for as long as the app runs.

    orchestrator.md sections 6 and 7: timers are rows, so this loop is what makes them fire;
    recovery runs first, before the first tick. Off under `APP_ENV=test`, where a test calls
    `tick()` itself with a fake clock and a background loop would race it.
    """
    import asyncio
    import contextlib

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        runtime = application.runtime
        if runtime is None or application.app_env == "test":
            yield
            return
        await asyncio.to_thread(runtime.runner.recover)
        stop = asyncio.Event()

        async def loop() -> None:
            failures = 0
            while not stop.is_set():
                failures = await asyncio.to_thread(tick_once, runtime.runner, failures)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)

        task = asyncio.create_task(loop())
        try:
            yield
        finally:
            stop.set()
            await task

    return lifespan


def _default_database() -> Path | None:
    """`data/cases.sqlite3` if the migration has been run, else nothing.

    An app with no database still starts and still answers `/healthz`, which is what makes
    "is my configuration right?" checkable before any storage exists.
    """
    path = Path("data") / "cases.sqlite3"
    return path if path.is_file() else None


def _build_runtime(
    config_dir: Path,
    database: Path,
    settings: Settings,
    clock: Clock,
    ids: IdGenerator,
    adapters: Adapters,
    registry: Registry,
) -> Runtime:
    """Wire storage, intake, the whole line and the runner. The only place this is assembled.

    The same `Line` the scenario suite and the demo use (api/line.py), so the running app and
    the tests cannot drift apart. Modules switched off are switched off here too: the line
    gives a disabled source or reasoner, and the case goes to a person naming the module (I6).
    """
    from datetime import timedelta

    from dispute_agent.api.line import Line
    from dispute_agent.api.runner import Runner
    from dispute_agent.infra.policy import load_policy, pattern_call_limit, with_pattern_budget
    from dispute_agent.infra.sqlite.control_flags import SqliteKillSwitch
    from dispute_agent.infra.sqlite.engine import create_sqlite_engine, verify_pragmas
    from dispute_agent.infra.sqlite.unit_of_work import unit_of_work_factory
    from dispute_agent.kernel.domain.enums import ActionType
    from dispute_agent.kernel.orchestrator.intake import Intake
    from dispute_agent.modules.email.write_port import MessageWritePort
    from dispute_agent.modules.ledger.write_port import CreditWritePort
    from dispute_agent.modules.ticketing.write_port import IssueWritePort
    from dispute_agent.packs.weight_dispute.pack import WeightDisputePack

    engine = create_sqlite_engine(database)
    verify_pragmas(engine)

    # One source for the allowlist. It used to be read twice - intake from the environment,
    # R06 from a PolicyConfig nobody loaded - and the two could disagree (safety review,
    # finding 3). The addresses come from TEST_SELLER_EMAIL so a checked-in file can never
    # name a mailbox (I11), and are merged into the policy here.
    from_env = tuple(settings.test_seller_email.split(",")) if settings.test_seller_email else ()
    policy = load_policy(config_dir, allowed_recipients=from_env)
    cross_case_on = registry.adapter_name("cross_case") == "sql_context"
    policy = with_pattern_budget(
        policy, cross_case_on=cross_case_on, limit=pattern_call_limit(config_dir)
    )

    intake = Intake(
        allowed_recipients=tuple(policy.messages.allowed_recipients),
        reopen_window=timedelta(days=policy.timers.reopen_window_days),
    )

    def ports(case: Any, action_type: ActionType) -> Any:
        if action_type is ActionType.SEND_MESSAGE:
            return MessageWritePort(adapters.email, case.id)
        if action_type is ActionType.UPSERT_ISSUE:
            project = getattr(adapters.ticketing, "project_key", "WD")
            return IssueWritePort(adapters.ticketing, case.id, project)
        return CreditWritePort(adapters.ledger, case.id)

    modules = {m.name: m.enabled for m in registry.all()}
    line = Line(
        pack=WeightDisputePack(),
        policy=policy,
        clock=clock,
        ids=ids,
        ports=ports,
        sources=lambda: {"evidence": adapters.evidence},
        reasoner=adapters.reasoner if modules.get("reasoning") else None,
        ledger=adapters.ledger if modules.get("ledger") else None,
        modules=modules,
        kill_switch=SqliteKillSwitch(engine, forced=settings.kill_switch),
        # HOOK A: the real provider when cross_case is on; None is exactly no patterns.
        patterns=adapters.patterns if cross_case_on else None,
    )
    jobs: list[Any] = []
    if cross_case_on:
        from dispute_agent.infra.sqlite.patterns import apply_pattern_schema

        apply_pattern_schema(engine)
        jobs.append(
            build_cross_case_job(
                engine=engine,
                adapters=adapters,
                policy=policy,
                clock=clock,
                ids=ids,
                modules=modules,
                kill_switch=line.kill_switch,
                analyst=_pattern_analyst(registry, modules, settings, policy, clock, ids),
            )
        )
    runner = Runner(engine, line, observer=adapters.outcomes, jobs=jobs)
    return Runtime(unit_of_work_factory(engine), intake, line, runner, clock, ids)


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
    from dispute_agent.modules.reasoning.llm.two_pass import TwoPassReasoner
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
        # Always behind the anchoring control (ADR 0025): one pass when a briefing has no
        # patterns, two when it has (safety review, Phase 7, MED-1).
        reasoner=TwoPassReasoner(
            LlmReasoner(prompts=registry, gateway=gateway, ids=ids, clock=clock)
        ),
        interpreter=LlmReplyInterpreter(prompts=registry, gateway=gateway),
        secondary=secondary,
    )


def build_live_models(
    settings: Settings, policy: PolicyConfig, clock: Clock, ids: IdGenerator
) -> ScenarioModels:
    """The real Foundry agent behind the spending guard, for live evals and the red-team run.

    Reasoner and reply interpreter share the one guarded gateway, so every call counts against
    the same per-case and lifetime caps (ADR 0017) and lands in the same call log.
    """
    from dispute_agent.api.scenario_runner import ScenarioModels
    from dispute_agent.modules.reasoning.llm.interpreter import LlmReplyInterpreter
    from dispute_agent.modules.reasoning.prompt_registry import FilePromptRegistry

    gateway = build_model_gateway(
        "foundry_agent_service", settings=settings, policy=policy, clock=clock, ids=ids
    )
    reasoner = build_llm_reasoner(gateway, settings=settings, policy=policy, clock=clock, ids=ids)
    registry = FilePromptRegistry(PROMPTS_DIR, allow_inactive=settings.prompts_allow_inactive)
    return ScenarioModels(
        gateway=gateway,
        reasoner=reasoner,
        interpreter=LlmReplyInterpreter(prompts=registry, gateway=gateway),
    )


#: The analyst summarises up to 12 cases into 2-4 hypotheses; at 4,000 the reasoning model
#: still ran out mid-answer on the larger clusters (second live run).
ANALYST_OUTPUT_TOKENS = 8000


def build_pattern_analyst(
    settings: Settings,
    policy: PolicyConfig,
    clock: Clock,
    ids: IdGenerator,
    *,
    route: str = "foundry_openai",
) -> Any:
    """The pattern analyst behind the spending guard (P7-04, I14).

    `foundry_openai` calls the same gpt-5-mini deployment directly, so no Foundry agent has to
    exist for this prompt (creating one is the owner's step, `make agents-sync`). Inactive
    prompts load only when PROMPTS_ALLOW_INACTIVE is set - the eval run, not the app.
    """
    from dispute_agent.modules.cross_case.analyzer.hypotheses import PatternAnalyst
    from dispute_agent.modules.reasoning.prompt_registry import FilePromptRegistry

    gateway = build_model_gateway(route, settings=settings, policy=policy, clock=clock, ids=ids)
    registry = FilePromptRegistry(PROMPTS_DIR, allow_inactive=settings.prompts_allow_inactive)
    # The policy's output budget, not the prompt's 1,200: a reasoning model spends most of
    # the budget thinking, and 1,200 cut every answer off mid-JSON (first live run, 0 of 6).
    return PatternAnalyst(
        registry,
        gateway,
        max_output_tokens=max(policy.llm.max_output_tokens, ANALYST_OUTPUT_TOKENS),
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
    reasoner = LlmReasoner(
        prompts=registry,
        gateway=gateway,
        ids=ids,
        clock=clock,
        max_output_tokens=policy.llm.max_output_tokens,
    )
    from dispute_agent.modules.reasoning.llm.two_pass import TwoPassReasoner

    # P7-06, always: with no patterns in the briefing this is the single pass it wraps, so
    # no route can reach a patterned case without the anchoring control (MED-1).
    return TwoPassReasoner(reasoner)
