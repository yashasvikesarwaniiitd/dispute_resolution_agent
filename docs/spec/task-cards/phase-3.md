# Phase 3 task cards — Real channels

Stage A5. Source prompt: `docs/build-prompts/phase-3-real-channels.md`. Phase outcome: one synthetic dispute becomes a saved case, sends a real Outlook email after you approve it in a review queue, resumes on a real reply, and opens a real Jira issue that is read back and verified. No AI yet; messages are templated.

**Phase defaults:** Sonnet, effort medium. **Safety-critical phase** (`CLAUDE.md` section 17): run `safety-reviewer` before closing it. This is the first phase that touches the outside world, so I2, I11, I12 and I13 all become real here rather than theoretical.

**Owner prerequisites** (`HANDOFF.md` section 6, nobody else can do these): an Entra app registration with minimal mail scopes on a personal Outlook.com account (ADR 0023, D36); a Jira Cloud site, a test project and an API token; a second email address as the test seller. Values go in `.env`, never in chat (I12).

| Card | Title | Size | Depends on | Opus review |
|------|-------|------|-----------|-------------|
| P3-01 | Contract suites for `MessageChannel` and `TicketingSystem` | L | P1-* | |
| P3-02 | `OutlookGraphChannel`: send and read-back | L | P3-01 | yes |
| P3-03 | `JiraCloudTicketing`: idempotent upsert and read-back | L | P3-01 | yes |
| P3-04 | Executor verification against real adapters | M | P3-02, P3-03 | yes |
| P3-05 | Message templates | M | P2-02 | |
| P3-06 | Approvals module and the message review queue | L | P1-06 | yes |
| P3-07 | Reply poller | L | P3-02 | yes |
| P3-08 | Contact limits | S | P1-06 | |
| P3-09 | Timeline view | S | P1-11 | |
| P3-10 | `make demo` and the owner setup guide | M | P3-06, P3-07 | |
| P3-11 | ADRs | S | P3-07 | |

---

## P3-01 — Contract suites for `MessageChannel` and `TicketingSystem`

**Files:** `tests/contract/test_message_channel.py`, `test_ticketing_system.py`.

**Implements:** `CLAUDE.md` section 8 (one suite per port, run against every adapter), `integrations.md` sections 1-2.

**Build:** **write these before the adapters.** A parametrised suite that any adapter must pass — fake today, real tomorrow. Cases: send returns a receipt; `get_message` reads back what was sent; sending with the same idempotency key twice produces one message; `find_sent` raises `AmbiguousOutcome` when the message is found only in Drafts (`integrations.md` section 1.3); `TicketingSystem.find_issue` treats a hit as reliable and a miss as **not** proof of absence (section 2.2).

**Tests first:** the suites are the deliverable; they run against the fakes in this card and against the real adapters in P3-02 and P3-03 under `@pytest.mark.integration`.

**Acceptance:** `pytest tests/contract -q` green against the fakes; no adapter-specific assertions in the suite.

**Size:** L. **Model:** Sonnet, effort medium.

**Watch:** the suite is what makes "a new adapter is done when it passes the suite" true. Any assertion that only a fake can satisfy defeats it.

---

## P3-02 — `OutlookGraphChannel`: send and read-back

**Files:** `src/dispute_agent/modules/email/outlook_graph/`.

**Implements:** `integrations.md` section 1, ADR 0019 (marked draft, extended property, immutable id), ADR 0023 (personal Outlook.com, delegated sign-in, encrypted token cache), I12, I14.

**Build:** create the message as a **marked draft** carrying our extended property, send it, then look it up by that property and read it back by immutable id (ADR 0019). Delegated sign-in with MSAL against the owner's personal account; the token cache is encrypted at rest; no secret reaches the repository, logs or config files (I12). Every call has a timeout; retries only on idempotent operations, with capped backoff (`CLAUDE.md` section 7).

**Tests first:** the P3-01 contract suite, run against this adapter with `@pytest.mark.integration`. Plus `test_token_cache_encrypted.py` and a sentinel test that no secret value appears in any log line.

**Acceptance:** `make test-integration` with real credentials present; `make verify` unaffected when they are absent.

**Size:** L. **Model:** Sonnet, effort medium. **Opus review:** yes (I12, I14, and the read-back that I2 depends on).

