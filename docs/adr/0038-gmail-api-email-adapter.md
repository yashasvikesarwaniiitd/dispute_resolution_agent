# 0038 — A Gmail API email adapter, and the first kernel change after Phase 7

Date: 2026-11-04
Status: accepted. The owner chose the Gmail API, and approved the kernel and spec line (D59).
Kernel change: one line in `kernel/registry/module_spec.py`, after the tag `phase-7-complete`.

## Decision

Add `gmail_api` as a third email adapter, beside `fake` and `outlook_graph`. It talks to one personal Gmail mailbox through the Gmail REST API, using a desktop OAuth client that the owner creates in their own Google Cloud project.

Outlook stays supported and is still the default real adapter in the spec (ADR 0023). It was set aside here for one reason: a new personal Microsoft account has no directory of its own (`AADSTS16000`), and getting one means an Azure sign-up whose free credit the owner is saving.

## The kernel line, disclosed

The registry catalogue in the kernel lists every adapter that startup validation accepts, so a new adapter cannot be added without editing it. This is the first kernel change since Phase 7 closed.

- **Phase 7 proof.** The repository was tagged `phase-7-complete` at the last commit with an untouched kernel. `tests/architecture/test_kernel_untouched.py` now checks that the kernel is unchanged between `phase-6-complete` and `phase-7-complete`. Both tags are fixed, so this proof is permanent.
- **Later kernel changes.** Every commit after `phase-7-complete` that touches `kernel/` must name an ADR in its message.
- **Spec.** `docs/spec/config.md` (frozen) gains the same adapter in its module table, plus the four `GMAIL_*` settings. The owner approved this change under this ADR.

## How it works

Sources, read on 2026-11-04:

- [users.messages.send](https://developers.google.com/gmail/api/reference/rest/v1/users.messages/send), [users.messages.list](https://developers.google.com/gmail/api/reference/rest/v1/users.messages/list), [users.messages.get](https://developers.google.com/gmail/api/reference/rest/v1/users.messages/get), [sending guide](https://developers.google.com/gmail/api/guides/sending), [search filtering](https://developers.google.com/gmail/api/guides/filtering)
- [scopes](https://developers.google.com/gmail/api/auth/scopes), [OAuth for desktop apps](https://developers.google.com/identity/protocols/oauth2/native-app), [OAuth overview: refresh token expiry](https://developers.google.com/identity/protocols/oauth2)
- [error handling](https://developers.google.com/workspace/gmail/api/guides/handle-errors), [quota](https://developers.google.com/workspace/gmail/api/reference/quota)

**Sign-in.**
- The adapter uses Google's loopback flow with PKCE (S256). The browser opens the consent page, Google redirects to a one-shot listener on `127.0.0.1:<random port>`, and the code is exchanged for tokens. The copy-paste flow is retired, and the device flow does not allow Gmail scopes.
- **Scopes:** `gmail.send` and `gmail.readonly`, and nothing that can change or delete mail.
- **Token storage:** the refresh token is encrypted at rest, with the same `msal-extensions` persistence the Outlook adapter uses. It never falls back to plain text.
- **Known limit:** a Google app in "Testing" status gets refresh tokens that expire after **7 days**. When that happens the adapter raises `reauth_required`, cases reach a person, and the owner runs `make gmail-login`.

**Sending.**
- A single `messages.send` call with the RFC 2822 message, base64url-encoded, as plain text.
- There is no draft step, so no message can be stranded in Drafts.
- `idempotent_writes` is false. The executor's look-before-write is what prevents repeats.

**Two markers.**
- Google does not promise that a custom header survives on the stored copy, or that a client-supplied Message-ID is kept. Every message therefore carries both:
  - the header `X-Dispute-Idempotency-Key`;
  - the Message-ID `<wd1-<hex>@dispute-agent.local>`.
- `find_sent` searches `in:sent rfc822msgid:` first. If that misses, it scans the last week of Sent for the header.
- Read-back takes the key from whichever marker survived. The first live run records which ones Gmail keeps.

**Read-back.** Only a message with the `SENT` label counts. The body compared is the text/plain part. "Verified" means in Sent with the approved text, never delivered (ADR 0010).

**Errors.**
- An unsent connection is transient.
- A write whose answer was lost is ambiguous, and so is a 5xx on a write.
- 401 is an auth error.
- Rate limits are transient. Gmail reports them as 429, or as 403 with reason `rateLimitExceeded` or `userRateLimitExceeded`.
- Any other 403 is an auth error.
- Errors carry Google's reason only, never the request or the token.

**Replies and bounces.**
- Replies are read with `in:inbox after:<epoch>`, which is made inclusive by one second, and every text arrives as `UntrustedText` (I5).
- A bounce from `mailer-daemon` or `postmaster` is routed by our own Message-ID found in the returned text, never by what the bounce claims about itself.

## Options considered

1. **Gmail API (chosen).** No password is stored, only an encrypted, narrowly scoped refresh token. It has documented send, search and read endpoints. It needs a free Google Cloud project and no card.
2. **Gmail with an app password over SMTP and IMAP.** The quickest setup, but the app password is a full mailbox password sitting in `.env`, and reading the Sent folder back is clumsier. Rejected.
3. **Wait for Outlook.** Kept as a later option. It costs the owner's Azure free credit now.

## Consequences

- With the owner's settings in place, `config/modules.local.yaml` (git-ignored) can switch the running app to `email: gmail_api`. The shared config and CI keep the fake.
- The adapter is tested offline against a stand-in Gmail server (`tests/unit/modules/email/test_gmail_api.py`), covering:
  - both markers and their fallbacks;
  - every error mapping;
  - bounces;
  - PKCE sign-in and 7-day expiry.
- It also joins the live `MessageChannel` contract suite.
- Every 7 days in Testing status, the owner signs in again. Publishing the app for production would need Google's verification and a security assessment, because `gmail.readonly` is a restricted scope. That is out of scope for a pilot.
