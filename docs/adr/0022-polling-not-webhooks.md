# ADR 0022: Poll for email replies and Jira findings; no webhooks in v1

Status: proposed
Date: 2026-11-02

## Decision
Email replies are polled with a Graph delta query on the Inbox every 60 seconds; Jira findings are polled with an overlapping JQL window every 120 seconds and re-read by key. Both are deduplicated by the inbox (dedupe keys `outlook:<Graph immutable id>`, `jira:<issue_key>:<finding_id>`; not the sender-controlled internetMessageId), so overlaps and replays are harmless. On a lost Graph delta token, the adapter resyncs by received date.

## Why
Webhooks (Graph change notifications, Jira webhooks) need a publicly reachable HTTPS endpoint, which the local pilot does not have and should not expose (ADR 0018 binds the API to localhost). Polling at these intervals is far inside documented limits (Graph: 10,000 requests per 10 minutes per mailbox; Jira token traffic: burst limits only). D11 already chose polling for email.

## Options considered
- Graph change notifications with a tunnel: public exposure, subscription renewal, more failure modes. Microsoft recommends pairing delta with notifications at scale; this pilot is not at scale (BACKLOG has the item).

## Consequences
Easier: no inbound network surface. Harder: replies arrive up to a minute late (irrelevant against 48 h timers). Watch: Graph delta tokens can be evicted; the resync path is tested.

## Sources
https://learn.microsoft.com/en-us/graph/api/message-delta?view=graph-rest-1.0 · https://learn.microsoft.com/en-us/graph/delta-query-overview · https://learn.microsoft.com/en-us/graph/throttling-limits · https://developer.atlassian.com/cloud/jira/platform/search-and-reconcile/ · https://developer.atlassian.com/cloud/jira/platform/rate-limiting/