**Watch:** "sent" is not "delivered" (ADR 0010). Verified means the message is in Sent Items with the approved text — nothing here may claim delivery. Scenario S45 is the case where it was genuinely sent and then bounced.

---

## P3-03 — `JiraCloudTicketing`: idempotent upsert and read-back

**Files:** `src/dispute_agent/modules/ticketing/jira_cloud/`.

**Implements:** `integrations.md` section 2, ADR 0020 (idempotency comes from our own action record; labels and a property are markers only).

**Build:** `upsert_issue` is idempotent on **our stored key**, not on a Jira search — search by the case label before creating, but treat a miss as unproven (section 2.2) and rely on the action record for the decision. `get_issue` reads back and compares the fields we set.

**Tests first:** the contract suite against this adapter (`integration`). Plus `test_jira_idempotency.py` — a create that times out after Jira accepted it produces one issue, not two (the ADR 0020 path).

**Acceptance:** `make test-integration`.

**Size:** L. **Model:** Sonnet, effort medium. **Opus review:** yes (I2).

---

## P3-04 — Executor verification against real adapters

**Files:** `src/dispute_agent/kernel/executor/executor.py` (verification), `verify_schedule`.

**Implements:** `executor.md` sections 5-6, ADR 0010 (bounded retry window), I2, I4.

**Build:** wire both real adapters through the executor: PLANNED, SENT, then verification by independent read-back — delivery confirmed in Sent Items with the approved text, issue exists with the expected fields. A mismatch or a timeout past the retry window becomes UNKNOWN and then `NEEDS_HUMAN_REVIEW` (I4). `VERIFY_RETRY` follows `executor.verify_schedule_s`.

**Tests first:** scenarios S12a (send times out but the message was sent — found by lookup, no second email), S12b (ambiguous, only in Drafts, a person resolves it), S12c (Jira refuses with 400). Run against fakes with the fault tokens; the real-adapter equivalents are `integration`.

**Acceptance:** `pytest tests/e2e/test_phase3_verification.py -q`.

**Size:** M. **Model:** Sonnet, effort medium. **Opus review:** yes (I2, I4).

---

## P3-05 — Message templates

**Files:** `src/dispute_agent/packs/weight_dispute/templates/{evidence_request,reminder}.md.j2`, `render.py`.

**Implements:** `policy-rules.md` R09 (number validation), I9, I13.

