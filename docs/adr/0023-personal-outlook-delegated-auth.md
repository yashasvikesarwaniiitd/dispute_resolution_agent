# ADR 0023: Email uses a personal Outlook.com account with delegated sign-in and an encrypted token cache

Status: accepted (owner decision D36, 2026-11-02)
Date: 2026-11-02

## Decision
The email adapter signs in as a dedicated personal Outlook.com pilot account with MSAL Python as a public client against the `consumers` authority, using the device code flow once (`make email-login`). It requests delegated `Mail.ReadWrite`, `Mail.Send`, `Mail.Read` and `offline_access`. The token cache is persisted encrypted with msal-extensions (DPAPI on Windows) and never in plain text. When silent refresh fails, sends escalate with `reauth_required` until the owner signs in again.

## Why
The owner's choice: a personal account avoids setting up a Microsoft 365 tenant and Exchange RBAC. App-only access is not available for personal accounts, so delegated sign-in is the only documented option. The mail permissions the design needs are documented as available for personal accounts, and the extended-property filter the design depends on is documented for them too.

## Options considered
- Microsoft 365 test tenant with app-only access scoped by RBAC for Applications (recommended in A3): narrower access and no re-login, but more setup.

## Consequences
Easier: setup is an app registration and one sign-in. Harder: the app holds the owner's full mail rights on that account (risk RR10), so a dedicated pilot account is strongly recommended; refresh tokens last about 90 days and can be revoked, so the owner must sign in again occasionally (risk K6); immutable ids are undocumented for personal mailboxes, so the extended-property lookup is authoritative (ADR 0019). Moving to a work tenant later is a configuration and adapter-credential change, not a kernel change.

## Sources
https://learn.microsoft.com/en-us/graph/permissions-overview · https://learn.microsoft.com/en-us/graph/permissions-reference · https://learn.microsoft.com/en-us/entra/identity-platform/msal-client-application-configuration · https://learn.microsoft.com/en-us/entra/identity-platform/supported-accounts-validation · https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens · https://learn.microsoft.com/en-us/entra/msal/python/advanced/msal-python-token-cache-serialization · https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens · https://github.com/AzureAD/microsoft-authentication-extensions-for-python · https://learn.microsoft.com/en-us/graph/api/singlevaluelegacyextendedproperty-get?view=graph-rest-1.0 · https://support.microsoft.com/en-us/outlook/sending-limits-in-outlook-com
