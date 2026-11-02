# Model strategy and cost playbook (Claude Code)

Opus decides, Sonnet builds, Haiku searches. Opus does the expensive thinking once, writes it down as specifications and task cards, and Sonnet follows them. This is about building the project. The model the agent uses at runtime is a separate choice (see `docs/HANDOFF.md` section 3).

Verified against the official docs: https://code.claude.com/docs/en/model-config, https://code.claude.com/docs/en/sub-agents, https://code.claude.com/docs/en/skills

## 1. The two stages

| Stage | What | Model plan | Launch |
|-------|------|------------|--------|
| A: Design (five sessions) | Specifications, threat model, task cards, freeze | Opus throughout | `./scripts/stage-a-opus.sh` |
| B: Build (phases 0 to 7) | Implementation from the frozen specs | `opusplan`: Opus in plan mode, Sonnet when executing | `./scripts/stage-b-build.sh` |

The `opusplan` alias uses Opus while you are in plan mode and switches to Sonnet for execution. Each phase starts in plan mode (Opus produces the plan), then you approve it and Sonnet builds.

## 2. Which model for which task

| Task | Model | Effort |
|------|-------|--------|
| Architecture, specs, ADRs, threat model, state machine, policy, executor design | Opus | medium; high for A2, A4 prompt review and the safety-critical tasks named in the stage prompts |
| Phase plans | Opus (plan mode) | medium; high for phases 1, 4, 5, 7 |
| Implementing from an approved plan or task card | Sonnet | medium |
| Writing tests from acceptance criteria | Sonnet | medium |
| Adapters (Graph, Jira, Foundry), migrations, endpoints, UI | Sonnet | medium |
| Docs, ADR drafting, progress notes | Sonnet | low |
| Codebase search and file discovery | Haiku (the `Explore` subagent) | default |
| Safety review before closing phases 1, 3, 4b, 5, 7 | Opus (the `safety-reviewer` subagent) | high |
| A hard design question mid-build | Opus (the `architect` subagent) | high |

Effort: Opus 5.5 and Sonnet 5.5 start at `medium`. Start there. Use `high` only for the tasks above. Avoid `max`; it is prone to overthinking and shows diminishing returns.

## 3. When Sonnet must stop and escalate to Opus

1. A change to a port signature, the state machine or any policy rule.
2. A specification ambiguity that touches an invariant.
3. Two failed attempts to fix the same failing test.
4. Anything touching approvals, credits, executor verification, authentication or telemetry redaction.
5. A new dependency, or any decision that deserves an ADR.
6. Two documents that disagree.
7. Any prompt change.

Escalate by delegating to the `architect` subagent (Opus, read-only), which returns a recommendation. Do not switch the whole session. Once the decision is recorded, continue on Sonnet.

## 4. Session discipline (where the savings come from)

- **One phase per session.** Run `/clear` between phases, and start from `docs/HANDOFF.md`.
- **Do not switch models mid-conversation.** The next response after a switch re-reads the whole history without cached context, which is why Claude Code asks you to confirm. Switch at session boundaries.
- **Keep `CLAUDE.md` short** (it loads every session). Put detail in `docs/` and let the phase prompt point to it.
- **Read only what is needed.** Use the `Explore` subagent (Haiku) for searches so file contents stay out of the main context.
- **Do not paste logs.** Ask for a summary of failing tests only. `/phase-verify` does this.
- **Compact at natural breakpoints**, with a focus, for example `/compact keep decisions, open questions and the task list`. Check size with `/context`.
- **Check usage after each session** with `/usage` (alias `/cost`) and note it in `docs/PROGRESS.md`.
- **Spec once, build many times.** A vague spec makes Sonnet guess, and guesses cost rework. If Sonnet is guessing, the spec is incomplete: fix the spec.

## 5. Configuration in this repo

`.claude/settings.json` sets `opusplan` as the default, pins the model versions, makes subagents default to Sonnet, and pre-approves the safe make targets. Aliases such as `opus` and `sonnet` float to the newest version; pinning keeps cost and behaviour stable. To adopt a newer model, change the pins on purpose and record it in `docs/PROGRESS.md`.

Subagents in `.claude/agents/` pin their own model: `architect` and `safety-reviewer` on Opus, `test-engineer` on Sonnet, `Explore` on Haiku. A skill's `model` and `effort` frontmatter applies for the turn it runs.

## 6. Do not use

- The `best` alias: it resolves to the Fable model where available.
- The `fable` alias or Fable models: depending on your plan they can bill usage credits.
- `max` effort and ultracode, except for a deliberate one-off.

## 7. Requirements

Run `claude update` before starting. Opus 5.5 needs Claude Code v2.1.280 or later, Sonnet 5.5 v2.1.284 or later and Haiku 5.5 v2.1.293 or later.
