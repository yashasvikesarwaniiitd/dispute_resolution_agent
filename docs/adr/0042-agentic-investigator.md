# 0042 — An agentic investigator: the AI chooses its own lookups and checks its draft

Date: 2026-11-05
Status: accepted as an engine, **off by default** (`REASONING_ENGINE=agent`). Its prompt stays `inactive`, like the investigator's, until accuracy clears the 90% bar (D61).

## Decision

Add a third investigation engine in which the AI **investigates** rather than answers once. Each turn it returns its next move, an argument and its current draft proposal:

| Move | What code does |
|---|---|
| `open_evidence <id>` | Returns the full value of one evidence item. The briefing caps values at 700 characters, 12 items and 5 outside texts. Outside-party text comes back neutralised, in an `untrusted_tool_result` block. |
| `policy_lookup <query>` | Returns passages from the policy rules and decision records (the LlamaIndex index, ADR 0040). |
| `check_draft` | Runs the same checks the final decision will face (menu, cited ids, required parameters) and reports the problems. |
| `decide` | The draft is checked. If it fails, the agent sees why and gets another turn; if it passes, it becomes the proposal. |

At most 4 turns; on the last one the model must decide. Prompt: `prompts/case_investigator_agent/v1`, derived from `case_investigator/v1` with an `agent_procedure` section and a step wrapper.

## Why the minimum, and why these tools

The owner asked for the smallest change that makes the core genuinely agentic.

- Code-orchestrated decisions stay. The agent gains freedom to **gather information and correct itself**, not freedom to act. For a money workflow that is the right line.
- **Why these tools.** The one real gap was that the briefing is cut to fit, so the model often decided on partial evidence with no way to ask for more. The three tools add information or feedback the model did not have. Each reads the briefing in memory or the local policy index: no new database connection, no network, no write port.

## Safety

- **I1.** The engine returns one `Proposal`, and policy judges it as before. `agentic.py` imports nothing that can write (`tests/architecture/test_agent_libraries.py`).
- **I14.** Every turn is a normal call through the guarded `ModelGateway`, so caps, call log, failover and screening apply. No exception was needed.
- **I5.** Opened outside text is neutralised and stays in an untrusted block. A test checks that it never reaches the system message.
- **Budget.** The agent keeps the **configured** per-case call cap; it spends extra turns only when it chooses to look something up. A case that runs out goes to a person. A first version tripled the cap, which defeated scenario S23b's deliberately tight cap, so it was removed.
- **Mid-investigation failure.** If the model stops answering after the investigation started, the case goes to a person (`invalid_model_output`), not to the rules baseline.

## Results

- **Offline:** 8 tests on the real investigate station with a scripted model:
  - deciding in one turn;
  - opening chosen evidence;
  - a policy lookup;
  - catching its own mistake with `check_draft`;
  - a rejected decision getting another turn;
  - deciding on the last turn;
  - an agent that never decides validly going to a person;
  - outside text staying untrusted.
- **Recorded suite:** 57 of 57 and 12 of 12 gates, with the engine on and with it off.
- **Live** (`scripts/agent_check.py`, per distinct dispute, gpt-5-mini through `foundry_openai`, USD 0.133). **PASS** under the rule fixed beforehand: every dispute decided validly, lookups used, under the cap.
  - Accuracy, reported but not gated: single call 6 of 11, agent 5 of 11, one run each, which is too few to separate them.
  - The agent used `check_draft` twice, getting both cases right, including one the single call got wrong. It decided immediately on the other nine and never opened evidence or read policy.

## Consequences

- The mechanism works and is safe. It does not improve accuracy yet, because the model rarely judges a lookup worth making. That is prompt work, not architecture.
- The engine stays off by default, and its prompt stays inactive.
- On the two planted-instruction disputes, both engines proposed something other than escalation. The input screen and policy rules (R21) are what route those cases to a person. The recorded scenarios S14a, S14b and S40 prove it end to end (all end in `NEEDS_HUMAN_REVIEW`, `manipulation_suspected`), and this result is a reminder of why that defence sits in code.
