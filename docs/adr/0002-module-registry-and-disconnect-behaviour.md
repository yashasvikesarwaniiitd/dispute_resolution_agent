# ADR 0002: Module registry, and what happens when a module is not there

Status: accepted (owner, 2026-11-03)
Date: 2026-11-03

## Decision
Features are modules, switched on or off in `config/modules.yaml`. Startup validates the combination and refuses to run an unsafe one, naming every fault at once. A module that is off is wired to an adapter whose every method raises `ModuleUnavailable(<name>)`, and the case reaches a person with the reason `module_unavailable:<name>` (I6).

Two modules are the exception, and both for the same reason:

| Module | Off behaviour | Why it is not an exception to I6 |
|--------|---------------|----------------------------------|
| `cross_case` | `Null` adapter: no patterns, no signal | Cross-case intelligence may only ever add caution (I7). Its absence removes nothing, so a case with no patterns is simply one decided on its own evidence — the normal state of affairs through Phase 6 |
| `tracing` | No-op tracer | Observability must not change what the system decides. The `Tracer` port's own contract says a tracer never raises, so a raising adapter would break the port it implements |

Everything else that is off raises, including `input_screen`.

## Why
In plain terms: a feature that is switched off must never look like a feature that found nothing.

That distinction is the whole decision, and `input_screen` is where it bites hardest. A disabled screen returning a clean result with no flags would be the single most dangerous adapter in this project, because downstream a clean screen *means something*: this text was looked at and found ordinary. I15's asymmetry — a flag adds caution, clean relaxes nothing — only holds if "clean" is true. So a screen that is not there raises, and the investigate step turns that into a `screen_flag` caution signal at minimum `REQUIRE_APPROVAL` (`orchestrator.md` section 5). Losing the screen makes the system more careful, not less.

The same reasoning runs through the others. A disabled evidence source returning `MISSING` would read as "we looked and there was nothing there", which is a fact about the shipment rather than about our configuration. A disabled ledger returning no entries would read as "nothing has been charged", and R16's cap on a credit is computed from exactly those entries.

The registry validates at startup rather than on first use because a combination like `ledger` enabled with `approvals` disabled is I3 defeated by configuration: credits would have somewhere to post and nowhere to be approved from, and no care taken downstream can close a hole opened in a YAML file. Finding that out at startup costs nothing; finding it out on a live case costs a credit nobody authorised.

## Options considered
- **Skip a disabled module and carry on.** The behaviour most systems have by default. Rejected outright by I6: silent degradation is how a case gets decided on less evidence than anyone believes it had.
- **Return a neutral result from a disabled module.** Cheaper to write and fatal for `input_screen` and `evidence`, for the reasons above. Neutral is a claim, and a module that is not running is not entitled to make one.
- **Validate lazily, when a module is first used.** Rejected under "fail fast at startup, fail safe at runtime" (CLAUDE.md section 6). An unsafe combination should stop the app, not the case.
- **Adapter factories in the registry**, as the `ARCHITECTURE.md` section 5 sketch has them. Not possible: factories live in `modules/`, and the kernel may not import them. The catalogue declares adapters by name and the composition root maps names to factories — which also means Phase 7 adds no row for `sql_context`, because the row is already there (D3).

## Consequences
Easier: every "module disabled" scenario (S05b, S20, S41) has one path and one reason code. Switching a module off in a test is a configuration line rather than a mock. `/healthz` reports what is actually wired, which for a disabled module is its `Disabled` adapter and not the adapter named in the file.

Harder: every module port needs a `Disabled` adapter, and adding a method to a port means adding it there too. The parametrised test over every adapter and every port method is what keeps that honest as the ports grow.

Watch: the two exceptions above are the only ones. A third would need an argument of the same shape — that the module's absence genuinely removes nothing a decision depends on — and a new ADR. `ARCHITECTURE.md` section 5 currently names only `cross_case` as an exception and should mention `tracing` when it is next opened; `orchestrator.md` section 5 is the authority and already does.

## Sources
Project documents only: `CLAUDE.md` sections 4 and 6, `docs/ARCHITECTURE.md` section 5, `docs/spec/orchestrator.md` section 5, `docs/spec/config.md` section 2. No external documentation was needed.
