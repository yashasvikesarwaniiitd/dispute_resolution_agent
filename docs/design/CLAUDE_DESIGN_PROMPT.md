# Prompt for Claude Design

**How to use it:**
1. Start a Claude Design session.
2. Attach `CLAUDE_DESIGN_BRIEF.md` and `SCREENS.pdf` (both in this folder).
3. If your session can read GitHub, also give it github.com/Nutlope/hallmark.
4. Paste everything inside the box below.
5. Follow up with the short prompts at the end, one at a time.

---

```text
You are a senior product designer for B2B operations tools. You specialise in
human-in-the-loop AI products, where trust comes from showing your working.

THE PRODUCT
Dispute Desk is the operations console for an AI agent that resolves shipping weight
disputes. A seller says "you charged me for 20 kg, my parcel weighs 7 kg".
- The agent gathers evidence and has CODE recalculate the charge. The AI never does
  the maths.
- The AI (or a rules engine when the AI is off) proposes one next step.
- A PERSON approves anything that touches money or the seller.
- The agent then acts (Gmail, Jira, a ledger) and proves each action happened by
  reading it back.
The idea every screen must make visible: the AI investigates and proposes, code
decides the numbers, a person approves, and every step says who did it.

YOUR INPUTS
1. CLAUDE_DESIGN_BRIEF.md is the source of truth. It covers users, 31 use cases,
   process flows (state machine, approval, evidence rounds, the with-a-person desk,
   not-eligible paths, sign-in), every screen, every state, exact labels, sample
   data, safety rules and an acceptance checklist.
2. SCREENS.pdf shows the console as built today: 20 screenshots, desktop and phone.
   It works and its structure is sound, but it looks like a styled Streamlit app.
   Keep its information architecture and wording. Make it feel like a crafted,
   calm, trustworthy product. Each screen in the brief has an "Improve on today"
   list. Solve those.
3. Design system: Hallmark (github.com/Nutlope/hallmark), Coral theme, app-screen
   scope. Use the tokens in the brief, section 11. The theme lends palette, type,
   radius and surfaces only: no hero, no marketing layout, no decorative scene.

WHAT TO MAKE
A clickable, high-fidelity prototype at 1440 px, with phone versions at 375 px for
Agent steps and Review:
- Gate: the site password screen.
- Raise a ticket (seller): click a shipment row to see its bill (charged, declared
  vs measured, one primary action). Warn before disputing a not-delivered shipment.
  Show the result line with a link to watch the agent. "Your tickets" says in a
  sentence what is happening on each.
- Agent steps (the signature screen; spend the most care here):
  - a case header (shipment, status chip, seller, opened/closed, AI calls);
  - a headline, then Why and Next, and a link to where the next move is made;
  - a five-phase stepper (Raised, Investigated, Approved, Acted, Closed). Each mark
    is in words: done / in progress / waiting / with a person / not needed /
    not reached / later;
  - code's figures, only for eligible cases;
  - a timeline grouped by round. Each step shows a number, a title, a one-line
    summary and "who · time". The AI's reasoning is a quote labelled "exactly as
    recorded". Details fold away. Bookkeeping steps are quiet.
  Show three cases: SHP-000002 credited ₹1,820.00, SHP-000009 awaiting approval
  of a ₹152.00 credit, and SHP-000011 not eligible after its scan history.
- Review (human in the loop):
  - hosted sign-in asks only for a name;
  - a one-line queue count, then two tabs;
  - approval cards put "What the agent recommends" (action, confidence, who
    proposed, reasoning, findings) beside "If you approve" (the exact amount and
    how code derived it, or the exact email with every number's source). Note
    field, Approve, Reject (a reason is required);
  - "With a person" cards: why, last proposal, link to every step, Take over /
    Return to the agent / Confirm withdrawal.
- Ledger (finance): three totals that are sums of rows; "Credits from disputes"
  with approved by and proved (read back); all entries with an All / Charges /
  Credits filter.

NON-NEGOTIABLES (from the brief, section 13)
- Approval shows exactly what will happen: the exact amount, or the exact
  recipient, subject and body. Never a summary.
- The AI's and the seller's words are plain quoted text, never formatting.
- The seller never sees a "correct charge". There is no free-text box anywhere a
  seller types.
- "Could not prove it happened" is never shown as success. Unknown means a person.
- Money is always ₹1,820.00. Times are always 7 Nov 2026, 16:08 UTC.
- Coral is spent only on the credited/settled state and on focus rings.
- No invented metrics, charts, testimonials, emoji icons, gradients, tracked-capital
  eyebrows, or cards inside cards.
- Internal ids and codes (case_01M…, txn_…, B3, f_charged) live only in details.
- Status, phases and marks always carry words, never colour alone. WCAG AA, visible
  focus, 40 px targets (44 on touch), no horizontal page scroll at 375 px.
- Every data region has loading, empty, error and full states (brief, section 8).

HOW TO WORK
1. Read the whole brief and look through every page of SCREENS.pdf.
2. Before drawing anything, reply with three things:
   (a) your read of each screen's one job;
   (b) the 5 biggest UX problems you see in SCREENS.pdf, each with your fix;
   (c) the layout plan for Agent steps.
   Keep it under 250 words, then go straight on to designing.
3. Build in the brief's order (section 14): tokens and shell, then Agent steps,
   Review, Raise a ticket, Ledger, gate and empty states, then phone.
4. Use the brief's sample data (section 10) and exact labels (section 9). Do not
   invent new states, numbers or features.
5. Finish by checking your work against the acceptance checklist (section 15).
   List anything you could not meet, and why.
```

---

## Follow-up prompts (paste one at a time after the first pass)

1. *"Show Agent steps for SHP-000002 (credited) at 375 px. The stepper becomes a vertical list; 'who · time' sits above each step title; earlier rounds can collapse."*
2. *"Show the Review credit card for SHP-000009 in three states: default, Reject pressed with no reason (inline error), and the integrity error (stored text changed: no buttons)."*
3. *"Show the loading (skeleton), empty and error states for the case card, the timeline, the review queue and the ledger tables."*
4. *"Design the 'Waiting for the hub' case (Jira WD-43): headline, Why/Next, stepper at Investigated = waiting, and a three-round timeline ending in a credit."*
5. *"Run the acceptance checklist in section 15 of the brief against everything you made. Fix what fails, then list what changed."*

---

## What a good result looks like

- Someone who has never seen the product opens Agent steps and can say, in under 10 seconds, where the case is, why, what happens next and who acts.
- A reviewer can approve a credit without leaving the card, and can name the exact amount and where it came from.
- Nothing on any screen needs the word "API", "state" or an internal id to be understood.
