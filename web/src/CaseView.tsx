import { Fragment, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { go } from "./App";
import { AskTab, DetailsTab, EvidenceTab, RecordTab } from "./CaseTabs";
import { Calc, Outside, Rationale } from "./parts";
import { marked, pillClass, typing, when } from "./lib";
import type { Approval, CaseDetail, Session, Verdict, WorkItem } from "./types";

type Tab = "overview" | "evidence" | "record" | "ask" | "details";
const TABS: [Tab, string][] = [
  ["overview", "Overview"],
  ["evidence", "Evidence"],
  ["record", "Full record"],
  ["ask", "Ask"],
  ["details", "Shipment & seller"],
];

const DRAFTS = "dd-drafts";
function readDraft(id: string): string {
  try {
    return (JSON.parse(sessionStorage.getItem(DRAFTS) ?? "{}") as Record<string, string>)[id] ?? "";
  } catch {
    return "";
  }
}
function writeDraft(id: string, text: string): void {
  try {
    const all = JSON.parse(sessionStorage.getItem(DRAFTS) ?? "{}") as Record<string, string>;
    if (text) all[id] = text;
    else delete all[id];
    sessionStorage.setItem(DRAFTS, JSON.stringify(all));
  } catch {
    /* private mode: the draft lasts while the case is open */
  }
}

/** A person's choice on a handed-over case: one of code's verdicts, or the desk's own. */
type Choice =
  | { kind: "verdict"; verdict: Verdict }
  | { kind: "take_over" }
  | { kind: "return" }
  | { kind: "withdraw" };

const label = (c: Choice): string =>
  c.kind === "verdict" ? c.verdict.label : c.kind === "take_over" ? "Take it over" : c.kind === "return" ? "Return it to the agent" : "Confirm the seller's withdrawal";

interface Props {
  detail: CaseDetail;
  session: Session;
  reviewer: string;
  next: WorkItem | null;
  say: (text: string) => void;
  refused: (e: unknown) => string;
  refresh: () => Promise<void>;
}

export function CaseView({ detail, session, reviewer, next, say, refused, refresh }: Props) {
  const [tab, setTab] = useState<Tab>("overview");
  const [note, setNote] = useState(() => readDraft(detail.case_id));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [acted, setActed] = useState<Record<string, "approve" | "reject">>({});
  const [choice, setChoice] = useState<Choice | null>(null);
  const [items, setItems] = useState<string[]>([]);
  const [notify, setNotify] = useState(false);
  const [happened, setHappened] = useState<Record<string, "confirmed_done" | "confirmed_not_done">>({});
  const [recorded, setRecorded] = useState<string | null>(null);
  const noteRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => writeDraft(detail.case_id, note), [detail.case_id, note]);

  const approval: Approval | undefined = detail.approvals.find((a) => !acted[a.approval_id]);
  // A decision is recorded but the runner has not acted yet (a few seconds), or it is acting.
  const waitingOnAgent =
    Object.keys(acted).some((id) => detail.approvals.some((a) => a.approval_id === id)) ||
    ["EXECUTING", "VERIFYING"].includes(detail.state) ||
    (detail.state === "AWAITING_APPROVAL" && detail.approvals.length === 0);
  const handover = detail.handover;

  async function decide(decision: "approve" | "reject") {
    if (!approval) return;
    if (decision === "reject" && !note.trim()) {
      setError("Write your reason for rejecting, then press Reject.");
      noteRef.current?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.post(`/approvals/${approval.approval_id}`, { reviewer, decision, note });
      setActed((a) => ({ ...a, [approval.approval_id]: decision }));
      setNote("");
      say(
        decision === "approve"
          ? `${detail.shipment_ref}: approved. The agent acts and reads it back within a few seconds.`
          : `${detail.shipment_ref}: rejected with your note. The agent re-plans.`,
      );
      await refresh();
    } catch (e) {
      setError(refused(e));
    } finally {
      setBusy(false);
    }
  }

  async function record() {
    if (!choice) {
      setError("Choose a decision first.");
      return;
    }
    if (choice.kind !== "take_over" && !note.trim()) {
      setError("Write why you decided this first: it is kept on the case's record.");
      noteRef.current?.focus();
      return;
    }
    const asking = choice.kind === "verdict" && choice.verdict.action === "request_seller_evidence";
    if (asking && items.length === 0) {
      setError("Tick at least one item to ask the seller for.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.post(`/cases/${detail.case_id}/decide`, {
        reviewer,
        choice: choice.kind,
        note,
        action: choice.kind === "verdict" ? choice.verdict.action : null,
        reason_code: choice.kind === "verdict" ? choice.verdict.reason_code : null,
        items: asking ? items : [],
        notify_seller: choice.kind === "take_over" && notify,
        resolutions: choice.kind === "return" ? happened : {},
      });
      setRecorded(label(choice));
      setNote("");
      say(`${detail.shipment_ref}: decision recorded. The agent applies it within a few seconds.`);
      await refresh();
    } catch (e) {
      setError(refused(e));
    } finally {
      setBusy(false);
    }
  }

  // A approves, R rejects (or puts the cursor in the note to write the reason first).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (typing(e.target) || e.metaKey || e.ctrlKey || e.altKey || busy) return;
      if (e.key === "a" && approval && approval.matches_approval && !session.kill_switch) void decide("approve");
      if (e.key === "r" && approval) {
        if (note.trim()) void decide("reject");
        else {
          e.preventDefault();
          noteRef.current?.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const phases = detail.journey.phases;
  const at = phases.findIndex((p) => p.mark === "current" || p.mark === "waiting" || p.mark === "person");
  const shipment = detail.shipment;
  const title = `Weight dispute · ${detail.shipment_ref}${shipment ? ` · ${shipment.charged} billed` : ""}`;

  return (
    <section className="case" aria-label={`Case ${detail.shipment_ref}`}>
      <header className="case-head">
        <div className="inner">
          <div className="r1">
            <span>
              {detail.seller} · <span className="num">{detail.shipment_ref}</span>
            </span>
            <span className="right">
              <span className={`pill ${pillClass(detail.tone)}`}>{detail.status}</span>
              <span className={`num ${detail.due.at_risk ? "" : "muted"}`} style={detail.due.at_risk ? { color: "var(--caution)", fontWeight: 600 } : undefined}>
                {detail.approvals.length ? detail.due.text : `opened ${when(detail.opened_at)}`}
              </span>
            </span>
          </div>
          <div className="r2">
            <h1>{title}</h1>
            <span className="phases" title={phases.map((p) => `${p.label}: ${p.mark}`).join(" · ")}>
              <span className="bars" aria-hidden="true">
                {phases.map((p) => (
                  <span key={p.label} className={p.mark === "skipped" || p.mark === "not_reached" || p.mark === "todo" ? "" : p.mark} />
                ))}
              </span>
              {at >= 0 ? `${phases[at]!.label} · ${at + 1} of ${phases.length}` : detail.is_open ? "" : "Done"}
            </span>
          </div>
          <div className="tabs" role="tablist">
            {TABS.map(([key, name]) => (
              <button key={key} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>
                {name}
              </button>
            ))}
          </div>
        </div>
      </header>

      <div className="case-body">
        <div className="inner">
          {tab === "overview" && (
            <Overview
              detail={detail}
              session={session}
              approval={approval}
              waitingOnAgent={waitingOnAgent}
              acted={acted}
              recorded={recorded}
              choice={choice}
              setChoice={setChoice}
              items={items}
              setItems={setItems}
              notify={notify}
              setNotify={setNotify}
              happened={happened}
              setHappened={setHappened}
            />
          )}
          {tab === "evidence" && <EvidenceTab detail={detail} />}
          {tab === "record" && <RecordTab detail={detail} />}
          {tab === "ask" && <AskTab detail={detail} session={session} />}
          {tab === "details" && <DetailsTab detail={detail} />}
        </div>
      </div>

      <footer className="footer">
        <div className="inner">
          {(approval || (handover && !recorded)) && (
            <>
              <label className="note-label" htmlFor={`note-${detail.case_id}`}>
                {approval
                  ? approval.is_credit
                    ? "Note (optional to approve; your reason is needed to reject)"
                    : "Note, or feedback for the agent's redraft if you reject"
                  : "Why you decided this (kept on the case's record)"}
              </label>
              <textarea id={`note-${detail.case_id}`} ref={noteRef} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
            </>
          )}
          {error && <p className="error-line" role="alert" style={{ margin: 0 }}>{error}</p>}
          <p className="hint" style={{ margin: 0 }}>
            {approval
              ? approval.is_credit
                ? `You approve this exact amount for ${detail.shipment_ref} only · single use · expires ${when(approval.expires_at)}`
                : `You approve this exact text · it is sent once and read back from Sent mail · expires ${when(approval.expires_at)}`
              : handover && !recorded
                ? "Decide, then approve: a decision that leads to a credit or an email still waits for approval."
                : detail.is_open
                  ? "Nothing needs you on this case right now."
                  : "This case is closed."}
          </p>
          <div className="actions">
            {approval && (
              <>
                <button
                  className="btn primary"
                  disabled={busy || !approval.matches_approval || session.kill_switch}
                  onClick={() => void decide("approve")}
                >
                  {approval.approve_label} <span className="key">A</span>
                </button>
                <button className="btn" disabled={busy} onClick={() => void decide("reject")}>
                  {approval.is_credit ? "Reject with note" : "Reject with feedback"} <span className="key">R</span>
                </button>
              </>
            )}
            {!approval && handover && !recorded && (
              <button className="btn primary" disabled={busy || !choice} onClick={() => void record()}>
                Record decision
              </button>
            )}
            {next && (
              <button className="next" onClick={() => go("work", next.case_id)}>
                <span>Next case ·</span> {next.seller} →
              </button>
            )}
          </div>
        </div>
      </footer>
    </section>
  );
}

interface OverviewProps {
  detail: CaseDetail;
  session: Session;
  approval: Approval | undefined;
  waitingOnAgent: boolean;
  acted: Record<string, "approve" | "reject">;
  recorded: string | null;
  choice: Choice | null;
  setChoice: (c: Choice) => void;
  items: string[];
  setItems: (i: string[]) => void;
  notify: boolean;
  setNotify: (v: boolean) => void;
  happened: Record<string, "confirmed_done" | "confirmed_not_done">;
  setHappened: (h: Record<string, "confirmed_done" | "confirmed_not_done">) => void;
}

function yourPart(d: CaseDetail, approval: Approval | undefined): string {
  if (approval?.is_credit)
    return `Every credit needs a person. Approve ${approval.amount}, or reject it with a note. The amount is code's; you cannot change it.`;
  if (approval) return "Every email to a seller needs a person. Approve this exact text, or reject it with feedback for a redraft.";
  if (d.handover) return d.handover.task;
  if (d.state === "AWAITING_APPROVAL" || d.state === "EXECUTING" || d.state === "VERIFYING")
    return "Nothing: the decision is recorded and the agent is acting on it, then reading the result back.";
  if (d.kind === "unverified") return "An action could not be confirmed. The agent will not retry it; a person resolves it when the case is handed over.";
  if (d.kind === "waiting") return `Nothing yet. ${d.journey.next_step}`;
  if (d.kind === "reopened") return `Nothing yet: the agent is working the new round. ${d.journey.next_step}`;
  if (!d.is_open) return "Nothing. This case is closed.";
  return `Nothing yet. ${d.journey.next_step}`;
}

function Overview(p: OverviewProps) {
  const { detail: d, approval } = p;
  const handover = d.handover;
  const actedOn = Object.entries(p.acted).filter(([id]) => d.approvals.some((a) => a.approval_id === id));
  const choices: Choice[] = handover
    ? [
        ...handover.verdicts.map((verdict) => ({ kind: "verdict", verdict }) as Choice),
        { kind: "take_over" },
        ...(handover.can_return ? [{ kind: "return" } as Choice] : []),
        ...(handover.can_withdraw ? [{ kind: "withdraw" } as Choice] : []),
      ]
    : [];
  const effect = (c: Choice) =>
    c.kind === "verdict"
      ? c.verdict.effect
      : c.kind === "take_over"
        ? "You handle it outside the agent from here; the agent stops."
        : c.kind === "return"
          ? "The agent starts a new round from fresh evidence."
          : "The case closes as withdrawn, as the seller asked.";
  const same = (a: Choice | null, b: Choice) => !!a && label(a) === label(b);

  return (
    <>
      {d.reopen_count > 0 && d.is_open && (
        <div className="banner you">
          <b>Reopened · round {d.round}, reopen {d.reopen_count} of 2</b>
          The seller replied within the 7-day window after the case closed, so it reopened for a fresh round.
        </div>
      )}
      {d.rejection_count > 0 && d.approvals.length > 0 && (
        <div className="banner caution">
          <b>Rejected {d.rejection_count === 1 ? "once" : `${d.rejection_count} times`}</b>
          A case allows two rejections; {d.rejection_count >= 2 ? "the next one hands it" : "a third hands it"} to a person.
        </div>
      )}
      {handover?.refused && (
        <div className="banner failed">
          <b>Your last decision was not applied</b>
          {handover.refused}
        </div>
      )}
      {(actedOn.length > 0 || p.waitingOnAgent) && (
        <div className="sheet pad">
          <p className="label">Progress</p>
          <ul className="progress">
            {actedOn.map(([id, what]) => (
              <li key={id}>✓ {what === "approve" ? "Approved and recorded" : "Rejected with your note"}</li>
            ))}
            <li>
              <span className="spin" aria-hidden="true" />
              {d.state === "VERIFYING"
                ? "Reading it back to prove it happened"
                : "The agent acts once, with a unique stamp, then reads it back"}
            </li>
          </ul>
        </div>
      )}
      {p.recorded && d.state === "NEEDS_HUMAN_REVIEW" && (
        <div className="banner proven">
          <b>Decision recorded: {p.recorded}</b>
          The agent applies it within a few seconds. Anything that needs approval comes back to My work.
        </div>
      )}

      <div className="agent-note">
        <div className="who">
          <b>{handover ? "Agent handover" : "Agent status"}</b>
          <span className="muted">written from the case records</span>
        </div>
        <p className="body">{d.journey.headline}</p>
        {d.journey.why && (
          <p className="why">
            {d.journey.why}
            {d.journey.why_code && <span className="muted num"> ({d.journey.why_code})</span>}
          </p>
        )}
        {handover?.reason_detail && handover.reason_detail !== d.journey.why && (
          <p className="why">{handover.reason_detail}</p>
        )}
        <div className="your-part">
          <b>Your part</b>
          {yourPart(d, approval)}
        </div>
      </div>

      {approval?.is_credit && (
        <div>
          <p className="label">What you are approving</p>
          <div className="sheet">
            {approval.equation ? (
              <div className="eq" role="group" aria-label="What you are approving">
                {approval.equation.map((t, i) => (
                  <Fragment key={t.label}>
                    {i > 0 && <span className="op" aria-hidden="true">−</span>}
                    <span className="term">
                      <small>{t.label}</small>
                      <span>{t.value}</span>
                    </span>
                  </Fragment>
                ))}
                <span className="op" aria-hidden="true">=</span>
                <span className="term credit">
                  <small>Credit · {approval.reason_words}</small>
                  <span>{approval.amount}</span>
                </span>
              </div>
            ) : (
              <div className="eq">
                <span className="term credit">
                  <small>Credit · {approval.reason_words}</small>
                  <span>{approval.amount}</span>
                </span>
              </div>
            )}
          </div>
          {!approval.matches_approval && (
            <div className="banner failed" style={{ marginTop: 12 }}>
              <b>The stored plan has changed since approval was requested.</b> It cannot be approved. Report this.
            </div>
          )}
          {d.shipment && <div style={{ marginTop: 14 }}><Calc shipment={d.shipment} remeasured={d.evidence.remeasured} /></div>}
          {approval.figures && approval.figures.length > 0 && (
            <details style={{ marginTop: 12 }}>
              <summary className="muted" style={{ cursor: "pointer" }}>Every figure code used</summary>
              <dl className="pairs sheet pad" style={{ marginTop: 8 }}>
                {approval.figures.map((f) => (
                  <Fragment key={f.label}>
                    <dt>{f.label}</dt>
                    <dd className="num">{f.value}</dd>
                  </Fragment>
                ))}
              </dl>
            </details>
          )}
        </div>
      )}

      {approval && !approval.is_credit && (
        <div>
          <p className="label">
            {approval.title} · <span style={{ textTransform: "none", letterSpacing: 0 }}>numbers from code</span>
          </p>
          <div className="sheet">
            <div className="mail-head">
              <span>To</span>
              <span>{approval.to}</span>
              <span>Subject</span>
              <span>{approval.subject}</span>
            </div>
            <div className="mail-body">
              {marked(approval.body ?? "", approval.numbers.map((n) => n.value)).map((part, i) =>
                part.mark ? <mark key={i}>{part.text}</mark> : <span key={i}>{part.text}</span>,
              )}
            </div>
            <div className="mail-foot">
              The sent text must match this exactly. There is no edit box: reject with feedback and the agent redrafts.
            </div>
          </div>
          {!approval.matches_approval && (
            <div className="banner failed" style={{ marginTop: 12 }}>
              <b>The stored text has changed since approval was requested.</b> It cannot be approved. Report this.
            </div>
          )}
        </div>
      )}

      {handover && !p.recorded && (
        <div>
          <p className="label">Your decision</p>
          {handover.checks.length > 0 && (
            <ul className="checks" style={{ marginTop: 0, marginBottom: 12 }}>
              {handover.checks.map((c) => <li key={c}>{c}</li>)}
            </ul>
          )}
          {handover.no_verdict && <p className="muted">{handover.no_verdict}</p>}
          <div className="tiles" role="radiogroup" aria-label="Decision">
            {choices.map((c) => (
              <button key={label(c)} className="tile" role="radio" aria-checked={same(p.choice, c)} onClick={() => p.setChoice(c)}>
                <b>{label(c)}</b>
                <small>{effect(c)}</small>
              </button>
            ))}
            {!handover.verdicts.some((v) => v.action === "propose_credit") && (
              <div className="tile" aria-disabled="true">
                <b>Credit the seller</b>
                <small>Not offered: code has proved no billing error on this case.</small>
              </div>
            )}
          </div>
          {p.choice?.kind === "verdict" && p.choice.verdict.action === "request_seller_evidence" && (
            <div className="extra">
              <b>What to ask for</b>
              {p.session.evidence_items.map((item) => (
                <label key={item}>
                  <input
                    type="checkbox"
                    checked={p.items.includes(item)}
                    onChange={(e) => p.setItems(e.target.checked ? [...p.items, item] : p.items.filter((x) => x !== item))}
                  />
                  {item}
                </label>
              ))}
            </div>
          )}
          {p.choice?.kind === "take_over" && (
            <div className="extra">
              <label>
                <input type="checkbox" checked={p.notify} onChange={(e) => p.setNotify(e.target.checked)} />
                Tell the seller a person is handling it (that email goes through approval first)
              </label>
            </div>
          )}
          {p.choice?.kind === "return" && handover.unknown.length > 0 && (
            <div className="extra">
              {handover.unknown.map((u) => (
                <fieldset key={u.action_id} style={{ border: 0, padding: 0, margin: "0 0 8px" }}>
                  <legend>
                    Did {u.what} actually happen? Check {u.where}. The agent could not confirm it and will not try again.
                  </legend>
                  {(["confirmed_done", "confirmed_not_done"] as const).map((v) => (
                    <label key={v}>
                      <input
                        type="radio"
                        name={`happened-${u.action_id}`}
                        checked={p.happened[u.action_id] === v}
                        onChange={() => p.setHappened({ ...p.happened, [u.action_id]: v })}
                      />
                      {v === "confirmed_done" ? "Yes, it happened" : "No, it did not happen"}
                    </label>
                  ))}
                </fieldset>
              ))}
            </div>
          )}
        </div>
      )}

      {d.came_back.length > 0 && (
        <div>
          <p className="label">What came back</p>
          <div className="sheet pad">
            <ul className="progress">
              {d.came_back.map((c, i) => (
                <li key={i} style={{ alignItems: "flex-start", flexDirection: "column", gap: 2 }}>
                  <span>
                    <b>{c.who}</b> <span className="muted">· {when(c.at)}</span>
                  </span>
                  <span>{c.summary}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <Outside detail={d} />
      <Rationale detail={d} />
    </>
  );
}
