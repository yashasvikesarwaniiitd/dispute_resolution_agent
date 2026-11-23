# 0055 — A reply is read without the quoted email below it

Date: 2026-11-23
Status: **accepted** (owner, 2026-11-23: "Yes, fix it"). Builds on ADR 0054 (the agent reads the reply) and ADR 0053 (the reply reaches the case).

## The problem

The owner replied to an evidence request from Gmail. The case went to a person with "The input screen flagged it: the seller's text read like instructions to the agent."

Gmail's plain-text reply puts a copy of our email below the seller's words, under a header line such as `On Sun, 23 Nov 2026 at 11:10, Dispute desk <desk@gmail.com> wrote:`. The reply step screened and read the whole body. The rules-based screen read `<desk@gmail.com>` as markup, and markup aimed at the system counts as an instruction attempt, so R21 escalated. The same body without the quote raises no flag. Every real Gmail reply would have done this. Live QA missed it because its test replies carried no quote.

## Decision

1. **Only the seller's own words are evidence** (kernel: `domain/mailbox.py` `reply_only`, used by `orchestrator/replies.py`). Before a reply becomes an evidence item, the quoted email is cut off. The screen, the reader and the person all see the same text.
2. **What counts as a quote** (`new_text`):
   - Gmail's "On … wrote:" header, including when Gmail wraps it over two or three lines.
   - Outlook's "Original Message" rule.
   - A `From:` line that names an address.
   - A line starting with `>`.

   A bare `From: our Pune warehouse` and an "On Monday …" sentence are the seller's words and are kept. A body that is only a quote is kept whole.
3. **The full email stays recorded.** The inbox row keeps the event exactly as it arrived; only the evidence item is cut.
4. **Truncation is re-derived.** If the 20,000-character cap fell inside the quote, the seller's words arrived whole and the item is not marked truncated.
5. **The card uses the same rule.** `ui/came_back.py` had its own quote pattern; it now uses `new_text`, so the timeline, the card and the reader cannot disagree.

## Why

The quoted part is our own email. It is never the seller's evidence, and screening it only measures our own wording. Cutting it removes a false flag that fired on every real Gmail reply. The screen stays as broad as it was for anything the seller writes.

## Options considered

- **Teach the screen that `<name@domain>` is not markup.** It fixes this one line but not the next: our email's own wording can trip other patterns, for example "reply … name@domain" reads as a recipient change. Rejected as the fix, though the screen still sees every word the seller wrote.
- **Cut the quote in the mail adapter, before the inbox.** It would lose the full email from the record. Rejected.

## Consequences

- An attacker could hide text below a fake "On … wrote:" line. That text is never shown to the reader model or the person, so it cannot instruct anything. The seller's words above it are screened as before.
- A seller who writes a line starting with `>` loses what follows it from the reading. The full email is still in the inbox record for a person to read.
- Replies recorded before this change keep their quoted evidence item; the card already strips the quote for display.

## Sources

None beyond the repository: the Gmail reply shape was taken from the owner's live reply on the hosted app.
