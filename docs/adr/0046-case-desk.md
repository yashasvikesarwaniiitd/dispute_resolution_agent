# 0046 — The case desk: a reviewer acts on cases handed to a person

Date: 2026-11-07
Status: accepted (D66).

## Decision

The Review screen's "With a person" tab no longer only lists cases: each shows why it is there, the agent's last proposal and a link to every step. It then offers the decisions the engine already supports:

| Button | Event | Transition | What happens |
|---|---|---|---|
| Take over | `HUMAN_TOOK_CASE` | T33, or T34 to T36 with the notice | The agent stops; the person owns the case. With the box ticked, a handed-to-a-person notice is drafted and waits in review like any message. |
| Return to the agent | `HUMAN_RETURNED_CASE` | T37 | The whole deterministic stage runs again from evidence. A reason is required. It is refused if a credit is already verified (the guard, checked first so the reviewer is told). |
| Confirm withdrawal | `HUMAN_CONFIRMED_WITHDRAWAL` | T38 | Shown only when the case is with a person because the seller wrote that they withdraw. |

## How, and why this way

`POST /inbound` refuses people's decisions (403). It cannot tell who is calling, and dispatch rule P3 trusts the declared actor. So `CaseDesk` (`ui/desk.py`) is the authenticated path, built the way `ReviewConsole` is for approvals:

- The reviewer comes from `sign_in`; no method takes an actor; a system actor cannot construct it.
- A decision is accepted into the inbox as an `ops_console` event carrying the reviewer.
- The runner's next tick (about 5 seconds) dispatches it. P3 checks it is a person, and the transition's guards decide.

The console still never moves a case itself. No kernel change.

## Found while building it

**A return reason does not reach the investigator.** The briefing's reviewer notes are built from rejected approvals only (`prior_rejections`). The reason is kept on the inbox row and the case's audit trail, and the screen says so rather than implying the agent reads it. Passing it to the investigator is a briefing change in the kernel (BACKLOG).

## Results

- **7 desk tests on a real case with a person** (SHP-000001, B5: image evidence), each applied by the real runner:
  - taking over hands it to the person, under their name;
  - taking over with a notice puts the notice through review;
  - returning runs T37 and keeps the reason;
  - a blank reason and a verified credit are refused;
  - withdrawal is offered only when the seller asked;
  - a case no longer with a person is left alone;
  - a system actor cannot use the desk.
- **One Streamlit test-runner test:** sign in, see the reason and its detail, take over, and the inbox holds one `ops_console` event by the reviewer.
