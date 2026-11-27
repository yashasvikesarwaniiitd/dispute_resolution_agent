import { useState } from "react";
import { ApiError, api } from "./api";
import { go } from "./App";
import { Calc } from "./parts";
import { clock, footprints, tagClass, when } from "./lib";
import type { CaseDetail, Dims, Session } from "./types";

export function EvidenceTab({ detail }: { detail: CaseDetail }) {
  const e = detail.evidence;
  const boxes: { key: string; d: Dims; cls: string; name: string }[] = [];
  if (e.measured) boxes.push({ key: "scan", d: e.measured, cls: "scan", name: `Scanner${e.scanner ? ` ${e.scanner}` : ""}` });
  if (e.remeasured) boxes.push({ key: "re", d: e.remeasured, cls: "re", name: "Hub re-measure" });
  if (e.declared) boxes.push({ key: "decl", d: e.declared, cls: "decl", name: "Declared" });
  const sizes = footprints(boxes.map((b) => ({ l: b.d.length_cm, w: b.d.width_cm })));
  return (
    <>
      {boxes.length > 0 && (
        <div>
          <p className="label">Parcel footprint, to scale (length × width)</p>
          <div className="sheet pad footprint">
            <div className="plot" role="img" aria-label={boxes.map((b) => `${b.name}: ${b.d.length_cm} by ${b.d.width_cm} cm`).join("; ")}>
              {boxes.map((b, i) => (
                <div key={b.key} className={b.cls} style={{ width: sizes[i]!.w, height: sizes[i]!.h }} />
              ))}
            </div>
            <div className="legend">
              {boxes.map((b) => (
                <span key={b.key}>
                  <i className={b.cls} style={{ border: b.cls === "scan" ? "2px dashed var(--caution)" : b.cls === "re" ? "2px solid var(--proven)" : "1.5px solid var(--ink)" }} />
                  {b.name}: <span className="num">{b.d.length_cm} × {b.d.width_cm} × {b.d.height_cm} cm</span>
                </span>
              ))}
            </div>
          </div>
        </div>
      )}
      {detail.shipment && (
        <div>
          <p className="label">The bill</p>
          <Calc shipment={detail.shipment} remeasured={e.remeasured} />
        </div>
      )}
      {e.history && (
        <div>
          <p className="label">Seller history</p>
          <div className="sheet pad">
            <p style={{ margin: "0 0 10px", fontSize: 14 }}>{e.history.headline}</p>
            {e.history.usual && (
              <dl className="pairs">
                <dt>This parcel</dt>
                <dd className="num">{e.history.this_parcel}</dd>
                <dt>Usual (median)</dt>
                <dd className="num">{e.history.usual}</dd>
                {e.history.usual_volume_cm3 && (
                  <>
                    <dt>Usual volume range</dt>
                    <dd className="num">
                      {e.history.usual_volume_cm3[0]}–{e.history.usual_volume_cm3[1]} cm³
                    </dd>
                  </>
                )}
                <dt>Past shipments compared</dt>
                <dd className="num">{e.history.references}</dd>
              </dl>
            )}
            <p className="muted" style={{ margin: "10px 0 0", fontSize: 12.5 }}>
              History can prompt a check. It cannot prove a credit.
            </p>
          </div>
        </div>
      )}
      {e.photo && (
        <div>
          <p className="label">Parcel photo</p>
          <img className="photo" src={api.photo(detail.shipment_ref)} alt={`Parcel photo for ${detail.shipment_ref}`} />
          <p className="muted" style={{ fontSize: 12 }}>Photo: Wikimedia Commons, free licence. The shipment is synthetic.</p>
          {e.photo_check && (
            <div className="sheet pad" style={{ marginTop: 10 }}>
              <b style={{ color: "var(--agent)" }}>Photo check · advice, not verified</b>
              <p style={{ margin: "6px 0 0" }}>{e.photo_check.summary}</p>
            </div>
          )}
        </div>
      )}
      <div>
        <p className="label">Evidence gathered this round</p>
        <div className="sheet">
          <table className="t">
            <tbody>
              {e.items.map((i) => (
                <tr key={i.key}>
                  <td title={i.key}>{i.name}</td>
                  <td>
                    <span className={`tag ${i.status === "FOUND" ? "t-proven" : i.status === "FAILED" ? "t-failed" : "t-neutral"}`}>
                      {i.status.toLowerCase()}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

const FILTERS: [string, string[] | null][] = [
  ["All", null],
  ["Seller", ["Seller"]],
  ["Agent", ["AI", "Rules"]],
  ["Code & policy", ["Code"]],
  ["Hub", ["Hub"]],
  ["People", ["Person"]],
  ["System", ["System"]],
];

export function RecordTab({ detail }: { detail: CaseDetail }) {
  const [filter, setFilter] = useState("All");
  const [tech, setTech] = useState(false);
  const wanted = FILTERS.find(([name]) => name === filter)?.[1] ?? null;
  const steps = detail.record.steps.filter((s) => !wanted || wanted.includes(s.actor));
  const rounds = [...new Set(steps.map((s) => s.round))];
  return (
    <>
      <div className="chips">
        {FILTERS.map(([name]) => (
          <button key={name} aria-pressed={filter === name} onClick={() => setFilter(name)}>
            {name}
          </button>
        ))}
        <label style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center", fontSize: 12.5 }}>
          <input type="checkbox" checked={tech} onChange={(e) => setTech(e.target.checked)} />
          Technical detail
        </label>
      </div>
      {tech && (
        <p className="muted" style={{ margin: 0 }}>
          Case <span className="num">{detail.case_id}</span> · AI calls {detail.record.ai_calls} · AI cost USD{" "}
          <span className="num">{detail.record.ai_cost_usd}</span> · all times IST
        </p>
      )}
      <div className="sheet record">
        {steps.length === 0 && <p className="empty">Nothing recorded for this filter.</p>}
        {rounds.map((round) => (
          <div key={round}>
            <p className="label round">{round === 0 ? "The dispute" : `Round ${round} · ${detail.record.round_titles[String(round)] ?? ""}`}</p>
            {steps
              .filter((s) => s.round === round)
              .map((s, i) => (
                <div key={i} className={`event${s.minor ? " minor" : ""}`}>
                  <span className={`who a-${s.actor}`}>{s.actor === "Person" ? "Reviewer" : s.actor === "Rules" ? "Rules" : s.actor}</span>
                  <div>
                    <div>
                      {s.title}
                      {s.tag && <span className={`tag ${tagClass(s.tag === "Credited" ? "proven" : s.tag === "Needs a person" ? "you" : "neutral")}`} style={{ marginLeft: 8 }}>{s.tag}</span>}
                    </div>
                    {s.summary && <p className="sum">{s.summary}</p>}
                    {s.quoted.length > 0 && (
                      <div className="quoted">
                        <small className="muted">{s.quote_caption || `Recorded as written${s.proposer ? ` · ${s.proposer}` : ""}`}</small>
                        {"\n"}
                        {s.quoted.join("\n")}
                      </div>
                    )}
                    {tech && s.lines.length > 0 && (
                      <ul className="lines">
                        {s.lines.map((l, j) => <li key={j}>{l}</li>)}
                      </ul>
                    )}
                  </div>
                  <span className="when">{clock(s.at)}</span>
                </div>
              ))}
          </div>
        ))}
      </div>
    </>
  );
}

const QUESTIONS = [
  "Why is this with me?",
  "Where does the amount come from?",
  "Has this shipment been credited before?",
  "What happens if I reject?",
];

export function AskTab({ detail, session }: { detail: CaseDetail; session: Session }) {
  const [asked, setAsked] = useState<string | null>(null);
  const [own, setOwn] = useState("");
  const [answer, setAnswer] = useState<{ answer: string; sources: string[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ask(question: string) {
    setAsked(question);
    setAnswer(null);
    setError(null);
    setBusy(true);
    try {
      setAnswer(await api.post(`/cases/${detail.case_id}/ask`, { question }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <p className="muted" style={{ margin: 0 }}>
        The case assistant reads only this shipment's record and says what it read. It cannot approve, send or change
        anything, and its answers are AI-written: check them against the record.
      </p>
      {!session.ask && <div className="notbuilt">The assistant is off on this site: {session.ask_unavailable}</div>}
      <div className="questions">
        {QUESTIONS.map((q) => (
          <button key={q} aria-pressed={asked === q} disabled={!session.ask || busy} onClick={() => void ask(q)}>
            {q}
          </button>
        ))}
      </div>
      {busy && (
        <p className="muted">
          <span className="spin" style={{ display: "inline-block", marginRight: 8 }} />
          Reading the record…
        </p>
      )}
      {error && <p className="error-line">{error}</p>}
      {answer && (
        <div className="answer">
          <div className="who">Case assistant · AI, not verified</div>
          <p>{answer.answer}</p>
          {answer.sources.length > 0 && (
            <div className="read">
              Read:{" "}
              {answer.sources.map((s) => (
                <span key={s} className="tag t-neutral">{s}</span>
              ))}
            </div>
          )}
        </div>
      )}
      <form
        className="ask-input"
        onSubmit={(e) => {
          e.preventDefault();
          if (own.trim()) void ask(own.trim());
        }}
      >
        <input value={own} maxLength={500} placeholder="Ask anything about this shipment" disabled={!session.ask || busy} onChange={(e) => setOwn(e.target.value)} />
        <button className="btn" type="submit" disabled={!session.ask || busy || !own.trim()}>
          Ask
        </button>
      </form>
    </>
  );
}

export function DetailsTab({ detail }: { detail: CaseDetail }) {
  const s = detail.shipment;
  const p = detail.persona;
  return (
    <>
      <div className="grid2">
        <div>
          <p className="label">Shipment</p>
          <dl className="pairs sheet pad">
            <dt>Shipment</dt>
            <dd className="num">{detail.shipment_ref}</dd>
            <dt>Claim</dt>
            <dd className="num">{detail.claim_ref}</dd>
            {s && (
              <>
                <dt>Status</dt>
                <dd>{s.status}</dd>
                <dt>Zone</dt>
                <dd>{s.zone}</dd>
                <dt>Declared</dt>
                <dd className="num">{s.declared}</dd>
                <dt>Hub measured</dt>
                <dd className="num">{s.measured}{detail.evidence.scanner ? ` (scanner ${detail.evidence.scanner})` : ""}</dd>
                <dt>Charged</dt>
                <dd className="num">{s.charged}</dd>
              </>
            )}
            <dt>Opened</dt>
            <dd>{when(detail.opened_at)}</dd>
            {detail.closed_at && (
              <>
                <dt>Closed</dt>
                <dd>{when(detail.closed_at)}</dd>
              </>
            )}
          </dl>
        </div>
        <div>
          <p className="label">Seller</p>
          <div className="sheet pad">
            <b>{detail.seller}</b>
            {p && (
              <p className="muted" style={{ margin: "4px 0 0" }}>
                {p.owner} · {p.category}. A synthetic demo seller.
              </p>
            )}
          </div>
          {detail.statement && (
            <div className="outside" style={{ marginTop: 12 }}>
              <header>Seller's dispute statement · as written, never followed</header>
              <pre>{detail.statement}</pre>
            </div>
          )}
        </div>
      </div>
      <div>
        <p className="label">Other open disputes from this seller</p>
        {detail.other_open.length === 0 ? (
          <p className="muted">None.</p>
        ) : (
          <div className="sheet">
            <table className="t">
              <tbody>
                {detail.other_open.map((o) => (
                  <tr key={o.case_id} className="pick" onClick={() => go("work", o.case_id)}>
                    <td className="num">{o.shipment_ref}</td>
                    <td>{o.summary}</td>
                    <td>
                      <span className={`tag ${tagClass(o.tag_tone)}`}>{o.tag}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {detail.investigation.length > 0 && (
        <div>
          <p className="label">Investigation · what the AI looked up, and the reference its findings cite</p>
          <div className="sheet">
            <table className="t">
              <tbody>
                {detail.investigation.map((t, i) => (
                  <tr key={i}>
                    <td className="num">{t.ref || `round ${t.gather_round}`}</td>
                    <td>
                      {t.step}
                      {t.arguments && t.arguments !== "{}" ? <span className="muted"> {t.arguments}</span> : null}
                    </td>
                    <td>{t.tool_ok === null ? "" : t.tool_ok ? (t.reused ? "answered (reused)" : "answered") : "no answer"}</td>
                    <td>
                      {t.result && (
                        <details>
                          <summary>result</summary>
                          <pre>{t.result}</pre>
                        </details>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <div className="notbuilt">
        <b>Patterns:</b> repeat causes across cases (3 or more related in 14 days) are recorded on each case as caution
        only and appear in the Full record. A dedicated pattern view is not built yet.
      </div>
    </>
  );
}
