import { describe, expect, it } from "vitest";
import {
  effectiveMeshGapRange,
  observationWeight,
  presetRangeForRelation,
} from "../preview/scanAnchorAudit.ts";

describe("scan anchor audit", () => {
  it("maps video curb relations to signed gap intervals", () => {
    expect(presetRangeForRelation("graze")).toEqual({ minM: -0.05, maxM: 0.15 });
    expect(presetRangeForRelation("on-curb")).toEqual({ minM: -0.35, maxM: -0.05 });
    expect(presetRangeForRelation("unreviewed")).toBeNull();
  });

  it("translates a video-boundary range into the collision-edge frame", () => {
    const shifted = effectiveMeshGapRange(-0.05, 0.15, 1.2);
    expect(shifted.minM).toBeCloseTo(1.15);
    expect(shifted.maxM).toBeCloseTo(1.35);
    const normalized = effectiveMeshGapRange(0.5, 0.2, 0.1);
    expect(normalized.minM).toBeCloseTo(0.3);
    expect(normalized.maxM).toBeCloseTo(0.6);
  });

  it("down-weights uncertain video observations", () => {
    expect(observationWeight("high")).toBe(1);
    expect(observationWeight("medium")).toBe(0.5);
    expect(observationWeight("low")).toBe(0.2);
  });
});
