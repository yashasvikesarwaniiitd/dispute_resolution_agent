# 0037 — The confirmation rule: only independent evidence moves a pattern

Date: 2026-11-04
Status: accepted (Phase 7, P7-08, P7-09). Implements FLOW section 6 and I7.

## Decision

A pattern changes status **only** on a structured operations finding recorded on its own Jira investigation. Nothing else can move it:

- not the number of disputes;
- not how the cases ended;
- not a model's hypothesis;
- not a note's text.

| Finding (structured fields only) | From | To |
|---|---|---|
| `no_fault_found` | UNCONFIRMED | DISMISSED |
| `inspected` with `cause_confirmed: true` | UNCONFIRMED, DISMISSED | CONFIRMED |
| `inspected` with `fix_deployed: true` | UNCONFIRMED, CONFIRMED | FIXED_MONITORING (`fixed_at` = the finding's time) |
| recurrence: at least `min_related_cases` disputes on the cause in the window after the fix | FIXED_MONITORING | CONFIRMED (`recurrence_after_fix`) |

Any other finding is recorded and changes nothing. One inspection that found no fault does not undo a confirmed cause; a person decides that.

## Why outcomes never count

A pattern makes the agent more cautious. Caution produces escalations and human reviews, and those outcomes would then look like evidence for the pattern. That loop would confirm any pattern that was ever shown, true or not.

So the rule takes a finding and nothing else. The "independent support" figure, which is reported but never acted on, leaves out every case that *could* have been shown the pattern.

The kernel's `CaseOutcome.used_pattern_ids` is never filled (Phase 7 changes no kernel line). The observer therefore links, as `informed`, every closed case that matches a pattern whose cluster had begun before the case closed. This over-approximates: it can only remove support, never add it.

## Why only structured fields

I5: an ops note is outside text. A note saying "confirmed, this proves fraud" is data, so it can move nothing. `verdict_of` reads `kind` and the boolean fields of `measured`, and nothing else.

## Options considered

1. **Confirm on dispute volume.** Rejected: volume is what the pattern was built from, so it is circular by construction.
2. **Confirm on outcomes, excluding pattern-assisted ones.** Rejected for now. Which cases were "assisted" can only be over-approximated without a kernel change, and an outcome is still a decision about one case, not a finding about a cause.
3. **Findings only (chosen).** This is what an operations team would accept as proof, and it cannot be produced by the system's own caution.

## Consequences

- S16 shows the legitimate-pattern path: re-running the analysis three times never confirms the pattern, one `no_fault_found` dismisses it, and a dismissed pattern briefs no one.
- `tests/unit/modules/cross_case/test_confirmation.py` shows that five cases escalated because of a pattern do not confirm it.
- The recurrence report (eval report, "Recurrence after confirmed fixes") gives before-and-after counts on a seeded synthetic world.

## Two exceptions, stated plainly (safety review LOW-3)

- **Recurrence.** Recurrence after a fix moves FIXED_MONITORING back to CONFIRMED on dispute counts, not on a finding. Both statuses look the same to the kernel (a confirmed cause), so this can only keep caution in place; it never confirms an unconfirmed pattern and never dismisses one.
- **Dismissal is permanent.** A DISMISSED pattern stays dismissed: the table allows one pattern per key. A later, genuinely new problem on the same scanner or SKU cannot raise a fresh pattern on that key. This is recorded in BACKLOG; reopening a dismissed key needs an ops finding or a person.
