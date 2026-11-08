# Design brief: Dispute Desk, the operations console for an AI dispute resolution agent

**For:** Claude Design
**Design system:** use **Hallmark** (github.com/Nutlope/hallmark), **Coral** theme, app-screen scope (details in section 9).
**Time box:** **30 to 45 minutes.** Section 11 says what to build first and what to leave out.
**Output:** a clickable, high-fidelity prototype of all the screens in section 5, with realistic sample data (section 8) and every state in section 6.

---

## 1. What the product is

An **AI agent that resolves billing disputes** end to end: a customer says "you charged me wrongly", and the agent carries the case to a checked, approved, proven outcome.

**For each case, the agent:**
1. Gathers the evidence.
2. Recalculates the charge in code.
3. Proposes one next step.
4. Gets a person's approval.
5. Acts in real systems: email, a Jira ticket, a credit in the ledger.
6. Proves each action happened by reading it back.

**The first domain is courier and shipping weight disputes.** A seller is billed for a parcel at the wrong weight (for example 20 kg instead of 7 kg) and disputes it.

**The core idea the design must make visible:**

> **The AI investigates and proposes. Code decides. A person approves anything that touches money or a customer.**

**The console has four users:** a **seller** (raises disputes), an **observer** (anyone curious how the agent thinks), a **reviewer** (the human in the loop) and **finance** (the ledger).

All data is **synthetic**. Amounts are in Indian rupees (₹).

---

## 2. Design goals, in priority order

1. **Trust through transparency.** Anyone can see *why* the agent did each thing, and *who* did it: code, AI, rules, a person, the seller or the hub.
2. **The human stays in control, visibly.** Approval is a deliberate, informed act. The reviewer sees the AI's recommendation *beside* exactly what will happen.
3. **Where is it, and what's next?** Every case answers both in one glance.
4. **Calm operations tool, not a marketing page.** Dense where it helps, quiet otherwise. Familiar patterns win: top bar, tabs, tables, cards.
5. **Honest.** No invented metrics, no fake testimonials, no decorative charts.

---

## 3. Users and what they need

| User | Goal | Signs in with | Must never be able to |
|---|---|---|---|
| **Visitor** (anyone with the link) | Get into the app | **Site password** (one shared password) | See any screen without it |
| **Seller** | Dispute a wrong charge, track their tickets | (site password only) | See the "correct" charge before the agent works it out |
| **Observer** | Watch how the agent reasons, step by step | (site password only) | Change anything |
| **Reviewer** (human in the loop) | Approve or reject credits and emails; handle cases the agent handed over | **Review token + their name** (a second lock) | Approve something different from what they saw |
| **Finance** | See every charge and credit per seller, and where each credit came from | (site password only) | Edit the ledger |

---

## 4. Navigation and global layout

- **Top bar**, not a sidebar: there are only four destinations.
  - **Left:** wordmark "Dispute desk".
  - **Tabs:** **Raise a ticket**, **Agent steps**, **Review**, **Ledger**. Use outline icons (receipt, route, checklist, bank), never emoji.
- **One content width**, capped at about 1200px and centred. Generous page margins.
- **Before any screen,** the site password gate (screen 0).
- The **selected case lives in the URL** (`/steps?case=…`), so a case view can be shared and reloaded.

---

## 5. Screens and use cases

### Screen 0: Site password gate

**Purpose:** the app does real things (real email, real Jira tickets), so nothing opens without the password.

**Content:**
- Title "Dispute desk".
- One sentence: *"A live dispute resolution agent on synthetic data. It sends real email to a test address and opens real Jira issues in a test project, so it is behind a password."*
- A password field and an **Enter** button.

**States:**
- **Default.**
- **Wrong password:** inline error "That password is not correct."
- **Not configured** (fails closed): the error *"This hosted console has no password set (CONSOLE_PASSWORD), so it stays closed. It sends real email and opens real Jira issues."* No form.
- **Service failed to start:** *"The dispute service could not start: <which setting is missing>"*. It never shows a secret value.

---

### Screen 1: Raise a ticket (seller)

**Header:** "Raise a ticket". Sub-line: *"Signed in as seller SELL-0001. Every shipment here is synthetic; emails about your tickets go to the test seller address."*

