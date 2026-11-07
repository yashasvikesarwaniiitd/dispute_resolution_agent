# 0043 — The AI chooses requested evidence from a closed list; it never words it

Date: 2026-11-07
Status: accepted. `case_investigator` v2; the engine renders v2. Like v1, v2 stays `inactive` until the live accuracy bar is met (D61). The hosted Foundry agent still holds v1 until the owner runs `make agents-sync` (D63).

## Decision

When the investigator asks a seller for evidence, it picks one to three items from a fixed list of six wordings and copies them exactly. The list lives in the shipping pack (`packs/weight_dispute/requested_items.py`). Prompt v2's output schema lists exactly those strings as an `enum`, and v2's instructions list them too.

Enforcement is in code, twice:

- The model gateway validates every answer against the prompt's schema.
- The reasoner rejects any item that isn't on the list, reading the list from the schema itself. That holds even on a route that skipped schema validation, such as the hosted agent, whose lock says `structured_output: false`.

An off-list item gets the usual one retry, then the case goes to a person (`invalid_model_output`).

## Why

The first full live run (2026-11-07) used the live investigator on `scanner_fault`. It chose the right step, but worded its own item ("two photos"). R09 blocked the draft, because a number word cannot be checked against a fact (MD-F1), and the case went to a person. At scale, most AI-handled evidence requests would do the same.

Asking for digits instead does not work. Every digit a seller reads must trace to a stored fact (I9), and "2" does not. So the items carry no numbers at all.

A closed list also removes the last place where model-written text reached a customer email: the template prints the pack's wording. That is a small gain for I5.

## What it costs

- **Narrower asks.** The investigator can only ask for what is on the list. A clarification re-requests listed items; anything else must escalate. Adding an item is a pack change plus a new prompt version, and a test fails the build if the two lists disagree.
- **The hosted route needs a sync.** The drift check compares the hosted agent's prompt with the repo's. Until the owner runs `make agents-sync`, cases on `foundry_agent_service` go to a person (`agent_drift`). That is the safe failure, and it is the documented process. The direct route (`foundry_openai`) reads v2 from the repo.
- **Not covered: the agentic engine.** It uses its own prompt (`case_investigator_agent/v1`, open list). It is off by default; a v2 of that prompt should copy this list before it is ever activated.

## Results

- **Offline:** 14 tests (`tests/unit/modules/reasoning/test_requested_items.py`). They cover:
  - the prompt and pack lists match, in order;
  - no item has a digit or a number word;
  - the rules engine (B7) and the recorded answers use listed items;
  - v2 differs from v1 only here;
  - on a real case, listed items reach the reviewer, a self-worded item goes to a person, and one bad answer followed by a listed one is retried, not escalated.
- **Recorded suite:** 57 of 57, and all 12 gates pass with v2.
- **Live** (gpt-5-mini, direct route, USD 0.019): the step that failed before passed. The AI chose two listed items, and the evidence request was sent through Gmail and verified.
  - After a simulated reply claiming "photo attached" (with no photo in the evidence), the AI re-requested the photos rather than going to the hub. That is a cautious, permitted choice, but it differs from S06's recorded path. Accuracy belongs to the live-evaluation backlog, not to this decision.
