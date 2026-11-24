# 0058 — "Ask about this shipment": the Case Explainer on the Review screen

Date: 2026-11-24
Status: accepted. The owner asked for it (D-numbered in HANDOFF when logged).

## Decision

**The layout.** The Review screen lists its shipments the way The agent's steps lists cases. This applies to each tab, "Awaiting your approval" and "With a person":

- On the left: a shipment list (the shared `picker` component) with a search box.
- On the right: only the chosen shipment's cards.
- On "Awaiting your approval", a credit and an email on the same case are one shipment, and both cards show.

**The assistant.** Above those cards sits an **Ask about <shipment>** card, the read-only Case Explainer of ADR 0039:

- **Suggested questions** for the tab, one click each. A suggestion leaves the row once it has been asked.
- **A free-text box**, limited to 500 characters.
- **Answers** are escaped plain text (I5). Under each one, a "Read:" line names the records the AI used.
- **A follow-up** carries the last two exchanges, so "and why?" has something to follow. The explainer itself keeps no memory.

## Pinned to one case

`ExplainerTools.only_case`: the Review screen builds the explainer with the selected case. Any case tool called with another `case_id` is refused before anything is read. Policy lookup stays open, because the rules are shared. This means a case id written in a seller's statement cannot lead the model into another seller's case.

## Spending

ADR 0039's limits still apply: Haiku only, USD 0.05 per question, and a USD 0.25 lifetime cap. Hosted, the lifetime cap is counted in a file that Streamlit Cloud wipes on restart, so each browser session is also limited to **10 questions** (`ask.ASK_LIMIT`, marked as a shortcut). Without the library, the key or the Haiku model, the card shows that the assistant is off and why. It never fails the page.

## Composition

Nothing inside `dispute_agent` imports the composition root, so the screen never builds the explainer itself. Instead, the entry points hand `main(explain=...)` a call:
- `streamlit_app.py` when hosted;
- the new root `console_app.py` for `make run-console`, the same way they hand in `post` and `start`.

A console started any other way shows the assistant as off.

## Hosting

`requirements.txt` lists the explainer's libraries (`claude-agent-sdk`, `llama-index-core`, `llama-index-retrievers-bm25`). pip does not install the `agents` dependency group from `pyproject.toml`. LangGraph is not added: nothing hosted uses it.

Found on the first hosted questions:
- NLTK refuses to read data from a folder another account can write to, and Streamlit Cloud's site-packages is group-writable. So the policy index could not be built there, and every question failed. `app.private_nltk_data` copies LlamaIndex's bundled word lists into a fresh owner-only temp folder (`NLTK_DATA`), once per process: no download, and NLTK's check stays on.
- When the assistant fails, the card names the error type and its first line, so a hosted failure can be read off the screen.

## Answers

Prompt `case_explainer/v2` (v1 kept for replay): one sentence that answers the question, then two to four lines, each starting with a bold label and ending with its source, under 80 words. The card escapes the answer first (I5). Then it turns only `- ` lines into a list and `**label**` into bold.

## Consequences

- The Explainer is now part of the product, not only `make explain`. Its answers are still a convenience, not a record, and nothing it says is acted on.
- Proven on 2026-11-24: the Agent SDK's bundled CLI starts on Streamlit Cloud, and a hosted question was answered once the NLTK fix above was deployed. If it ever fails, the card names the error, and the rest of the Review screen is unaffected.
