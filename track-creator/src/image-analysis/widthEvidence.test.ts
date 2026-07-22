import { describe, expect, it } from "vitest";
import { effectiveMaxOffset } from "./widthEvidence.ts";
import { DEFAULT_WIDTH_OPTIONS } from "./types.ts";
import type { WidthAnalysisOptions } from "./types.ts";

const base: WidthAnalysisOptions = { ...DEFAULT_WIDTH_OPTIONS, minOffset: 3, maxOffset: 20 };

describe("effectiveMaxOffset", () => {
  it("returns the global max without corridors", () => {
    expect(effectiveMaxOffset(base, 1000, "left")).toBe(20);
  });

  it("caps only the matching side inside the range", () => {
    const options = {
      ...base,
      corridors: [{ startDist: 100, endDist: 300, side: "left" as const, maxOffset: 9 }],
    };
    expect(effectiveMaxOffset(options, 200, "left")).toBe(9);
    expect(effectiveMaxOffset(options, 200, "right")).toBe(20);
    expect(effectiveMaxOffset(options, 400, "left")).toBe(20);
    expect(effectiveMaxOffset(options, 100, "left")).toBe(9);
    expect(effectiveMaxOffset(options, 300, "left")).toBe(9);
  });

  it("handles a wrapping range on closed tracks (startDist > endDist)", () => {
    const options = {
      ...base,
      corridors: [{ startDist: 4224, endDist: 598, side: "left" as const, maxOffset: 9 }],
    };
    expect(effectiveMaxOffset(options, 4400, "left")).toBe(9);   // after start
    expect(effectiveMaxOffset(options, 100, "left")).toBe(9);    // wrapped, before end
    expect(effectiveMaxOffset(options, 2000, "left")).toBe(20);  // outside
  });

  it("applies the tightest of overlapping corridors and never dips below minOffset", () => {
    const options = {
      ...base,
      corridors: [
        { startDist: 0, endDist: 500, side: "right" as const, maxOffset: 12 },
        { startDist: 200, endDist: 400, side: "right" as const, maxOffset: 1 },
      ],
    };
    expect(effectiveMaxOffset(options, 100, "right")).toBe(12);
    expect(effectiveMaxOffset(options, 300, "right")).toBe(3); // clamped to minOffset
  });
});
