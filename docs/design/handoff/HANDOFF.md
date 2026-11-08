# Dispute Desk: design handoff for Claude Code

This file tells you how to build the new Dispute Desk console from the approved designs. Read it top to bottom before writing code.

## 0. What you have

| File | What it is | Priority if they disagree |
|---|---|---|
| `CLAUDE_DESIGN_BRIEF.md` | Users, flows, states, exact labels, safety rules | 1 (wins on behaviour and safety) |
| `HANDOFF.md` (this file) | How the designs map to code: tokens, components, screens, data, copy changes | 2 (wins on look and layout) |
| `design/*.dc.html` | The design source for each screen (HTML + a small JS logic class) | 3 (reference for exact markup and inline styles) |
| `SCREENS.pdf` | The console as built today | Only to see what changes |

The `.dc.html` files are design prototypes, not production code. Each one is plain HTML with inline styles plus a `class Component extends DCLogic` block whose `renderVals()` holds the sample data and the click logic. Read them for exact spacing, colours and copy. Do not ship them as-is.

| Design file | Screen | Route |
|---|---|---|
| `design/Main.dc.html` | Screen 0: site password gate | `/` (before sign-in) |
| `design/RaiseTicket.dc.html` | Screen 1: Raise a ticket | `/raise` |
| `design/AgentSteps.dc.html` | Screen 2: The agent's steps | `/steps?case=SHP-000002` |
| `design/Review.dc.html` | Screen 3: Review | `/review` |
| `design/Ledger.dc.html` | Screen 4: Ledger | `/ledger` |
| `design/States.dc.html` | Loading, empty and error states; control states; chips | (reference only) |

Scope: desktop first (1440 design width). Layouts are fluid and already wrap at narrow widths, but phone layouts were not designed in detail. Follow brief section 12 for phone behaviour.

---

## 1. Design tokens

Put these on `:root` once. Every colour, size, radius and shadow in the app goes through a token. No raw hex in components.

```css
:root {
  /* Coral theme (Hallmark), from the brief */
  --color-paper: #f8f4f1;   --color-paper-2: #f1ece8;  --color-paper-3: #e8e1dd;
  --color-ink: #1d1614;     --color-ink-2: #312926;    --color-muted: #615955;
  --color-rule: #d9d3cf;    --color-rule-2: #c7bfba;
  --color-cotton: #fdfbf8;  --color-board: #1f1917;    --color-board-type: #f6f3ef;
  --color-paid: #ba4a34;    /* coral: ONLY the credited/settled state */
  --color-focus: #bd3a2b;   /* focus rings */
  --color-error: #8a2416;

  --font-body: "General Sans", "Instrument Sans", ui-sans-serif, system-ui, sans-serif;
  --font-figure: "Geist Mono", ui-monospace, monospace;   /* figures and ids only */

  --text-sm: .875rem; --text-md: 1rem; --text-lg: 1.2rem; --text-xl: 1.44rem; --text-2xl: 1.728rem;
  --space-2xs: 4px; --space-xs: 8px; --space-sm: 12px; --space-md: 16px; --space-lg: 24px; --space-xl: 40px;
  --radius-card: 8px; --radius-control: 6px; --control-h: 40px;

  /* Added in the design: the "cotton stock" card material (lit top edge + soft cast shadow, no border) */
  --shadow-stock: inset 0 1px 0 rgba(255,255,255,.9), 0 0 0 1px rgba(29,22,20,.05),
                  0 1px 2px rgba(29,22,20,.06), 0 10px 28px -14px rgba(29,22,20,.22);
}
```

Fonts:
- **General Sans** comes from Fontshare, not Google Fonts. Self-host it if you can. The designs fell back to **Instrument Sans** (Google Fonts) only because the design tool could load nothing else.
- **Geist Mono** (Google Fonts, weights 400/500/600) is for money, weights, shipment ids, transaction ids and recorded reasoning. Never for body text.
- Turn on `font-variant-numeric: tabular-nums` wherever figures line up.

Base styles:

```css
* { box-sizing: border-box; }
body { margin: 0; background: var(--color-paper); color: var(--color-ink);
       font-family: var(--font-body); font-size: var(--text-md); line-height: 1.5; }
:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
```

The Hallmark floor must hold (brief section 11):
- no `transition: all`; only animate the property that changes, for about 150 ms;
- no gradients, no emoji, no tracked capitals or eyebrows (all labels are sentence case);
- one card level only: never a card inside a card (a tinted panel inside a card is fine);
- no display heads: the h1 on each screen is `--text-2xl`, weight 700;
- motion only for state changes.

---

## 2. Shell

**Top bar** (every screen except the gate)
- Full-width strip: background `--color-cotton`, bottom border `1px solid --color-rule`.
- Inner row: `max-width: 76rem`, centred, padding `10px 24px`, flex, `gap: 12px 40px`, wraps.
- Left: wordmark "Dispute desk", `--text-lg`, weight 700, links to Raise a ticket.
- Tabs: Raise a ticket, Agent steps, Review, Ledger. Each tab is a link:
  - `min-height: 40px`, padding `0 12px`, radius `--radius-control`, `--text-sm`, gap 8px between icon and label;
  - inactive: `--color-muted`, weight 500; hover background `--color-paper-2`;
  - active: `aria-current="page"`, background `--color-paper-3`, `--color-ink`, weight 600.
