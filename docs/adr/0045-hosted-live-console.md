# 0045 — The console, hosted live: real Gmail and Jira behind one password

Date: 2026-11-07
Status: accepted (D65). Deployed by the owner on Streamlit Community Cloud from `streamlit_app.py`.

## Decision

The owner chose a hosted console that does the real things: real email through their Gmail and real issues in their Jira test project. The alternative was a stand-in demo. The two choices that came with it:

- **One password in front of every screen** (`CONSOLE_PASSWORD`). Approvals still also need the review console token. *Amended by ADR 0047: hosted, the password stands in for the token, and Review offers only the listed approvers.*
- **Gmail renewed weekly.** While the Google app is in Testing status, its sign-in lasts 7 days.

## How

- **One process.** `streamlit_app.py`, at the repository root like the scripts, migrates the case store and builds the same FastAPI app `make run-api` serves. It enters it with FastAPI's in-process test client, which runs the lifespan: recovery, then a runner tick every 5 seconds. A seller's ticket goes to `POST /events` in memory, the same path as locally. Nothing inside `dispute_agent` imports `dispute_agent.app` (test_composition_root).
- **Configuration** is chosen by name: `MODULES_FILE=modules.hosted.yaml` turns on real Gmail and Jira, with the rules engine deciding. That file names no secret. A named file that is missing is an error, never a fall back to another configuration.
- **Gmail's sign-in travels as a secret** (`GMAIL_REFRESH_TOKEN`), held in memory only (`SecretRefreshStore`). The encrypted Windows file cannot open on Linux. The owner's machine keeps the encrypted file and does not fall back to plain text.
- **No local-only lock in the shared config.** `.streamlit/config.toml` no longer pins `server.address`, so the host can bind. `make run-console` passes `--server.address 127.0.0.1`, so the local console stays on loopback (T09).

## Why it is safe enough, and what it is not

- **Fails closed.**
  - No password configured: the console does not open.
  - Wrong password: no screen.
  - A missing account: the service does not start, and the page names the missing setting, never a value.
  - An expired Gmail sign-in: sends fail as `reauth_required`, and those cases go to a person (I4, I6).
- **Who can be emailed:** only `TEST_SELLER_EMAIL` (I11, R06). A stranger cannot be.
- **What runs without approval:** a password holder can raise tickets, and some cases open a Jira issue without approval (policy allows `UPSERT_ISSUE`). The 14 synthetic shipments and one open case per shipment bound that to a handful. Every credit and every message still needs the review token holder (I3, I13).
- **Secrets** live only in the host's secret store. They are exposed to the app as environment variables, redacted from logs (`secret_values` covers every `SecretStr`), and never in the repository.
- **What is new to accept:** the owner's Gmail sign-in and Jira token now sit with a third party (Streamlit Community Cloud's secret store). Revoke them at myaccount.google.com/permissions and id.atlassian.com if the app is retired.
- **Not production.** The case store and the simulated ledger are files on the host and vanish when it restarts the app; real emails and issues stay. The AI is the rules engine, because the Foundry sign-in is tied to the owner's machine.

## Results

- **Tests (17):**
  - the gate: no password stays closed, a wrong one shows nothing, the right one opens;
  - the secret Gmail store is never shown in a repr and is used when given;
  - new secrets are redacted;
  - the hosted file uses real Gmail and Jira with rules;
  - a missing named file is an error;
  - hosted without accounts refuses to start and names them;
  - the local console stays on loopback;
  - the Secrets block is valid TOML and names what is missing.
- `make verify` is green.
- **Run locally as Streamlit Cloud would:** the gate held. The in-process service started and recovered. A ticket on SHP-000007 went through `POST /events` in memory and closed as `REJECTED_INELIGIBLE`, with nothing sent.
