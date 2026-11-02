# ADR 0024: The briefing separates trusted facts from untrusted text, and the rules-only baseline is the bar

Status: proposed
Date: 2026-11-02

## Decision
The case briefing renders code-produced facts and system evidence as trusted data, and every piece of outside-party text (seller replies, ops notes, dispute statements, and machine readings of them) in its own `untrusted_` block, with each evidence item labelled `source_trust`. Contact status and reviewer notes are explicit blocks. The menu removes only options that are dangerous to propose; anything else policy denies with a recorded reason. A rules-only baseline that never reads free text decides clear cases and is the evaluation bar: the AI may not do worse than it on any scenario (gate `no_regression_vs_baseline`).

## Why
The adversarial review found that untrusted text was mixed into evidence values, the reply reading was unlabelled, and the model could not see contact history, so it could not follow D19. Labelling trust at the data level, not just in the instructions, is the stronger defence against indirect injection (I5). A baseline that is pure code shows exactly what the AI adds and prevents an AI from silently making easy cases worse.

## Options considered
- One combined evidence list with trust described only in the system prompt: relies on the model remembering the distinction.
- Hiding denied options from the menu: fewer bad proposals, but loses the recorded reason, which audits and evals need.

## Consequences
Easier: injection tests have a precise target; evals have a deterministic comparison. Harder: the briefing builder must classify every evidence item's trust. Watch: briefing size caps (24,000 characters) are measured in Phase 4.

## Sources
Project documents (docs/spec/agent-design.md, docs/spec/prompt-review.md, prompts/README.md). No external documentation was needed.
