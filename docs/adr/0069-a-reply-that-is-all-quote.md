# 0069 — A reply that is all quote has no words of the seller's

Date: 2026-11-27
Status: **accepted** (owner, 2026-11-27: "check and fix"). Refines ADR 0055.

## The problem

Live, SHP-102026: the seller replied from Gmail with only a photo. The Dispute Desk's outside-text box showed "Seller email" with our own evidence-request email in it ("On Fri, Nov 27 … wrote: > Hello, > You disputed the charge …").

ADR 0055 cuts the quoted email below a seller's words, but `new_text` kept the **whole body when the quote was all there was**. A photo-only reply is exactly that: Gmail's header and our quoted email, nothing typed. So our email was:

- shown to the reviewer as the seller's message;
- passed to the reply reader as the seller's words.

## Decision

`kernel/domain/mailbox.new_text` returns the seller's own words only:

1. **Nothing quoted** → the whole body, as before.
2. **Words above the quote** → those words, as before.
3. **The reply starts with the quote** → the lines the seller wrote **between** the quoted lines (an inline answer, ">" quoting only), and `""` when there are none.
   - An Outlook header block ("Original Message", or "From: … @ …") quotes the original without marking its lines, so nothing below it is kept.

**Display:**
- The outside-text box (`ui/outside_notes.notes`) applies the same cut when it shows a stored seller email, so a reply stored before this change also reads right.
- A reply with no words shows no box.
- "What came back" (`ui/came_back.seller_reply`) says the seller wrote no message of their own, only what was attached.

## Consequences

- The reply reader gets an empty text plus the attachment names (ADR 0054) for a photo-only reply, instead of our email.
- A seller who types only ">"-prefixed lines is read as writing nothing. **[Assumption]** This is rare, and the full email stays in the inbox record.
- Tests: `tests/unit/domain/test_mailbox.py` (photo only, bare quote, Outlook header, inline answer, reader input) and `tests/unit/ui/test_reply_words.py`. The old test that kept a bare quote whole encoded the bug and was replaced.
