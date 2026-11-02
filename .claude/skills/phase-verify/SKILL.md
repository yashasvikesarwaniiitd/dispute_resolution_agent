---
description: Run the verification gate and map the results to the phase acceptance criteria.
argument-hint: [phase, e.g. 3]
disable-model-invocation: true
model: sonnet
effort: low
allowed-tools: Bash(make verify) Bash(make arch) Bash(git diff *) Bash(git status)
---

Verify phase $ARGUMENTS.

1. Run make verify. If it fails, summarise only the failing tests and the likely cause. Do not paste full logs.
2. Open the phase prompt in docs/build-prompts/ and list every acceptance criterion. For each, name the command or test that proves it, run what you need, and report PASS or FAIL.
3. Report anything built outside the phase scope and any ADR still missing.
4. If this is phase 1, 3, 4b, 5 or 7, remind me to run the safety-reviewer subagent before I close the phase.
