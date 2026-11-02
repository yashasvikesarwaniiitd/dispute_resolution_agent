# Integrations (Stage A3)

Status: draft for owner approval. Contracts for the three write ports (`MessageChannel`, `TicketingSystem`, `Ledger`) against current official documentation, read on 2026-11-02. Every external claim cites a page actually read; "undocumented" means the docs were checked and are silent, and the design does not depend on the missing behaviour.

Plain-language summary: Outlook and Jira were not built for "do this exactly once", so the design adds its own guarantees. Each email carries a hidden unique key so we can always find it again. Each Jira issue carries a unique label and is checked by reading it back directly. When we cannot tell whether something happened, the case goes to a person (I4). The simulated ledger is strict by design.

Every external call below has a timeout (`runtime.http`, `executor.call_timeout_s`), a retry rule, an idempotency rule and a read-back rule; the summary table is section 4.

## 1. Outlook through Microsoft Graph (`modules/email/outlook_graph`)

### 1.1 Account, authentication and permissions (owner decision D36: personal Outlook.com, delegated)

- **Account:** a personal Outlook.com account. Strongly recommended: a **new account used only for this pilot** (`GRAPH_MAILBOX`), because delegated access cannot be narrowed to part of a mailbox (risk K10). App-only access is not available for personal accounts ([permissions overview](https://learn.microsoft.com/en-us/graph/permissions-overview)), so the app acts as the signed-in owner.
- **App registration:** supported account types "work or school and personal accounts" (`AzureADandPersonalMicrosoftAccount`), used with the authority `https://login.microsoftonline.com/consumers/`, which restricts sign-in to personal accounts. The docs say MSA-only sign-in is achieved exactly this way ([client application configuration](https://learn.microsoft.com/en-us/entra/identity-platform/msal-client-application-configuration)). Consumer registrations have limits, for example at most 30 permissions per resource ([supported account types](https://learn.microsoft.com/en-us/entra/identity-platform/supported-accounts-validation)).
- **Client type:** public client (`msal.PublicClientApplication`). There is **no client secret**. Sign-in uses the device code flow (`initiate_device_flow`, then `acquire_token_by_device_flow`), which needs no redirect URI; `acquire_token_interactive` with redirect `http://localhost` is the alternative ([acquiring tokens](https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens)).
- **Delegated scopes:**
  - `Mail.ReadWrite` (create the draft, set the extended property), `Mail.Send` and `Mail.Read`, each "available for consent in personal Microsoft accounts" ([permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference)).
  - The API pages list a "Delegated (personal Microsoft account)" row for create, send and delta ([create message](https://learn.microsoft.com/en-us/graph/api/user-post-messages?view=graph-rest-1.0), [send](https://learn.microsoft.com/en-us/graph/api/message-send?view=graph-rest-1.0), [delta](https://learn.microsoft.com/en-us/graph/api/message-delta?view=graph-rest-1.0)).
  - Plus `offline_access` for a refresh token. Its personal-account support is not stated explicitly; MSAL adds it by default, and `make email-login` checks that a refresh token was cached.
- **Unattended operation:**
  - `acquire_token_silent` refreshes from the cache. It returns None when it cannot; the adapter then raises `AuthPortError(reauth_required)` (sends escalate, polling stops), logs ERROR, and `/healthz` shows `email: reauth_required`.
  - The owner runs `make email-login` again. Refresh tokens last 90 days by default, are rotated on each use, and can be revoked at any time, for example on a password change ([refresh tokens](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens)). Inactivity rules for personal accounts are not documented.
- **Token cache (a secret, I12):**
  - MSAL's cache is in memory unless serialized ([token cache serialization](https://learn.microsoft.com/en-us/entra/msal/python/advanced/msal-python-token-cache-serialization)).
  - The adapter persists it with `msal-extensions`: `PersistedTokenCache(build_encrypted_persistence(path))`, encrypted with DPAPI on Windows, Keychain on macOS and LibSecret on Linux. It raises instead of falling back to plain text unless told otherwise ([msal-extensions](https://github.com/AzureAD/microsoft-authentication-extensions-for-python)). Plain-text fallback is never enabled.
  - Path `GRAPH_TOKEN_CACHE_PATH` (default `data/msal_cache.bin`); `data/` is git-ignored.

Alternative not selected (kept for a later move to a work tenant, ADR 0023): app-only client credentials scoped to one mailbox with Exchange RBAC for Applications ([RBAC for Applications](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac)).

### 1.2 Sending: draft, mark, send

`sendMail` returns "202 Accepted" with no body, and 202 "doesn't indicate that the request processing has completed" ([sendMail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0)), so it gives no id to verify. The adapter therefore uses the draft flow:

Calls use `/me/...` (delegated; the signed-in mailbox). Below, `/users/{mailbox}` reads as `/me`.

1. `POST /users/{mailbox}/messages` with header `Prefer: IdType="ImmutableId"`. Body: subject; body `contentType: "Text"`; `toRecipients` = the one recipient; and a single-value extended property `String {GUID} Name DisputeAgentIdempotencyKey` = the action's idempotency key (`singleValueExtendedProperties`). Response `201 Created` with the message and its immutable id ([create message](https://learn.microsoft.com/en-us/graph/api/user-post-messages?view=graph-rest-1.0)). Also set the custom header `x-dispute-idempotency-key` (headers can only be added at creation and must start with `x-`: [message resource](https://learn.microsoft.com/en-us/graph/api/resources/message?view=graph-rest-1.0)); the header is for humans reading raw mail, the extended property is for lookup.
2. `POST /users/{mailbox}/messages/{id}/send`: `202 Accepted`, no body; the message is saved in Sent Items ([send message](https://learn.microsoft.com/en-us/graph/api/message-send?view=graph-rest-1.0)). With immutable ids, "Get the message using the ID from the first step. This is the copy in Sent Items", but "getting the message in Sent Items might not succeed immediately" ([immutable ids](https://learn.microsoft.com/en-us/graph/outlook-immutable-id)).
3. `SendReceipt.external_message_id` = the immutable id from step 1.

**Immutable ids on personal mailboxes are undocumented** (the [immutable id page](https://learn.microsoft.com/en-us/graph/outlook-immutable-id) neither includes nor excludes consumer mailboxes). So the immutable id is only a **fast path**: the extended-property `$filter` on Sent Items, which is documented for personal accounts ([extended property filter](https://learn.microsoft.com/en-us/graph/api/singlevaluelegacyextendedproperty-get?view=graph-rest-1.0)), is the authoritative way to find the sent copy (sections 1.3 and 1.4).

The `{GUID}` is a fixed project constant in adapter settings. The key is high-entropy and matched with `eq`, because value matching is case-insensitive and `contains` is also supported ([extended property filter](https://learn.microsoft.com/en-us/graph/api/singlevaluelegacyextendedproperty-get?view=graph-rest-1.0)).

**Port flags:** `idempotent_writes = False` (Graph has no idempotency key), `lookup_supported = True`.

### 1.3 Lookup by key (`find_sent`)

`GET /users/{mailbox}/mailFolders/sentitems/messages?$filter=singleValueExtendedProperties/Any(ep: ep/id eq 'String {GUID} Name DisputeAgentIdempotencyKey' and ep/value eq '<key>')`, then the same on `drafts` ([extended property filter](https://learn.microsoft.com/en-us/graph/api/singlevaluelegacyextendedproperty-get?view=graph-rest-1.0)).

| Found in | Meaning | Adapter returns |
|----------|---------|-----------------|
| Sent Items | sent | `SendReceipt` |
| Drafts only (one or more copies) | created; `/send` either never ran or is still being processed (the move is asynchronous: [send mail notes](https://learn.microsoft.com/en-us/graph/outlook-things-to-know-about-send-mail)) | raises `AmbiguousOutcome(external_ref=<draft ids>)` (the executor records UNKNOWN; the case reaches a person) |
| Neither | nothing was created | `None` |

Re-using a draft found in Drafts is never done automatically by a later `execute` run: sending it might send a second copy if the first `/send` was accepted but not yet moved. (The only reuse is inside one `send()` call, after a failed create, before `/send` has ever been called.) A human resolves it, following the rule: **never send a draft that carries an idempotency key; check Sent Items, then delete stray drafts.**

### 1.4 Read-back (`get_message`)

`GET /users/{mailbox}/messages/{immutable id}` with `Prefer: IdType="ImmutableId"` and `Prefer: outlook.body-content-type="text"` ([create and send](https://learn.microsoft.com/en-us/graph/outlook-create-send-messages)), `$select=subject,body,toRecipients,conversationId,parentFolderId,internetMessageHeaders,singleValueExtendedProperties` (plus `$expand` of the extended property). If the GET by id returns 404, the adapter falls back to the `find_sent` filter on Sent Items and verifies the message it finds (personal mailboxes may not keep the id stable). Neither finds it -> `ExternalNotFound` (expected for a while after sending; VERIFYING rests, ADR 0010). If the message is found but `parentFolderId` is not Sent Items (with immutable ids the id resolves to the **Drafts** copy until the move happens), the snapshot has `in_sent_items = false` and the adapter raises `ExternalNotFound`, so the case keeps waiting and becomes UNKNOWN at the deadline. It is never verified and never fails early.

Body normalisation before hashing: whether a Text body round-trips byte-for-byte is undocumented, and examples show HTML wrapping ([create message](https://learn.microsoft.com/en-us/graph/api/user-post-messages?view=graph-rest-1.0)). The executor's `message_content_hash` therefore hashes **normalised** text on both sides: Unicode NFC, `\r\n` -> `\n`, trailing whitespace stripped per line, runs of blank lines collapsed to one, leading and trailing whitespace removed. Plus the exact subject, the recipient set, and the extended-property key. If the Phase 3 integration test shows Exchange still alters the text, an ADR switches the body check to a token-level comparison; the subject, recipient and key checks stay exact. Risk K1 in section 6.

### 1.5 Replies, bounces and sender authentication (`fetch_replies`)

- **Polling with delta:** `GET /users/{mailbox}/mailFolders/inbox/messages/delta?changeType=created` with `$select` of the fields needed. Follow `@odata.nextLink` to the `@odata.deltaLink`, persist it, and use it next round ([message delta](https://learn.microsoft.com/en-us/graph/api/message-delta?view=graph-rest-1.0), [delta for messages](https://learn.microsoft.com/en-us/graph/delta-query-messages)).
  - Interval: `email.settings.poll_interval_s` (60).
  - Delta tokens can be evicted (`syncStateNotFound`), and `410 Gone` means a full resync ([delta overview](https://learn.microsoft.com/en-us/graph/delta-query-overview)). On either, the adapter resyncs with `$filter=receivedDateTime ge <last processed - 1 h>`; inbox dedupe makes the overlap harmless. The dedupe key is `outlook:<Graph immutable id>`, **not** the `internetMessageId`, which the sender controls: a forged message reusing a real message's `internetMessageId` must not suppress the real one.
  - Replays can occur, which the same dedupe absorbs.
  - Delta with immutable ids can miss some changes (Graph known issues: [known issues](https://learn.microsoft.com/en-us/graph/known-issues)). Every 15 minutes the adapter also lists Inbox messages with `$filter=receivedDateTime ge <watermark - 1 h>` to reconcile; dedupe makes it harmless.
- **Routing fields:** `conversationId` (documented only as "the ID of the conversation the email belongs to": [message resource](https://learn.microsoft.com/en-us/graph/api/resources/message?view=graph-rest-1.0)), `internetMessageId`, `from`, `subject`, `receivedDateTime`, `internetMessageHeaders`. In-Reply-To and References are read from `internetMessageHeaders` when present (not named in the docs). Routing (`orchestrator.md` section 2) uses `conversationId` first.
- **Sender authentication:** Exchange Online stamps an `Authentication-Results` header with spf, dkim, dmarc and compauth ([EOP message headers](https://learn.microsoft.com/en-us/defender-office-365/message-headers-eop-mdo)). Graph returns internet headers via `internetMessageHeaders`. A sender can add their own `Authentication-Results` header, and the Outlook.com header format and authserv-id are **not documented**. Best effort: the adapter reads only the **topmost** `Authentication-Results` header, and if `email.settings.trusted_authserv_id` is set (recorded at provisioning from a test message), only when its authserv-id matches. From that header: `fail` if `dmarc=fail` or `compauth=fail`; `pass` if `dmarc=pass` or `compauth=pass`; otherwise `none`. `fail` raises the `sender_unauthenticated` signal (ESCALATE); `none` raises nothing (residual risk RR7).
- **Bounces (NDRs):** NDRs land in the sender's Inbox ([send mail notes](https://learn.microsoft.com/en-us/graph/outlook-things-to-know-about-send-mail)), but recognising one through Graph is **not documented**. Best-effort heuristic, labelled as such:
  - The sender local part is `postmaster` or `mailer-daemon`.
  - Or the MIME (`GET .../$value`) has `Content-Type: multipart/report; report-type=delivery-status`.
  - The failed message is matched by the `x-dispute-idempotency-key` or the original subject in the returned headers.

  A match produces `DELIVERY_FAILURE_RECEIVED`; anything else is an ordinary inbound message (and probably UNROUTABLE). Risk K2.
- **Untrusted text:** subject and body become `UntrustedText` (I5), truncated at 20,000 characters.

### 1.6 Errors and limits

| Graph response | Adapter raises |
|----------------|----------------|
| 400, 422 | `PermanentPortError(400)` |
| 401, 403 | `AuthPortError` |
| 404 on create/send target | `PermanentPortError(404)` |
| 404 on read-back | `ExternalNotFound` |
| 429 | `TransientPortError(retry_after_s from Retry-After, reason=rate_limited)`; exponential backoff if absent ([throttling](https://learn.microsoft.com/en-us/graph/throttling)) |
| 5xx or timeout on create | the adapter looks in Drafts for the key (one read). Exactly one unsent draft -> it reuses that draft and continues to `/send` in the same `send()` call. None -> `TransientPortError` (the executor may retry; a second create can leave a stray draft, never a second email). More than one -> `AmbiguousOutcome` |
| 5xx or timeout on `/send` | `AmbiguousOutcome` |
| connect error | `TransientPortError(reason=unavailable)` |

Limits:
- Graph allows 10,000 requests per 10 minutes and 4 concurrent requests per app per mailbox ([throttling limits](https://learn.microsoft.com/en-us/graph/throttling-limits)). The adapter caps concurrency at 2.
- Outlook.com: Microsoft 365 subscribers get 5,000 recipients per day, 500 per message and 1,000 per day to new recipients; non-subscribers get unstated "lower" limits, and new accounts start with a temporary low quota ([Outlook.com sending limits](https://support.microsoft.com/en-us/outlook/sending-limits-in-outlook-com)). Pilot volume is a few messages per day; a limit hit surfaces as a send failure and the case escalates.

## 2. Jira Cloud (`modules/ticketing/jira_cloud`)

### 2.1 Authentication and permissions

- Basic auth with the account email and an API token, `Authorization: Basic base64(email:token)`; passwords are deprecated ([basic auth](https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/)).
- Atlassian recommends **scoped** tokens, valid 1-365 days; scoped Jira tokens must call `https://api.atlassian.com/ex/jira/{cloudId}` instead of the site URL ([API tokens](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/)). `JIRA_BASE_URL` takes either form; the adapter does not care. The token's expiry date goes in the provisioning checklist. Exact scope names are verified when the owner creates the token (Phase 3).
- Project permissions: Browse projects, Create issues, Edit issues (for properties), per the v3 OpenAPI spec ([v3 spec](https://developer.atlassian.com/cloud/jira/platform/swagger-v3.v3.json)).

### 2.2 Creating an issue idempotently

Jira has **no idempotency key** on create (the only parameter is `updateHistory`; the spec has no idempotency or request-id text), and JQL search is eventually consistent: "Recent updates might not be immediately visible", with delays from "a few seconds to minutes" ([search and reconcile](https://developer.atlassian.com/cloud/jira/platform/search-and-reconcile/)). So "search by label, then create" can race. The design does not rely on search for correctness:

1. Our own record comes first. The PLANNED action row (with its key) is committed before any call (executor.md section 4). A crash after create but before the SENT commit makes recovery see `attempts_before > 0`. Jira is `idempotent_writes = False`, so the executor returns UNKNOWN `recovery_unconfirmed` and **never creates a second issue** (executor.md section 5). A person resolves it, using the label search below.
2. `POST /rest/api/3/issue` with:
   - project key, issue type (`ticketing.settings.issue_type`, default `Task`), summary;
   - description in **Atlassian Document Format**, which is required for `description` ([v3 spec](https://developer.atlassian.com/cloud/jira/platform/swagger-v3.v3.json));
   - labels `dispute-agent` and `wd1-<first 40 hex of the key>`. Labels cannot contain spaces ([labels](https://support.atlassian.com/jira/kb/how-to-create-and-use-labels-in-jira-cloud/)); the maximum label length is not clearly documented, so 44 characters is kept well short of any plausible limit.
3. `PUT /rest/api/3/issue/{key}/properties/dispute_agent` with `{"idempotency_key": "<full key>", "case_id": "<id>", "action_id": "<id>"}`. Properties are JSON of at most 32,768 characters and can be read back with `GET /issue/{key}?properties=dispute_agent` ([v3 spec](https://developer.atlassian.com/cloud/jira/platform/swagger-v3.v3.json)). They are **not** a search key without an indexing app ([entity properties](https://developer.atlassian.com/cloud/jira/platform/jira-entity-properties/)), so they are used for verification only. If this PUT fails after the create succeeded, the adapter raises `AmbiguousOutcome` with the issue key attached, so the executor records UNKNOWN with the key for the human (the issue exists; only the marker is missing).

`find_issue(key)` (lookup, best effort): `POST /rest/api/3/search/jql` with `jql = project = <KEY> AND labels = "wd1-<40 hex>"`, bounded as the API requires ([v3 spec](https://developer.atlassian.com/cloud/jira/platform/swagger-v3.v3.json)). The old `/rest/api/3/search` is "currently being removed". A hit returns the `IssueRef`. A miss is **not** proof of absence (eventual consistency). The executor treats a miss on a recovery path as unconfirmed, per section 5.1 of `executor.md`.

**Port flags:** `idempotent_writes = False`, `lookup_supported = True` (weak: hits are reliable, misses are not).

### 2.3 Read-back (`get_issue`)

`GET /rest/api/3/issue/{key}?fields=summary,description,labels,status,project&properties=dispute_agent`. Consistency of get-by-key is not stated in the docs (only search is called eventually consistent). A 404 ("not found or no permission") is treated as `ExternalNotFound` within the verification window. Checks (executor.md section 6), against the **stored intent**, not against what the adapter sent:
- project key equal;
- both labels present;
- property `idempotency_key` equal to the action's key;
- summary equal to `intent.payload.summary`;
- description: the plain text extracted from the returned ADF, normalised (`normalise_text`), equals the normalised `intent.payload.description`. Jira may restructure ADF on storage (risk K9), so the text, not the ADF JSON, is compared;
- not resolved as a duplicate (`resolution` not in `ticketing.settings.duplicate_resolutions`).

### 2.4 Operations findings (`fetch_findings`)

Free-text comments are untrusted notes (I5); a credit reason can only come from structured fields (`money-rules.md` section 2). The test project therefore has custom fields that operations fill in:

| Setting (`ticketing.settings`) | Jira field | Type |
|--------------------------------|-----------|------|
| `field_finding_kind` | Finding kind | select: remeasured, inspected, no_fault_found |
| `field_length_cm`, `field_width_cm`, `field_height_cm` | Remeasured L / W / H | number |
| `field_dead_weight_kg` | Remeasured dead weight | number |
| `done_statuses` | e.g. `Done` | statuses meaning the finding is final |

Field ids are discovered once with `GET /rest/api/3/field` (returns `id`, `name`, `schema`: [v3 spec](https://developer.atlassian.com/cloud/jira/platform/swagger-v3.v3.json)) and written into `config/modules.yaml` by the owner. They are not secrets.

Polling every `ticketing.settings.poll_interval_s` (120):
1. JQL `project = <KEY> AND labels = dispute-agent AND updated >= -<2 x interval>m` (an overlapping window, because search lags).
2. For each candidate, `GET` by key. Search results are not trusted for field values; `reconcileIssues` can be passed with the ids of issues we created to force consistency for those ([search and reconcile](https://developer.atlassian.com/cloud/jira/platform/search-and-reconcile/)).
3. A finding is emitted when the status is in `done_statuses` and `finding_kind` is set. `finding_id` = `sha256` of the canonical structured values, so dedupe key `jira:<issue_key>:<finding_id>` fires once per distinct finding. A changed value is a new finding.
4. The latest comment is attached as an `UntrustedText` note.

### 2.5 Errors and limits

| Jira response | Adapter raises |
|---------------|----------------|
| 400 (missing or invalid fields, permission) | `PermanentPortError(400)` |
| 401 | `AuthPortError` |
| 403 | `AuthPortError` |
| 404 (get, property) | `ExternalNotFound` |
| 422 (configuration problem) | `PermanentPortError(422)` |
| 429 | `TransientPortError(retry_after_s, reason=rate_limited)` on reads. On **create** a 429 means the request was refused, so it is retried only with the `Retry-After` header present; Atlassian says retry only when "the API is idempotent and the response includes a Retry-After header" ([rate limiting](https://developer.atlassian.com/cloud/jira/platform/rate-limiting/)) |
| 429 on create **without** `Retry-After` | `PermanentPortError(429)` (do not retry a non-idempotent create blindly) |
| 5xx or timeout on create | `AmbiguousOutcome` |

Limits:
- API-token traffic is governed by burst limits: about 100 requests per second for GET/POST and 50 for PUT/DELETE. The points-based quotas that began on 2 March 2026 apply to Forge, Connect and OAuth apps, not to API tokens.
- Per-issue writes are limited to 20 per 2 seconds and 100 per 30 seconds ([rate limiting](https://developer.atlassian.com/cloud/jira/platform/rate-limiting/)).
- Useful headers: `X-RateLimit-Remaining` and `RateLimit-Reason`.

## 3. Simulated ledger (`modules/ledger/simulated`)

Specified in `money-rules.md` section 7 (operations, idempotency, failure injection). Interface details:

| Operation | Behaviour |
|-----------|-----------|
| Storage | its own SQLite file (`storage.ledger_db_path`); table `ledger_entries (transaction_id PK, shipment_ref, claim_ref, type, amount_paise, reason_code, status, idempotency_key UNIQUE, posted_at)` |
| Seeding | DEBIT entries are written by the synthetic generator (Phase 2) for every shipment's charge line |
| Timeouts | in-process; failure injection simulates timeouts by raising, not by sleeping (tests stay fast) |
| Flags | `idempotent_writes = True`, `lookup_supported = True` |

## 4. Summary: every external call

| Call | Timeout | Retry | Idempotency | Read-back |
|------|---------|-------|-------------|-----------|
| Graph create draft | 10 s | no in-adapter retry; the executor retries transient errors (`max_attempts`) | extended-property key; a stray duplicate draft is never sent | — |
| Graph send | 10 s | **never** on ambiguous; transient (connect error) only | lookup in Sent Items and Drafts first | `get_message` by immutable id, normalised content hash |
| Graph delta poll | 10 s | yes (reads) | inbox dedupe on internetMessageId | — |
| Jira create | 10 s | 429 with Retry-After only; never on ambiguous | label + our PLANNED row; recovery never re-creates | `get_issue` by key + property |
| Jira set property | 10 s | yes (PUT is idempotent) | same property key | in `get_issue` |
| Jira search / get | 10 s | yes (reads) | — | — |
| Ledger post credit | in-process | yes (idempotent by key) | key UNIQUE | `get_transaction` + entries |
| Graph `find_sent` (2 folders) | 10 s each | yes (reads), executor `lookup_attempts` | — | — |
| Graph `get_message` | 10 s | VERIFY_RETRY schedule | — | is the read-back |
| Graph MIME `$value` (NDR check) | 10 s | yes (read) | — | — |
| MSAL token acquisition | 10 s | yes (2x); failure -> `AuthPortError` or `TransientPortError(unavailable)` | — | — |
| Jira `fetch_findings` search and GETs | 10 s | yes (reads) | inbox dedupe on finding id | GET by key is the source of values |
| Jira `find_issue` search | 10 s | executor `lookup_attempts` | — | — |
| Foundry drift GET (agent version) | 10 s | yes (read); cached 60 s | — | — |
| Foundry / Anthropic model call | `llm.call_timeout_s` (120 s) | 2 in-adapter retries (model calls have no side effects) | stateless | schema validation |

## 5. Contract tests (one suite per port, run against the fake and the real adapter)

Real-adapter runs are `@pytest.mark.integration` and opt-in (CLAUDE.md section 8).

- `MessageChannel`:
  - send then `find_sent` returns the same id;
  - `get_message` eventually returns a matching content hash;
  - a draft left unsent makes `find_sent` raise `AmbiguousOutcome`;
  - the delta poll returns a reply exactly once across two polls;
  - a 429 is mapped with `retry_after_s`.
- `TicketingSystem`:
  - create then `get_issue` matches;
  - `find_issue` finds it within 3 minutes;
  - a failed property PUT surfaces `AmbiguousOutcome` with the key;
  - a finding is emitted once and again only when values change.
- `Ledger`: the failure-injection table in `money-rules.md` section 7.

## 6. Risks found in the documentation

| Id | Risk | Mitigation |
|----|------|------------|
| K1 | Graph may normalise a Text body, so a byte-exact hash fails | Normalised hash; Phase 3 integration test against the real mailbox; ADR fallback to token comparison |
| K2 | No documented NDR detection | Heuristic, labelled best effort; residual RR6 |
| K3 | Graph send has no completion signal; Sent Items lags | Read-back window (VERIFYING rests, ADR 0010); a draft-only lookup result is ambiguous and goes to a person |
| K4 | Jira search is eventually consistent; no create idempotency | Our PLANNED row is the source of truth; recovery never re-creates; a human resolves a lost create |
| K5 | Immutable ids are undocumented for personal mailboxes | Extended-property filter is authoritative; the id is a fast path |
| K6 | The refresh token can expire or be revoked (90-day default; inactivity rules undocumented) | `reauth_required` escalates and shows on `/healthz`; `make email-login` |
| K7 | Outlook.com sending limits for non-subscribers are unstated; new accounts are throttled | Tiny pilot volume; send failure escalates |
| K10 | Delegated access gives the app the owner's full mail rights on that account | A dedicated pilot account (checklist step 1); the code only touches its own drafts, Sent Items and Inbox; encrypted token cache |
| K11 | Outlook.com `Authentication-Results` format undocumented | Best effort; a pass never relaxes anything (I15); residual RR7 |
| K8 | Scoped Jira tokens expire (1-365 days) | Expiry date in the checklist; an `AuthPortError` escalates with an ERROR log naming the module |
| K9 | Jira may restructure ADF when storing a description | Read-back compares normalised plain text extracted from ADF, not the ADF JSON; the Phase 3 integration test confirms |
