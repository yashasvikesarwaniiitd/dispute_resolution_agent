import { useMemo, useState } from "react";
import { api } from "./api";
import type { AddOptions } from "./types";

type Sides = [number, number, number];

/** Add a synthetic shipment for this demo seller (ADR 0072): one of their products and
 *  packagings, a quantity, a hub and scanner the network has, and the day it was measured -
 *  so the agent can compare it with the seller's real history. Code works out every charge. */
export function AddShipment({
  sellerId,
  options,
  onAdded,
}: {
  sellerId: string;
  options: AddOptions;
  onAdded: (ref: string, message: string) => void;
}) {
  const first = options.products[0];
  const [sku, setSku] = useState(first?.sku_id ?? "");
  const product = options.products.find((p) => p.sku_id === sku) ?? first;
  const [packagingId, setPackagingId] = useState(first?.packaging_id ?? "");
  const box = options.packagings.find((b) => b.packaging_id === packagingId) ?? options.packagings[0];
  const [quantity, setQuantity] = useState(1);
  const [hubId, setHubId] = useState(options.hubs[0]?.hub_id ?? "");
  const hub = options.hubs.find((h) => h.hub_id === hubId) ?? options.hubs[0];
  const [scannerId, setScannerId] = useState(hub?.scanners[0] ?? "");
  const [measuredOn, setMeasuredOn] = useState(new Date().toISOString().slice(0, 10));
  const [zone, setZone] = useState(options.zones.includes("B") ? "B" : (options.zones[0] ?? ""));
  const nominal: Sides = box ? box.cm : [20, 15, 10];
  const nominalGrams = (product?.item_grams ?? 0) * quantity + (box?.tare_grams ?? 0);
  const [declared, setDeclared] = useState<Sides | null>(null);
  const [declaredGrams, setDeclaredGrams] = useState<number | null>(null);
  const [measured, setMeasured] = useState<Sides | null>(null);
  const [measuredGrams, setMeasuredGrams] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Until a person types a figure, the form shows the booked packaging's own size and weight.
  const shown = useMemo(
    () => ({
      declared: declared ?? nominal,
      declaredGrams: declaredGrams ?? nominalGrams,
      measured: measured ?? nominal,
      measuredGrams: measuredGrams ?? nominalGrams,
    }),
    [declared, declaredGrams, measured, measuredGrams, nominal, nominalGrams],
  );

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const answer = await api.post<{ ok: boolean; ref: string; message: string }>(
        `/sellers/${encodeURIComponent(sellerId)}/shipments`,
        {
          sku_id: sku,
          packaging_id: packagingId || box?.packaging_id,
          quantity,
          hub_id: hubId,
          scanner_id: scannerId,
          measured_on: measuredOn,
          zone,
          declared: shown.declared,
          declared_grams: shown.declaredGrams,
          measured: shown.measured,
          measured_grams: shown.measuredGrams,
        },
      );
      onAdded(answer.ref, answer.message);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const sides = (value: Sides, set: (v: Sides) => void, label: string) => (
    <div className="add-sides" role="group" aria-label={label}>
      {(["Length", "Width", "Height"] as const).map((name, i) => (
        <label key={name}>
          <span>{name} (cm)</span>
          <input
            type="number"
            min={0.1}
            max={300}
            step={0.1}
            value={value[i]}
            onChange={(e) => {
              const next = [...value] as Sides;
              next[i] = Number(e.target.value);
              set(next);
            }}
          />
        </label>
      ))}
    </div>
  );

  return (
    <div className="add-shipment">
      <p className="muted" style={{ margin: 0 }}>
        Synthetic data only. Pick what this seller really ships; the rate card works out the charge from the hub's
        measurement.
      </p>
      <div className="add-grid">
        <label>
          <span>Product</span>
          <select
            className="select"
            value={sku}
            onChange={(e) => {
              setSku(e.target.value);
              const p = options.products.find((x) => x.sku_id === e.target.value);
              if (p) setPackagingId(p.packaging_id);
            }}
          >
            {options.products.map((p) => (
              <option key={p.sku_id} value={p.sku_id}>{p.name} ({p.sku_id})</option>
            ))}
          </select>
        </label>
        <label>
          <span>Packaging</span>
          <select className="select" value={packagingId} onChange={(e) => setPackagingId(e.target.value)}>
            {options.packagings.map((b) => (
              <option key={b.packaging_id} value={b.packaging_id}>{b.kind} - {b.cm.join(" x ")} cm</option>
            ))}
          </select>
        </label>
        <label>
          <span>Quantity</span>
          <input type="number" min={1} max={50} value={quantity} onChange={(e) => setQuantity(Number(e.target.value))} />
        </label>
        <label>
          <span>Hub</span>
          <select
            className="select"
            value={hubId}
            onChange={(e) => {
              setHubId(e.target.value);
              setScannerId(options.hubs.find((h) => h.hub_id === e.target.value)?.scanners[0] ?? "");
            }}
          >
            {options.hubs.map((h) => (
              <option key={h.hub_id} value={h.hub_id}>{h.hub_id}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Scanner</span>
          <select className="select" value={scannerId} onChange={(e) => setScannerId(e.target.value)}>
            {hub?.scanners.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Measured on</span>
          <input type="date" value={measuredOn} max={new Date().toISOString().slice(0, 10)} onChange={(e) => setMeasuredOn(e.target.value)} />
        </label>
        <label>
          <span>Zone</span>
          <select className="select" value={zone} onChange={(e) => setZone(e.target.value)}>
            {options.zones.map((z) => (
              <option key={z} value={z}>{z}</option>
            ))}
          </select>
        </label>
      </div>
      <p className="label" style={{ margin: "10px 0 4px" }}>As the seller declared</p>
      <div className="add-row">
        {sides(shown.declared, setDeclared, "Declared size")}
        <label>
          <span>Weight (g)</span>
          <input type="number" min={1} value={shown.declaredGrams} onChange={(e) => setDeclaredGrams(Number(e.target.value))} />
        </label>
      </div>
      <p className="label" style={{ margin: "10px 0 4px" }}>As the hub measured</p>
      <div className="add-row">
        {sides(shown.measured, setMeasured, "Measured size")}
        <label>
          <span>Weight (g)</span>
          <input type="number" min={1} value={shown.measuredGrams} onChange={(e) => setMeasuredGrams(Number(e.target.value))} />
        </label>
      </div>
      {error && <p className="error-line" role="alert">{error}</p>}
      <button className="btn primary" disabled={busy || !sku || !scannerId} onClick={() => void save()} style={{ alignSelf: "flex-start" }}>
        Add shipment
      </button>
    </div>
  );
}
