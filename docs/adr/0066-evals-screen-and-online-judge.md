# 0066 — The Evals screen: Decision Quality offline and online

Date: 2026-11-25
Status: accepted. Built to the owner's four instructions:
1. A separate screen at the far right of the top bar, away from the four working screens.
2. Only the Decision Quality metric.
3. Offline and online evaluation.
4. A switch on that screen that turns online evaluation on or off.

## Context

Decision Quality (ADR 0064) is measured offline: 18 decision points the owner approved, graded by code, plus a judge calibrated against the owner's labels (25 of 28 agree, 7 of 7 critical flaws caught). The runs were stored as JSON and had no screen.

Online means the cases people feed in now: disputes raised on the seller page, and shipments added later with the planned "add a shipment" option. They have no answer key. The owner's point: that is what the calibrated judge is for. Haiku decides, the judge reads the same evidence and Haiku's explanation, and the owner looks at where the two disagree.

The judge never saw the answer key, even offline: its prompt takes the evidence, the action and the explanation only. So the same judge and prompt run online unchanged, and the calibration still describes it.

## Decision

**1. The screen** (`ui/screens/evals.py`, the last page in `ui/app.py`; `console.css` pushes it to the far right).
- **Offline** reads the committed run records.
  - Three cards: the AI's latest run, the rules baseline, and the judge's agreement with the owner.
  - The points where the AI was not acceptable, with the situation and what was acceptable there.
  - Every run.
  - Nothing is run from the screen.
- **Online** has the switch, then four counts (judged, holds up, flagged, waiting), then one row per live AI decision: the action, the judge's verdict, why, and the AI's explanation.
- The architecture test that keeps the answer key off the agent's path now allows this screen. It shows stored results and is not on that path.

**2. Recording** (`api/online_judge.py`, `JudgedReasoner`, composed in `app.py`).
- **What wraps:** the live AI reasoner. The rules reasoner is not wrapped.
- **What it records:** while the switch is on, each proposal with `reasoner == "llm"`, together with the evidence lines from that briefing (`judge.evidence_lines`).
- **Which evidence:** full values up to 2,500 characters, as the agent can open them, not the briefing's trimmed view. Seller and outside-party words are neutralised and labelled as such.
- **Off means nothing:** while the switch is off, nothing is recorded and no file is made.
- **Best effort:** a recording failure is logged, and the proposal goes on unchanged.

**3. Judging** (`OnlineJudgeJob`, a runner job).
- **Pace:** while the switch is on, it judges one recorded decision per tick. It runs outside any case transaction, so the case store's lock is not held during the call.
- **Its own file:** results go to `data/online_evals.sqlite3`, apart from the case store.
- **Failures:** a failed judgement is stored as "could not judge".
- **Pass or fail:** computed by code, as offline.
- **The judge's model:** the hosted model route (Haiku), built on first use with its own call log, `data/online_judge_calls.sqlite3`.
- **Spending:** capped at USD 0.50. A judgement costs about USD 0.001. At the cap the job switches online evaluation off and leaves the decision waiting, rather than failing every remaining one.

**4. The switch** lives in the same file.
- **Default:** off.
- **Restarts:** Streamlit Cloud wipes `data/` on restart, which turns it off again and resets the cap (audit finding 10).
- **Who can flip it:** anyone past the site password. It is not limited to the owner; the cap is the cost guard.

The judge's answer never moves a case. It is shown on this screen for a person to read.

## Not done

- **Owner-only control** of the switch.
- **A cap that survives restarts.**
- **The "add a shipment" option.** It is next; its decisions feed this screen with no further work.
- **Activating the judge prompt.** It still loads with allow_inactive, as for calibration.
- **Running the judge on the agent's tool results.** It judges against the full evidence, which is what those tools read.
