# ADR 0020: Jira idempotency comes from our own action record; labels and a property are markers, not locks

Status: accepted (owner, 2026-11-02, D37)
Date: 2026-11-02

## Decision
Our PLANNED action row (committed before any call) is the source of truth that an issue was attempted. Each issue carries the labels `dispute-agent` and `wd1-<40 hex>` and an issue property `dispute_agent` with the full key. Read-back is by issue key, never by search. Label search is a best-effort lookup: a hit is trusted, a miss is not proof of absence. On recovery after an attempt whose result was lost, the adapter never creates a second issue: the action becomes UNKNOWN and a person resolves it. Operations findings are read from structured custom fields, not comments.

## Why
Jira has no idempotency key on create, and its JQL search is eventually consistent ("a few seconds to minutes"), so "search by label, then create" can race and duplicate. Issue properties are not searchable without an indexing app. Reading by key and keeping our own record avoids depending on search consistency. Structured fields keep credit-relevant numbers out of free text (money-rules.md section 2).

## Options considered
- Search-before-create as the idempotency guarantee (the Phase 3 prompt's sketch): unsafe under eventual consistency.
- A Forge app to index properties: more moving parts than the pilot needs.

## Consequences
Easier: no duplicate issues, whatever the timing. Harder: a lost create after a crash needs a human (rare); the owner creates custom fields once. Watch: the old `/rest/api/3/search` is being removed; use `/rest/api/3/search/jql`.

## Sources
https://developer.atlassian.com/cloud/jira/platform/swagger-v3.v3.json · https://developer.atlassian.com/cloud/jira/platform/search-and-reconcile/ · https://developer.atlassian.com/cloud/jira/platform/jira-entity-properties/ · https://developer.atlassian.com/cloud/jira/platform/rate-limiting/ · https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/ · https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/ · https://support.atlassian.com/jira/kb/how-to-create-and-use-labels-in-jira-cloud/
