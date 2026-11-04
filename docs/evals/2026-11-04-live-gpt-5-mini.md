# Live eval baseline: gpt-5-mini, 2026-11-04

First-round decisions on every scenario in the catalogue, by the Foundry agent
(`wd-case-investigator` v1, gpt-5-mini, prompt `case_investigator/v1`) against the rules-only
baseline. Command: `make eval ARGS="--live"`. Cost: $0.0701 (app estimate; Azure bill lags a day).

This is the **first** of the two runs P4b-11 needs. Open decision O1 (gpt-5-mini versus
gpt-5.4-mini) needs the same run against a gpt-5.4-mini deployment; it is not decided here.

## Headline

| | Correct first action |
|---|---|
| Foundry agent (gpt-5-mini) | **62%** of 45 |
| Rules-only baseline | 48% of 45 |
| Regressions (baseline right, model wrong) | 7 |
| Excluded (no single expected action) | 12 |

What is scored is the model's **proposal**, before the policy engine, the two-pass caution
ranking and human approval. A wrong proposal here is not a wrong outcome in the full flow; how
many of the misses the rules catch is not measured by this run (Phase 6 full scripts will).

## What it does well

- **Credit cases: 10 of 11 right** (S01, S10, S11, S13a/b, S23a, S25, S29a, S37, S39; S26 missed).
- **Asks for evidence where the rules give up**: most `request_seller_evidence` scenarios, where
  the baseline escalates to a person.

## Where it is weak

1. **Under-escalates.** Seven scenarios that should go to a person got `request_seller_evidence`
   instead: S05a, S14a, S18b, S21, S29b, S43b, plus S40 (`request_remeasurement`). These are all
   seven regressions. The model defaults to "ask the seller" when unsure.
2. **Never asks for a remeasurement when it should**: S03, S04, S12c, S31, S44 (0 of 5).
3. **Never upholds**: S09a.
4. **Credits where a person should decide**: S24 and S43a (the baseline does the same).
5. S26 (credit expected) got `request_seller_evidence`; S43c produced no decision.

## Full table

```
scenario expected                 baseline                 model                    ok
--------------------------------------------------------------------------------------
S01      propose_credit           propose_credit           propose_credit           yes
S02      request_seller_evidence  escalate_to_human        request_seller_evidence  yes
S03      request_remeasurement    escalate_to_human        request_seller_evidence  NO
S04      request_remeasurement    escalate_to_human        request_seller_evidence  NO
S05a     escalate_to_human        escalate_to_human        request_seller_evidence  NO
S05c     request_seller_evidence  escalate_to_human        request_seller_evidence  yes
S05b     n/a                      escalate_to_human        request_seller_evidence  n/a
S06      request_seller_evidence  request_seller_evidence  request_seller_evidence  yes
S07      request_seller_evidence  escalate_to_human        request_seller_evidence  yes
S08      request_seller_evidence  escalate_to_human        request_seller_evidence  yes
S09a     uphold_charge            escalate_to_human        request_seller_evidence  NO
S09b     n/a                      escalate_to_human        request_seller_evidence  n/a
S10      propose_credit           propose_credit           propose_credit           yes
S11      propose_credit           propose_credit           propose_credit           yes
S12a     request_seller_evidence  escalate_to_human        request_seller_evidence  yes
S12b     request_seller_evidence  escalate_to_human        request_seller_evidence  yes
S12c     request_remeasurement    escalate_to_human        request_seller_evidence  NO
S13a     propose_credit           propose_credit           propose_credit           yes
S13b     propose_credit           propose_credit           propose_credit           yes
S14a     escalate_to_human        escalate_to_human        request_seller_evidence  NO
S14b     escalate_to_human        escalate_to_human        escalate_to_human        yes
S17      n/a                      -                        -                        n/a
S18a     request_seller_evidence  escalate_to_human        request_seller_evidence  yes
S18b     escalate_to_human        escalate_to_human        request_seller_evidence  NO
S19      request_seller_evidence  escalate_to_human        request_seller_evidence  yes
S20      n/a                      escalate_to_human        request_seller_evidence  n/a
S21      escalate_to_human        escalate_to_human        request_seller_evidence  NO
S22      n/a                      escalate_to_human        request_seller_evidence  n/a
S23a     propose_credit           propose_credit           propose_credit           yes
S23b     n/a                      escalate_to_human        request_seller_evidence  n/a
S24      escalate_to_human        propose_credit           propose_credit           NO
S25      propose_credit           propose_credit           propose_credit           yes
S26      propose_credit           escalate_to_human        request_seller_evidence  NO
S27      n/a                      -                        -                        n/a
S28      request_seller_evidence  escalate_to_human        request_seller_evidence  yes
S29a     propose_credit           propose_credit           propose_credit           yes
S29b     escalate_to_human        escalate_to_human        request_seller_evidence  NO
S30      request_seller_evidence  escalate_to_human        request_seller_evidence  yes
S31      request_remeasurement    escalate_to_human        request_seller_evidence  NO
S32      n/a                      -                        -                        n/a
S33      request_seller_evidence  escalate_to_human        request_seller_evidence  yes
S34      request_seller_evidence  escalate_to_human        request_seller_evidence  yes
S35      request_seller_evidence  request_seller_evidence  request_seller_evidence  yes
S36      request_seller_evidence  escalate_to_human        request_seller_evidence  yes
S37      propose_credit           propose_credit           propose_credit           yes
S38      escalate_to_human        escalate_to_human        escalate_to_human        yes
S39      propose_credit           propose_credit           propose_credit           yes
S40      escalate_to_human        escalate_to_human        request_remeasurement    NO
S41      n/a                      escalate_to_human        request_seller_evidence  n/a
S42      n/a                      escalate_to_human        request_seller_evidence  n/a
S43a     escalate_to_human        propose_credit           propose_credit           NO
S43b     escalate_to_human        escalate_to_human        request_seller_evidence  NO
S43c     request_seller_evidence  -                        -                        NO
S43d     n/a                      escalate_to_human        request_seller_evidence  n/a
S44      request_remeasurement    escalate_to_human        request_seller_evidence  NO
S45      request_seller_evidence  escalate_to_human        request_seller_evidence  yes
S46      n/a                      escalate_to_human        request_seller_evidence  n/a
--------------------------------------------------------------------------------------
correct_action_rate (model):    62% of 45
correct_action_rate (baseline): 48%
regressions vs baseline:        7
excluded (actions n/a):         12

Model cost of this run: $0.0701
```
