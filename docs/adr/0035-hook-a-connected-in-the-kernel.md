# 0035 — HOOK A connected in the kernel (one disclosed kernel change before Phase 7)

Date: 2026-11-04
Status: accepted. Approved by the owner before the change was made (D49).
Precedes P7-01. After the tag `phase-6-complete`, Phase 7 makes zero changes in `kernel/`.

## Decision

The investigation step now asks the `PatternContextProvider` port for open patterns that match
the case, and every pattern it returns becomes a caution floor. This is a kernel change, made
once, on purpose and in the open, before Phase 7 starts. The tag `phase-6-complete` marks the
commit that carries it; the Phase 7 "kernel untouched" proof diffs against that tag.

## Why

CLAUDE.md promises that connecting cross-case intelligence "must change zero lines in
`kernel/`". Planning Phase 7 found the socket was built but never plugged in: the port, the
null adapter, the `pattern_context` field on the briefing and the caution signals all existed,
yet nothing in the kernel ever called `patterns_for`. Any real adapter added in Phase 7 would
have been ignored. Keeping the promise literally was impossible; the honest option was to
finish the socket first and say so, instead of hiding the change inside Phase 7.

## What changed (69 lines in three files)

- `kernel/orchestrator/investigate.py`
  - A new optional `patterns` dependency (None or the null adapter: behaviour identical to
    before).
  - At most `MAX_PATTERNS = 5` patterns, newest first, are placed in the briefing's
    `pattern_context`.
  - Each one adds a `pattern_match` caution signal with the floor from
    `config/policy.yaml` `patterns.match_min_verdict` (REQUIRE_APPROVAL or ESCALATE, never
    ALLOW).
  - If the provider cannot be read (a port error or a disabled module), the case gets
    `pattern_context_unavailable`. Not knowing is a reason to be careful, never a silent skip.
  - If the second reasoning pass says patterns changed its recommendation
    (`pattern_influence.changed_recommendation`, ADR 0025), a `pattern_changed_recommendation`
    signal requires approval.
  - Approvals exist only for credits and messages. When policy asks for approval on an ops
    issue (for example a pattern lifting a remeasurement request), the case goes to a person
    (`POLICY_ESCALATED`) instead. This is strictly more cautious.
- `kernel/policy/signals.py`: `pattern_match_signal` and `pattern_changed_signal`.
- `api/line.py`: the line passes `patterns` through. Phase 7 sets it in `app.py`.

## Options considered

1. **Connect it first, disclosed (chosen).** One reviewed change. Afterwards the Phase 7 rule
   "zero kernel changes" is real and testable.
2. **Connect it inside Phase 7 quietly.** This breaks the promise without saying so, and the
   kernel-untouched proof would have to exclude the change. Rejected.
3. **Feed patterns from outside the kernel** (for example by rewriting the briefing in an
   adapter). That would bypass policy, so patterns could not be shown to only add caution
   (I7). Rejected.

## Safety (I7)

Patterns can only raise the verdict. The tests in `tests/e2e/drills/test_hook_a.py` show this:

- With no patterns, a remeasurement goes out as before (AWAITING_OPS, 1 issue).
- With a matching pattern, it goes to a person (NEEDS_HUMAN_REVIEW, 0 issues).
- With a provider that fails, it also goes to a person (NEEDS_HUMAN_REVIEW, 0 issues).

Phase 7 adds a property test over many cases and patterns. It checks that no verdict is ever
lower with patterns than without them.

## Consequences

- When `cross_case` is off, nothing changes. All 57 recorded scenarios and the full test suite
  (3,085 tests) pass unchanged.
- Phase 7 work happens in `modules/cross_case`, `infra`, `app.py` and the tests. Any need for
  a further kernel change stops the work for a new ADR.
