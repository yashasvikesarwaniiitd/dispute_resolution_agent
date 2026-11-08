# Deploying the live console to Streamlit Community Cloud

ADR 0045. The hosted console sends **real email** through your Gmail and opens **real Jira issues**, behind one password. These steps take about ten minutes. Every value in them stays in Streamlit's secret store, never in the repository.

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

## Good to know

- **Data resets** when Streamlit restarts the app: cases and the simulated ledger start fresh. Emails sent and Jira issues opened are real and stay.
- **A push updates the running app in place.** Streamlit reloads the code without restarting; the hosted entry notices and restarts the dispute service from the new code (`streamlit_app.py`, `Services`). If a page ever shows an error right after a push, open Manage app, ⋮, Reboot app.
- **The AI is the rules engine** here. The Foundry sign-in is tied to your machine.
- **To retire the app:** delete it in Streamlit. Then revoke the Gmail access at myaccount.google.com/permissions and the Jira token at id.atlassian.com/manage-profile/security/api-tokens.
