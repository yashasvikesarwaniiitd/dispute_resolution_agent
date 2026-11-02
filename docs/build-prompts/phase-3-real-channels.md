# Phase 3: Real channels, executor and verification

## Role
Principal engineer connecting the system to two real services without compromising safety.

## Context
Evidence and calculation work. The executor and policy exist with fakes. Read the current official documentation for Microsoft Graph mail (send and read) and Jira Cloud REST before coding, and link them in the ADR. Do not rely on memory.


**Task cards: `docs/spec/task-cards/phase-3.md`.** They are the executable form of the tasks below: each names its files, the spec sections it implements, the tests to write first and the command that proves it. Where a card and this prompt differ, the card is newer — it was written against the frozen `docs/spec/` (`CLAUDE.md` section 18). Read `docs/spec/INDEX.md` and `docs/spec/escalation-guide.md` first.

## Objective
One synthetic dispute becomes a saved case, sends a real Outlook email after you approve it in a review queue, resumes on a real reply, and opens a real Jira issue that is read back and verified. No AI yet; messages are templated.

## Scope
### In scope
Outlook adapter, Jira adapter, fakes for both, shared contract suites, executor with read-back verification, reply poller and matching, templated messages, contact limits, a minimal approvals module with a message review queue, timeline view, demo script.
### Out of scope
LLM, ledger, credit approvals, cross-case.

## Tasks
1. Contract test suites for `MessageChannel` and `TicketingSystem`. Write them first. Run them against the in-memory fakes.
2. `OutlookGraphChannel`: send, fetch replies, `get_message` for read-back; authentication with the owner's test account through environment variables; store message and conversation ids.
3. `JiraCloudTicketing`: `upsert_issue` idempotent on a stored key (search by a case label before creating) and `get_issue` for read-back.
4. Wire both through `ActionExecutor`: PLANNED, SENT, then verification by reading back (delivery confirmed, issue exists with the expected fields). A mismatch or timeout becomes UNKNOWN and then `NEEDS_HUMAN_REVIEW`.
5. Templated messages in `packs/weight_dispute/templates`: evidence request and reminder. In the pilot policy every message kind needs approval (D6), so each waits in the review queue. The upheld-charge and credit-confirmation templates arrive in Phase 5.
6. Reply poller: match replies by conversation id and case reference. Ignore unrelated mail. No match goes to human review. Treat reply text as untrusted data (I5).
7. Contact limits from `policy.yaml`; a retry never sends a second copy.
8. Timeline view: a simple page listing a case's events in order with time and cause.
9. Demo script `make demo` for the kurta case, plus a setup guide for the Entra app registration and Jira API token that uses `.env.example` names only.
10. ADRs: polling versus webhooks for email, Jira idempotency approach, verification strategy, message review design.
11. Minimal approvals module: a store-backed `ApprovalService` for messages and a Streamlit review queue showing the exact draft and each number with its source, with approve and reject (with a reason). Approving stores the approver, the time and a hash of the exact text. The executor sends only text whose hash matches. Rejecting returns the case to `INVESTIGATING`. Credits are not handled here.

## Technical requirements
- Real-service tests are marked `integration` and need explicit opt-in. Default `make verify` uses fakes only.
- Every network call has a timeout and capped retries on idempotent calls only.

## Acceptance criteria
- Both fakes and both real adapters pass the same contract suites (`make test` for fakes; `make test-integration` for real).
- Running `make demo`: the evidence-request draft appears in the review queue; after you approve it the email arrives; the case waits in `AWAITING_SELLER`; a reply from the test seller address moves it to `INVESTIGATING`; a Jira remeasurement issue is created and read back; its key is stored.
- Retrying the send does not produce a second email or a second issue (test).
- An unrelated email is ignored; an unmatched reply goes to human review (tests).
- A message of a review-required kind is not sent until approved, and the text sent equals the approved text by hash. A rejected draft is never sent. With `approvals` switched off, the case ends in `NEEDS_HUMAN_REVIEW` with reason `module_unavailable:approvals` (tests).
- With `email` disabled, the case moves to `NEEDS_HUMAN_REVIEW` with reason `module_unavailable:email` (test).
- The timeline shows every step with time and cause.

## Deliverables
Adapters, fakes, contract suites, templates, demo script, setup guide, ADRs.

## Constraints
I2, I5, I10, I11, I12. Never print or log tokens or message bodies unredacted.

## Process
Plan mode first. Ask the owner to create the Entra app registration and Jira token themselves; never request secrets in chat.

## Model and effort
`opusplan`, effort medium. Run the `safety-reviewer` subagent after the executor and read-back code. Escalate to Opus if an API behaviour contradicts the contract suite.

## Stop and ask if
A permission scope looks broader than needed, or a documented API behaviour differs from what the contract suite assumes.