- Review screen only, right side: "Reviewing as **owner**" with an 8px `--color-board` dot.

**Tab icons** (16px, outline, `stroke-width 1.7`, round caps and joins, `viewBox 0 0 24 24`):

| Tab | SVG paths |
|---|---|
| Raise a ticket (receipt) | `<path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3z"/><path d="M9 8h6M9 12h6"/>` |
| Agent steps (route) | `<circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/><path d="M8 19h8a3 3 0 0 0 0-6H8a3 3 0 0 1 0-6h8"/>` |
| Review (checklist) | `<path d="M4 6l1.5 1.5L8 5M4 12l1.5 1.5L8 11M4 18l1.5 1.5L8 17M11 6h9M11 12h9M11 18h9"/>` |
| Ledger (bank) | `<path d="M3 10l9-6 9 6M5 10v8M9 10v8M15 10v8M19 10v8M3 21h18"/>` |

**Page body:** `max-width: 76rem`, centred, padding `32px 24px 64px`, a flex column with `gap: 24px` to `32px`. One h1 per screen.

**Formats** (make two helpers and use them everywhere):
- Money: `₹1,820.00` (always 2 decimals, Indian rupee sign, comma grouping). Never "INR 1820.00".
- Time: `7 Nov 2026, 16:08 UTC`. Inside one case's timeline, the time alone (`16:08`) is enough.

---

## 3. Components

Build these once and reuse them. The exact CSS is in the `.dc.html` files. The tables below are the spec.

### 3.1 Status chip

Pill: `height 24px`, padding `0 9px`, radius 999px, `--text-sm`, weight 500, no wrap. **The label is always the words from brief section 4.1.** Colour is never the only signal.

| Tone | States | Style |
|---|---|---|
| working | Received, Gathering evidence, Recalculating, Investigating, Acting, Checking it happened | `1px solid --color-rule-2`, text `--color-ink-2` |
| waiting | Waiting for the seller, Waiting for the hub, Awaiting approval | `1px dashed --color-ink-2`, text `--color-ink-2` |
| attention | With a person, Handed to a person | `1.5px solid --color-ink`, text ink, weight 700 |
| done | Resolved: charge upheld | `1px solid --color-rule-2`, background `--color-paper-2` |
| closed | Withdrawn, Closed: no reply, Not eligible | `1px solid --color-rule`, text `--color-muted` |
| settled | Resolved: credited | `1.5px solid --color-paid`, text `--color-paid`, weight 700 |

Put this state-to-tone map in one place in code.

### 3.2 Actor pill (who did a step)

Height 24px, padding `0 8px`, radius `--radius-control`, `--text-sm`. Each actor has a 7px shape before its name so the actor reads without colour.

| Actor | Pill style | Shape |
|---|---|---|
| Code | background `--color-paper-3`, label in `--font-figure` | filled square, ink |
| AI | `1px solid --color-ink-2` border, background cotton | outlined diamond (square rotated 45°) |
| Rules engine | same as AI | outlined diamond |
| Reviewer | background `--color-board`, text `--color-board-type`, weight 600 (stands out most) | filled circle, board-type |
| Seller | `1px solid --color-rule-2` | outlined circle |
| Hub | `1px solid --color-rule-2` | outlined square |
| System | text `--color-muted`, no border | none |

The difference between **Code** and **AI / Rules engine** is the product's main point. Keep the two looking clearly different.

### 3.3 Stepper (five phases)

Phases: Raised, Investigated, Approved, Acted, Closed. Lay them out as a 5-column grid. Each column has a 30px circle, a connector line to the next circle, the phase label (weight 600) and the mark word (`--text-sm`).

| Mark | Word shown | Circle | Word style |
|---|---|---|---|
| done | done | filled `--color-ink`, white check icon | muted |
| done on a credited case (Closed only) | done | filled `--color-paid`, check icon | `--color-paid`, 600 |
| in progress | in progress | `2px solid ink` ring, phase number | ink, 600 |
| waiting | waiting | `2px dashed ink` ring, phase number | ink, 600 |
| with a person | with a person | `2.5px solid ink` ring on `--color-paper-3`, number | ink, 600 |
| not needed | not needed | `1px rule-2` ring on paper-2, "–" | muted |
| not reached | not reached | `1px rule-2` ring on paper-2, "–" | muted |
| later | later | `1px rule-2` ring on cotton, number | muted |

Connector: `2px solid ink` when both sides happened. Otherwise `2px dotted --color-rule-2`. **Never use strike-through.**

### 3.4 Timeline step

Each step is a row: a 24px rail column (mark plus a 2px `--color-rule` line down to the next step; no line after the last step) and a content column.