**Layout:** two columns on desktop, stacked on mobile.

**Left: "Your shipments"** (table):

| Shipment | Status | Zone | Charged |
|---|---|---|---|

- Status values: *Delivered*, *Returned to origin*.

**Right: "Dispute a charge"**
1. **Shipment** selector (placeholder "Choose a shipment").
2. Once chosen, **the bill**:
   - **Charged ₹2,800.00** for zone E
   - You declared 40 x 35 x 25 cm, 7.00 kg
   - The hub measured 40 x 35 x 25 cm, 7.00 kg
3. **Primary button: "Raise a dispute".**
4. **Result message:**
   - success: *"Ticket raised. The agent has started on it."*
   - duplicate: *"This ticket was already raised; showing the existing case."*
   - not allowed: *"This seller address is not on the test allowlist."*
   - service down: *"The dispute service is not running…"*
5. If the shipment already has an **open** ticket: no button, but *"You already have an open ticket on this shipment:"* plus a status chip.

**Below: "Your tickets"** (table):

| Case | Shipment | Status | Opened | Last update |
|---|---|---|---|---|

Then a selector "See the agent's steps for…" and a button **"Open the steps"**, which goes to screen 2 with that case.

**Use cases:**
- **UC-1** A seller disputes a wrongly billed parcel and sees the ticket appear.
- **UC-2** The seller raises the same dispute twice: they're told it already exists (no duplicate case).
- **UC-3** The seller disputes a returned parcel: the ticket closes as "Not eligible" (visible in tickets and in Agent steps).
- **UC-4** The seller checks their tickets' status and jumps to the agent's steps.

**Empty states:**
- No tickets: *"No tickets yet. Choose a shipment above to raise one."*
- No store yet: *"There is no case store yet…"*, with the setup action.

**Must not show:** the "correct" charge. Only what was billed and measured. The seller form has **no free-text box**, deliberately: a seller's words are untrusted input to the AI.

---

### Screen 2: Agent steps (the AI's process, step by step)

**This is the signature screen.** It's where people *see the agent think*. Read top to bottom, it answers three questions in order.

**Top: case selector.** Each option reads like "SHP-000002, Awaiting approval, opened 07 Nov 13:24".

**Block A: "Where is it, and what's next?" (headline card)**
- A **status chip**, for example "Awaiting approval".
- A **headline** in one sentence, larger. Examples by state:
  - *"Waiting for a reviewer to approve a ₹1,820.00 credit."*
  - *"With a person: proposal escalated (B5: the round contains image evidence, which needs judgement)."*
  - *"Waiting for the seller to reply."*
  - *"Waiting for the hub to re-measure the parcel (Jira WD-43)."*
  - *"Resolved: the seller was credited ₹1,820.00."* (the one place the accent colour celebrates)
  - *"Closed: this shipment could not be disputed."*
  - *"The agent is working on it."*
- **Next:** one sentence on what happens next. Examples:
  - *"Approve or reject it on the Review screen. Nothing is sent until then."*
  - *"If they do not reply, a reminder goes out, and then the case closes."*
  - *"Nothing left to do. The credit was proved by reading it back from the ledger."*
- A muted meta line: *"Shipment SHP-000002, opened 07 Nov 13:24, 3 AI calls, USD 0.0186, refreshing while open."*
  - When no AI was used: "no AI calls".

**Block B: progress, five phases**

**Raised → Investigated → Approved → Acted → Closed**

Each phase shows its state **in words** (never colour alone):

| Mark | Meaning | Suggested look |
|---|---|---|
| done | passed | quiet, solid outline |
| now | the agent is working here | bold, ink outline |
| waiting | paused on someone (seller, hub, reviewer) | bold, **dashed** outline |
| with a person | handed to a human | bold, tinted background |
| skipped | never happened (for example an ineligible case) | dotted, muted |
| later | not reached yet | muted |

On a credited case, "Closed" takes the accent (coral) as the settled state.

**Block C: what happened, round by round**

