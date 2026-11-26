import { useEffect, useState } from "react";
import { api } from "./api";
import { go } from "./App";
import { when } from "./lib";
import type { Evals, Ledger, RunSummary, Session, Team } from "./types";

function useLoad<T>(path: string | null, every = 0): { data: T | null; error: string | null } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!path) return;
    let live = true;
    const load = () =>
      api
        .get<T>(path)
        .then((d) => live && (setData(d), setError(null)))
        .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    void load();
    const t = every ? window.setInterval(load, every) : undefined;
    return () => {
      live = false;
      if (t) window.clearInterval(t);
    };
  }, [path, every]);
  return { data, error };
}

const rupees = (v: string) => `₹${Number(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export function LedgerPage({ session }: { session: Session }) {
  const [sellerId, setSellerId] = useState(session.sellers[0]?.seller_id ?? "");
  const { data, error } = useLoad<Ledger>(sellerId ? `/ledger/${encodeURIComponent(sellerId)}` : null, 5000);
  return (
    <div className="page">
      <div className="inner">
        <div>
          <h1>Ledger</h1>
          <p className="lede">
            A seller's charges and credits. Each credit is traced to its case, its approver and its read-back.{" "}
            <select className="select" value={sellerId} onChange={(e) => setSellerId(e.target.value)}>
              {session.sellers.map((s) => (
                <option key={s.seller_id} value={s.seller_id}>{s.business}</option>
              ))}
            </select>
          </p>
        </div>
        {error && <p className="error-line">{error}</p>}
        {data && (
          <>
            <div className="grid3">
              {(
                [
                  ["Charged", data.totals.charged],
                  ["Credited", data.totals.credited],
                  ["Net", data.totals.net],
                ] as const
              ).map(([k, v]) => (
                <div key={k} className="sheet pad stat">
                  <small>{k}</small>
                  <div className="big">{rupees(v)}</div>
                </div>
              ))}
            </div>
            <div>
              <p className="label">Proposed, not in the ledger</p>
              {data.proposed.length === 0 ? (
                <p className="muted">None. Every proposed credit has been decided and read back.</p>
              ) : (
                <div className="sheet">
                  <table className="t">
                    <tbody>
                      {data.proposed.map((p) => (
                        <tr key={`${p.case_id}-${p.stage}`} className="pick" onClick={() => go("work", p.case_id)}>
                          <td className="num">{p.shipment_ref}</td>
                          <td className="n">{p.amount ? rupees(p.amount) : ""}</td>
                          <td>{p.stage}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
            <div>
              <p className="label">All entries</p>
              <div className="sheet">
                <table className="t">
                  <thead>
                    <tr>
                      <th>Posted</th>
                      <th>Shipment</th>
                      <th>Entry</th>
                      <th className="n">Amount</th>
                      <th>Reason</th>
                      <th>Approved by</th>
                      <th>Proved</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.rows.map((r) => (
                      <tr key={r.transaction_id}>
                        <td>{when(r.posted_at)}</td>
                        <td className="num">{r.shipment_ref}</td>
                        <td>{r.kind === "Credit" ? <span className="tag t-proven">Credit</span> : "Charge"}</td>
                        <td className="n" style={r.kind === "Credit" ? { color: "var(--proven)", fontWeight: 600 } : undefined}>
                          {rupees(r.amount)}
                        </td>
                        <td>{r.reason}</td>
                        <td>{r.approved_by}</td>
                        <td>{r.proved}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="muted" style={{ fontSize: 12.5 }}>The ledger is simulated: no money moves.</p>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

const day = (iso: string) => when(iso).split(",")[0];
const words = (code: string | null) => (code ?? "").replace(/_/g, " ");

export function EvalsPage() {
  const { data, error } = useLoad<Evals>("/evals");
  const [view, setView] = useState<"offline" | "online">("offline");
  return (
    <div className="page">
      <div className="inner">
        <div>
          <h1>Decision Quality</h1>
          <p className="lede">
            At each decision point, is the AI's choice one a careful reviewer would accept? Offline it is graded against
            the decision points the owner approved; online a calibrated judge checks each new live decision.
          </p>
        </div>
        <div className="chips">
          <button aria-pressed={view === "offline"} onClick={() => setView("offline")}>Offline</button>
          <button aria-pressed={view === "online"} onClick={() => setView("online")}>Online</button>
        </div>
        {error && <p className="error-line">{error}</p>}
        {data && view === "offline" && (
          <>
            <div className="banner caution">
              <b>Small sample, single run</b>
              18 decision points, one run each. Read these as a first measurement, not a rate.
            </div>
            <div className="grid3">
              {data.ai && (
                <Stat
                  label="The AI's decisions"
                  big={`${data.ai.acceptable} of ${data.ai.n}`}
                  note={`acceptable; ${data.ai.prohibited} prohibited, ${data.ai.not_acceptable} not acceptable. ${data.ai.model ?? ""}, ${day(data.ai.created_at)}.`}
                />
              )}
              {data.rules && (
                <Stat
                  label="Rules baseline"
                  big={`${data.rules.acceptable} of ${data.rules.n}`}
                  note={`acceptable; ${data.rules.prohibited} prohibited. The fixed rules table on the same points.`}
                />
              )}
              {data.judge && (
                <Stat
                  label="Judge agrees with the owner"
                  big={`${data.judge.agree} of ${data.judge.n}`}
                  note={`labels; ${data.judge.critical_caught} of ${data.judge.critical} critical flaws caught, ${data.judge.misses} missed, ${data.judge.false_alarms} false alarms.`}
                />
              )}
            </div>
            <div>
              <p className="label">Where the AI's decision was not acceptable ({data.misses.length})</p>
              <div className="sheet">
                <table className="t">
                  <thead>
                    <tr>
                      <th>Point</th>
                      <th>Situation</th>
                      <th>AI chose</th>
                      <th>Grade</th>
                      <th>Acceptable here</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.misses.map((m) => (
                      <tr key={m.id}>
                        <td className="num">{m.id}</td>
                        <td>{m.situation}</td>
                        <td>{words(m.chose)}</td>
                        <td>{words(m.grade)}</td>
                        <td>{m.acceptable.map(words).join(", ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div>
              <p className="label">Every run ({data.runs.length})</p>
              <div className="sheet">
                <table className="t">
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>What ran</th>
                      <th>Model</th>
                      <th>Result</th>
                      <th className="n">Cost (USD)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.runs.map((r, i) => (
                      <tr key={i}>
                        <td>{day(r.created_at)}</td>
                        <td>{r.kind === "judge" ? "Judge vs owner labels" : words(r.decider)}</td>
                        <td>{r.model ?? "none (rules)"}</td>
                        <td>{result(r)}</td>
                        <td className="n">{r.cost_usd ?? ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}
        {data && view === "online" && (
          <>
            <div className="sheet pad">
              <b>Online evaluation is {data.online.on ? "on" : "off"}.</b>
              <p style={{ margin: "6px 0 0" }}>
                Spent USD {Number(data.online.spent_usd).toFixed(4)} of the USD {data.online.cap_usd} cap. The judge's answer
                never changes a case. Turning it on spends money, so it needs the owner's authorisation and is not offered
                here.
              </p>
            </div>
            <div className="grid4">
              <Stat label="Judged" big={String(data.online.judged)} note="live decisions checked" />
              <Stat label="Holds up" big={String(data.online.judged - data.online.flagged)} note="explanation supported" />
              <Stat label="Flagged" big={String(data.online.flagged)} note="for a person to look at" />
              <Stat label="Waiting" big={String(data.online.waiting)} note="not yet judged" />
            </div>
            {data.online.rows === 0 && (
              <p className="muted">Online judging has never run on a live case, so there is nothing to show yet.</p>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function result(r: RunSummary): string {
  return r.kind === "judge"
    ? `${r.agree} agree, ${r.critical_caught}/${r.critical} critical caught`
    : `${r.acceptable} acceptable, ${r.prohibited} prohibited`;
}

function Stat({ label, big, note }: { label: string; big: string; note: string }) {
  return (
    <div className="sheet pad stat">
      <small>{label}</small>
      <div className="big">{big}</div>
      <p>{note}</p>
    </div>
  );
}

export function TeamPage() {
  const { data, error } = useLoad<Team>("/team", 10000);
  return (
    <div className="page">
      <div className="inner">
        <div>
          <h1>Team</h1>
          <p className="lede">Where the work is, from the case store. Counts are live; there is no assignment or per-person queue yet.</p>
        </div>
        {error && <p className="error-line">{error}</p>}
        {data && (
          <>
            <div className="grid4">
              {data.flow
                .filter((f) => ["needs", "hub", "investigating", "sellerwait"].includes(f.key))
                .map((f) => (
                  <Stat key={f.key} label={f.label} big={String(f.count)} note={f.section} />
                ))}
            </div>
            <div>
              <p className="label">Where work is stuck</p>
              <div className="sheet">
                <table className="t">
                  <tbody>
                    {data.stuck.map((s) => (
                      <tr key={s.label}>
                        <td>{s.label}</td>
                        <td className="n">{s.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="muted" style={{ fontSize: 12.5 }}>
                Every credit and email waits for a person by design: that safeguard stays, so approvals are the expected
                bottleneck. Known issue: the investigator gets 6 AI calls per round, the reply reader 10.
              </p>
            </div>
            <div>
              <p className="label">Approvers</p>
              <div className="sheet">
                <table className="t">
                  <thead>
                    <tr>
                      <th>Approver</th>
                      <th className="n">Decided today</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.approvers.map((a) => (
                      <tr key={a.name}>
                        <td>{a.name}</td>
                        <td className="n">{a.decided_today}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div className="notbuilt">
              <b>Not built yet:</b> assignment, SLA timers per person, median time per approval and ageing charts. They
              need work items with owners, which the engine does not record today.
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export function PatternsPage() {
  return (
    <div className="page">
      <div className="inner">
        <div>
          <h1>Patterns</h1>
          <p className="lede">Repeat causes across cases: one scanner, one product, one hub.</p>
        </div>
        <div className="sheet pad">
          <p className="label">What a pattern can and cannot do</p>
          <ul className="can">
            <li>Adds caution to every linked case</li>
            <li>Can lead to one shared hub investigation</li>
            <li className="no">Cannot prove a credit: only code's recalculation can</li>
            <li className="no">Cannot be confirmed by its own cases</li>
          </ul>
        </div>
        <div className="notbuilt">
          <b>Not built yet.</b> The agent already detects related cases (3 or more within 14 days) and records the
          caution on each case, visible in its Full record. A pattern store with stats, a time series and linked
          disputes does not exist yet, so this page shows nothing rather than invented numbers.
        </div>
      </div>
    </div>
  );
}