Content column:
1. Title row (flex, space-between, wraps): **"n. Title"** (weight 600) plus an optional tag chip; on the right, the actor pill and the time.
2. One-line summary, `--color-ink-2`, max 68ch.
3. For proposals, a recorded-reasoning block (3.5).
4. A **Details** fold: native `<details>`, summary "Details" in muted underlined text with a chevron that turns 90° when open. Internal ids and rule codes go here and nowhere else.

Step marks (12–14px):

| Kind | Used for | Mark |
|---|---|---|
| done | ordinary completed step | filled ink circle |
| propose | "Proposed: …" | outlined diamond, 2px ink |
| person | a proposal that sends the case to a person | ring with a 4px ink border |
| credited | the read-back that settles a credit | filled `--color-paid` with a soft coral halo |
| closed | "Closed: not eligible", "Checked eligibility: not eligible" | `2px solid --color-muted` ring on paper-3 |
| pending | the "Now: waiting for …" line at the end of an open case | `2px dashed --color-ink-2` ring; no number; italic text |
| minor | bookkeeping ("Outcome recorded for pattern detection", "Handed to a person") | 6px `--color-rule-2` dot; small muted text; no number |

Step numbers count only major steps (not minor or pending ones).

Tags: `Credited` (settled tone), `Needs a person` (attention tone), `Not eligible` (closed tone).

Group header for each round: `--text-sm` weight 600, followed by a 1px rule filling the row. Titles: "The dispute", "Round 1 · First look", then "Round 2 · After the seller replied", "After the hub's finding", "After a deadline passed", "After a person returned it" (brief 4.5).

### 3.5 Recorded text block (AI reasoning, seller's words, recorded detail)

- Caption above it (muted, `--text-sm`): "Its reasoning, exactly as recorded · proposed by Rules engine (the AI was off or not needed)". On Review: "Reasoning, exactly as recorded:". For a with-a-person reason: "The detail, exactly as recorded:".
- `<blockquote>`: padding `12px 14px`, background `--color-paper-2`, radius `--radius-control`, `--font-figure` at `--text-sm`, `white-space: pre-wrap`, `overflow-wrap: anywhere`.
- **Safety (I5):** render as escaped plain text only. Never markdown, never HTML. No left-border accent.

### 3.6 Buttons

| Kind | Style |
|---|---|
| Primary | height `--control-h`, padding `0 20px`, radius `--radius-control`, background `--color-board`, text `--color-board-type`, weight 600. Hover `--color-ink-2`. Active: `translateY(1px)`. |
| Secondary | same size, background `--color-cotton`, `1px solid --color-rule-2`, weight 500. Hover `--color-paper-2`. |
| Disabled | real `disabled` attribute, opacity .55, `cursor: not-allowed`, plus a visible reason next to it in muted text |
| Loading | spinner icon plus a verb ("Recording"), `aria-busy="true"` |
| Error | `1.5px solid --color-error` border, error text ("Not recorded. Retry") |
| Success | paper-2 background, check icon, "Recorded" |

All eight control states are drawn in `States.dc.html`. Targets are at least 40px on desktop and 44px on touch, with 8px gaps.

### 3.7 Other pieces

- **Card:** background `--color-cotton`, radius `--radius-card`, `box-shadow: var(--shadow-stock)`, no border.
- **Segmented filter** (tickets, ledger): a track with background `--color-paper-3`, padding 3px. Each segment is a `<button aria-pressed>`, height 34px. The selected one is cotton with a 1px shadow and weight 600.
- **Toast:** fixed at bottom centre, background `--color-board`, `role="status" aria-live="polite"`, with a check icon, the message and a dismiss button (`aria-label="Dismiss"`). It never takes focus.
- **Figures row:** a `<dl>` of label (muted `--text-sm`) over value (`--font-figure`, `--text-lg`). The "lead" figure is `--text-xl`, weight 600.
- **Live refresh note:** "· refreshes every 4 s" with a 6px muted dot, inside `role="status" aria-live="polite"`, shown only on open cases.

---

## 4. Screens

### Screen 0: Site password gate (`Main.dc.html`)

- No top bar. A centred column, max 30rem: an icon plus h1 "Dispute desk", then a card.
- Card text: "A live dispute resolution agent on synthetic data. It sends real email to a test address and opens real Jira issues in a test project, so it is behind a password."
- Form: label "Site password", a password input (`autocomplete="current-password"`), and an **Enter** primary button.
- Footnote under the card: "Synthetic data only: 14 made-up shipments and one test seller."

| State | What shows |
|---|---|
| Default | the form |
| Wrong password | input border `1.5px --color-error`, `aria-invalid="true"`; under it, with `role="alert"`: "That password is not correct." |
| Not configured | **no form.** A paper-2 panel with a lock icon: "This hosted console has no password set (CONSOLE_PASSWORD), so it stays closed. It sends real email and opens real Jira issues." |
| Service failed to start | no form. A panel in error ink: "The dispute service could not start: `<missing setting>` is not set." Never show a secret value. |

