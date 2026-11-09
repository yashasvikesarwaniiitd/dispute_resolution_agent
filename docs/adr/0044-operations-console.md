# 0044 — An operations console: four screens over the running system

Date: 2026-11-07
Status: accepted (D64). `make run-console`, beside `make run-api`.

## Decision

A Streamlit app at `src/dispute_agent/ui/`, a shell like `api/`, with four screens in a top bar:

| Screen | Who | What it shows and does |
|---|---|---|
| Raise a ticket | Seller | The seller's shipments and bills. A dispute is posted to `POST /events`, the dispute form's own route. The seller's tickets show with their status. |
| Agent steps | Anyone | One case told step by step: who did each step (code, AI, rules, a person, the seller, the hub) and its detail. Covers eligibility, each evidence round, the recalculation, the proposal with reasoning, policy's verdict, approvals, actions and their read-back, and any handover with its reason. Also shows AI calls and cost. It refreshes while the case is open, and the case lives in the URL. |
| Review | The human in the loop | Each pending credit or message, with the investigator's recommendation beside exactly what will happen on approval. Every number shows its source. Approve or reject; cases handed to a person are listed with their reason. |
| Ledger | Finance | A seller's charges and credits. Each credit is traced to its case, its approver and its read-back. Totals are sums of ledger rows. |

The logic lives in plain Python (`seller.py`, `trail.py`, `review.py`, `finance.py`, `labels.py`), tested without a browser on cases driven through the real line. The `screens/` files are layout, also tested with Streamlit's test runner.

## The boundaries it keeps

- **It writes nothing of its own.**
  - A ticket goes through the API, so intake, the allowlist (I11) and dedupe apply.
  - A reviewer's decision goes through `ReviewConsole` (ADR 0018). The identity comes from sign-in and is bound to the stored text's hash; nothing is sent from the page (I2).
  - Two architecture tests hold this: nothing in the system imports `dispute_agent.ui`, and the console never opens a case itself. The import-linter contracts stay verbatim from ARCHITECTURE section 2.
- **Model-written text stays text.** Every value interpolated into markup is escaped. The rationale and findings are shown with `st.text`, never as markdown, so a seller's or a model's words cannot change the page.
- **No answer key.** The seller screen shows what was billed and measured, never the synthetic world's correct charge. *Narrowed by D73 (owner, 2026-11-10):* the bill also shows what the published rate card gives, on the hub's measurement and on the declared size, worked out by the pack's calculator - as a carrier publishes its rates. The screen still never reads the answer key (`correct_paise`), and the agent proves any error itself.
- **No free-text complaint.** Like the API's form, a dispute carries no statement: a seller's words are untrusted input to the investigator, and adding a box is its own decision.
- **Reads never wait.** Reads use deferred engines, so a screen does not wait on the runner. Two read-only methods were added in `infra` for this: `assessments.for_case` and `usage_for_case`.

## Design: Hallmark, Coral, app scope

The look follows Hallmark (github.com/Nutlope/hallmark, MIT), a design skill the owner asked for. Only its markdown guidance was read: its scripts were not downloaded or run, and nothing of it is in the repository.

- **App-screen rules:** the theme lends palette, faces, radius and surfaces, never layout. Tables come before cards. Each data region handles its empty and error states. The accent marks state and focus only.
- **The Floor:** every colour and font is a token. There are no tracked-caps labels, no emoji icons and no invented figures. Every control has a visible focus ring, and there is a reduced-motion fallback.
- **The theme is Coral**, picked by hand from the catalogue: warm grey paper, near-black ink, General Sans with Geist Mono for figures, and ink-board primary controls. Coral itself is spent only on the credited state and focus. Its OKLCH tokens are converted to hex for `.streamlit/config.toml`.
- **Fonts** load through Streamlit's font setting, because Streamlit strips `<link>` tags from injected HTML.

## Found while building it

- **The API answers an unknown inbound source with HTTP 500**, not 422 (BACKLOG). The console now posts with the form's own source, `dispute_api`, and a test checks the source is on the known list.
- **`tests/unit/api/test_healthz.py` depended on the working folder.** With a real `data/cases.sqlite3` present, as for any owner who has run the app, it built the full runtime and failed. Its fixture now runs in an empty temporary folder.

## Results

- 16 logic tests and 5 screen tests:
  - the seller's shipments and tickets, with no answer key;
  - the trail in order, with who did each step;
  - a reviewer signs in with a test-made token, sees the recommendation beside the exact ₹1,820 credit, approves, and the store records it under the reviewer's name;
  - a wrong token shows no queue;
  - the ledger traces a credit to its case, approver and read-back.
- `make verify` is green.
- **Browser, on the running app:** a dispute raised on SHP-000002 went through the API and the rules engine to "Awaiting approval", and its eight steps read correctly. The reviewer and ledger screens render.
