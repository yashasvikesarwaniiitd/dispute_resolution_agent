import type { Tone, WorkItem } from "./types";

/** India Standard Time, as every console screen shows it (ui/labels.py: IST, no DST). */
const IST = "Asia/Kolkata";

export function clock(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: IST,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(iso));
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  const day = new Intl.DateTimeFormat("en-IN", { timeZone: IST, day: "numeric", month: "short" }).format(d);
  return `${day}, ${clock(iso)} IST`;
}

export function initials(name: string): string {
  return name
    .split(/[\s._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");
}

/** A case state's tone as the design's pill colours (design 2.3 status pills). */
export function pillClass(tone: Tone): string {
  switch (tone) {
    case "settled":
    case "done":
      return "t-proven";
    case "working":
      return "t-agent";
    case "waiting":
      return "t-hub";
    case "attention":
      return "t-you";
    default:
      return "t-neutral";
  }
}

export const tagClass = (tone: string): string => `t-${tone}`;

/** The rail view's items, in the list's order. */
export function inView(items: WorkItem[], view: string, query: string): WorkItem[] {
  const q = query.trim().toLowerCase();
  if (q) {
    return items.filter((i) =>
      [i.shipment_ref, i.seller, i.status, i.tag].some((v) => v.toLowerCase().includes(q)),
    );
  }
  return items.filter((i) => i.views.includes(view));
}

/** The next or previous case in the list, J/K style; stays at the ends. */
export function step(ids: string[], current: string | null, by: 1 | -1): string | null {
  if (ids.length === 0) return null;
  const at = current === null ? -1 : ids.indexOf(current);
  if (at === -1) return ids[0]!;
  return ids[Math.min(ids.length - 1, Math.max(0, at + by))]!;
}

/** Keyboard shortcuts never fire while someone is typing. */
export function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

/** The figures in a stored email body, split out so each can be marked (text, never HTML). */
export function marked(body: string, numbers: string[]): { text: string; mark: boolean }[] {
  const wanted = [...new Set(numbers.filter(Boolean))].sort((a, b) => b.length - a.length);
  if (wanted.length === 0) return [{ text: body, mark: false }];
  const escaped = wanted.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  // A figure stands alone: never the "2" inside "SHP-102027" or the "20" inside "₹120".
  const figure = new RegExp(`(?<![\\w.,])(${escaped.join("|")})(?![\\w]|[.,]\\d)`, "g");
  const out: { text: string; mark: boolean }[] = [];
  let last = 0;
  for (const m of body.matchAll(figure)) {
    if (m.index! > last) out.push({ text: body.slice(last, m.index), mark: false });
    out.push({ text: m[0], mark: true });
    last = m.index! + m[0].length;
  }
  if (last < body.length) out.push({ text: body.slice(last), mark: false });
  return out;
}

/** Scale three footprints (L x W, cm) into one plot, anchored bottom-left (design 3.2). */
export function footprints(
  boxes: { l: number; w: number }[],
  width = 200,
  height = 140,
): { w: number; h: number }[] {
  const maxL = Math.max(...boxes.map((b) => b.l), 1);
  const maxW = Math.max(...boxes.map((b) => b.w), 1);
  const scale = Math.min(width / maxL, height / maxW);
  return boxes.map((b) => ({ w: Math.round(b.l * scale), h: Math.round(b.w * scale) }));
}
