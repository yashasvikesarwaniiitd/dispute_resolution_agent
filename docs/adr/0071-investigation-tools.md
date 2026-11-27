# 0071 — Five investigation tools, each in the module that owns its data

Date: 2026-11-27
Status: **accepted** (owner, 2026-11-27: "can you make these 5 tools modularized?"). Kernel change: one port, its data types and the Toolbox.

## The problem

The plan is a hybrid agent:
- code still gathers the basic evidence and does every calculation;
- the agent chooses extra look-ups where judgement matters.

Those look-ups had no common shape. The logic existed but was not callable on demand:
- past shipments: a comparison computed once, in the catalogue;
- past disputes: pattern matching inside the cross-case module;
- the photo check: a step that runs only after a person's Return;
- policy: an index private to the Ask assistant.

Scanner statistics did not exist.

## Decision

**One port, one result shape** (`kernel/ports/investigation_tool.py`, `kernel/domain/tools.py`):
- `spec`: name, description, argument schema;
- `run(ctx, args) -> ToolOutput`.

`ToolContext` carries only the case the tool is called for, plus the evidence code already gathered. A tool cannot be pointed at another case (ADR 0058).

**Each tool lives in the module that owns its data**, so modules stay independent (import-linter) and need no new module:

| Tool | Module | Reads |
|---|---|---|
| `search_past_shipments` | evidence (`synthetic_shipments/tools.py`) | this seller's earlier shipments, and code's comparison (ADR 0060) |
| `get_scanner_statistics` | evidence (same file) | this scanner's earlier readings against the hub's other scanners (new) |
| `search_past_disputes` | cross_case (`sql_context/tools.py`) | earlier cases sharing seller, scanner, hub or SKU, and how they ended |
| `analyze_parcel_image` | image_check (`tool.py`) | the photo, through the configured `ImageChecker` (paid with the vision adapter) |
| `lookup_policy` | reasoning (`policy_tool.py`) | the local policy index (ADR 0040) |

**The Toolbox** (`kernel/orchestrator/toolbox.py`) holds the tools by name. A call never raises: an unknown tool, bad arguments or a crash come back as `ok=False` with a code (I4). `app.build_toolbox` assembles it from the modules that are on, and the runtime holds it as `runtime.tools`.

## Rules every tool keeps

- **Read-only.** No write port, channel or store write (I1).
- **Code computes.** Every figure is computed in code and returned as a string (I9). A model's or a person's words are `UntrustedText` (I5): image observations are; policy passages are the project's own documents.
- **No future.** Past shipments and scanner statistics count only shipments measured before this one.
- **No other seller named.** Past shipments are this seller's only. Scanner statistics are counts. Past disputes list this seller's and only count other sellers' by outcome.

## Not done here

- **Not yet called by the agent.** Wiring the Toolbox into the agent loop changes the kernel's "code fetches all evidence every round" and "one choice per round" rules. It needs its own ADR, and a measured comparison against today's engine before it becomes the default.
- **[Gap]** Past shipments and scanner statistics read the seller catalogue only. Named eval fixtures and shipments added on the console answer `not_in_catalogue`.
- **[Assumption]** "Bigger than declared" means measured volume over 1.25x declared, the same factor the history comparison uses (`ANOMALY_FACTOR`).
- Showing past outcomes can anchor a model. The kernel's caution rules still apply, and code still computes every amount.

Tests: `tests/unit/tools/`.
