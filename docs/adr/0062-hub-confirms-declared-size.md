# 0062 — The hub can confirm the seller's declared size; evidence named in words

Date: 2026-11-25
Status: accepted. The owner approved the smaller plan: a readable "the seller is right" reply and clearer Jira wording. Re-asking the hub on Jira after an unreadable reply is deferred (see "Not done").

## Context

On the live site the hub (the owner, testing) answered a scanner-audit issue for SHP-102025 with "The seller measurement is correct." Code reads only two first lines, `Remeasured: …` and `Measurement confirmed`, so this was stored as an unreadable note. Haiku then proposed **wait**, reading the note as no answer, and the case sat on "waiting for the hub's finding".

- **Waiting after an unreadable hub note is allowed on purpose (S31).** "Still checking" is unreadable to code too, and waiting is right there. Code cannot tell the two apart; only a reader can. The rules baseline would have sent the case to a person (B5). The model's wait is therefore a judgement error, a case for the evaluation set, not a kernel bug.
- **"The seller's size is right" is the most natural answer a hub gives** when its scan was wrong. It needs no figures: the declared ones are already on record.

## Decision

**1. A third hub reply: the seller's declared size is right** (`modules/ticketing/hub_comment.py`).
- **Read from the first line:** "Declared size confirmed", and the plain ways of saying it ("The seller measurement is correct", "Seller's measurement was right", "The declared dimensions are accurate", "Seller declaration stands").
- **Never read:** a line with a negation, a statement that is not a confirmation ("The seller measurement is too small"), or the phrase anywhere but the start. "The measurement is correct" without "seller" or "declared" still means the hub's own scan (`no_fault_found`).
- **The comment carries no figures.** It is parsed as a `remeasured` finding flagged `declared_confirmed`. The evidence source (`synthetic_shipments/library.py`) fills in the sizes and weight from the shipment's own declared record; with no record it carries none, and nothing is proved.
- **Why `remeasured` and not a new kind:** the pack, the rules (B3, B6a) and prompt v3 already treat a `remeasured` finding as the hub's answer, with figures code recalculates. A new kind would need a new prompt version and a paid re-evaluation. The flag keeps the record honest, and the steps screen says "Confirmed the seller's declared size: …", not "Re-measured".
- **Money is unchanged:** code proves `remeasurement_lower` from the declared figures, works out the amount, and a person approves the credit. A note after the phrase stays untrusted text; an instruction in it is flagged by the input screen and sends the case to a person (R21), as before.
- **The known limitation of ADR 0061 applies equally:** anyone who can comment on the issue can write this line, as they can write `Remeasured: …`. The reviewer's approval of the credit is the stop.

**2. The Jira issue lists the third reply** (`kernel/orchestrator/investigate.py`, the issue text only): "Declared size confirmed (if the size the seller declared is right)".

**3. The AI's reasoning names evidence in words** (`ui/trail.py`, `ui/review_view.py`). The model cites evidence by id (`evi_01M87…`). The steps screen and the Review card now replace each id with what it is ("hub measurement", "declared size and weight", "comparison with past shipments"); an id the case does not hold reads "an evidence id not in this case", never a name. The exact recorded text stays one click away under Details, and the caption says the text was reworded. Text with no ids is shown, as before, exactly as recorded.

## Not done

**Re-asking the hub after an unreadable reply.** A follow-up comment must be a kernel action (policy, kill switch, audit, read-back) and must not restart the 120-hour hub timer, which today any new plan does. That is a state-machine change, and S31's designed wait would need rework. Deferred until unreadable replies still occur with the third reply available. Posting the comment from the reply poller would be smaller, but it would bypass the kill switch and policy, so it is not an option.

## Tests

- `tests/unit/modules/ticketing/test_hub_comment.py`: six ways of confirming the declared size; six near misses that must not be read so; "The measurement is correct" still means the hub's own.
- `tests/unit/api/test_hub_answer.py`: on the running app with the fake Jira, "The seller measurement is correct." moves the case out of waiting, code proves `remeasurement_lower` from the declared 20 x 15 x 10 cm, 0.48 kg, and the steps say so.
- `tests/unit/ui/test_evidence_in_words.py`: ids become names, an unknown id is said so, rules text is untouched, the exact text is kept.
