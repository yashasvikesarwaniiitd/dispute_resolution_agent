# 0067 — A reopened case gets its AI call allowance again

Date: 2026-11-25
Status: accepted. The owner chose "per round" after the live case below.

## Context

Live, SHP-102026: two rounds (a hub re-measure, then an upheld close) used the case's AI calls. A minute after the close the seller replied with a photo. The reply reopened the case (T40) and was read, but the new round's first investigator call hit M01, `max_calls_per_case`, and the case went to a person as `llm_budget_exceeded`. The limit was for the case's whole life, so a reopen, which is new evidence and a new round, had nothing left to spend.

The console also labelled that stop "The AI spend cap was reached". The money cap (M03) was untouched at USD 0.0076 of 150; what ran out was the count of calls for one case.

## Decision

1. **M01 is `max_calls_per_case × (1 + reopen_count)`.** Each reopen (T39–T42) gives the case the full allowance again. The money caps (M03, M05) are unchanged and still bind everything, so the total spend stays capped.
2. **How the gateway knows.** Every model call a case makes runs inside one of its steps, and every step runs through `Orchestrator._run_traced`. That sets `kernel/domain/model_calls.STEP_CASE` to `(case id, reopen_count)` for the step and resets it after. `BudgetedModelGateway` reads it and applies the reopens only when the id matches the request's case. A ContextVar rather than a new field on `ModelRequest`, because five request builders sit many calls below the step and none of them holds the case. A separate database read would not work either: the reopen is still uncommitted while the new round runs.
3. **The label** for `LLM_BUDGET_EXCEEDED` is now "This round used all its AI calls".

## Consequences

- A case that keeps being reopened can spend more in total, bounded by `max_agent_reopens` (2): at most three allowances. After that, late replies go to a person (T43/T44) as before.
- Model calls outside a step (the online judge, pattern analysis) see no step case and keep the plain limit.
- Tests: `test_budgeted_gateway.py::TestPerCaseCap::test_a_reopened_case_gets_the_allowance_again`, and `test_t90_from_gathering.py::test_a_step_runs_with_its_case_s_reopens_on_record`.
