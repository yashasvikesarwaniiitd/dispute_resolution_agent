# ADR 0018: Human events are authenticated; the actor comes from the session, never the request

Status: proposed
Date: 2026-11-02

## Decision
Approval decisions and every `HUMAN_*` event are accepted only through review/console routes that require authentication. The API sets the event's actor from the authenticated principal and ignores any actor in the request body. v1 pilot mechanism: the API binds to 127.0.0.1; these routes require a `REVIEW_CONSOLE_TOKEN` secret from `.env`; the approver id must be on `approvals.approver_ids`. Machine sources (dispute intake, simulator) use separate routes that cannot create human events.

## Why
Every money and message control ends in "a human approved it". If the API trusted an `actor` field in the request, anyone who could reach it could approve a credit (threat T09). Binding identity to the session is the standard fix. A local token is the smallest mechanism that works for a single-user pilot with a separate Streamlit process.

## Options considered
- No authentication, rely on localhost only: any local process could approve.
- Entra ID sign-in for the console: right for a real deployment, heavy for a local pilot; revisit before any shared use.

## Consequences
Easier: approvals are attributable and unforgeable through the pipeline. Harder: one more secret to manage. Watch: CLAUDE.md I14 says the Anthropic key is "the one permitted API key"; this token is a local session secret, not a provider API key, but it needs the owner's confirmation (open decision O11).

## Sources
Project documents (CLAUDE.md I12-I14; docs/spec/approvals.md section 6; docs/spec/threat-model.md T09).
