# Stage A5: Build plan and freeze (Opus)

## Role
Principal architect turning the specification into task cards a smaller model can execute cheaply and correctly, then freezing the design.

## Context
Stages A1 to A4 are approved. Read all of `docs/spec/` and every phase prompt.

## Objective
Task cards for every phase, an updated set of phase prompts, an escalation guide, and a tagged, frozen architecture.

## Scope
### In scope
Task cards, phase prompt updates, risk register, demo outline, spec index, freeze.
### Out of scope
Implementation.

## Tasks
1. `docs/spec/task-cards/phase-<N>.md` for phases 0 to 7 and 4b. Each card: id, title, files, the spec sections it implements, tests to write first, the acceptance command, size (S, M or L), dependencies, and the recommended model and effort. Default to Sonnet; mark the few cards that need an Opus review.
2. Update the phase prompts to reference their task cards and `docs/spec/`. Resolve any conflicts found since the prompts were written, and record them.
3. A risk register and a milestone plan mapped to the 12-step demonstration in the owner's brief.
4. A project-specific escalation guide: the situations in which Sonnet must stop and use the `architect` or `safety-reviewer` subagents.
5. `docs/spec/INDEX.md` listing every spec document with its status, owner approval and the invariants it covers.
6. Update `docs/HANDOFF.md` ("Next action: Phase 0") and `docs/PROGRESS.md`. After the owner approves, tag the repository `arch-v1`.

## Technical requirements
A task card must be implementable without further design. If it is not, the missing decision belongs in the spec, not in the card.

## Acceptance criteria
- Any card can be given to Sonnet with the spec and produce a correct result.
- `docs/spec/INDEX.md` shows every document approved.
- The repository is tagged `arch-v1`.

## Deliverables
Task cards, updated prompts and docs, INDEX, tag.

## Constraints
After the freeze, any change to `docs/spec/` needs an ADR and the owner's approval.

## Process
Draft one phase's cards, review with the owner, then do the rest.

## Model and effort
Opus, effort medium.

## Stop and ask if
A card cannot be written without changing an approved spec.
