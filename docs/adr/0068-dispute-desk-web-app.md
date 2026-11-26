# 0068 — Dispute Desk: a web workspace beside the Streamlit console

Date: 2026-11-27
Status: accepted. The owner asked for the "Dispute Workspace v2" design handoff to be built as designed.

## Context

The design handoff (Dispute Workspace v2) replaces the five Streamlit screens with one inbox-style workspace: a views rail, a case list, and a case view that changes with the kind of work (a credit, an email, a handover, a parked case), with keyboard shortcuts, per-case drafts and live updates. The handoff itself says Streamlit cannot deliver that layout or interaction model, and recommends a dedicated frontend over new API endpoints without changing the engine.

## Decision

1. **A React + TypeScript app in `web/`**, built by Vite into `web/dist`. `make web` installs, type-checks, tests and builds it. `web_app.py` at the repository root (like `streamlit_app.py`) composes the dispute service, the workspace API and the built pages in one process: `make run-web`, at `/desk`, with `/` redirecting there. One process, so one runner owns the store; it replaces `make run-api`, never runs beside it.
2. **A read-and-decide API at `/api/ws`** (`src/dispute_agent/ui/web/`, inside the console package: it is the console's web face, so nothing outside `ui` imports it and `app.py` is unchanged). Reads are the Streamlit console's own view models as JSON (journey, trail, case sheet, review queue, outside notes, ledger rows, seller catalogue), so the two faces cannot disagree. Writes go through the same objects: `ReviewConsole` for approvals (bound to the payload hash, single use) and `CaseDesk` for a person's decisions. The engine is untouched.
3. **Who may act.** Every write names a listed approver (policy.yaml). Behind a site password (`CONSOLE_PASSWORD`, sent as `X-Console-Password`) the password is the credential, as on the hosted console (ADR 0047). Without one, the review console token is required (`X-Review-Token`), exactly as on the Streamlit Review screen. An already-decided approval answers 409 and nothing is done twice. The kill switch refuses approvals.
4. **A dispute is raised one way.** `api/events.raise_dispute` is shared by `POST /events` and the seller view.
5. **Honest gaps.** Where the engine records nothing (pattern stats, assignment, per-person SLA, the AI email writer), the app says "Not built yet" instead of showing invented numbers.

## Consequences

- **Hosted on Streamlit Community Cloud** (owner's request, 2026-11-27): `streamlit_app.py` is an `st.App` (Streamlit 1.66), which `streamlit run` detects and serves with uvicorn - Streamlit plus `/api/ws` and `/desk`. Its one Streamlit page (`desk_page.py`) frames the desk. The service starts in the App's lifespan; the file watcher is off, so a push needs a reboot and never mixes code copies. `web/dist` is committed (no Node on the host); every web path is relative because the host serves under `/~/+/`. On the hosted site `open_site=True` keeps the owner's choice of no password, as on the Streamlit console before it. The four-screen Streamlit console (with the v2 look) runs locally.
- The web app polls every 4 s; live push (SSE) is a later step.
- Shared console wording that names Streamlit screens ("on the Review screen") is translated in `ui/web/views.py`, so the old console's text is not changed.
- The Decision Quality guard (`test_decision_quality_is_hidden.py`) allows `ui/web/router.py` beside the Streamlit Evals screen: both show stored results and neither is on the agent's path.
- `make verify` does not build the web app (CI has no Node step yet); `make web` does, with 7 unit tests. A test checks the committed build uses relative paths.
- Tests: `tests/unit/workspace/test_workspace_api.py` (a real dispute through the offline line, and each refusal), `web/src/lib.test.ts`.