### Screen 1: Raise a ticket (`RaiseTicket.dc.html`)

Header: h1 "Raise a ticket". Sub-line (muted, sm): "Signed in as seller SELL-0001. Every shipment here is synthetic; emails about your tickets go to the test seller address."

Layout: a flex row that wraps. Shipments take `flex: 3 1 560px`. The bill card takes `flex: 2 1 360px` and is `position: sticky; top: 16px`. "Your tickets" goes full width below.

**Your shipments** (a card)
- Heading row: "Your shipments" (h2, `--text-xl`) and, on the right, "Click a row to see its bill."
- Columns: (radio mark) · Shipment · Status · Zone · Charged (right-aligned, mono) · Your ticket (chip of the open ticket, or blank).
- **Every row is a real `<button aria-pressed>`** laid out on the same grid (`28px 1.2fr 1.4fr .6fr .9fr 1.5fr`), min-height 44px. Selected row: background `--color-paper-3` and a filled radio dot.
- "Returned to origin" is shown in `--color-error`, weight 500.
- **All 14 rows show.** No fixed-height inner scroll. On narrow screens the card scrolls sideways inside itself (`overflow-x: auto`, min-width 560px).
- Fix from today: **one selection state drives both the row and the dropdown**, so they can never disagree.

**Dispute a charge** (bill card)
- "Shipment" select, placeholder "Choose a shipment". It is bound to the same selection state.
- Nothing chosen: "Choose a shipment to see its bill: what you were charged, what you declared and what the hub measured."
- Chosen:
  - "Charged for zone B" (muted), then the amount in mono `--text-2xl`;
  - a definition list between two rules: **You declared** 20 x 15 x 10 cm, 0.48 kg · **The hub measured** 40 x 30 x 20 cm, 1.10 kg (the measured value is weight 600);
  - not delivered: a warning line in error ink with a triangle icon, `role="alert"`: "Its status is "Returned to origin", not delivered. Only delivered shipments can be disputed, so a ticket on it would close as not eligible." The button still shows (the brief allows raising it);
  - open ticket exists: "You already have an open ticket on this shipment:" plus its chip, then a link "See what the agent has done on it". **No button.**
  - otherwise: one primary, full-width button, **Raise a dispute**.
- After raising: **one line, not a boxed alert**, `role="status" aria-live="polite"`:

| Result | Line | Ink | Link |
|---|---|---|---|
| raised | Ticket raised. The agent has started on it. | ink | **Watch the agent work on it** → `/steps?case=<id>` |
| duplicate | This ticket was already raised; showing the existing case. | ink | same link |
| not on allowlist | This seller address is not on the test allowlist. Use the test seller address and try again. | error | none |
| service down | The dispute service is not running, so the ticket was not raised. Try again in a minute. | error | none |
| refused | The dispute service refused the ticket (HTTP n). Nothing was raised. | error | none |

- When raised, the new ticket appears **at once** at the top of Your tickets: chip "Received", sentence "The agent is working on it.", times "Just now", row tinted paper-2. The shipment row's ticket column shows "Received".
- Card footer note: "The agent checks the charge in code and a person approves any credit. You will get an email at the test seller address."
- **Must not show** the correct charge, or any free-text field (brief 13.5).