- Group headings: **"The dispute"**, then **"Round 1: First look"**, **"Round 2: After the seller replied"**, **"Round 3: After the hub's finding"**, **"Round N: After a person returned it"**.
- Each **step** is a compact row:
  - **Left:** an **actor tag** (Code, AI, Rules, Person, Seller, Hub, System) and the time.
  - **Right:** the **numbered title**, a **one-line summary**, and an optional tag ("Credited" or "Needs a person").
  - **Details** (collapsed): the full detail lines, and *"Reasoning and findings, exactly as recorded:"* followed by the AI's text **as plain monospace text**.

**A full example trail** (case SHP-000002):

| # | Actor | Title | Summary |
|---|---|---|---|
| **The dispute** | | | |
| 1 | Seller | Dispute raised on SHP-000002 | |
| 2 | Code | Checked eligibility: eligible | |
| **Round 1: First look** | | | |
| 3 | Code | Gathered the evidence | Found 5 of 8; missing: parcel image, earlier shipments of this item, hub finding |
| 4 | Code | Recalculated the charge | Charged ₹2,800.00, correct ₹980.00, overcharged ₹1,820.00. Code proved calculation error. |
| 5 | Rules / AI | Proposed: Credit the seller | High confidence. Policy: allowed, but a person must approve first |
| 6 | Person | owner approved the credit of ₹1,820.00 | Note: … |
| 7 | Code | Posted a credit to the ledger (txn_b5c2…) | |
| 8 | Code | Proved it happened by reading it back | Next: Awaiting approval |
| 9 | Person | owner approved the message | |
| 10 | Code | Sent an email (1a1b…) | |
| 11 | Code | Proved it happened by reading it back | Next: Resolved: credited (**Credited** tag) |
| 12 | System | Outcome shared with pattern detection | |

**More step types to design for:**
- *"The seller replied"* (Seller)
- *"The hub sent its finding"* (Hub)
- *"Opened a ticket for the hub (WD-43)"* (Code)
- *"A deadline passed"* (System)
- *"Could not prove it happened"* ("Needs a person" tag)
- *"Handed to a person"* (System)
- *"Checked eligibility: not eligible"*
- *"A person took the case over"*
- *"A person returned the case to the agent"*

**Use cases:**
- **UC-5** An observer opens a case and immediately understands where it is and what's next.
- **UC-6** An observer follows a multi-round case: evidence request → seller reply → hub re-measure → credit.
- **UC-7** An observer sees *why* a case went to a person, in plain words.
- **UC-8** An observer reads the AI's reasoning, clearly labelled as recorded text.
- **UC-9** A live case updates by itself while it is open (show a subtle "refreshing while open").

**Empty state:** *"No cases yet. Raise a ticket on the seller screen and its steps appear here."* plus a link to Raise a ticket.

---

### Screen 3: Review (the human in the loop)

**Step 1: sign in** (the second lock).
- Card text: *"Sign in with the review console token to see the queue."*
- Fields: **Review console token** (password) and **Your name** (default "owner"). Button **Sign in**.
- Error: *"the review console token is not correct"*.
- Once signed in, a caption: *"Signed in as owner. Your name is recorded against each decision, bound to the exact amount or text shown."*

**Step 2: two tabs.**

**Tab A: "Awaiting your approval (N)".** One **card per pending item**:
- **Card header:** "Credit" or "Message: credit confirmation", plus shipment and a status chip.
- **Two columns, side by side** (this comparison is the heart of the screen):

| **What the agent recommends** | **If you approve** |
|---|---|
| "Credit the seller, confidence high" | **Credit:** "A credit of **₹1,820.00** is posted to the ledger for calculation error." |
| muted: "Rules engine (the AI was off or not needed)" or "AI (gpt-5-mini, instructions case_investigator v2)" | caption: "Derived by code from the assessment… The model chose to propose a credit; it did not choose the amount." |
| **Reasoning, exactly as recorded:** plain text | **Code's figures behind it:** chargeable weight 7,000 g, recalculated charge ₹980.00, amount charged ₹2,800.00, overcharge ₹1,820.00 |
| Findings: "Verified fact: …" (cites ids) | **Message:** "An email to …" / Subject / body as plain text / **"Every number in it, and where it came from":** ₹1,820.00: credit amount derived by code |

