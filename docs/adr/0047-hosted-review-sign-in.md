# 0047 — Hosted, the site password stands in for the review token

Date: 2026-11-08
Status: accepted (D67; corrected the same day, D69). Amends ADR 0045 (one password in front of every screen) and the hosted use of ADR 0018 (the review console token).

## Decision

On the hosted console, someone who has passed the site password (`CONSOLE_PASSWORD`) reviews without the review console token. They choose **which listed approver they are reviewing as**, from the names in `config/policy.yaml` (`approvals.approver_ids`; today only "owner"). The token is no longer asked for there, and `make hosted-secrets` no longer copies it.

Run locally, there is no site password, so the token is still the only lock on Review. The same "Review as" choice of listed approvers replaces the free-text name there too.

## The correction (D69)

The first version let the reviewer type any name. That broke approvals. The engine accepts a decision only from an approver on the policy list (T09 when the decision is applied; R10 and R13 again at the executor). A credit approved as "asha" was recorded, then set aside, and the case went to a person. It failed safe: nothing was paid or sent wrongly. But no approval under another name could ever take effect. The tests had checked that the decision was recorded, not that the engine then acted on it.

Now Review offers only names the engine accepts, read from the same file the engine reads (`ui/data.approvers`). To let someone review under their own name, the owner adds `human:<name>` to `approvals.approver_ids`.

## Why

- **Two locks on one door.** Hosted, nobody reaches any screen without the site password. The token was a second secret behind the first, guarding the same people.
- **The demo's most important screen was out of reach.** Anyone given the site password could raise tickets and watch the agent, but could not approve. Approving is the human-in-the-loop step the project exists to show.
- **The approver list is the identity rule, and it stays.** The safety properties sit in the record, not the login:
  - a stored approval by a listed approver (I3, I13);
  - the hash binding;
  - R10's and R13's approver re-check;
  - the executor's read-back.

## Options considered

1. Keep both locks (status quo): safest, but the review screen stays locked to anyone without a second secret.
2. **The site password stands in for the token; the reviewer chooses a listed approver (chosen).**
3. Let any typed name approve on the hosted site (widen the approver list to anyone with the password). The approver list would then mean nothing there. Rejected.
4. Record every hosted approval as "owner" and show the typed name. The record would name someone other than who clicked. Rejected.
5. Remove sign-in entirely: approvals would be recorded against nobody, which breaks I3 and I13. Rejected.

## How it is enforced

- `ui/screens/reviewer.py` offers the token-free form only when `gate_passed` is in the session. Only the hosted gate (`ui/app.py`, `_through_the_gate`) sets that, after checking the password. A browser cannot set server-side session state.
- Both forms offer only `ui/data.approvers()`, which is `approvals.approver_ids` from `config/policy.yaml`, the file the engine is built from.
- `reviewer_named` (approvals module) still refuses a malformed name, as a second check.
- Tests (`tests/unit/ui/test_console_screens.py`):
  - past the gate, only listed approvers are offered, and there is no free-text name;
  - run locally, the token is still required;
  - **a credit approved on the Review screen is posted to the ledger** (driven through the real line);
  - a decision by an unlisted name is not acted on (why no other name is offered).

## Consequences

- On the live site, anyone with the site password reviews as a listed approver ("owner") and can approve credits and messages. Credits post to the simulated ledger; messages go only to the owner's test address (I11). Share the password only with people you would let approve.
- The recorded approver is the person the password holder chose to act as, not verified identity. That is what the shared token gave too.
- Old hosted Secrets that still hold `REVIEW_CONSOLE_TOKEN` keep working. The value is unused there and can be deleted.

## Sources

None new. Streamlit session state is server-side (already relied on by ADR 0045's gate).