**Your tickets** (a card with a table)
- Heading row: "Your tickets" plus a segmented filter **Open (n) · Closed (n) · All (n)**. Open means any non-terminal state.
- Columns: Shipment (mono) · **What is happening** (chip on top, sentence below: one column, so the status isn't said twice) · Opened · Last update · **Open the steps →** (a link in each row, with `aria-label` "Open the agent's steps for SHP-…").
- The old "See the agent's steps for" picker plus button is **removed**. Each row's link replaces it.
- Empty: "No tickets yet. Choose a shipment above and raise a dispute; it appears here with its status." Filtered and empty: "No tickets in this view."

### Screen 2: The agent's steps (`AgentSteps.dc.html`): the signature screen

h1: "The agent's steps".

Layout: a flex row that wraps.
- **Left: case list** (`flex: 1 1 272px; max-width: 300px`). Heading "Cases (9)", then a search input (placeholder "Shipment or status", with a visually hidden label) that filters by shipment id or status. Each case is a `<button aria-pressed>`: mono id, "opened 16:09" on the right, status chip below. Selected: cotton background with `--shadow-stock`. No match: "No case matches that. Try a shipment number such as 000002."
- **Right: main column** (`flex: 999 1 560px; min-width: 0`).
- The selected case is in the URL (`/steps?case=SHP-000002`). The case list replaces the long dropdown.

**Case card** (the only card on the screen), top to bottom:
1. Name line: shipment id (mono, `--text-xl`, 600) plus the status chip.
2. Facts line (muted, sm): "Seller SELL-0001 · opened 7 Nov 2026, 09:58 UTC · closed 7 Nov 2026, 09:58 UTC · no AI calls". Open cases show "open" instead of the closed time and add "· refreshes every 4 s". On an AI case: "3 AI calls (USD 0.0186)".
3. Headline: `--text-lg`, 600, max 62ch.
4. A `<dl>` with a 56px label column: **Why** (only when the state does not explain itself) and **Next**. A rule code can follow Why in muted mono, for example "(B5)".
5. Onward link, shown as a primary button with an arrow: **Decide on the Review screen** (awaiting approval, with a person) or **See the credit in the ledger** (credited).
6. The stepper (3.3).
7. **Code's figures**, above a top rule: the caption "Code's figures · worked out in code, not by the AI", then the figures row. **Left out entirely for not-eligible cases.**

**What happened** (on the page background, not in a card)
- h2 "What happened".
- Legend (a list of actor pills plus meanings): **Code** fixed checks and calculations, no AI · **AI or rules engine** proposes what to do · **Reviewer** a person who approves · **System** the service's own bookkeeping · Times are UTC.
- Then rounds of steps (3.4). Open cases end with a **pending** line ("Now: waiting for …").

**Headline / Why / Next / stepper per state**: use brief section 7's table word for word. The stepper marks used in the designs:

| Case | Stepper (Raised, Investigated, Approved, Acted, Closed) |
|---|---|
| Resolved: credited | done, done, done, done, done (coral) |
| Awaiting approval | done, done, waiting, later, later |
| With a person (escalated at investigation) | done, with a person, later, later, later |
| Waiting for the seller | done, waiting, later, later, later |
| Resolved: charge upheld | done, done, done, done, done (neutral) |
| Not eligible, T05 (after evidence) | done, done, not reached, not reached, done |
| Not eligible, T02 (at intake) | done, not reached, not reached, not reached, done |

**Copy fixes in the timeline** (these change what the backend writes, or how the UI words it):

| Today | New |
|---|---|
| "Found 5 of 8; missing: parcel image, earlier shipments of this item, hub finding" | "Found 5 of 8. Not needed this round: hub finding. Not on file: parcel image, earlier shipments of this item." The found items move to Details. |
| "owner approved the message" | "owner approved **the credit confirmation email**" / "the evidence request email" / "the explanation email" (say which message) |
| "Posted a credit to the ledger (txn_b7bd…)" | "Posted a credit to the ledger", summary "₹1,820.00 for calculation error.". The txn id goes in Details. |
| "Sent an email (msg-0001)" | "Sent the credit confirmation email", summary "To seller+1@example.invalid.". The message id goes in Details. |
| "Proved it happened by reading it back · Next: Awaiting approval" (in the middle of a credit) | "Proved it happened by reading it back", summary "The credit is in the ledger. Next, the credit confirmation email to the seller needed approval." |
| last read-back on a credit | tag **Credited**, mark *credited*, summary "The email was read back as sent. The case is resolved: credited." |
| T05 recalculation step showing ₹ figures | summary "Worked out before the scan history was read. Not used, because the shipment turned out not to be eligible."; the figures go in Details |
| "Policy: allowed, but a person must approve first" | "High confidence. Policy allows it, but a person must approve first." |
| Duplicate charge: "overcharged ₹0.00" | "The weight charge of ₹152.00 is correct, but it was billed twice. Code proved a duplicate charge of ₹152.00." |

**Duplicate-charge figures (SHP-000009).** The lead figure is the credit's basis: **Duplicate debit (the credit's basis) ₹152.00** · Weight charge ₹152.00 · Correct weight charge ₹152.00, with the note: "The weight charge itself was right. The seller was billed for it twice, so the credit returns the second debit."

**Escalation reasons in words.** Use the "suggested words" column of brief 9.3, with the code in brackets or Details. Examples from the design:
- SHP-000001: "Proposal escalated: the round contains a parcel photo, and images need a person's judgement." (B5)
- SHP-000006: "The input screen flagged it: the seller's text read like instructions to the agent. It was treated as data, not followed." (R21)

Empty state: "No cases yet. Raise a ticket on the seller screen and its steps appear here." plus a link to Raise a ticket.

Live refresh: poll every 4 s **only while the case is open**. Don't move focus or scroll position on refresh.

### Screen 3: Review (`Review.dc.html`)

**Sign-in** (a card, max 32rem)
- Hosted: "Choose who you are reviewing as. That approver is recorded against each decision you make." Then a **Review as** select listing **only the approvers in `config/policy.yaml`** (today: owner), helper "Only approvers listed in the policy appear here. The agent ignores decisions by anyone else.", and the button **Start reviewing**. **No free-text name field** (ADR 0047).
- Local: "Sign in with the review console token to see the queue.", a **Review console token** password field, the same Review as select, and **Sign in**. Error: "The review console token is not correct."

**Signed in**
- Caption: "Signed in as owner. Your name is recorded against each decision, bound to the exact amount or text shown." plus a small "Switch approver" link.
- Count line (`--text-lg`, 600), computed live: "2 waiting for your decision: a ₹152.00 credit, 1 email. 2 cases with a person."
- Tabs (`role="tablist"`, real buttons, 44px tall, a 2px ink underline on the selected one): **Awaiting your approval (n)** · **With a person (n)**.

