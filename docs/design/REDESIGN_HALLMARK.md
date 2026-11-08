# Hallmark redesign of the operations console

Hallmark v2.0.0 (github.com/Nutlope/hallmark), `redesign` verb, **full** depth, **app** scope, run by hand. Node is not available, so its linter and theme scripts did not run: every gate was walked manually and the finish review ran in a fresh context (gates.md § 6). Target: `src/dispute_agent/ui/` (Streamlit). Users and use cases: `docs/design/FRONTEND_BRIEF.md`.

## Before (recorded at 1440 × 900)

- **What it is for:** an operations console. A seller raises a dispute, an observer watches the agent work, a reviewer approves credits and emails or handles cases with a person, and finance reads the ledger. One action per screen.
- **What works and stays:**
  - the Coral palette and faces;
  - the Agent steps story: headline, next step, five phases in words, named rounds, one-line summaries with folded details;
  - Review's side-by-side "what the agent recommends" and "if you approve";
  - the AI's reasoning as literal text;
  - every empty state.
- **What reads as generated:**
  - display-size page titles on every screen, where an app screen takes a fixed scale and no display heads;
  - form fields stretched to 1,300 px (review sign-in, case selector);
  - outsized monospace totals on the ledger;
  - no product name in the chrome;
  - the seller's bill panel is thin, and their tickets sit below the fold;
  - meta lines and captions that compete with headlines.
- **What is broken:** the default page ignores its url_path, so `/seller` answers "Page not found" while `/` serves it.

## Picks

- **Redesign:** full, 4 app screens and the password gate (`/`, `/steps`, `/review`, `/ledger`).
- **Keeps:**
  - every route, widget key, label and test hook the tests read ("Raise a dispute", "Approve", "Reject", "Take over", "Return to the agent", "Confirm withdrawal", the tab names, the titles);
  - every figure and line of copy;
  - the Coral system;
  - the escaping rules: model text stays literal.
- **Files:** edit `ui/theme.py`, `ui/app.py`, `ui/screens/*.py`, `.streamlit/config.toml`; create nothing new in `src/`; delete none.
- **Theme:** Coral (by request), app scope. Palette, General Sans and Geist Mono for figures, 8px card radius, 6px controls, ink-board primary control, coral spent only on the settled (credited) state and focus.
- **Chrome:** the top bar keeps four destinations (fewer than seven: no sidebar), with the wordmark "Dispute desk" before them. One container recipe, capped at 76rem and centred.
- **Type:** the fixed app scale, about 1.2 ratio. Screen titles are page heads at one step above section heads, never display size. Figures in Geist Mono with tabular numerals.
- **Signature:** the case trail (Agent steps): where it is, what's next, five phases, then the rounds.
- **Per screen:**
  - **Raise a ticket:** the bill becomes a stock surface (charged figure, declared and measured side by side, the one action), beside the shipments table. Tickets move up with their states in words.
  - **Agent steps:** headline card on stock, phase row, rounds. A figure row (charged, correct, overcharged) when code has recalculated: composition from the case's own facts.
  - **Review:** a narrow sign-in card. The queue opens with a one-line count. Each card has a header line, the two columns, and a decision footer behind a hairline rule. Cases with a person follow the same recipe.
  - **Ledger:** a stat row (label and tabular figure, no trend), then the table.
- **States:** every region has loading, empty (with its one action), error and full. Controls are styled through their data-testid hooks for hover, focus-visible, active and disabled.
- **Sweep:** by hand against gates.md § 1-5, then the fresh-context finish review.

## Sweep (by hand, gates.md § 1-5)

