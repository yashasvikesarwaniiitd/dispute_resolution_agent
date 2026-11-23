# 0054 — The agent reads the seller's reply, and acts on it

Date: 2026-11-23
Status: **accepted** (owner, 2026-11-23: "agent should read the reply, build it"; chose "Read, then act" and "Claude Haiku, test it"). Builds on ADR 0053 (the reply reaches the case) and ADR 0052 (the hub's reply).

## The problem

After ADR 0053 a seller's emailed reply reached its case, but the running app had no reader: it runs the rules engine with no model, so every reply was recorded as unread and went to a person. The owner asked for the agent itself to read the reply and act on it.

The project already had a reader: the `seller_reply_interpreter` prompt, a strict output schema (`ReplyReading`) and `LlmReplyInterpreter`. But the prompt was `inactive` because its evaluation suite had never been written, and the running app could not build any model route.

## Decision

1. **A reply reader on the running app.** `REPLY_READER=anthropic_direct` builds `LlmReplyInterpreter` on Claude Haiku, behind the same spending guard as every model call (I14, ADR 0017), against the Haiku route's own cap. Unset, nothing is spent and replies go to a person as before. The hosted secrets block carries it when `.env` holds the Anthropic key.
2. **The prompt earned `active`.** `tests/evals/seller_reply_interpreter` implements RI-E1..E9 from `docs/spec/prompt-review.md` plus three plain replies, opt-in (`-m live_model`). On Haiku it found one real weakness: the summary sometimes copied a phrase of an injected instruction (RI-E7). Step 6 of the prompt now forbids that (a pre-release edit, logged in `meta.yaml`). Then 12 of 12 passed, and RI-E7 passed 3 runs of 3. A base64-encoded instruction makes Haiku refuse; a refusal is a failed reading, so it fails safe to a person.
3. **The agent acts on what it read: row B4a** in the pack's baseline (`docs/spec/agent-design.md` section 5), above B5. When the reply reads as `provides_requested_evidence` with every requested item provided and no manipulation flag, the size is in dispute (measured beyond tolerance of declared), no money error is proven, and the hub has not been asked or answered yet, the agent asks the hub to remeasure. Anything less falls through to B5 and a person. No money moves on a reply: a seller's figures are never used, and a credit still needs the hub's new measurement, code's proof and a person's approval.
4. **Attachments reach the reader.** The Gmail adapter lists a reply's files by name and type (nothing downloaded), and the reply round (kernel, `replies.py`) passes the names to the reader. Without them "photo attached" could not be told from a photo that was not.
5. **A reply written before its case is not an answer to it** (kernel, dispatch rule P5). Found live: after a restart the mailbox is read again from two days back, and yesterday's reply about the same shipment was routed to today's new case, superseding the evidence request it had not yet sent. It is recorded as ignored, and the timeline says "An email was set aside".
6. **A credit confirmation is exempt from the daily contact limit** (kernel, R05), as a clarification already is. Found live: with the hub's answer read the same day as an evidence request, the credit posted and the confirmation was refused, leaving a paid case with a person. Telling a seller their money was credited is owed, and the case cannot close without it (ADR 0012). R04's per-case total still bounds it.
7. **The card and the timeline show the reading.** "The agent read the reply: the seller sent the evidence we asked for. Photo of the packed box beside a tape measure - sent." beside the seller's own words.

## Why

Reading is what a model is for here; deciding money is not. The reader's output is structured and checked, and it can only add caution (R20, R21) or, through one narrow row, start a step that moves no money. Every other outcome is a person's.

## Options considered

- **Turn on the full AI investigator (`reasoning: llm`).** It would decide more on its own, but the running app cannot build that route today, and it is a larger, costlier change than the owner asked for.
- **Read and explain only.** Smaller, but on photo cases B5 would still send every answered request to a person; the owner chose to act.
- **Foundry gpt-5-mini.** The live site has no Azure sign-in (the same wall ADR 0049 hit).

## Consequences

- Live end to end, 2026-11-23, real Gmail, real Haiku, real Jira: a person asked for a photo; the seller replied from Gmail with the photo attached; Haiku read it as the evidence asked for; the agent asked the hub (WD-71); the hub answered "Remeasured: 20 x 15 x 10 cm, 0.48 kg"; code proved ₹76.00; the credit and the confirmation were approved; the case resolved credited.
- Each reply read is one Haiku call, about US$0.0004.
- Kernel changes: `orchestrator/replies.py` (attachments), `orchestrator/dispatch.py` (P5), `policy/rules/r05_contact_limit_daily.py` (confirmation exempt). Spec: `agent-design.md` (B4a), `policy-rules.md` (R05).
- Known gap, unchanged: B2's stop-intent check looks for readings with AI provenance, but the reply round stores a reading on the seller's reply item. Withdrawals and instruction attempts still reach a person through B5, R20 and R21, so it fails safe; B4a reads the real item.
- Tests: `tests/unit/packs/test_baseline.py` (B4a), `tests/unit/api/test_agent_reads_reply.py` (the whole loop on fakes), `tests/unit/api/test_seller_reply.py` (P5), `tests/unit/policy/test_contact_limits.py` (R05), `tests/unit/modules/email/test_gmail_api.py` (attachments), `tests/unit/ui/test_hosted.py` (secrets), `tests/evals/seller_reply_interpreter` (live).

## Sources

- Anthropic Messages API: https://docs.anthropic.com/en/api/messages
- Gmail API, message parts and attachments: https://developers.google.com/gmail/api/reference/rest/v1/users.messages#MessagePart
