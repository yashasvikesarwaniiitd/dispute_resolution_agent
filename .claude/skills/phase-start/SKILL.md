---
description: Start a build phase or design stage. Reads the right documents and produces a plan for approval before any code.
argument-hint: [phase or stage, e.g. 0, 4b, a1]
disable-model-invocation: true
---

Start $ARGUMENTS.

1. Read CLAUDE.md, docs/HANDOFF.md and docs/PROGRESS.md. Then read the file in docs/build-prompts/ whose name starts with "phase-$ARGUMENTS-" or "stage-$ARGUMENTS-" (include the trailing hyphen so that 4 does not match 4b), and the files in docs/spec/ and docs/spec/task-cards/ that it references (if they exist).
2. Do not write code or files yet. For a build phase, if this session is not in plan mode, tell me to press Shift+Tab to enter plan mode so that Opus does the planning.
3. Reply with:
   a. your understanding in 10 lines or fewer,
   b. the task plan: files you will create or change, and the test for each task (for a design stage, the outline of the documents),
   c. at most 5 open questions, each with your proposed default,
   d. conflicts between documents, if any,
   e. which tasks need the architect or safety-reviewer subagents.
4. Wait for my approval. After approval, work one task at a time and keep make verify green.
