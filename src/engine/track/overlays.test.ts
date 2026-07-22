import { describe, expect, it } from "vitest";
import { DEFAULT_CURB_WIDTH, DEFAULT_STRIPE_LENGTH, parseCurbSpan, parseOverlays } from "./overlays";

describe("parseCurbSpan", () => {
  it("accepts a well-formed span", () => {
    const span = parseCurbSpan({ startDist: 10, endDist: 40, side: "right", width: 1.5, stripeLength: 3 });
    expect(span).toEqual({ startDist: 10, endDist: 40, side: "right", width: 1.5, stripeLength: 3 });
  });

  it("fills defaults for missing width / stripeLength", () => {
    const span = parseCurbSpan({ startDist: 0, endDist: 20, side: "left" });
    expect(span?.width).toBe(DEFAULT_CURB_WIDTH);
    expect(span?.stripeLength).toBe(DEFAULT_STRIPE_LENGTH);
  });

  it("ignores unknown keys (e.g. _label authoring comments)", () => {
    const span = parseCurbSpan({ _label: "T1 apex", startDist: 5, endDist: 15, side: "right" });
    expect(span).not.toBeNull();
    expect(span?.startDist).toBe(5);
  });

  it("rejects malformed spans", () => {
    expect(parseCurbSpan(null)).toBeNull();
    expect(parseCurbSpan("nope")).toBeNull();
    expect(parseCurbSpan({ startDist: 40, endDist: 10, side: "right" })).toBeNull(); // end <= start
    expect(parseCurbSpan({ startDist: 0, endDist: 10, side: "middle" })).toBeNull(); // bad side
    expect(parseCurbSpan({ startDist: "x", endDist: 10, side: "left" })).toBeNull(); // non-numeric
  });
});

describe("parseOverlays", () => {
  it("parses a valid payload and keeps only valid curbs", () => {
    const data = parseOverlays({
      version: 1,
      curbs: [
        { startDist: 850, endDist: 950, side: "right", width: 1.5, stripeLength: 2.5 },
        { startDist: 950, endDist: 800, side: "right" }, // invalid, dropped
        { startDist: 40, endDist: 110, side: "left" },
      ],
    });
    expect(data.version).toBe(1);
    expect(data.curbs).toHaveLength(2);
    expect(data.curbs[0].side).toBe("right");
    expect(data.curbs[1].width).toBe(DEFAULT_CURB_WIDTH);
  });

  it("degrades to an empty curb list for malformed input (never throws)", () => {
    expect(parseOverlays(null).curbs).toEqual([]);
    expect(parseOverlays({}).curbs).toEqual([]);
    expect(parseOverlays({ curbs: "nope" }).curbs).toEqual([]);
  });
});
