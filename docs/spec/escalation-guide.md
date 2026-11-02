# Escalation guide

Status: approved (D45), frozen at `arch-v1`. When the building model must stop, and who it stops to. `CLAUDE.md` section 17 sets the model plan; this guide makes it specific to the situations this project will actually hit.

The short version: **Sonnet builds, Opus decides, the owner approves anything that changes a frozen spec or touches the outside world.** When unsure which applies, stop — a paused card costs minutes, a wrong decision baked into the kernel costs a phase.

## 1. Stop and use the `architect` subagent (Opus, read-only)

Use it for a hard design question **during** a Sonnet session, instead of switching the whole session's model (`CLAUDE.md` section 17). It answers in at most 300 words with a recommendation, the invariants affected and the files that must change. It never writes.

Call it when:

- A card cannot be built without a design decision the card does not state, **and** the decision does not obviously belong to the owner. If the card is simply ambiguous, the missing decision belongs in the spec — see section 3.
- A port signature needs to change. Ports are the kernel's contract with everything else; widening one to make a card work usually means the card is wrong.
- Two spec documents disagree and the authority order (`CLAUDE.md` section 18) does not settle it — for example two `docs/spec/` files contradicting each other, where neither outranks the other.
- An import-linter contract would have to be weakened to make a test pass (card P0-10 names this explicitly).
- A test can only be made to pass by relaxing what it asserts.
- The kernel diff in Phase 7 is not empty (card P7-11). That means the socket was designed wrong, which is a design question before it is a code question.

## 2. Stop and run the `safety-reviewer` subagent (Opus, read-only)

**Mandatory before closing phases 1, 3, 4b, 5 and 7** (`CLAUDE.md` section 17; Phase 5's is called out as mandatory in the phase table). It reads the diff against invariants I1-I15 and names a way to break each one it touches.

Also run it, regardless of phase, when a card:

- Adds or changes a write path — anything that reaches `MessageChannel`, `TicketingSystem` or `Ledger`.
- Touches the approval binding, the amount check or the text hash (I3, I13).
- Touches the kill switch (I10).
- Changes what reaches a prompt, or how untrusted text is fenced (I5).
- Changes the caution order, a verdict, or anything a pattern can influence (I7, I15).
- Adds a new secret, or a new place a secret could be logged (I12, I14).

The 44 cards marked **Opus review** in the task-card tables are these situations, identified in advance. The list is not exhaustive — a card that turns out to touch one of the above acquires the requirement.

## 3. Stop and ask the owner

Only the owner can approve these. Claude Code never decides them alone.

**Anything that changes `docs/spec/` after the `arch-v1` freeze.** The freeze is the point of Stage A5: after it, a spec change needs an ADR stating what changed and why, and the owner's approval before the change lands. This includes:
- A task card that cannot be written or executed without changing an approved spec.
- Card P1-13, which resolves the T90 open item by either documenting a cause or deleting a transition.
- Card P6-03, where the draft gate thresholds become real (O6).

**Anything involving cloud resources, accounts or credentials.** `CLAUDE.md` section 16 puts these out of scope entirely: the Entra app registration, the Jira site and token, the Azure account and Foundry project, the Anthropic key, the Application Insights connection string. Claude Code writes the setup guide; the owner performs it. Secrets never appear in chat (I12).

**Anything that spends money or starts a clock.** The Azure free credit is USD 200 for 30 days from sign-up (D18), so the owner decides *when* Phase 4b starts, not the build. Raising a spend cap, setting `allow_paid_usage`, or choosing a model with a different price are owner decisions.

**The open decisions O1-O8** (`INDEX.md`). Each names the phase that resolves it; each resolution is the owner's.

**Anything that would weaken an invariant.** There is no procedure for this, because the answer is no — but if a card appears to require it, that is a design error worth surfacing immediately rather than working around.

## 4. Do not stop for these

Stopping has a cost too. Carry on without asking when:

- The card states the decision and you simply need to implement it carefully.
- A spec document and a phase prompt disagree, and the authority order settles it (`docs/spec/` wins). Note the conflict in the phase summary; do not pause for it.
- `ARCHITECTURE.md` section 4 and `kernel-interfaces.md` give different port signatures. Section 4 calls itself a sketch; `kernel-interfaces.md` wins. This is already settled in card P0-05.
- A test needs a fixture, a helper or a refactor that changes no behaviour.
- You find a bug in earlier phase code. Fix it, starting with a failing test (`CLAUDE.md` section 8), and say so in the phase summary.
- You want to park an idea. Put it in `docs/BACKLOG.md` and move on (`CLAUDE.md` section 1).

## 5. How to stop well

A good stop gives the owner or the subagent everything needed to answer in one pass:

1. **The card and the specific line** that cannot be executed.
2. **What the spec says**, quoted, and what the card needs instead.
3. **The options**, with the one you would choose and why.
4. **Which invariants are affected**, by number.
5. **What it costs to wait** — whether other cards can proceed meanwhile.

A stop that says only "this is ambiguous" costs a round trip. A stop that quotes both sides and proposes a default usually ends in one reply.
