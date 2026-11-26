import { Fragment } from "react";
import type { CaseDetail, Dims, Shipment } from "./types";

const fmt = (d: Dims) => `${d.length_cm} × ${d.width_cm} × ${d.height_cm} cm${d.grams != null ? `, ${(d.grams / 1000).toFixed(3)} kg` : ""}`;

/** The rate card worked out step by step: as declared beside as the hub's scanner read it,
 *  each from the pack's own calculator (ui/seller.py breakdown). A hub re-measure, when one
 *  came back, is shown beneath: code's figure for it is in the approval itself. */
export function Calc({ shipment, remeasured }: { shipment: Shipment; remeasured: Dims | null }) {
  return (
    <div className="sheet">
      <div className="calc" style={{ ["--cols" as string]: 2 }}>
        <div className="h">How the charge is worked out</div>
        <div className="h">Declared</div>
        <div className="h">Scanner read</div>
        {shipment.breakdown.map((row, i) => {
          const last = i === shipment.breakdown.length - 1;
          return (
            <Fragment key={row.step}>
              <div className={`k${last ? " total" : ""}`}>{row.step}</div>
              <div className={`v${last ? " total" : ""}`}>{row.declared}</div>
              <div className={`v${last ? " total" : ""}`}>{row.measured}</div>
            </Fragment>
          );
        })}
      </div>
      {remeasured && (
        <p className="source" style={{ background: "var(--proven-cell)", color: "var(--proven)" }}>
          Hub re-measure: {fmt(remeasured)}
        </p>
      )}
      <p className="source" style={{ margin: 0 }}>
        Source: the pack's rate card on the declared size and on the hub scan · charged {shipment.charged}
      </p>
    </div>
  );
}

/** What people outside the system wrote: as written, never followed (I5; ADR 0061). Text only. */
export function Outside({ detail }: { detail: CaseDetail }) {
  const notes = detail.outside;
  if (notes.length === 0) return null;
  return (
    <div>
      <p className="label">Outside text · shown as written, never followed</p>
      {notes.map((n, i) => (
        <div key={i} className="outside">
          <header>
            {n.source} · <span className="num">{n.reference}</span>
          </header>
          <pre>{n.text}</pre>
          {n.flags.length > 0 && <p className="flag">The input screen flagged that it {n.flags.join("; ")}. Treated as data.</p>}
        </div>
      ))}
    </div>
  );
}

/** The proposer's recorded reasoning, exactly as recorded. Never generated here. */
export function Rationale({ detail }: { detail: CaseDetail }) {
  const rec = detail.recommendation;
  if (!rec) return null;
  return (
    <div className="rationale">
      <b>{rec.by.startsWith("AI") ? "AI's" : "Rules engine's"} recorded rationale, not verified:</b> proposed{" "}
      <i>{rec.action.toLowerCase()}</i>, {rec.confidence} confidence.
      <p style={{ margin: "6px 0 0", whiteSpace: "pre-wrap" }}>{rec.rationale}</p>
      {rec.findings.length > 0 && (
        <ul>
          {rec.findings.map((f, i) => (
            <li key={i}>
              {f.statement}
              {f.cites.length > 0 && (
                <span className="muted" title={f.cites.join(", ")}>
                  {" "}
                  (rests on {f.cites.length} recorded fact{f.cites.length === 1 ? "" : "s"})
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