- **Note** field: "Note, or your reason for rejecting".
- **Buttons:** **Approve** (primary) and **Reject** (secondary). Rejecting requires a reason.
- **Integrity error state:** *"The stored text has changed since approval was requested. It cannot be approved. Report this."* No buttons.

**Tab B: "With a person (N)".** One card per case the agent handed over:
- Shipment, status chip, "since 7 Nov 14:06".
- **"Why it is with a person: proposal escalated"**, then the detail as plain text: *"B5: the round contains image evidence, which needs judgement"*.
- Muted: *"Last proposal: Hand to a person, Rules engine (the AI was off or not needed)"*.
- Link: *"See every step of this case"* (to screen 2).
- **Note** field: "Your note (needed to return it to the agent)".
- Checkbox: *"When taking over, tell the seller a person is handling it (the email goes through review first)"*.
- **Three actions:**
  - **Take over** (primary): the agent stops and the person owns the case.
  - **Return to the agent:** re-runs the case from fresh evidence. Needs a note. Disabled, with the caption *"A credit on this case is already verified, so it cannot go back to the agent"*, when a credit was already paid.
  - **Confirm withdrawal:** shown **only** when the seller asked to withdraw.
- After any action, a toast: *"Recorded. The agent applies it within a few seconds."*

**Use cases:**
- **UC-10** A reviewer approves a ₹1,820 credit after comparing the AI's recommendation with code's figures.
- **UC-11** A reviewer approves a confirmation email after checking every number's source.
- **UC-12** A reviewer rejects a draft with a reason; the agent re-plans.
- **UC-13** A reviewer takes over a case with a photo the AI can't judge, optionally notifying the seller.
- **UC-14** A reviewer returns a case to the agent with a note.
- **UC-15** A reviewer confirms a seller's withdrawal.
- **UC-16** A wrong token shows no queue at all.

**Empty states:**
- *"Nothing is waiting. Every credit and message has been decided."*
- *"No case is waiting for a person."*

---

### Screen 4: Ledger (finance)

- **Header:** "Ledger". A **Seller** selector (SELL-0001).
- **Three figures**, tabular numerals, no trend arrows:
  - **Charged** INR 11,660.00
  - **Credited back** INR 1,820.00
  - **Net billed** INR 9,840.00
- Caption: *"Every figure is a sum of ledger rows below. Credits show who approved them and whether the agent proved them by reading them back."*
- **Table:**

| Posted | Shipment | Type | Amount (INR) | Reason | Case | Approved by | Proved | Transaction |
|---|---|---|---|---|---|---|---|---|
| 7 Nov 2026, 14:20 | SHP-000002 | **Credit** (accent) | 1820.00 | calculation error | case_01M6… | owner | Read back | txn_b5c2… |
| 1 Jan 2026, 00:00 | SHP-000002 | Charge | 2800.00 | weight charge | | | | TXN-SHP-000002-1 |

- A credit **not** posted by the agent shows *"Not posted by the agent"* under Proved.

**Use cases:**
- **UC-17** Finance sees a seller's net position.
- **UC-18** Finance traces any credit to its case, its approver and its read-back proof.

**Empty state:** *"The ledger has no entries yet. It is created and seeded with the synthetic charges when the dispute service starts."*

---

## 6. Case states: the vocabulary (use these exact labels)

| Internal state | Label shown | Tone |
|---|---|---|
| RECEIVED | Received | working |
| GATHERING_EVIDENCE | Gathering evidence | working |
| CALCULATING | Recalculating | working |
| INVESTIGATING | Investigating | working |
| AWAITING_SELLER | Waiting for the seller | waiting |
| AWAITING_OPS | Waiting for the hub | waiting |
| AWAITING_APPROVAL | Awaiting approval | waiting |
| EXECUTING | Acting | working |
| VERIFYING | Checking it happened | working |
| NEEDS_HUMAN_REVIEW | With a person | attention |
| HANDED_TO_HUMAN | Handed to a person | attention |
| RESOLVED_CREDITED | Resolved: credited | **settled (the only accent use)** |
| RESOLVED_UPHELD | Resolved: charge upheld | done |
| WITHDRAWN | Withdrawn | closed |
| CLOSED_NO_RESPONSE | Closed: no reply | closed |
| REJECTED_INELIGIBLE | Not eligible | closed |

