# 0048 — The console rebuilt from the Claude Design handoff, on Streamlit

Date: 2026-11-08
Status: accepted (D70). Builds on ADR 0044 (the operations console) and 0047 (hosted review sign-in).

## Decision

The four screens and the password gate were rebuilt to the approved Claude Design handoff (`HANDOFF.md`, `design/*.dc.html`, `CLAUDE_DESIGN_BRIEF.md`). The rebuild stays on Streamlit; the owner chose that over a React front end.

- **Material.** One stylesheet, `ui/assets/console.css`, holds the handoff's tokens on `:root` and one class per component: status chip, actor pill, stepper, timeline step, recorded text, card, segmented filter. `ui/theme.py` loads it and builds the small shared parts.
- **Clickable lists and tables.** `ui/picker.py` is a Streamlit v2 component: our own HTML and JavaScript, run on the page, with no build step and no Node. It draws a case list or a table from data and reports `pick` (a row was chosen) and `open` (a row's link was followed).
  - Streamlit's own table cannot hold a chip, a pill or a per-row link.
  - A plain HTML link would reload the page and start a new session, which on the hosted site means the password again.
- **Wording.**
  - `ui/trail_words.py` says each kind of step as the handoff's copy table asks: which email, the credit's reason, what the read-back proved, "Not needed this round" versus "Not on file", and the closing "Now: waiting for …" line.
  - `ui/labels.handed_over` turns a recorded hand-off reason into a sentence plus its rule code. The code moves to Details.
- **Review.** `ui/review_view.py` draws the two halves of a decision card: the recommendation with its reasoning "exactly as recorded", and exactly what approval does (the credit and its figures, or the email with every number marked and its source listed). Approve names the thing; Reject waits for a reason; the integrity error replaces both.
- **Email subjects** (owner approved; a pack template change) now name the shipment ("The weight charge on your shipment SHP-000005"), not the internal case id. Replies are matched by thread first, then by the shipment reference in the subject (`outlook_graph/poller.py`). The old subjects had no shipment reference, so that fallback could never match.

## Why Streamlit, not React

A React front end would match the designs to the pixel, but it needs Node, a new host (Streamlit Cloud cannot serve it) and new authenticated API endpoints for approvals and the case desk, the riskiest code in the system. Streamlit 1.66's v2 components close most of the gap with no new hosting or auth.

## Safety (handoff section 8, brief 13)

- **Data is never markup.** Every value interpolated into HTML goes through `theme.escape`. Recorded text (reasoning, findings, review details) sits in a quote block of escaped text with its line breaks written as entities. The picker writes only with `textContent` and takes style names from fixed lists. Tests: `TestTheDesignHandoff`.
- **What is approved is exactly what is shown.** The email body is the stored text, escaped; only the cited numbers are marked. Decisions still go through `ReviewConsole` (hash-bound) and `CaseDesk`. Nothing is sent from a page (I2).
- **Coral is spent only on credited**: the chip, the Closed phase of a credited case, the credited mark and tag, credit amounts and the Credit type.
- **"Could not prove it happened" is never success**: attention mark, no coral, no check.
- **No correct charge and no free text on the seller screen.** Totals are sums of rows, never stored.

## Known gaps

- **Not built yet:**
  - the Review "sticky decision bar" on a phone (cards stack instead);
  - the top bar's "Reviewing as owner" note (the caption under the title says it).
- **Built differently:**
  - The ledger's opening-charges group is an expander, not a row inside the table.
  - "Waiting for the hub" (Jira) is built from the brief's words, not drawn.
- **Unused data:** the B7 case shows the generic figures (charged, correct, overcharged), not a chargeable-weight figure.
- **Reading the picker in tests:** Streamlit's test runner does not draw v2 components. The picker's content is tested through the plain data it is given, and its safety through its source.

## Consequences

- The console's look is now defined in one CSS file and a few pure Python modules, each tested without a browser.
- A design change is mostly a CSS change.
- `picker.show` mounts a fresh component when its data changes. A mounted v2 component keeps the DOM it first drew, so its key carries a digest of the data.

## Acceptance checklist (handoff section 10), as built

| Check | Result |
|---|---|
| Gate plus four tabs; Review offers "Review as" (listed approvers) hosted, the token too locally | Pass |
| Agent steps reads: case header → headline → Why/Next → stepper (with words) → figures (eligible only) → timeline by round | Pass |
| Every step shows who and when; bookkeeping steps are quiet | Pass |
| The stepper uses "not needed" and "not reached", never strike-through | Pass |
| T02 and T05 read without contradiction and show no figures | Pass (tests) |
| AI reasoning is a quote labelled "exactly as recorded", with who proposed it | Pass |
| Review shows "What the agent recommends" beside "If you approve", with every number's source | Pass |
| With-a-person cards: Take over; Return (note required; refused once a credit is verified); Confirm withdrawal only when the seller asked | Pass |
| The seller is warned before disputing a not-delivered shipment, and never sees a correct charge | Pass (test) |
| All money reads ₹1,820.00; all times read 7 Nov 2026, 16:08 UTC | Pass |
| Coral only on credited; focus ring on every control | Pass |
| Internal ids and codes only in Details | Pass, with one exception by design: the recorded reasoning is quoted exactly, and it begins with its rule code |
| Every data region has loading, empty, error and full states | Partial: empty, error and full are built; loading uses Streamlit's own spinner, not the static skeletons |
| Works at 375 px with no horizontal page scroll; keyboard focus always visible | Pass for Agent steps (checked); the tables scroll inside their own frame |
