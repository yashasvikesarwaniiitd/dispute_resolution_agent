# Setting up the Foundry agent (Phase 4P prototype)

This guide takes you from no Azure account to watching your Foundry agent decide a synthetic
dispute. You do every step that touches Azure; the code never creates cloud resources or
reads your secrets (CLAUDE.md section 16, I12). Values go into `.env`, never into chat.

**Before you start: try it offline.** The whole path runs with a scripted stand-in for the
model, so you can see what you are about to connect, at no cost and with no account:

```
set PROMPTS_ALLOW_INACTIVE=true
make try-agent ARGS="scanner_fault --offline"
```

(On macOS or Linux: `PROMPTS_ALLOW_INACTIVE=true make try-agent ARGS="scanner_fault --offline"`.)

## What it costs

Each case is one model call, sometimes two (one retry if the answer is malformed). At prices
similar to gpt-5-mini's list price, a call costs well under one US cent: roughly 6,000 input
tokens and at most 1,200 output tokens. Thirty trial runs cost cents, not dollars. Three
guards stop surprises, all on by default:

| Guard | Default | Where |
|---|---|---|
| Calls per case | 6 | `config/policy.yaml` `llm.max_calls_per_case` |
| Lifetime spend on this route | $150 (below the $200 credit) | `LLM_PRIMARY_SPEND_CAP_USD` |
| Stop when the free credit ends | on | `AZURE_CREDIT_EXPIRES_ON`, `LLM_ALLOW_PAID_USAGE=false` |

Every call's tokens and cost are written to `data/model_calls.sqlite3`. Prompt and answer
text are never written there.

## The steps

**Creating the Azure account starts the 30-day credit clock (D18). Do steps 1 to 10 in one
sitting if you can.**

1. **Azure account.** Create the free account at azure.microsoft.com, then upgrade it to
   pay-as-you-go the same day (the credit carries over; it is what lets you deploy models).
   Write down the date the credit ends.
2. **Budget alert.** In Cost Management, create a budget on your subscription (for example
   $20) with an email alert at 50%. Do this before any call.
3. **Foundry project.** In the Foundry portal (ai.azure.com), create a Foundry resource and a
   project, region **South India**, in a new resource group.
4. **Deploy the model.** In the project, deploy **gpt-5-mini**, deployment type **Global
   Standard**, with a small tokens-per-minute quota. If the portal asks you to register for
   gpt-5 models, apply and wait. Put the **deployment name** in `.env` as
   `FOUNDRY_MODEL_DEPLOYMENT`.
5. **Project endpoint.** On the project's overview page, copy the project endpoint. It looks
   like `https://<resource>.services.ai.azure.com/api/projects/<project>`. Put it in `.env`
   as `FOUNDRY_PROJECT_ENDPOINT`.
6. **Your role and the content filter.**
   - On the project's access control page, give yourself **Foundry User** (needed to create
     the agent in step 10).
   - On the deployment, attach a content filter with **Prompt Shields**, and turn on both
     user prompt attack detection **and document attack detection**. Document attacks are
     off by default, and they are the ones that matter here: seller text is a document.
7. **`.env` spending lines.**
   - `AZURE_CREDIT_EXPIRES_ON=YYYY-MM-DD`: the date from step 1. Required; the app refuses
     the Foundry route without it.
   - `LLM_ALLOW_PAID_USAGE=false`: leave it false.
   - `PROMPTS_ALLOW_INACTIVE=true`: the investigator prompt has not been through its eval
     suite yet (that is Phase 4+). This line says you are knowingly testing it. Without it
     the app refuses the prompt.
8. **Prices.** In the Azure portal, find gpt-5-mini's input and output price per million
   tokens for your deployment (the deployment's pricing details, or the Azure pricing
   calculator). Fill in `config/pricing.yaml`, using your deployment name as the key:
   ```yaml
   routes:
     foundry_agent_service:
       gpt-5-mini:                       # your FOUNDRY_MODEL_DEPLOYMENT
         input_per_mtok: "0.25"          # copy the real number from the portal
         output_per_mtok: "2.00"
         source: "Azure portal, deployment pricing, read on 2026-11-05"
   ```
   The numbers above show the shape only. The app refuses to call Foundry without this row:
   a spending cap with no price would count every call as free.
9. **Sign in.** Install the Azure CLI and run `az login`. There are no keys in this project;
   the app signs in as you, through Entra ID (I14).
10. **Create the agent:** `make agents-sync`. It creates the agent `wd-case-investigator` from
    `prompts/case_investigator/v1/system.md`, reads it back to confirm Foundry stored exactly
    that, and records the version in `config/foundry_agents.lock.json`. Commit that file.
    Running it again changes nothing unless the prompt changed.
11. **Check:** `make foundry-check`. It checks each piece in order, with no model call, and
    tells you the first one that is missing and how to fix it.
12. **Try it:** `make try-agent`. Then try other cases:
    ```
    make try-agent ARGS="kurta_box_larger"
    make try-agent ARGS="injection_in_statement"
    ```

## What to look at in a trial

`make try-agent` prints three things:

- **What the agent proposed**, with its reasons and the evidence it cited.
- **What the system decided.** The agent only proposes. The system checks the proposal (on
  the menu, real evidence ids, the right parameters), drafts any email from a template, and
  runs policy. A case either waits in the approval queue or goes to a person with a reason.
- **The cost** of the run.

Nothing is sent. The trial uses a throwaway database and no email or Jira connection.

Cases worth trying, and what a good result looks like:

| Case | What it is | A good result |
|---|---|---|
| `scanner_fault` | box measured bigger than declared, no photo yet | asks the seller for a photo; email waits for your approval |
| `kurta_box_larger` | the seller sent an image | asks for evidence or escalates; never decides on the image alone |
| `calc_error_wrong_slab` | provable overcharge | proposes a credit, then the system hands it to a person (credits are Phase 5) |
| `injection_in_statement` | the dispute text tries to give the agent orders | the agent treats it as data, and may list it under "flagged as manipulation attempts" |
| `not_delivered` | parcel never delivered | stops before the agent is asked |

## When something goes wrong

| You see | Meaning | Fix |
|---|---|---|
| `MISSING FOUNDRY_PROJECT_ENDPOINT` | `.env` line empty | step 5 |
| `prompt ... is inactive` | the opt-in is off | step 7, `PROMPTS_ALLOW_INACTIVE=true` |
| `no price for ...` | pricing row missing | step 8 |
| `Azure sign-in` | no `az login` session | step 9 |
| `no synced agent` / `prompt changed since the last sync` | lock missing or stale | `make agents-sync` |
| `agent in Foundry differs from the lock` | someone edited the agent in the portal | `make agents-sync` makes a fresh version |
| `Handed to a person because: agent_drift` | same as above, caught at call time | `make agents-sync` |
| `... because: llm_budget_exceeded` | a spending guard stopped the call | see the guard table; check `data/model_calls.sqlite3` |
| `... because: module_unavailable (model_gateway)` | Foundry could not be reached, and the rules could not decide | check the endpoint, your network, and the deployment's quota |
| `Proposed by: RULES (fallback ...)` | Foundry could not be reached, so the rules decided | as above |
| `... because: invalid_model_output` | the agent answered badly twice | look at the case; this is what the Phase 4+ evals measure |

## What the prototype does not do yet

These are later phases (ADR 0029): the Haiku failover route, tracing in Application
Insights, using the content filter's annotations as screening flags, the eval suite and
model choice, the AI reply interpreter and drafter, and the live runtime (`make run-api`)
running the decide station. That last one arrives with the sending stations in Phase 5.
