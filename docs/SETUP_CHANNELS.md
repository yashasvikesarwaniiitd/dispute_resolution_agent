# Setting up the real channels (owner only)

Phase 3 is the first time this project talks to anything outside itself. Three accounts have
to exist before the live tests and `make demo` can run, and only you can create them — Claude
Code never creates cloud resources (`CLAUDE.md` section 16).

Everything else in Phase 3 works without them. `make verify` stays green, and the live tests
skip with a reason naming what is missing.

## The rule that matters most

**No value from this page ever goes into chat, a commit, a log line, a screenshot or Claude
Code's settings.** Values go in `.env`, which is git-ignored. `.env.example` lists names and
nothing else. That is invariant I12, and it is the one mistake in this setup that cannot be
undone — a token pasted into a chat transcript has been disclosed, whatever happens next.

If you ever paste one by accident: revoke it at the source first, then create a new one.
Deleting the message is not revocation.

## What you are creating, and why each one

| | What | Why this shape |
|---|---|---|
| 1 | A **personal Outlook.com account** used only by this project, registered as an Entra **public client** app | Delegated sign-in as a pilot account, decided in ADR 0023. A public client has **no client secret** to leak — you sign in once and an encrypted token cache does the rest |
| 2 | A **Jira Cloud site**, a test project, and an **API token** | ADR 0020: issues are created idempotently against our own action record, so a test project with throwaway issues is all that is needed |
| 3 | A **second email address** acting as the test seller | I11: the system must never be able to reach a real seller. This address is the only recipient the policy allows |

---

## 1. Outlook, through Microsoft Graph

### 1a. The pilot mailbox

Create a new personal Outlook.com account for this project, or use an existing one you do
not mind the project sending from. It will send real email.

### 1b. Register the app

> **Found on 2026-11-04:** a brand-new personal Microsoft account has no directory of its own, and entra.microsoft.com then fails with `AADSTS16000 ... does not exist in tenant 'Microsoft Services'`. The account needs a directory first; signing up for Azure with it creates one (a "Default Directory"). Do not register this app in an employer's directory.