**Approval card** (one card, three parts)
- Header row (bottom rule): kind ("Credit" / "Message: evidence request", `--text-lg` 700) · shipment (mono) · status chip · "requested 7 Nov 2026, 12:37 UTC · expires after 72 h" · on the right, the link **See every step of this case** → `/steps?case=…`.
- Body: two columns that wrap (`flex: 1 1 400px` each).
  - **Left, "What the agent recommends"** (cotton): "**Credit the seller**, high confidence"; "Proposed by the Rules engine (the AI was off or not needed)" with the diamond shape (for AI: "AI (gpt-5-mini, instructions case_investigator v2)"); the recorded reasoning block (3.5); "Findings, as recorded:" as a check-icon list **in plain words**; a fold "Codes behind the findings" holding the raw codes (`B3 · cites f_chargeable_grams, …`).
  - **Right, "If you approve"** (a tinted `--color-paper-2` panel, not a nested card):
    - Credit: "A credit of **₹152.00** (mono, xl) is posted to the ledger for duplicate charge." Then the derivation: "Code set the amount: the second debit of the same weight charge, less anything already credited on this shipment. The rules engine chose to propose a credit; it did not choose the amount." Then **Code's figures behind it**: Duplicate debit ₹152.00 (bold, first) · Already credited on this shipment ₹0.00 · *The weight charge itself was correct:* Chargeable weight 1,980 g · Recalculated charge ₹152.00 · Amount charged ₹152.00 · Overcharge on the weight ₹0.00. Numbers are right-aligned and mono.
    - Email: the heading is "If you approve, this exact email is sent". **To** `seller+1@example.invalid` (the test seller address) · **Subject** · the full body in a cotton box with `white-space: pre-line`, exactly as stored. **Every number in the body** gets a paper-3 mono highlight and a superscript index. Under the body, **Every number in it, and where it came from**: `1  2,244 g  Chargeable weight in grams, from the hub's measurement, worked out by code` · `2  ₹190.00  Amount charged, from the billing entries`.
- Footer (top rule): label "Note, or your reason for rejecting", a textarea, and a helper line that changes:
  - empty: "A note is optional to approve. A reason is needed to reject."
  - filled: "Approving keeps this as a note. Rejecting sends it to the agent as your reason."
