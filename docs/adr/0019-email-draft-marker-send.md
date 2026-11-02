# ADR 0019: Email is sent as a marked draft, looked up by an extended property, read back by immutable id

Status: proposed
Date: 2026-11-02

## Decision
The Outlook adapter creates a draft with `Prefer: IdType="ImmutableId"` and a single-value extended property holding the action's idempotency key, then sends the draft. Lookup filters Sent Items and Drafts on that property; read-back gets the message by its immutable id with a text body and compares a normalised content hash. A draft found unsent is treated as ambiguous and never re-sent automatically. App-only access is scoped to the one test mailbox with Exchange RBAC for Applications.

## Why
`sendMail` returns 202 with no body and no completion guarantee, so it cannot be verified or made idempotent. The draft flow gives an id that, as an immutable id, is also the Sent Items copy, and the extended property is the only marker Graph documents as filterable server-side. Normalising the body before hashing is needed because Graph documents no exact round-trip for message bodies.

## Options considered
- `sendMail` plus searching Sent Items by subject: not unique; no id to verify.
- Custom `x-` internet headers as the lookup key: settable at creation, but no documented server-side filter.
- Delegated auth on a personal Outlook.com account: app-only is not available there (open decision O13).

## Consequences
Easier: every email can be found and verified. Harder: the owner must set up RBAC for Applications and remove tenant-wide grants; Microsoft prefers open extensions over extended properties for custom data, so this is a documented-but-discouraged use. Watch: the Phase 3 integration test confirms the property survives the move to Sent Items and that the normalised body hash matches.

## Sources
https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0 · https://learn.microsoft.com/en-us/graph/api/user-post-messages?view=graph-rest-1.0 · https://learn.microsoft.com/en-us/graph/api/message-send?view=graph-rest-1.0 · https://learn.microsoft.com/en-us/graph/outlook-immutable-id · https://learn.microsoft.com/en-us/graph/api/singlevaluelegacyextendedproperty-get?view=graph-rest-1.0 · https://learn.microsoft.com/en-us/graph/outlook-create-send-messages · https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac · https://learn.microsoft.com/en-us/graph/permissions-overview
