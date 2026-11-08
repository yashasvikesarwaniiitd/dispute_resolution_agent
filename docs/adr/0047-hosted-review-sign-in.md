# 0047 — Hosted, the site password stands in for the review token

Date: 2026-11-08
Status: accepted (D67). Amends ADR 0045 (one password in front of every screen) and the hosted use of ADR 0018 (the review console token).

## Decision

On the hosted console, someone who has passed the site password (`CONSOLE_PASSWORD`) reviews by typing only their name. The review console token is no longer asked for there, and `make hosted-secrets` no longer copies it.

Run locally, nothing changes. There is no site password locally, so the token is still the only lock on Review.

A name is still required. `reviewer_named` (approvals module, next to `sign_in`) accepts letters, digits, spaces, dots, dashes and underscores, up to 40 characters. It records the reviewer with the same `human:` prefix that R10 checks before any send. So every approval and every case-desk decision is still recorded against a named person, bound to the exact amount or text (I3, I13).

## Why

- **Two locks on one door.** Hosted, nobody reaches any screen without the site password. The token was a second secret behind the first, guarding the same people.
- **The demo's most important screen was out of reach.** Anyone given the site password to try the app could raise tickets and watch the agent, but could not approve. Approving is the human-in-the-loop step the project exists to show.
- **What the token added was identity, and the name keeps it.** The safety properties sit in the record, not the login: a stored, named approval; the hash binding; R10's human-prefix check; the executor's read-back.

## Options considered

1. Keep both (status quo): safest, but the review screen stays locked to anyone without a second secret.
2. **The site password stands in for the token; a name is required (chosen).**
3. Remove sign-in entirely: approvals would be recorded against nobody, which breaks I3 and I13. Rejected.

## How it is enforced

- `ui/screens/reviewer.py` offers the name-only form only when `gate_passed` is in the session. Only the hosted gate (`ui/app.py`, `_through_the_gate`) sets that, after checking the password. A browser cannot set server-side session state.
- Without it (a local run), the token form is shown, unchanged.
- Tests (`tests/unit/ui/test_hosted.py`):
  - past the gate, a name opens the queue, and a decision is recorded as `human:<name>`;
  - a blank or odd name is refused;
  - run locally, the token is still required.

## Consequences

- On the live site, anyone with the site password can approve credits and messages. Credits post to the simulated ledger; messages go only to the owner's test address (I11). The password should be shared only with people the owner would let approve.
- A typed name is not verified identity. It is accountability within a group already trusted by the password, which is what the token gave too: the token was shared, not personal.
- Old hosted Secrets that still hold `REVIEW_CONSOLE_TOKEN` keep working. The value is unused there and can be deleted.

## Sources

None new. Streamlit session state is server-side (already relied on by ADR 0045's gate).
