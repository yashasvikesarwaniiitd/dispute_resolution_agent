# ADR 0013: The kernel approvals table is the source of truth; ApprovalService only notifies

Status: proposed
Date: 2026-11-02

## Decision
The kernel writes the approval record (PENDING) in the same transaction that moves the case to AWAITING_APPROVAL. The `ApprovalService` port only notifies the review queue (`request`, `withdraw`), idempotently. The decision returns as an `APPROVAL_DECIDED` inbound event carrying the approver and the payload hash they saw.

## Why
One record in one place: the executor checks approvals against the kernel table (I3, I13), so that table must be authoritative. If the approvals module owned the record, the kernel would read across a module boundary at execution time, and a module outage could make an approval appear or vanish. Routing the decision as an event puts every approval into the audit trail with the same dedupe and correlation rules as any input.

## Options considered
- The sketch's `request -> ApprovalId` and `get -> ApprovalStatus`: two sources of truth.

## Consequences
Easier: approvals survive module restarts; the Streamlit screen reads pending rows and posts decisions to `/events`. Harder: the approvals module needs read access to pending approvals through a kernel port injected by `app.py`. Watch: A2 approvals.md adds expiry and audit fields by migration.

## Sources
Project documents only (docs/ARCHITECTURE.md section 4, docs/FLOW.md section 5a). No external documentation was needed.
