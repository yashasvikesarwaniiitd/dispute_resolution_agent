# 0070 — A seller can add a note when raising a dispute

Date: 2026-11-27
Status: **accepted** (owner, 2026-11-27: "add a note adding option for seller while raising the dispute, and store it and make sure it is readily available in future to use by the agent"). Supersedes the "No free-text complaint" bullet of ADR 0044.

## The problem

ADR 0044 left the seller's words out of the raise form: "adding a box is its own decision". The case already had a place for them (`CaseFacts.statement`, untrusted text), and every reader already used it, but the live forms never filled it. Only the eval fixtures did.

## Decision

The seller view (Dispute Desk and Streamlit console) has an optional **Note for the agent** box, up to 1,000 characters.

- The note travels as `statement` on `POST /events` (`RaiseDispute`). Blank means none. Longer than 1,000 characters is refused with 422, and nothing is raised.
- Intake stores it on the case's facts as `UntrustedText` (origin `dispute_form`, reference `form-<event id>`). Facts are saved with the case, so the note lasts as long as the case does.
- **What reads it (unchanged code, now fed):**
  - every round's briefing, where it is fenced outside text the agent can cite (`dispute_statement`);
  - the input screen, before the agent sees it;
  - the decision-quality judge;
  - the Ask assistant;
  - the case's Details tab ("Seller's dispute statement").

## Consequences

- The note is the seller's claim, never a figure: code still proves every amount (I5, I9). An injection attempt in it is screened like the existing `injection_in_statement` eval fixture.
- **[Gap]** A note sent while the shipment already has an open case, or when a closed case is reopened, is not added to that case. The seller screens block a second raise on an open ticket, so only a reopen can lose a note.
- Tests: `tests/unit/workspace/test_seller_note.py` (stored, shown, in the briefing; blank; too long; one limit) and the console raise test.
