# Stage A3: Integrations and Foundry specification (Opus)

## Role
Principal architect specifying every external touchpoint from current official documentation.

## Context
Stages A1 and A2 are approved. Read `docs/ARCHITECTURE.md` section 12 and `docs/FOUNDRY_SETUP.md`. Use web search and fetch on official documentation only. Do not rely on memory for any API detail.

## Objective
Specifications for Outlook, Jira, the simulated ledger and Foundry that make each adapter a mechanical build.

## Scope
### In scope
Contracts for `MessageChannel`, `TicketingSystem` and `Ledger`; the Foundry specification; configuration schemas; observability.
### Out of scope
Implementation.

## Tasks
1. `docs/spec/integrations.md`: Microsoft Graph mail (least-privilege scopes, send, polling for replies, conversation id matching, idempotent send), Jira Cloud (label-based idempotency, fields, read-back), and the simulated ledger API (endpoints, idempotency, transaction types, failure injection).
2. `docs/spec/foundry.md`: authentication; project endpoint and region (South India, `gpt-5-mini`, Global Standard); Agent Service prompt agents (versions, tool-less definition, stateless call through the Responses API with an agent reference, whether response storage can be disabled); the agent sync and drift-check design and lock file; the fallback direct gateway; output validation and retry; error mapping (429, 5xx, content filter); guardrail behaviour; tracing design (span and attribute list, no content); evaluation and red-team approach for hosted agents; cost model, `model_calls` and the spend cap; the secondary Haiku-only gateway (direct Anthropic API: authentication, model allowlist, error mapping, failover contract), and the owner's provisioning checklist. Verify model, region and Agent Service support against current documentation, including retirement dates. Link every document you read.
3. `docs/spec/config.md`: complete schemas and defaults for `modules.yaml`, `policy.yaml`, `pricing.yaml`, `release_gates.yaml` and every environment variable.
4. `docs/spec/observability.md`: log fields, redaction rules, trace attributes and the metrics behind each success KPI.
5. A risk list: rate limits, quotas, region and model availability, preview features, with a mitigation each.

## Technical requirements
Every external call has a timeout, a retry rule, an idempotency rule and a read-back rule. Preview features are isolated behind adapters.

## Acceptance criteria
- Each adapter can be built and contract-tested from the spec alone.
- The owner's provisioning checklist is complete and ordered.
- Every claim about an external API cites a document you actually read.

## Deliverables
Files in `docs/spec/`, ADRs, an updated `docs/FOUNDRY_SETUP.md`.

## Constraints
I11, I12, I14, I15. Claude on Foundry is deferred; do not design for it beyond keeping the `ModelGateway` port open.

## Process
Outline first. List the documents you will read. Report any place where documentation is unclear or contradicts the design.

## Model and effort
Opus, effort medium.

## Stop and ask if
Documentation shows a capability the design depends on is unavailable or preview-only.
