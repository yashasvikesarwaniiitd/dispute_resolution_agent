# Glossary

Domain words as the code and the specs use them. `docs/spec/transitions.yaml` is the authority for states and transitions; this file explains the words.

**Soft-terminal**: a closed case the agent may reopen by itself, without a person: `RESOLVED_UPHELD` and `CLOSED_NO_RESPONSE`. A seller's reply or a new dispute inside the reopen window reopens it for a fresh round. Every other closed state (credited, withdrawn, not eligible, handed to a person) needs a person to reopen it, because a credited dispute reopening itself is how a shipment gets paid twice. In code: `SOFT_TERMINAL_STATES` in `kernel/domain/enums.py`.

**Reopen window**: how long after a soft-terminal close the agent may still reopen the case by itself: `policy.timers.reopen_window_days`, 7 days. In code: `within_reopen_window` in `kernel/orchestrator/intake.py`.

**Reopen cap**: how many times the agent may reopen one case by itself: `policy.cases.max_agent_reopens`, 2. A reply or dispute inside the window but over the cap, or outside the window, goes to a person (T43/T44). Each reopen gives the case a fresh allowance of AI calls (ADR 0067).