**Chip styles by tone:**

| Tone | Style |
|---|---|
| working | neutral outline |
| waiting | dashed outline |
| attention | ink outline, bold |
| done | neutral |
| closed | muted |
| settled | coral outline and coral text, bold |

**AI proposal actions** (plain words):
- Credit the seller
- Uphold the charge
- Ask the seller for evidence
- Ask the hub to re-measure
- Hand to a person
- Wait

**Evidence item names:**
- Delivery status
- Declared size and weight
- Hub measurement
- Delivery zone
- Billing entries
- Parcel image
- Earlier shipments of this item
- Hub finding
- Seller's reply

**Actor tags:** Code, AI, Rules, Person, Seller, Hub, System. Make **AI** and **Rules** visually distinct from **Code**, since that's the point of the product. Make **Person** stand out.

---

## 7. Safety rules the design must keep (non-negotiable)

1. **The AI's own words are always plain text**, never rendered as rich text or markdown, and always labelled *"exactly as recorded"*.
2. **Every amount is written the same way:** `₹1,820.00` (rupee sign, Indian grouping, two decimals). Never "INR 1820.00" in prose; the Ledger table may use "Amount (INR)" with plain decimals.
3. **Approve shows exactly what will happen:** the exact amount, or the exact email text, recipient and subject. No summaries in place of the real thing.
4. **Every number in an email shows its source** ("₹1,820.00: credit amount derived by code").
5. **No "correct charge" on the seller screen.** Only the agent's recalculation shows it, in Agent steps.
6. **Two locks:** the site password for everyone, then the review token for approvals and case actions. Show the reviewer's name after sign-in.
7. **No invented figures anywhere.** No dashboards of fake KPIs, no testimonials, no "trusted by" logos.

---

## 8. Sample data (synthetic; use it)

**Seller:** SELL-0001.

**Shipments:**

| Shipment | Status | Zone | Charged | Story (for your content, not shown to the seller) |
|---|---|---|---|---|
| SHP-000001 | Delivered | B | ₹152.00 | Box measured larger; parcel photo, so it goes to a person (B5) |
| SHP-000002 | Delivered | E | ₹2,800.00 | Keyed 20,000 g for a 7,000 g parcel; credit ₹1,820.00 |
| SHP-000003 | Delivered | B | ₹380.00 | Ambiguous image: with the AI on, a hub re-measure; with rules only, to a person |
| SHP-000004 | Delivered | B | ₹152.00 | Same item billed lighter before: with the AI on, a hub re-measure; with rules only, to a person |
| SHP-000005 | Delivered | B | ₹190.00 | Scanner fault: evidence request → reply → hub → credit |
| SHP-000007 | Returned to origin | B | ₹152.00 | Not eligible |
| SHP-000010 | Delivered | E | ₹2,800.00 | Two definite errors, so it goes to a person |

