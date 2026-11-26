import { describe, expect, it } from "vitest";
import { describeFailure } from "./api";
import { clock, footprints, inView, marked, step } from "./lib";
import type { WorkItem } from "./types";

const item = (id: string, views: string[], seller = "ThreadLane Apparel"): WorkItem =>
  ({
    case_id: id,
    shipment_ref: `SHP-${id}`,
    seller,
    status: "Awaiting approval",
    tag: "Approve credit",
    views,
  }) as WorkItem;

describe("the case list", () => {
  it("shows a view's cases, or every match while searching", () => {
    const items = [item("1", ["needs", "credits"]), item("2", ["hub"], "CaseKraft")];
    expect(inView(items, "credits", "").map((i) => i.case_id)).toEqual(["1"]);
    expect(inView(items, "credits", "casekraft").map((i) => i.case_id)).toEqual(["2"]);
  });

  it("moves J/K through the list and stops at the ends", () => {
    expect(step(["a", "b", "c"], "b", 1)).toBe("c");
    expect(step(["a", "b", "c"], "c", 1)).toBe("c");
    expect(step(["a", "b", "c"], "a", -1)).toBe("a");
    expect(step(["a", "b"], "gone", 1)).toBe("a");
    expect(step([], null, 1)).toBeNull();
  });
});

describe("what the reviewer reads", () => {
  it("shows times in India Standard Time", () => {
    expect(clock("2026-11-26T21:30:00Z")).toBe("03:00");
  });

  it("marks each figure in an email as text, never as markup", () => {
    const parts = marked("You were charged ₹114.00. A credit of ₹38.00 <b>", ["₹114.00", "₹38.00"]);
    expect(parts.filter((p) => p.mark).map((p) => p.text)).toEqual(["₹114.00", "₹38.00"]);
    expect(parts.map((p) => p.text).join("")).toContain("<b>");
  });

  it("marks a figure only where it stands alone", () => {
    const parts = marked("Shipment SHP-102027: 2 slabs, ₹120.00 and 20 cm", ["2", "20 cm"]);
    expect(parts.filter((p) => p.mark).map((p) => p.text)).toEqual(["2", "20 cm"]);
    expect(parts.map((p) => p.text).join("")).toBe("Shipment SHP-102027: 2 slabs, ₹120.00 and 20 cm");
  });

  it("draws footprints to one scale", () => {
    const [a, b] = footprints([{ l: 34, w: 22 }, { l: 17, w: 11 }]);
    expect(a!.w).toBe(2 * b!.w);
  });

  it("never shows a proxy's HTML page as an error", () => {
    expect(describeFailure(502, null)).toBe("The dispute service had a problem. Try again in a moment.");
    expect(describeFailure(409, { detail: "Already approved by owner." })).toBe("Already approved by owner.");
  });
});
