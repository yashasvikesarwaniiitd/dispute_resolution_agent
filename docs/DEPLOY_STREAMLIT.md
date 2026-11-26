# Deploying the live console to Streamlit Community Cloud

ADR 0045, ADR 0068. **The site opens on the Dispute Desk** (the v2 web workspace): `streamlit_app.py` is an `st.App` that serves Streamlit plus the workspace API at `/api/ws` and the built web app at `/desk/`, and the Streamlit page frames the desk. The four-screen Streamlit console still runs locally (`make run-console`).

The hosted site sends **real email** through your Gmail and opens **real Jira issues**, behind one password. These steps take about ten minutes. Every value in them stays in Streamlit's secret store, never in the repository.

> **The password is currently off** (owner's choice, 2026-11-25). Every visitor is let in and reviews as a listed approver, so anyone with the link can raise tickets, approve credits and emails as "owner", trigger the test email and spend the AI budget. To close the site again, pass `open_site=False` in `streamlit_app.py` (its `shortcut:` comment says where) and keep `CONSOLE_PASSWORD` in the Secrets: the desk then asks for the password first. The steps below still describe the site with the password on.

## Before you start

- Gmail and Jira work locally. Check with `make demo ARGS="calc_error_high_value"`.
- You are signed in to Gmail: run `make gmail-login` (repeat every 7 days).

## 1. Copy the Secrets block (on your laptop)

```bash
make hosted-secrets
```

This builds the whole block from your `.env` and Gmail sign-in and copies it to your clipboard, printing no secret. It prints one thing: **the console password**. Note it; you give it to whoever should use the app. If something is missing, it names it and copies nothing.

## 2. Create the app (share.streamlit.io)

1. Sign in with GitHub, then **Create app**, then **Deploy a public app from GitHub**.
2. Fill in:
   - **Repository:** `yashasvikesarwaniiitd/dispute_resolution_agent`
   - **Branch:** `main`
   - **Main file path:** `streamlit_app.py`
   - **App URL:** pick a name, for example `dispute-desk`.
3. **Advanced settings:**
   - **Python version:** 3.12
   - **Secrets:** paste the clipboard (Ctrl+V), then **Save**.
4. **Deploy.** The first start takes a few minutes while it installs.
5. Copy something else to clear the clipboard.

Open the app, enter the console password, and you are in. On the Review screen, choose who you review as (the approvers listed in `config/policy.yaml`, today "owner"). That approver is recorded against each decision. The review console token is not needed on the hosted app (ADR 0047), and if your Secrets still hold `REVIEW_CONSOLE_TOKEN` you can delete that line.

## Every 7 days: renew Gmail

```bash
make gmail-login
```

```bash
make gmail-export
```

Then in Streamlit, open your app, go to **Settings**, then **Secrets**, replace the `GMAIL_REFRESH_TOKEN` line with the clipboard, and **Save**. The app restarts with the new sign-in. Until you renew, email cases go to a person instead of sending.

## The photo check (optional, ADR 0049)

When a person returns a photo case to the agent, a vision model describes the parcel photo and the case comes back with its verdict. Out of the box the live site **replays** the verdicts gpt-5-mini gave in its evaluation run (`image_check: recorded` in `config/modules.hosted.yaml`), so it works with no model call and no secret, and the card says it was replayed.

For **live** checks the site needs its own Foundry sign-in. Your laptop's sign-in cannot travel, so the site uses a Microsoft Entra **app registration** (still Entra ID only, I14). Steps, in the Azure portal:

1. **Microsoft Entra ID → App registrations → New registration.** Name it `dispute-desk-photo-check`, single tenant, no redirect URI. Register.
2. On its **Overview**, note the **Application (client) ID**.
3. **Certificates & secrets → New client secret.** Expiry 90 days. Copy the **Value** once (it is shown only once). Never paste it into chat.
4. Open your **Foundry resource** (the one with the `gpt-5-mini` deployment) → **Access control (IAM) → Add role assignment** → role **Cognitive Services OpenAI User** (or **Azure AI User**) → members: the app registration → **Review + assign**.
5. In your `.env` on the laptop, add:
   - `FOUNDRY_CLIENT_ID=` the client id
   - `FOUNDRY_CLIENT_SECRET=` the secret value
6. Run `make hosted-secrets` again. The block now also carries `AZURE_TENANT_ID`, `FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_MODEL_DEPLOYMENT`, `FOUNDRY_CLIENT_ID` and `FOUNDRY_CLIENT_SECRET`. Paste it into Streamlit's Secrets and Save.
7. In `config/modules.hosted.yaml`, change `image_check`'s adapter from `recorded` to `foundry_vision`, commit and push.

Each live check is one gpt-5-mini call (under US$0.005), logged and counted against the same spending caps as every other model call. To stop it, set the adapter back to `recorded`. When the secret expires, checks fail safe: the card says the sign-in failed and the case still reaches a person.

## The reply reader (ADR 0054)

When a seller answers an email, the agent reads the reply from the Gmail inbox within about a minute. Claude Haiku reads it (the `seller_reply_interpreter` prompt, which passed its evaluation suite on Haiku): what the seller means, and which of the items asked for arrived. If the seller sent the evidence asked for on a size dispute, the agent itself asks the hub to remeasure in Jira. Anything else - a question, a partial answer, a withdrawal, an attempt to instruct the agent - goes to a person, who sees the reply and the agent's reading on the Review card. No money moves on a reply: a credit still needs the hub's new measurement, code's proof and your approval.

1. Your `.env` must hold `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL_HAIKU` (the project's Haiku id).
2. Run `make hosted-secrets`. With the key present the block also carries `REPLY_READER = "anthropic_direct"`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL_HAIKU` and `LLM_SECONDARY_SPEND_CAP_USD`. Paste it into Streamlit's Secrets and Save.
3. Reboot the app (**⋮ → Reboot app**): the service reads its settings when it starts.

Each reply is one Haiku call (about US$0.0004), logged and counted against the Haiku route's own cap (`LLM_SECONDARY_SPEND_CAP_USD`, US$10 by default). To turn it off, delete the `REPLY_READER` line and reboot: every reply then goes to a person, as before. If a call fails or the cap is reached, the reply is recorded as unread and a person reads it.

## Good to know

- **Data resets** when Streamlit restarts the app: cases and the simulated ledger start fresh. Emails sent and Jira issues opened are real and stay.
- **After a push, reboot the app** (Manage app, ⋮, Reboot app). The dispute service is built once when the server starts and Streamlit's in-place reload is off, so old and new code never mix (a pydantic error on raising a ticket was seen live when they did, 2026-11-08). Until the reboot the site keeps running the previous code.
- **The web app is committed built** (`web/dist`): the host has no Node. After changing `web/src`, run `make web` and commit `web/dist` with it.
- **The AI is the rules engine** here. The Foundry sign-in is tied to your machine.
- **To retire the app:** delete it in Streamlit. Then revoke the Gmail access at myaccount.google.com/permissions and the Jira token at id.atlassian.com/manage-profile/security/api-tokens.