**Cases to show:**
- one **Awaiting approval** (SHP-000002, ₹1,820 credit);
- one **With a person** (SHP-000001, B5 image evidence);
- one **Resolved: credited** (SHP-000005, three rounds: first look, after the seller replied, after the hub's finding, with a Jira ticket WD-43);
- one **Not eligible** (SHP-000007).

**AI cost line:** "3 AI calls, USD 0.0186" on an AI case; "no AI calls" on a rules case.

**Reviewer name:** "owner" or "Asha".

---

## 9. Design direction: Hallmark, Coral theme, app-screen scope

**Use Hallmark** (github.com/Nutlope/hallmark). This is an **app screen** (a tool people work in), so apply its app-screen rules: the theme lends **palette, type, radius and surfaces only**, never a marketing layout, hero or decorative scene.

**Coral tokens** (already used in the live app; match them):

```css
:root {
  --color-paper: #f8f4f1;   --color-paper-2: #f1ece8;  --color-paper-3: #e8e1dd;
  --color-ink: #1d1614;     --color-ink-2: #312926;    --color-muted: #615955;
  --color-rule: #d9d3cf;    --color-rule-2: #c7bfba;
  --color-cotton: #fdfbf8;  --color-board: #1f1917;    --color-board-type: #f6f3ef;
  --color-paid: #ba4a34;    /* coral: the settled (credited) state only */
  --color-focus: #bd3a2b;   /* focus rings */
  --color-accent-2: #bb3d31;/* link underline on hover */
  --font-body: "General Sans", ui-sans-serif, system-ui, sans-serif;  /* 400/500/600 */
  --font-figure: "Geist Mono", ui-monospace, monospace;               /* figures in columns, AI text */
  --radius-card: 8px; --radius-chip: 6px; --rule-hair: 1px;
}
```

- **Fonts:** General Sans (Fontshare) for everything; Geist Mono (Google Fonts) for money columns, figures and the AI's recorded text.
- **Primary buttons:** ink board (`--color-board` with `--color-board-type` text), 8px radius. Secondary buttons: cotton card with a hairline.
- **Coral (`--color-paid`) is spent on one thing:** the credited / settled state (and focus rings). Never a band, a fill behind body text, or a heading colour.

**The Hallmark Floor** (must hold):
- every colour and font through a token;
- no `transition: all`;
- visible `:focus-visible` on every control;
- a `prefers-reduced-motion` fallback;
- no eyebrows or tracked-capitals labels (labels are sentence case, the body face a step smaller);
- no emoji icons;
- no purple-to-blue gradients;
- no card inside a card;
- no invented metrics;
- no middle-dot-joined meta strings.

**App-screen specifics:**
- **Density:** controls 32 to 40px, tables before cards.
- **Every data region has four states:** loading (skeleton), empty (with its one action), error (what broke and what to do) and full.
- **Motion is state only** (short, no choreography).
- **The signature object is the case trail** (Agent steps). Make that screen the most considered.

---

## 10. Responsive and accessibility

- **Desktop first** (1280 to 1440 wide). At 375 wide: tabs scroll, two-column layouts stack, and in Review the recommendation stacks **above** "If you approve".
- **Phase marks and chips carry words**, not colour alone.
- **Contrast:** WCAG AA for all text. Every control is keyboard-reachable with a visible focus ring.
- **Times** shown as "07 Nov 13:24" (local).

---

## 11. Plan for the 30 to 45 minute time box

**Build in this order. Stop when time runs out; the first four are the must-haves.**

1. **(5 min)** Tokens, type and the shell: top bar with four tabs, page width, chips.
2. **(12 min)** **Agent steps**: headline card, phase bar, rounds and step rows with collapsed details. Show SHP-000002 (awaiting approval) and SHP-000005 (credited, three rounds).
3. **(10 min)** **Review**: sign-in card, Tab A with the two-column credit card and message card, Tab B with the "with a person" card and its three actions.
4. **(6 min)** **Raise a ticket**: shipments table, bill panel, success message, tickets table.
5. **(4 min)** **Ledger**: three figures and the table with one credited row in coral.
6. **(3 min)** **Password gate** and the empty states.
7. **If time is left:** mobile layouts for Agent steps and Review, the error and integrity states.

**Leave out:**
- any landing or marketing page;
- charts;
- dark mode;
- settings;
- user management;
- notifications;
- anything not listed above.

---

## 12. Acceptance checklist

- [ ] Four tabs plus the password gate. Review has a second sign-in.
- [ ] Agent steps opens with **headline, next step, five phases (with words), then rounds**.
- [ ] Every step shows **who** did it (Code, AI, Rules, Person, Seller, Hub, System).
- [ ] The AI's reasoning is **plain text** and labelled "exactly as recorded".
- [ ] Review shows **"What the agent recommends"** beside **"If you approve"**, with every number's source.
- [ ] "With a person" cards offer **Take over**, **Return to the agent** (note required) and **Confirm withdrawal** (only when the seller asked).
- [ ] All amounts read **₹1,820.00** (the Ledger table may show plain decimals under "Amount (INR)").
- [ ] Coral appears **only** for credited / settled, and focus.
- [ ] No invented numbers, emoji icons, tracked-caps labels or gradients.
- [ ] Every data region has its empty state.
