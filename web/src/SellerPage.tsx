import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { go } from "./App";
import { pillClass, when } from "./lib";
import type { SellerPage as Page, Session } from "./types";

const PERSONA = "dd-seller";

/** The seller's side (design 1.2, Seller view): a demo-seller switcher that is not a login,
 *  the bill worked out step by step, one action, and the tickets with what is happening. */
export function SellerPage({ session, say }: { session: Session; say: (t: string) => void }) {
  const [sellerId, setSellerId] = useState(
    () => sessionStorage.getItem(PERSONA) ?? session.sellers[0]?.seller_id ?? "",
  );
  const [page, setPage] = useState<Page | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string; case_id: string | null } | null>(null);

  const load = useCallback(async () => {
    if (!sellerId) return;
    setPage(await api.get<Page>(`/sellers/${encodeURIComponent(sellerId)}`));
  }, [sellerId]);

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(t);
  }, [load]);

  const shipment = page?.shipments.find((s) => s.ref === chosen) ?? null;
  const open = shipment ? page?.tickets.find((t) => t.shipment_ref === shipment.ref && t.is_open) : undefined;

  async function raise() {
    if (!shipment) return;
    setBusy(true);
    try {
      const answer = await api.post<{ ok: boolean; message: string; case_id: string | null }>("/sellers/raise", {
        seller_id: sellerId,
        shipment_ref: shipment.ref,
        note,
      });
      if (answer.ok) setNote("");
      setMessage({ ok: answer.ok, text: answer.message, case_id: answer.case_id });
      if (answer.ok) say(`Ticket raised on ${shipment.ref}. The agent has started on it.`);
      await load();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : String(e), case_id: null });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <div className="inner">
        <div>
          <h1>Seller view</h1>
          <p className="lede">
            What a seller sees. Viewing as{" "}
            <select
              className="select"
              value={sellerId}
              onChange={(e) => {
                setSellerId(e.target.value);
                sessionStorage.setItem(PERSONA, e.target.value);
                setChosen(null);
                setMessage(null);
              }}
            >
              {session.sellers.map((s) => (
                <option key={s.seller_id} value={s.seller_id}>
                  {s.business}
                </option>
              ))}
            </select>{" "}
            <span className="muted">· a synthetic demo seller, not a login.</span>
          </p>
        </div>
        <div className="grid2">
          <div className="sheet">
            <table className="t">
              <thead>
                <tr>
                  <th>Shipment</th>
                  <th>Status</th>
                  <th className="n">Expected</th>
                  <th className="n">Charged</th>
                  <th>Your ticket</th>
                </tr>
              </thead>
              <tbody>
                {page?.shipments.map((s) => {
                  const t = page.tickets.find((x) => x.shipment_ref === s.ref && x.is_open) ?? page.tickets.find((x) => x.shipment_ref === s.ref);
                  return (
                    <tr key={s.ref} className="pick" aria-selected={s.ref === chosen} onClick={() => { setChosen(s.ref); setMessage(null); }}>
                      <td className="num">{s.ref}</td>
                      <td style={s.status !== "Delivered" ? { color: "var(--failed)" } : undefined}>{s.status}</td>
                      <td className="n">{s.expected}</td>
                      <td className="n">{s.charged}</td>
                      <td>
                        {t ? (
                          <span className={`pill ${pillClass(t.tone)}`}>{t.status}</span>
                        ) : s.billing_error ? (
                          <span className="tag t-caution">Billing error</span>
                        ) : (
                          <span className="muted">Not disputed</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="source" style={{ margin: 0 }}>
              Expected: the rate card on the size you declared. Charged: what you were billed after the hub weighed the parcel.
            </p>
          </div>
          <div className="sheet pad">
            {!shipment ? (
              <p className="muted" style={{ margin: 0 }}>Choose a shipment to see its bill.</p>
            ) : (
              <>
                <p className="muted" style={{ margin: 0 }}>You were charged, zone {shipment.zone}</p>
                <p className="num" style={{ fontSize: 26, fontWeight: 600, margin: "2px 0 10px" }}>{shipment.charged}</p>
                <div className={`banner ${shipment.billing_error ? "caution" : "you"}`}>{shipment.verdict}</div>
                <table className="t" style={{ marginTop: 12 }}>
                  <thead>
                    <tr>
                      <th>Step</th>
                      <th className="n">As you declared</th>
                      <th className="n">As the hub measured</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shipment.breakdown.map((b) => (
                      <tr key={b.step}>
                        <td className="muted">{b.step}</td>
                        <td className="n">{b.declared}</td>
                        <td className="n">{b.measured}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 8 }}>
                  {shipment.status !== "Delivered" && (
                    <p className="error-line" style={{ margin: 0 }}>
                      Its status is “{shipment.status}”, not delivered. Only delivered shipments can be disputed.
                    </p>
                  )}
                  {message && (
                    <div className={`banner ${message.ok ? "proven" : "failed"}`}>
                      {message.text}{" "}
                      {message.case_id && (
                        <a href="#" onClick={(e) => { e.preventDefault(); go("work", message.case_id); }}>
                          Watch the agent work on it in the workspace →
                        </a>
                      )}
                    </div>
                  )}
                  {open ? (
                    <p style={{ margin: 0 }}>
                      You already have an open ticket on this shipment: <span className={`pill ${pillClass(open.tone)}`}>{open.status}</span>
                    </p>
                  ) : page?.can_raise ? (
                    <>
                      <label className="note-label" htmlFor="seller-note">Note for the agent (optional)</label>
                      <textarea
                        id="seller-note"
                        className="seller-note"
                        value={note}
                        maxLength={1000}
                        placeholder="Anything the agent should know, e.g. the box you packed it in, or what was inside."
                        onChange={(e) => setNote(e.target.value)}
                      />
                      <button className="btn primary" disabled={busy} onClick={() => void raise()} style={{ alignSelf: "flex-start" }}>
                        Raise a dispute
                      </button>
                    </>
                  ) : (
                    <p className="muted" style={{ margin: 0 }}>{page?.cannot_raise}</p>
                  )}
                  <p className="muted" style={{ margin: 0, fontSize: 12.5 }}>
                    The agent checks the charge in code and a person approves any credit. You see a credit here only after it
                    is posted and read back.
                  </p>
                </div>
              </>
            )}
          </div>
        </div>
        <div>
          <p className="label">Your tickets</p>
          <div className="sheet">
            {page && page.tickets.length === 0 ? (
              <p className="empty">No tickets yet. Choose a shipment and raise a dispute.</p>
            ) : (
              <table className="t">
                <thead>
                  <tr>
                    <th>Shipment</th>
                    <th>What is happening</th>
                    <th>Opened</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {page?.tickets.map((t) => (
                    <tr key={t.case_id}>
                      <td className="num">{t.shipment_ref}</td>
                      <td>{t.headline}</td>
                      <td>{when(t.opened_at)}</td>
                      <td><span className={`pill ${pillClass(t.tone)}`}>{t.status}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
