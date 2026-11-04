# Claude Code Build Pack (v2) - how this project was built

Weight Dispute Resolution & Cross-Case Intelligence Agent. An independent portfolio project on synthetic data; not affiliated with any logistics company.

This pack is the full handoff to Claude Code. It builds the project in two stages and eight phases, with the design done once on Opus and the implementation done on Sonnet to keep credit use down. The agent runs its reasoning in Microsoft Foundry Agent Service (a `gpt-5-mini` deployment in South India), behind a port. Cross-case intelligence is built last into a socket that exists from Phase 0.

## What is in the pack

```
README.md                       this file
CLAUDE.md                       project rules, 15 safety invariants, model discipline (read every session)
.claude/
  settings.json                 opusplan default, pinned model versions, safe permissions
  agents/                       architect and safety-reviewer (Opus), test-engineer (Sonnet), Explore (Haiku)
  skills/                       /phase-start  /phase-verify  /adr  /session-handoff
scripts/
  stage-a-opus.sh               launch design sessions on Opus
  stage-b-build.sh              launch build sessions on opusplan
.env.example  .gitignore        names only; no secrets
docs/
  HANDOFF.md                    current state, decisions made, open decisions, owner to-dos, next action
  MODEL_STRATEGY.md             which model for which task, escalation triggers, session discipline
  FOUNDRY_SETUP.md              the Azure and Foundry steps you do yourself
  ARCHITECTURE.md  FLOW.md      layers, ports, modules, Foundry integration, states, orchestrator loop
  PROGRESS.md  BACKLOG.md       status and session log; parked ideas
  adr/  spec/                   decisions; specifications written in Stage A
  build-prompts/                stage-a1 to a5 (Opus design), phase-0 to phase-7 and phase-4b (build)
prompts/                        the agent's own runtime prompts (versioned, provider-neutral)
```

## How to use it

1. Create the repo (`weight-dispute-agent`), `git init`, and copy this pack's contents into the repo root. Run `claude update`.
2. **Stage A (design, Opus).** Run `./scripts/stage-a-opus.sh`, then type `/phase-start a1`. Review the outline, approve, let it write the specs, review them. Repeat in a fresh session (`/clear` or a new launch) for `a2` to `a5`. At the end the design is frozen and tagged `arch-v1`.
3. **Stage B (build, Sonnet builds, Opus plans).** Run `./scripts/stage-b-build.sh`, press Shift+Tab to enter plan mode, then `/phase-start 0`. Approve the plan and Sonnet builds. Close the phase with `/phase-verify 0`, the `safety-reviewer` subagent where the phase prompt says so, and `/session-handoff`. Repeat for phases 1, 2, 3, 4, 4b, 5, 6 and 7.
4. Between sessions, read the new ADRs and `docs/PROGRESS.md`, then commit.

If a `/` command is not recognised, run `/skills` to see what loaded, or paste the manual kickoff below.

## Manual kickoff (if you do not use the skills)

```
You are starting <Stage A1 | Phase N>.
1. Read CLAUDE.md, docs/HANDOFF.md, docs/PROGRESS.md, and the matching file in
   docs/build-prompts/ plus the spec and task-card files it references.
2. Do not write code or files yet. For a build phase, I will put you in plan mode.
3. Reply with: understanding (10 lines or fewer); the task plan with files and the
   test for each task; at most 5 open questions with proposed defaults; any conflicts
   between documents; which tasks need the architect or safety-reviewer subagents.
4. Wait for my approval, then work one task at a time with make verify green.
```

## Your checklist after each phase (PM view)

- Does the demo or test output match the acceptance criteria in the words of the phase prompt?
- Can you explain every new ADR in two sentences to a non-engineer?
- Was anything outside the phase scope built? It belongs in `docs/BACKLOG.md`.
- Did the kernel gain any import from modules, packs or infra? `make arch` must say no.
- Did you check `/usage` and note it in `docs/PROGRESS.md`?

## What only you can do

Create the repo, the Entra app registration, the Jira site and token, the Azure subscription, the Foundry project in South India and the `gpt-5-mini` deployment, assign roles, and put values in `.env`. The sequence is in `docs/HANDOFF.md` section 6 and `docs/FOUNDRY_SETUP.md`. Never paste secrets into Claude Code.
