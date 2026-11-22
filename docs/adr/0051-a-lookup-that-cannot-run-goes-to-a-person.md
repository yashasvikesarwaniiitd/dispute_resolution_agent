# 0051 — A lookup or read-back that cannot run goes to a person

Date: 2026-11-22
Status: **accepted** (owner, 2026-11-22: "Commit and push"). Builds on ADR 0007 (idempotency keys and lookup) and ADR 0010 (verification).

## The problem

On the hosted demo, a reviewer approved an explanation email at 13:18 UTC. The case then showed "The agent is working on it" for more than two hours. The logs showed `advance_failed error=AuthPortError` on that case every 7 minutes, 25 times.

The Gmail sign-in had expired (Google expires the refresh token of an app in "Testing" after 7 days). Before sending, the executor looks for the email in Sent (ADR 0007, look before writing). That lookup caught only `AmbiguousOutcome` and `TransientPortError`. The `AuthPortError` escaped `execute()`. The runner's sweep logged it and tried again when the claim lease (420 s) ran out. Nothing ever marked the action failed or unknown, so the case stayed in EXECUTING and never reached a person.

The read-back in `verify()` had the same gap: only `TransientPortError` and `ExternalNotFound` were handled.

## Decision

1. **A look-before-write that cannot run settles the action.** Any other `PortError`, or `ModuleUnavailable`:
   - on a first attempt, the action is FAILED (`lookup_failed`). Nothing was ever sent: the lookup comes before the send.
   - after an earlier attempt, the action is UNKNOWN. That attempt may have sent (I4).

   Either way the execute step escalates, so a person sees the case with the reason.
2. **The lookup after a transient send failure** treats the same errors as "not found". The send itself promised nothing was written, so the action is FAILED, as before.
3. **A read-back that cannot run** (any other `PortError`, or `ModuleUnavailable`) makes a SENT action UNKNOWN (`read_back_failed`), and the verify step escalates. An expired sign-in does not fix itself, so the 10-minute verify deadline is not waited out.

## Why

An error that escapes a step is retried forever by the sweep, and the reader sees "working on it". The kernel's rule is that every outcome is settled: done and proven, FAILED, or UNKNOWN for a person (I4). An escaping error was a fourth outcome nobody designed.

## Options considered

- **Catch every exception in the runner's sweep and escalate after N failures.** It is broader, but it hides the type of failure from the executor, which is the one place that knows whether anything may have been sent. Worth doing later as a backstop.
- **Treat an auth failure on the lookup as "not found" and send.** The send fails the same way, and after an earlier attempt it could send a second copy. Rejected.

## Consequences

- An expired Gmail or Jira sign-in now hands the case to a person within one tick, as `docs/DEPLOY_STREAMLIT.md` already promised.
- A case stuck this way before the fix has earlier attempts, so it resolves as UNKNOWN, never as a send.
- Kernel change: `kernel/executor/executor.py` (`execute`, `verify`, new `_lookup_failed`). Tests: `tests/unit/executor/test_executor.py`, `TestALookupThatCannotRunNeverStrandsTheCase` (four cases, each failing before the fix).

## Sources

- Google, "Using OAuth 2.0 to Access Google APIs", refresh token expiration: https://developers.google.com/identity/protocols/oauth2#expiration