- Buttons: the primary names the exact thing ("**Approve the ₹152.00 credit**" / "**Approve and send this email**"). **Reject** is secondary and **disabled until the reason is not empty**.
- **Integrity error** (the stored text's hash no longer matches): **replaces the note and both buttons** with an error-bordered panel, `role="alert"`: "**The stored text has changed since approval was requested.** It cannot be approved. Report this."
- After a decision, the card collapses to one status line, for example "owner approved the ₹152.00 credit on SHP-000009. Recorded. The agent picks it up from here." A rejection quotes the reason and adds "The agent re-plans. A case allows two rejections before it goes to a person." The toast shows "Recorded. The agent picks it up from here."
- **The page never sends anything.** It only records the decision with the approver and the hash. The agent acts.

**With-a-person card**
- Header: "Shipment SHP-000001" · chip · "since 7 Nov 2026, 14:23 UTC" · See every step of this case.
- Left: "Why it is with a person" → the reason in words (`--text-lg` 600, for example "Proposal escalated") → a plain sentence → the recorded detail block.
- Right (paper-2): "Last proposal" → "**Hand to a person**, by the Rules engine (…)"; "What each choice does": **Take over**: you handle it from here, the agent stops. **Return to the agent**: the agent starts a new round from fresh evidence; needs a note.
- Footer: "Your note (needed to return it to the agent)" textarea · checkbox "When taking over, tell the seller a person is handling it (the email goes through review first)" · **Take over** (primary) · **Return to the agent** (secondary). Return is disabled until there is a note, with the helper "Write a note to return it."
- When a credit on the case is already verified: Return stays disabled with "A credit on this case is already verified, so it cannot go back to the agent."
- **Confirm withdrawal** shows **only** when the seller wrote that they withdraw.
- After Take over: "owner took over SHP-000001. The agent has stopped." If the box was ticked, add "The handed-to-a-person email to the seller now waits for approval." The toast shows "Recorded. The agent applies it within a few seconds."

Empty states: "Nothing is waiting. Every credit and message has been decided." / "No case is waiting for a person."

### Screen 4: Ledger (`Ledger.dc.html`)

- Header row: h1 "Ledger" on the left; the **Seller** select (mono, SELL-0001) on the right.
- **Totals:** one card split into 3 columns by vertical rules: **Charged** ₹11,660.00 · **Credited back** − ₹1,820.00 · **Net billed** = ₹9,840.00 (mono, `--text-2xl`, tabular). The − and = signs show the sum. No trend arrows. Caption: "Every figure is a sum of the ledger rows below." **Compute them from the rows.** Never store them.
- **Credits from disputes (n):** columns Posted · Shipment · Amount (right-aligned, mono, coral) · Reason · Approved by (a Reviewer pill) · Proved (check icon plus "Read back", or "Not posted by the agent") · Case (**a link "See the steps →"** to `/steps?case=…`, not the raw `case_…` id). Caption: "Proved means the agent read the credit back from the ledger after posting it. A credit the agent did not post reads "Not posted by the agent"."
- **All entries:** heading plus "Show" with a segmented filter **All · Charges · Credits**. Columns Posted · Shipment · Type (Credit in coral, weight 700) · Amount (right-aligned) · Reason · Transaction (mono, muted). Credits are listed first. The many 1 Jan 2026 charges sit under **one group row you can fold**: "Weight charges posted 1 Jan 2026, 00:00 UTC · one per shipment · ₹11,660.00 · Hide/Show" (`aria-expanded`), with the rows indented under it.
- Empty states: "The ledger has no entries yet. It is created and seeded with the synthetic charges when the dispute service starts." / "No credits yet. When a reviewer approves a credit and the agent posts it, it appears here with who approved it and whether it was proved."

---

## 5. Loading, empty and error states

All of them are drawn in `States.dc.html`. Every data region needs all four (brief section 8):

| Region | Loading | Empty | Error |
|---|---|---|---|
| Shipments table | skeleton rows | (never empty: 14 rows) | – |
| Bill card | – | "Choose a shipment…" | the result line in error ink |
| Your tickets | skeleton | "No tickets yet…" | "There is no case store yet. It is created when the dispute service starts." |
| Case card | skeleton (id bar, chip, lines, 5 circles) plus "Loading the case…" | "No cases yet…" plus a link | – |
| Timeline | 3 skeleton rows plus "Loading the steps…" | – | – |
| Review queue | – | the two empty sentences | sign-in error / integrity error / approval expired / return refused |
| Ledger | – | the two empty sentences | – |

Skeletons are static paper-2 and paper-3 blocks (no shimmer) inside `role="status"` with a text label.

---

## 6. Sample data (synthetic; seed the dev build with it)

Seller SELL-0001. Reviewer: owner (the only listed approver). The full per-case timeline content is in `AgentSteps.dc.html` → `data()`.

| Shipment | Status | Zone | Charged | Ticket state | Opened (7 Nov 2026, UTC) |
|---|---|---|---|---|---|
| SHP-000001 | Delivered | B | ₹152.00 | With a person (B5, image) | 14:23 |
| SHP-000002 | Delivered | E | ₹2,800.00 | Resolved: credited ₹1,820.00 | 09:58 |
| SHP-000003 | Delivered | B | ₹380.00 | none (declared 20 x 15 x 10 cm, 0.48 kg; measured 40 x 30 x 20 cm, 1.10 kg) | – |
| SHP-000004 | Delivered | B | ₹152.00 | none | – |
| SHP-000005 | Delivered | B | ₹190.00 | Awaiting approval (email, B7) | 13:30 |
| SHP-000006 | Delivered | B | ₹152.00 | With a person (R21, injection) | 15:16 |
| SHP-000007 | Returned to origin | B | ₹152.00 | Not eligible, T02 (measured 30 x 22 x 15 cm, 1.10 kg) | 10:51 |
| SHP-000008 | Delivered | B | ₹152.00 | none | – |
| SHP-000009 | Delivered | B | ₹152.00 | Awaiting approval (₹152.00 duplicate credit) | 12:37 |
| SHP-000010 | ? | ? | ? | none | – |
| SHP-000011 | Delivered (scan history says not) | ? | ₹152.00 | Not eligible, T05 | 16:09 |
| SHP-000012 | ? | ? | ? | none | – |
| SHP-000013 | Delivered | ? | ₹114.00 | Waiting for the seller | 11:44 |
| SHP-000014 | Delivered | ? | ₹980.00 | Resolved: charge upheld | 09:05 |

"?" means the value was not in the design pack. **Take it from the real seed data.** The designs show these as `[brackets]`.

---

## 7. What the backend or data layer must provide

The designs assume these. Check each one, and add it if it is missing:

1. **Evidence split for the gather step:** found / not needed this round / not on file, as three lists.
2. **Plain-words findings** next to the raw codes (for example "Verified fact: code proved one definite error, a duplicate charge." next to `B3 · cites …`).
3. **The credit's basis figure** for duplicate charges (the duplicate debit, and already-credited), separate from the weight recalculation.
4. **Message purpose** on every email action (credit confirmation, evidence request, explanation, reminder, handed to a person), so steps can say which message.
5. **Every number in an email, with its source** (value, label, source record), for the "Every number in it" list (I9).
6. **Email subject without the internal case id:** the design shows "The weight charge on your shipment SHP-000005". This is a **template change**, because the card must show the exact text that will be sent (I3).
7. **Content hash on each approval request**, checked when the card renders. A mismatch shows the integrity error, and Approve is never shown.
8. **Approval expiry** (72 h), and the **rejection count** (at most 2).
9. **Escalation reason codes mapped to plain words** (brief 9.3), with the code kept for Details.
10. **Proposer identity** on proposals: "Rules engine (the AI was off or not needed)" or "AI (model, instructions name and version)".
11. **AI call count and cost** per case, or "no AI calls".
12. **"Credit verified" flag** per case, to disable Return to the agent.
13. **"Seller wrote that they withdraw" flag**, to show Confirm withdrawal.

---

## 8. Safety rules (non-negotiable; brief section 13)

1. No control lets the AI's output trigger anything. The UI only records decisions.
2. What is approved is exactly what is shown: the exact amount, or the exact recipient, subject and body. Bind the decision to the hash.
3. Every number in an email shows its source.
4. AI and seller text is escaped plain text, labelled "exactly as recorded". Never render markdown or HTML from it.
5. No correct charge and no free-text field on the seller screen.
6. "Could not prove it happened" is never shown as success. Show it with a closed or attention mark, never coral and never a check icon.
7. Every decision carries a listed approver. No free-text names.
8. No invented figures. Totals are sums of rows.
9. Emails go only to the test address. Say so where the seller acts.

Coral (`--color-paid`) appears **only** on: the Resolved: credited chip, the Closed phase of a credited case, the credited timeline mark and tag, and credit amounts and the Credit type in the ledger. Focus rings use `--color-focus`.

---

## 9. Known gaps in the designs

- **Waiting for the hub (Jira WD-43)** was not designed, because the brief gives it no shipment number. Build it from brief section 7: headline "Waiting for the hub to re-measure the parcel (Jira WD-43).", stepper Investigated = waiting, rounds titled "After the hub's finding", and the actor **Hub**.
- **Phone layouts** were not designed in detail. Follow brief section 12:
  - the stepper becomes a vertical list;
  - "who · time" moves above the step title;
  - Review stacks the recommendation above "If you approve", with a sticky decision bar;
  - tables scroll inside their frame.
- SHP-000005's timeline was modelled on SHP-000013 (no screenshot existed). Use the real record.
- The ledger shows one charge row per shipment. If SHP-000009 has a second debit row in the real ledger, show it in the group.
- A multi-round case (collapsing earlier rounds) was not drawn. When a case has 3 or more rounds, fold all but the latest round by default.

---

## 10. Acceptance checklist (brief section 15)

- [ ] Gate plus four tabs. Review offers "Review as" (listed approvers) when hosted; the token too, locally.
- [ ] Agent steps reads: case header → headline → Why/Next → stepper (with words) → figures (eligible only) → timeline by round.
- [ ] Every step shows who and when; bookkeeping steps are quiet.
- [ ] The stepper uses "not needed" and "not reached", never strike-through.
- [ ] T02 and T05 read without contradiction and show no figures.
- [ ] AI reasoning is a quote labelled "exactly as recorded", with who proposed it.
- [ ] Review shows "What the agent recommends" beside "If you approve", with every number's source.
- [ ] With-a-person cards: Take over; Return to the agent (note required; refused once a credit is verified); Confirm withdrawal (only when the seller asked).
- [ ] The seller is warned before disputing a not-delivered shipment, and never sees a correct charge.
- [ ] All money reads ₹1,820.00; all times read 7 Nov 2026, 16:08 UTC.
- [ ] Coral only on credited/settled; focus ring on every control.
- [ ] Internal ids and codes only in Details.
- [ ] Every data region has loading, empty, error and full states.
- [ ] Works at 375 px with no horizontal page scroll; keyboard focus always visible.

---

## 11. Suggested build order

1. Tokens, base styles, the shell (top bar and routes), and the money and time helpers.
2. Components: chip, actor pill, stepper, timeline step, recorded text block, buttons, card, segmented filter, toast.
3. Agent steps, with all 9 sample cases and the URL-driven selection.
4. Review: sign-in, count line, tabs, credit card, email card, with-a-person cards, integrity error.
5. Raise a ticket.
6. Ledger.
7. Gate, then the loading, empty and error states.
8. Phone pass, then run the acceptance checklist.

### If you stay on Streamlit

Streamlit cannot reproduce most of this cleanly: custom row buttons, `<details>` folds inside a timeline, sticky panels and the exact chip styles. Two options:
- **(a)** Render each screen's HTML through a small custom component (`streamlit.components.v1` or a `st.html` block with escaped data), and keep the Python side for data and decisions.
- **(b)** Move the UI to a small React (Vite) front end over the existing service API.

Pick one before starting, and keep the safety rules either way.

### Prompt to start Claude Code with

```text
Read HANDOFF.md, then CLAUDE_DESIGN_BRIEF.md, then open each file in design/.
Rebuild the Dispute Desk console UI to match the designs, following the build
order in HANDOFF.md section 11. Use the tokens in section 1 exactly. Treat
section 8 (safety rules) as hard requirements and section 7 as a list of data
the UI needs; tell me before changing any backend behaviour. Do not invent
figures: where the seed data lacks a value, leave it visible as a gap and tell
me. Finish by running the acceptance checklist in section 10 and listing
anything that fails.
```