1. Sign in to the [Microsoft Entra admin centre](https://entra.microsoft.com) with that
   account and go to **Applications → App registrations → New registration**.
2. Name it anything (`dispute-agent-local` reads well in the consent screen).
3. **Supported account types:** *Personal Microsoft accounts only*. This is what makes the
   `consumers` authority correct; picking a tenant option here is the usual cause of
   `AADSTS50194` later.
4. Leave the redirect URI blank — the device code flow does not use one.
5. Register, then on **Authentication**, turn **Allow public client flows** to **Yes**. The
   device code flow fails without it.
6. On **API permissions → Add a permission → Microsoft Graph → Delegated permissions**, add
   exactly these four and no more (ADR 0023; least privilege is the point):

   - `Mail.ReadWrite` — create the draft that carries our idempotency marker
   - `Mail.Send` — send that draft
   - `Mail.Read` — read it back from Sent Items, and poll for replies
   - `offline_access` — refresh silently, so you sign in once rather than daily

   There is deliberately no `Mail.ReadWrite.Shared`, no `User.Read.All`, and no application
   permission. The app can touch one mailbox: its own.

7. Copy the **Application (client) ID** from the Overview page into `.env` as
   `GRAPH_CLIENT_ID`, and the pilot mailbox address as `GRAPH_MAILBOX`.

The client ID is not a secret — it is in every sign-in URL. It still belongs in `.env` rather
than in a commit, because it names your app registration.

### 1c. Sign in once

```bash
make email-login
```

This prints a short code and a URL. Open it, sign in **as the pilot account**, and consent.
The token lands in an encrypted cache at `GRAPH_TOKEN_CACHE_PATH` (DPAPI on Windows). That
file *is* a secret: it lives under `data/`, which is git-ignored, and must never be copied
anywhere.

When the refresh token eventually expires, sends stop with `reauth_required` and cases go to
human review rather than failing silently. Run `make email-login` again.

---

## 1-alt. Gmail instead of Outlook (ADR 0038)

Use this if you would rather not create a Microsoft directory. You need a **dedicated Gmail**
for the project (the sender) - not your main one, so the project never sees your own inbox.

1. Create a new Gmail account for the project, e.g. `yk.dispute.agent@gmail.com`.
2. Signed in **as that account**, open [console.cloud.google.com](https://console.cloud.google.com)
   and create a project (`dispute-agent`). No card and no credits are needed for the Gmail API.
3. **APIs & Services → Library**: search **Gmail API** → **Enable**.
4. **Google Auth Platform** (or **APIs & Services → OAuth consent screen**): start the setup.
   - App name `dispute-agent`, support email: the project Gmail.
   - Audience: **External**. Leave it in **Testing**.
   - **Test users → Add users**: the project Gmail.
   - Data access / scopes: add `.../auth/gmail.send` and `.../auth/gmail.readonly`.
5. **Clients → Create client** (or **Credentials → Create credentials → OAuth client ID**):
   type **Desktop app**, name `dispute-agent-local`. Copy the **Client ID** and **Client secret**.
6. Put them in `.env` as `GMAIL_CLIENT_ID` and `GMAIL_CLIENT_SECRET`, and the project Gmail as
   `GMAIL_MAILBOX` (use the clipboard command; never paste them into chat).
7. Sign in once: `make gmail-login` (or `uv run python scripts/make.py gmail-login`). A browser
   tab opens; sign in as the project Gmail. Google shows **"Google hasn't verified this app"**
   for your own testing app: choose **Continue**. Allow both permissions.

**Every 7 days** (Testing status) the sign-in expires: sends stop with `reauth_required`, cases
go to a person, and you run `make gmail-login` again.

---

## 2. Jira Cloud

1. Create a free Jira Cloud site at [atlassian.com](https://www.atlassian.com/software/jira/free)
   if you do not have one, and create a **test project** inside it. A Team-managed Kanban
   project is fine; the project key is what matters.
2. Create an API token at
   [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens).
   Give it a name you will recognise later, so revoking it is obvious.
3. Fill in `.env`:

   - `JIRA_BASE_URL` — your site, e.g. the `https://<something>.atlassian.net` form
   - `JIRA_EMAIL` — the Atlassian account the token belongs to
   - `JIRA_API_TOKEN` — the token
   - `JIRA_PROJECT_KEY` — the test project's key

**The token is a password.** Jira's REST API authenticates with email plus token over Basic
auth, so anyone holding both can act as you across every project you can see. That is exactly
why this is a dedicated test project and why the token belongs nowhere but `.env`. Revoke it
on the page above the moment this project is finished with it.

---

## 3. The test seller

Create or pick a second email address you control — a second personal account, or an alias.
Put it in `.env` as `TEST_SELLER_EMAIL`.

This address is the **only** recipient the policy will allow (`policy.messages.allowed_recipients`).
A case whose seller contact is anything else is refused before any send is attempted, which is
how I11 stops a synthetic dispute from reaching a real person. The synthetic generator only
ever produces addresses at `example.invalid`, a domain reserved by RFC 2606 that cannot
resolve — so the fixtures are safe even if that rule were ever removed.

---

## 4. The review console token

`REVIEW_CONSOLE_TOKEN` protects the local approvals queue (ADR 0018). Generate a long random
value and put it in `.env`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

It only guards a page on your own machine, but the queue is where messages are approved for
sending, so it is a secret like any other.

---

## 5. Check it

> To use a real channel in the running app on your machine only, copy `config/modules.yaml` to `config/modules.local.yaml` (git-ignored) and change the adapter, e.g. `ticketing` to `jira_cloud`. The shared file and CI keep the fakes.

```bash
make test-integration
```

Runs the live tests against both services. Without credentials they skip, naming what is
missing. With them, they send real email to the test seller and create real issues in the
test project.

```bash
make demo
```

Runs one synthetic dispute end to end: a case opens, an evidence request is drafted, you
approve it in the queue, it is sent, read back from Sent Items and verified; a reply resumes
the case; a Jira issue is opened and verified by read-back.

## If something goes wrong

| Symptom | Usually |
|---|---|
| `AADSTS50194` or "application is not configured as a multi-tenant application" | Supported account types is not *Personal Microsoft accounts only* (step 1b.3) |
| Device code flow returns `invalid_client` | **Allow public client flows** is off (step 1b.5) |
| Sends fail with `reauth_required` | The refresh token expired — run `make email-login` again |
| Jira returns 401 | The token is tied to a different Atlassian account than `JIRA_EMAIL` |
| Jira returns 403 on create | The account cannot create issues in `JIRA_PROJECT_KEY` |
| A send is refused as `recipient_not_allowed` | The case's seller contact is not `TEST_SELLER_EMAIL`. Working as intended (I11) |

## When you are done with the project

Revoke the Jira API token, delete the Entra app registration, and delete
`GRAPH_TOKEN_CACHE_PATH`. Credentials that outlive their purpose are the ones that leak.