**Build:** the evidence request and reminder templates only — upheld-charge and credit-confirmation arrive in Phase 5. Every number in a rendered message comes from stored evidence or calculation output (I9); templates take approved facts, not free text. Digits only, never number words (the drafter's MD-F1 rule applies to templates too, because R09 validates both).

**Tests first:** `test_templates.py` — a rendered evidence request contains no number absent from the facts; a template given a missing fact refuses to render rather than emitting a blank.

**Acceptance:** `pytest tests/unit/packs/test_templates.py -q`.

**Size:** M. **Model:** Sonnet, effort medium.

---

## P3-06 — Approvals module and the message review queue

**Files:** `src/dispute_agent/modules/approvals/store_backed/`, `streamlit_ui/`.

**Implements:** `approvals.md`, ADR 0013 (the kernel approvals table is the source of truth; `ApprovalService` only notifies), ADR 0018 (human events are authenticated), D6, D16, I13.

**Build:** a store-backed `ApprovalService` for **messages** (credits arrive in Phase 5). A Streamlit queue showing the exact draft and each number with its source. Approve stores the approver, the time and a **hash of the exact text**; the executor sends only text whose hash matches (I13). Reject takes a reason and returns the case to `INVESTIGATING` (T11), with the reason reaching the next briefing as `prior_rejections`. The approver's identity comes from the session, never from the request body (ADR 0018); the console is protected by `REVIEW_CONSOLE_TOKEN` (I14).

**Tests first:**
- `test_hash_binding.py` — approve text A, attempt to send text B: refused. This is I13's enforcement point.
- `test_rejection_returns_case.py` — scenario S33 (T11 re-plans with the note; T12 skips a rejected reminder).
- `test_actor_from_session.py` — an approval claiming a different actor in its body is refused.

**Acceptance:** `pytest tests/unit/modules/approvals -q`; `make run-approvals` serves the queue.

**Size:** L. **Model:** Sonnet, effort medium. **Opus review:** yes (I13, and ADR 0018's authentication).

---

## P3-07 — Reply poller

**Files:** `src/dispute_agent/modules/email/outlook_graph/poller.py`.

**Implements:** ADR 0022 (poll, no webhooks in v1), `integrations.md` section 1.4, I5.

**Build:** match replies by conversation id and case reference. Unrelated mail is ignored. **No match goes to human review**, never to a guessed case. Reply text is `UntrustedText` from the moment it enters (I5) — it is stored as such, and nothing downstream may treat it as a fact. Record `auth_result` from `Authentication-Results` (`kernel-interfaces.md` section 14); a `fail` raises the `sender_unauthenticated` signal, which can only add caution.

**Tests first:**
- `test_poller_matching.py` — a reply on the right conversation attaches; unrelated mail is ignored; an ambiguous match escalates.
- `test_reply_is_untrusted.py` — the stored reply is `UntrustedText` and no code path converts it to a fact.
- scenario S45 — a hard bounce after a verified send escalates `delivery_failed` rather than letting the seller timers run down.

**Acceptance:** `pytest tests/unit/modules/email -q`.

**Size:** L. **Model:** Sonnet, effort medium. **Opus review:** yes (I5).

---

## P3-08 — Contact limits

**Files:** `src/dispute_agent/kernel/policy/rules/contact_limits.py`, `config/policy.yaml`.

**Implements:** `policy-rules.md` (the contact-limit rules), D7 (3 per case, 1 per day).

**Build:** limits from `policy.yaml`. A retry never sends a second copy — the executor's idempotency already guarantees that, and this rule guarantees the agent does not *plan* a second one.

**Tests first:** `test_contact_limits.py` — a fourth message in a case is denied with its reason; a second message the same day is denied; a retry of an already-sent message is not counted twice.

**Acceptance:** `pytest tests/unit/policy/test_contact_limits.py -q`.

**Size:** S. **Model:** Sonnet, effort low.

---

## P3-09 — Timeline view

**Files:** `src/dispute_agent/api/timeline.py`.

**Implements:** `CLAUDE.md` section 11, `observability.md`.

**Build:** a page listing a case's events in order with time and cause. Untrusted text is displayed **boxed and labelled** (`agent-design.md` section 6: the review screen is one of the places seller text legitimately reaches).

**Tests first:** `test_timeline.py` — events appear in order with their cause; seller text is rendered escaped and labelled untrusted.

**Acceptance:** `pytest tests/unit/api/test_timeline.py -q`.

**Size:** S. **Model:** Sonnet, effort low.

---

## P3-10 — `make demo` and the owner setup guide

**Files:** `scripts/demo.py`, `docs/SETUP_CHANNELS.md`, `.env.example`.

**Implements:** the phase prompt task 9, I11, I12.

**Build:** `make demo` runs the kurta case end to end against the real test accounts. The setup guide covers the Entra app registration and the Jira API token using `.env.example` **names only** — no value, no screenshot containing one (I12). The guide is for the owner; Claude Code never creates cloud resources (`CLAUDE.md` section 16).

**Tests first:** `test_env_example_complete.py` — every environment variable the code reads appears in `.env.example`, and `.env.example` contains no value that looks like a secret.

**Acceptance:** `make demo` completes against test accounts; `pytest tests/unit/test_env_example_complete.py -q`.

**Size:** M. **Model:** Sonnet, effort medium.

---

## P3-11 — ADRs

**Files:** `docs/adr/`.

**Implements:** `CLAUDE.md` section 12.

**Build:** ADRs 0010, 0013, 0018, 0019, 0020, 0022 and 0023 already exist and are accepted — they decided polling, Jira idempotency, verification and message review during Stages A1-A3. Record implementation notes against them. Write a new ADR only where implementation actually changed a decision, which needs the owner's approval.

**Tests first:** not applicable.

**Acceptance:** `docs/PROGRESS.md` updated; no accepted ADR silently altered.

**Size:** S. **Model:** Sonnet, effort low.

---

## Phase exit

`make verify` green with fakes only; `make test-integration` green with real credentials; `make demo` sends a real approved email and opens a real Jira issue, both verified by read-back; a real reply resumes the case; `/phase-verify 3`; **run `safety-reviewer`**; `/session-handoff`.