| Gate | Found | Fix |
|---|---|---|
| 4, 71 | Page titles at display size; fields under a title sat tight against it | h1 at the 2xl step of the 1.2 scale; space under the heading |
| 24a, 48 | Colour literals in screens (the ledger's credited row) | One `PAID` value feeds the CSS token and the ledger; nothing past `:root` names a raw value |
| 40 | Streamlit fades captions to 60%, so muted text fell under 4.5:1 | Captions at full opacity, muted colour set on the text |
| 54 | Fields stretched to the full 1,300 px width | Sign-in, case and seller pickers held to a narrow column |
| 73 | The page title clipped under Streamlit's header | Top padding on the block container |
| F21 | Section heads were bold paragraphs | Real headers (`st.header`) |
| Broken | `/seller` answered "Page not found" | Streamlit serves the default page at `/`; the url_path stays because the top navigation needs it there. `/` is the seller's link |

## Finish review (fresh context): "fix", 8 findings

| # | Finding | Result |
|---|---|---|
| 1 | Captions faded under contrast | Resolved |
| 2 | Top navigation missing on the seller page | Not a defect: the navigation draws a moment after the page; the links were in the page and a later capture shows them |
| 3 | Decision buttons spread across the card | Resolved: buttons sit together in one row |
| 4 | Status chip floating apart from the headline | Resolved: the chip rides the headline line; the shipment and date line sits below |
| 5 | Phase tiles were a card inside a card | Resolved: phases are an unboxed row with underline marks, a list on a phone |
| 6 | Three date formats across screens | Resolved: one form everywhere, "7 Nov 2026, 16:08 UTC" |
| 7 | Fields too close under page titles | Resolved |
| 8 | Coral's material (stock cards, pressed controls) missing | Resolved: the bill, the case card and each decision card are cotton stock with a lit edge and a cast; primary buttons are the ink board |

Recaptured after the fixes: Raise a ticket, Agent steps (desktop and 375 px), Review (both tabs, signed in), Ledger.

## Handoff

- **Before:** generic Streamlit with a coral tint: display titles, stretched fields, boxed notices inside cards, three date formats, no product name.
- **After:** a quiet working desk. One object per screen sits on a stock card (the bill, the case, each decision); the page titles step down; coral appears only on credited money and focus.
- **Kept:** every route, label, widget key and test hook; every figure and line of meaning; the escaping rules (model text stays literal); every empty and error state.
- **Changed:**
  - "Dispute desk" wordmark in the top bar.
  - The seller's bill shows the charged figure, then declared beside measured. The result of raising a dispute is a line, not a boxed notice.
  - Agent steps adds a figure row (charged, correct charge, overcharged) once code has recalculated.
  - Review opens with a count line, e.g. "1 waiting for your decision: a ₹1,820.00 credit. 1 case with a person."
  - Times read the same on every screen ("7 Nov 2026, 16:08 UTC").
- **Dropped:** the boxed success notice on the seller card; the date-picker column formats in the tables (replaced by the shared format).
- **Yours to decide:**
  - The status chip repeats the headline's first words ("With a person" twice on Agent steps). Shortening the headline is a copy change in `ui/journey.py`.
  - On a phone the three figures stack tall; a compact row would need a custom block instead of `st.metric`.
  - Hallmark's linter did not run (no Node). Running `npx hallmark lint` would check the gates automatically.

## UX pass (after the owner's review of the live Agent steps page)

The owner found the Agent steps page vague and badly placed, on a live case (SHP-000011). Research before changing anything:

- **ui-ux-pro-max** (github.com/nextlevelbuilder/ui-ux-pro-max-skill, MIT): its rule set was read as reference, and no scripts were run. Rules applied:
  - visual hierarchy;
  - content priority;
  - progressive disclosure;
  - colour is never the only signal;
  - tabular figures;
  - one primary action;
  - empty states with a way forward;
  - deep links;
  - multi-step progress.
- **Activity-timeline guidance** (Maersk design system; SaaS audit-log patterns): group the history, avoid a flat feed where everything weighs the same, and keep the record trustworthy.
- **Human-in-the-loop approval patterns:** show the action the AI wants, its reasoning and its sources in one view. A bare Approve button invites rubber-stamping.

**What was wrong on Agent steps, and the fix:**

| Problem | Fix |
|---|---|
| A shipment proved ineligible by its scan history (T05) read "Checked eligibility: eligible", then simply stopped. Only the header said "Not eligible". | A "Closed: not eligible" step after the recalculation, saying why (`trail.INELIGIBLE`) |
| "Investigated: skipped", struck through, although the evidence was gathered and the charge recalculated | Investigated counts the evidence round. A closed case marks a phase **not needed** (a later phase happened without it) or **not reached** (the case closed first). |
| Charged, correct and overcharged (₹0.00) shown for a shipment that cannot be disputed | Figures only when the case was not proved ineligible |
| The headline repeated the status chip ("With a person … With a person:") | Headline, then **Why** and **Next** as a definition list |
| The "Code" and "System" actors unexplained; the full date on every step; boxed "Details" bars; bookkeeping weighed like a decision | A legend; the time only (the date when it differs); a timeline with a mark and a line; light "Details" toggles; the bookkeeping step shown small and muted |
| The AI's reasoning hidden in a fold, in code font | Shown openly as a quoted block (escaped: model text stays text) |
| No way onward | A link to Review when a person must act, and to the Ledger when credited |

**The other screens:**
- **Raise a ticket:**
  - click a shipment row to choose it;
  - each shipment shows its open ticket;
  - a shipment that was not delivered says so before a ticket is raised on it;
  - each ticket says in a sentence what is happening;
  - after raising, the result and a link to watch the agent appear.
- **Review:**
  - each approval card shows when it was requested and links to every step of its case;
  - the reasoning and findings read as text, not code font.
- **Ledger:**
  - money reads ₹ as on every other screen (it read "INR");
  - credits from disputes have their own table, with who approved each and whether it was proved;
  - all entries are filterable (All, Charges, Credits).

Checked at 1,440 px and at 375 px, on the hosted case's fixture (`not_delivered_late_scan`), on a case with a person, and on a credit awaiting approval. New tests: `test_a_shipment_proved_ineligible_by_its_evidence_says_so` and `test_the_steps_explain_a_shipment_closed_after_its_evidence`.
