# Foundry setup (owner steps)

Claude Code never creates Azure resources or asks for secrets. You do these steps once, before Phase 4b. Check each against the current Microsoft documentation, because portal labels, regions and model availability change.

**Verified on 2026-11-02 in Stage A3** (sources in `docs/spec/foundry.md`): `gpt-5-mini` and `gpt-5.4-mini` Global Standard and Agent Service (Responses API and Agents) are available in South India. The complete ordered checklist for every service (Microsoft 365 mailbox, Jira, Anthropic, Azure) is `docs/spec/foundry.md` section 13; this page explains the Azure part in more detail.

## Decisions already made

| Choice | Value | Why |
|--------|-------|-----|
| Runtime model | `gpt-5-mini` (Azure OpenAI), Global Standard | Mid-cheap, first-party Azure model that your Azure credits can pay for; available in South India for both deployment and Agent Service |
| Fallback to compare | `gpt-5.4-mini` | Newer (retires 2027-09-21); available in South India; also the alternative if `gpt-5-mini` needs a registration you cannot get |
| Region | South India | Listed for `gpt-5-mini` Global Standard and for Agent Service model support; nearest region to you |
| Agent platform | Foundry Agent Service, prompt agents without tools | Owner decision for v1 |
| Avoid | `gpt-4.1-mini` | Deprecated: base inference retires 2027-04-14 (fine-tuned 2027-10-14), and deprecated models cannot be newly deployed on new subscriptions. (Earlier versions of this page said 2026-10-14; corrected from the official retirement schedule) |
| Claude on Foundry | Deferred | Needs a project in East US 2 or Sweden Central and Marketplace billing, which Azure credits may not cover |

## Your Azure free account: what the terms mean for this project

- The $200 credit is valid for up to 30 days from sign-up. At day 30 or when the credit runs out (whichever comes first), your services are disabled unless you move to pay-as-you-go.
- If you move to pay-as-you-go before day 30, you keep the remaining credit until day 30.
- Free-trial subscriptions can have **zero quota** for Azure OpenAI models even while the credit shows. Moving to pay-as-you-go unlocks quota while the credit stays valid. Plan to upgrade the same day you sign up.
- The credit cannot buy Azure Marketplace offers until you are on pay-as-you-go, which is another reason Claude on Foundry is deferred.
- Signing up needs a card, but you are not charged unless you move to pay-as-you-go. After the upgrade, usage beyond the credit is billed to the card.
- You can see the remaining credit under Cost Management in the Azure portal.

**Therefore:** create the account only when Phase 4b starts, because the 30-day clock starts at sign-up. Upgrade to pay-as-you-go immediately, then write the credit expiry date into `llm_primary.credit_expires_on` (`AZURE_CREDIT_EXPIRES_ON`). Leave `LLM_ALLOW_PAID_USAGE=false`: after the window, Foundry calls stop and the Haiku secondary or a human takes over. Do all the Foundry-dependent work inside the window: Phase 4b live evals, demo recording, and the Phase 6 Foundry evaluation and red team. Everything else runs on fakes and recorded fixtures.

## Secondary model: Claude Haiku (your Anthropic API)

- Add `ANTHROPIC_API_KEY` to `.env` only. Set a monthly spend limit in the Anthropic Console as well.
- The model id defaults to `claude-haiku-5-5` (`ANTHROPIC_MODEL_HAIKU`). Confirm it against Anthropic's current model page. The code refuses any model id that is not Haiku.
- It is used only when Foundry is unavailable, rate-limited, out of quota or past the credit window. It has its own cap, `LLM_SECONDARY_SPEND_CAP_USD` (default 10).
- It can also run the Phase 4 prompt evals live before the Azure window opens, which keeps your 30 days for Foundry work.
- Use synthetic data only. Never put the key in Claude Code settings or chat.

## Steps

1. Create the Azure free account (if you have not), upgrade to pay-as-you-go, then create a resource group and a Foundry resource and project in **South India**. If the portal does not offer South India for a Foundry project or for Agent Service, pick the nearest region where the `gpt-5-mini` Global Standard deployment and Agent Service both work (for example Central India or Southeast Asia) and record it in `docs/PROGRESS.md`.
2. In the Foundry portal, deploy `gpt-5-mini` with **Global Standard** and a small quota. Microsoft notes that gpt-5 models may need registration; if the portal asks, apply, or use `gpt-5.4-mini` instead. Note the **deployment name** (it can differ from the model name). It goes in `FOUNDRY_MODEL_DEPLOYMENT`.
3. Note the project endpoint, of the form `https://<resource>.services.ai.azure.com/api/projects/<project>` (the portal shows the exact value). It goes in `FOUNDRY_PROJECT_ENDPOINT`.
4. Assign yourself **Foundry User** on the Foundry project (it was called Azure AI User before; it covers creating agents). Optional hardening: give the runtime a separate identity with only **Foundry Agent Consumer**.
5. Install the Azure CLI and run `az login`. The SDKs use `DefaultAzureCredential`, so there are no keys to manage. The Foundry route uses Entra ID only.
6. In the portal, attach a content filter to the deployment with Prompt Shields **user prompt attack** detection (on by default) and **document attack** detection (off by default: turn it on, because seller text is untrusted content). Phase 4b observes its annotations. Do not rely on "Guardrails for Agents", which is preview.
7. Put values in `.env` (copy `.env.example`). `.env` is git-ignored. Never paste values into Claude Code chat.
8. Optional: create an Application Insights resource and set `APPLICATIONINSIGHTS_CONNECTION_STRING` to see traces.

## Cost controls

- Create an Azure budget with an alert on the resource group before the first call.
- Keep the deployment quota small.
- Prices live in `config/pricing.yaml`. On 2026-11-02 the official Azure OpenAI pricing page showed "$-" for `gpt-5-mini` Global, so read the price in the Azure portal (deployment details or the pricing calculator) and record where it came from. The app refuses to start a route with no price, so the spend cap always works.
- The project enforces `llm.max_calls_per_case` and a spend cap per route (`llm_primary.spend_cap_usd`, default 150 of your 200; `llm_secondary.spend_cap_usd`, default 10) from the `model_calls` table, and `make foundry-spend` shows the running total. Reasoning models bill hidden reasoning tokens as output, so the cap measures reported usage, not guesses.

## Good to know

- Agent Service supports Azure OpenAI deployments, but exact model and tool availability varies by region. The agent classic documentation noted that GPT-5 family agents could use only certain built-in tools; this project uses no tools, so that limit does not apply. Confirm in the current Agent Service documentation.
- Agent definitions are created from `prompts/` by `make agents-sync`, which you run yourself. Runtime refuses drifted agents.
- The SDK's GenAI tracing instrumentation is preview and is not used; the project emits its own spans with content recording off. Cloud red teaming does not run in South India (supported: East US 2, France Central, Sweden Central, Switzerland West, North Central US); owner decision D35: red-team locally with the project's own injection suite instead.
- Calls to agents always pin the agent version from `config/foundry_agents.lock.json` and use `store=false` (no stored responses; not a zero-retention guarantee, which is fine for synthetic data).
- Synthetic data only. Do not send real seller data to any model.

## Check

After Phase 4b is built, `make foundry-check` makes one small call per agent and prints deployment, agent version, latency and token counts. It prints no content and no tokens.
