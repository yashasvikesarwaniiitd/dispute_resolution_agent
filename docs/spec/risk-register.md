# Risk register and milestone plan

Status: Stage A5. Risks that could stop this project or make its results untrustworthy, each with its trigger, mitigation and owner. `HANDOFF.md` section 9 lists the same themes; this is the operational version, mapped to cards.

## 1. Risks

| # | Risk | Trigger that tells you it is happening | Mitigation | Owner |
|---|------|----------------------------------------|------------|-------|
| R1 | **The Azure credit window closes before the Foundry work is done.** USD 200 for 30 days from sign-up (D18). | The window opened and Phase 4b is not finished. | The owner signs up **when Phase 4b starts, not earlier**, and upgrades to pay-as-you-go immediately. Order Phase 4b so the live-call cards (P4b-03, P4b-11, P4b-10) come early. `allow_paid_usage` stays false, so spend stops rather than surprising anyone. `make foundry-spend` shows days left. | Owner |
| R2 | **Scope creep.** Every phase has an out-of-scope list and each one is tempting. | A card is taking longer than its size suggests, or produces files no card names. | Park ideas in `docs/BACKLOG.md`. Work only on the named phase (`CLAUDE.md` section 5). The phase exit checklist is the test. | Build |
| R3 | **Over-trust in synthetic results.** The system will look more capable than it is, because the data is cooperative. | A README draft, report or demo script claims accuracy, autonomy or production readiness. | `release-gates.yaml` `claims_not_to_make`, enforced by the grep test in card P6-09. Every figure quotes its scenario count and run count. | Build + Owner |
| R4 | **Prompt injection succeeds in a way the corpus did not anticipate.** | A red-team finding (P6-11) that the 12-item corpus misses. | Defence in depth: `neutralise` as code with its own tests (P4-01), trust-labelled briefing blocks (P4-02), code-proven credit reasons only (R14), approval on every credit and message (D6), number validation (P4-07). Every red-team failure becomes a local scenario. | Build |
| R5 | **Model drift.** Aliases float; behaviour and cost change under the project. | Eval results move without a code change. | Pin versions in `.claude/settings.json` for the building models; pin agent versions in `agents.lock` and drift-check them for the runtime model (P4b-04). Drift escalates `agent_drift` and never fails over. | Build |
| R6 | **Preview features break.** Foundry tracing instrumentation, red teaming and memory stores are preview. | A preview SDK breaks `make verify`. | Keep them inside adapters and out of `make verify` (`HANDOFF.md` section 9). Cards P4b-10 and P6-11 state this. | Build |
| R7 | **A secret leaks** into the repo, a log, a span or a fixture. | The secret-scanning test fails, or a sentinel scan finds a hit. | One settings loader for the Anthropic key (P4-10); Entra ID only for Foundry (P4b-03); redaction processor (P0-09); secret-scanning test in CI (P4b-12); sentinel scenario S30. `.env` git-ignored, `.env.example` names only. | Build + Owner |
| R8 | **Cost runs away** on the runtime model. | `make foundry-spend` passes 50% of a cap early in a phase. | Per-case call budget and per-route spend caps as **kernel** policy rules (P4b-07), not gateway concerns. Warnings at 50% and 80%. Never fail over past a cap (D17). | Build |
| R9 | **The frozen spec turns out to be wrong** mid-build. | A card cannot be executed without changing `docs/spec/`. | That is what the freeze is for: an ADR and the owner's approval (`escalation-guide.md` section 3). Known instance: card P1-13. | Owner |
| R10 | **The cross-case socket was designed wrong**, and Phase 7 cannot connect without kernel changes. | `git diff <phase-6-tag>..HEAD -- src/dispute_agent/kernel` is not empty (P7-11). | The socket is exercised from Phase 0 by a contract suite and an architecture test (P0-10), so the failure surfaces early rather than in Phase 7. If it still happens: stop, use `architect`, raise an ADR. | Build |
| R11 | **Real-account work is blocked** by a tenant policy, a missing scope or a personal-account restriction. | Phase 3 integration tests cannot authenticate. | ADR 0023 already chose a personal Outlook.com account with delegated sign-in (D36) for exactly this reason. Fakes keep `make verify` green, so the build is never blocked by the outside world. | Owner |
| R12 | **Evaluation becomes circular** — the system is judged by the thing it produced. | A pattern is confirmed by cases that the pattern itself influenced. | The confirmation rule (P7-08): CONFIRMED only on independent evidence; outcomes decided with pattern help never count. The rules-only baseline is the independent bar for the reasoner. | Build |

## 2. Milestone plan

The owner's brief with the 12-step demonstration was not in the pack handed to this repository, so this plan is **derived** from what is approved: the single-case sequence in the Phase 5 prompt (task 8), the 16 required scenarios in `FLOW.md` section 8, and the phase acceptance criteria. If the original brief turns up, reconcile this against it.

| Milestone | What can be shown | Phases | Demonstrates |
|-----------|-------------------|--------|--------------|
| **M1 Skeleton** | `/healthz` lists every module and its adapter; `make arch` enforces the dependency rules; the cross-case socket exists and is empty. | 0 | The architecture is real and enforced, not aspirational. |
| **M2 A case that moves** | A synthetic dispute opens one case, moves only along legal transitions, logs every change with its cause, and survives a restart. | 1 | Code owns the lifecycle (D5). |
| **M3 A case that reasons in code** | The kurta fixture gathers evidence, recalculates the charge, reconciles against the ledger and reaches a decision — with **no AI at all**, via the rules-only baseline. | 2 | The deterministic core works before any model is involved. |
| **M4 A case that talks** | A real approved email to a real test mailbox, a real reply that resumes the case, a real Jira issue read back and verified. Still no AI. | 3 | The outside world is handled, and every message passes a human. |
| **M5 A case that is investigated** | The LLM proposes one step from a trust-labelled briefing; every output is validated; the injection corpus fails by all four entry routes; the baseline-versus-LLM table exists. | 4 | The AI reads and proposes; it cannot write (I1). |
| **M6 The same, on Foundry** | The same behaviour through Agent Service with Entra ID, drift-checked, metered, traced without content, failing over only for availability. | 4b | The model is a replaceable component behind a port. |
| **M7 A case that pays** | The full single-case sequence: dispute, evidence, recommendation, email, reply, Jira, approval, ledger credit, verification, timeline — from one command. | 5 | The demonstration the project exists for. |
| **M8 A case you can trust** | 57 scenarios, metrics, release gates that fail the build, failure and kill-switch drills, and a README that states its limits. | 6 | Honest evaluation, with the gates enforcing it. |
| **M9 Cases that learn** | Patterns across disputes, a linked investigation opened through the normal executor path, caution-only influence, and an empty kernel diff. | 7 | The architectural claim, proven last. |

**The demo to record** is M7, extended with M9's pattern context if Phase 7 is reached: one command, one case, a human approving a real message and a real credit, and a timeline anyone can read. Record it inside the Azure credit window (R1).

## 3. What would make this project fail quietly

Worth naming, because none of these announces itself:

- **Green tests that assert nothing.** The ledger simulator sharing a code path with the kernel (P5-01), a contract suite only a fake can pass (P3-01), a gate whose denominator excludes every interesting case (P6-02). Each is called out on its card.
- **A claim nobody checks.** "Full coverage" was false at the transition level in the Stage A4 draft until a script was written to check it. `scripts/check_scenarios.py` exists so that claim stays true.
- **An invariant enforced only by intention.** Every one of I1-I15 has a mechanism and a test in `traceability.md`; a card that weakens one without noticing is what `safety-reviewer` is for.
