import { describe, expect, it } from "vitest";
import {
  blendHighConfidenceOffsets,
  interpolateAcrossLowConfidence,
} from "./proposalBlend.ts";

function maxAdjacentDelta(values: number[], closed = false): number {
  let max = 0;
  const end = closed ? values.length : values.length - 1;
  for (let i = 0; i < end; i++) {
    max = Math.max(max, Math.abs(values[(i + 1) % values.length] - values[i]));
  }
  return max;
}

describe("blendHighConfidenceOffsets", () => {
  it("does not turn alternating confidence into a saw-tooth edge", () => {
    const n = 100;
    const values = blendHighConfidenceOffsets(
      new Array(n).fill(6.5),
      new Array(n).fill(19),
      Array.from({ length: n }, (_, i) => i % 2 === 0 ? 0.9 : 0.1),
      { threshold: 0.6, step: 2, closed: true },
    );

    expect(maxAdjacentDelta(values, true)).toBeLessThan(0.1);
  });

  it("keeps broad rejected spans unchanged and broad accepted spans applied", () => {
    const n = 200;
    const confidence = new Array(n).fill(0.1);
    for (let i = 60; i < 140; i++) confidence[i] = 0.9;
    const values = blendHighConfidenceOffsets(
      new Array(n).fill(6.5),
      new Array(n).fill(12),
      confidence,
      { threshold: 0.6, step: 2, closed: false },
    );

    expect(values[10]).toBeCloseTo(6.5, 8);
    expect(values[100]).toBeCloseTo(12, 8);
    expect(maxAdjacentDelta(values)).toBeLessThanOrEqual(0.5);
  });

  it("suppresses an isolated accepted sample", () => {
    const confidence = new Array(41).fill(0.1);
    confidence[20] = 0.95;
    const values = blendHighConfidenceOffsets(
      new Array(41).fill(7),
      new Array(41).fill(20),
      confidence,
      { threshold: 0.6, step: 2, closed: false },
    );

    expect(values).toEqual(new Array(41).fill(7));
  });

  it("limits rapid changes in the accepted proposal, including the closed seam", () => {
    const proposed = new Array(80).fill(7);
    proposed[0] = 20;
    proposed[1] = 16;
    proposed[79] = 18;
    const values = blendHighConfidenceOffsets(
      new Array(80).fill(7),
      proposed,
      new Array(80).fill(0.95),
      { threshold: 0.6, step: 2, closed: true },
    );

    expect(maxAdjacentDelta(values, true)).toBeLessThanOrEqual(0.5);
  });

  it("rejects mismatched arrays", () => {
    expect(() => blendHighConfidenceOffsets(
      [6],
      [7, 8],
      [0.9],
      { threshold: 0.6, step: 2, closed: false },
    )).toThrow(/matching lengths/);
  });
});

describe("interpolateAcrossLowConfidence", () => {
  it("bridges an interior low-confidence run linearly", () => {
    const proposed = [10, 10, 99, 99, 99, 16, 16];
    const confidence = [0.9, 0.9, 0.1, 0.1, 0.1, 0.9, 0.9];
    const out = interpolateAcrossLowConfidence(proposed, confidence, 0.6, false);

    expect(out[1]).toBe(10);
    expect(out[2]).toBeCloseTo(11.5, 8);
    expect(out[3]).toBeCloseTo(13, 8);
    expect(out[4]).toBeCloseTo(14.5, 8);
    expect(out[5]).toBe(16);
  });

  it("interpolates across the closed-loop seam instead of stepping at it", () => {
    // Accepted values 10 (index 2) and 14 (index 5); the low-confidence run
    // wraps 6,7,0,1 across the seam.
    const proposed = [99, 99, 10, 10, 14, 14, 99, 99];
    const confidence = [0.1, 0.1, 0.9, 0.9, 0.9, 0.9, 0.1, 0.1];
    const out = interpolateAcrossLowConfidence(proposed, confidence, 0.6, true);

    // Linear ramp 14 → 10 over the wrapped run (4 gaps + both ends = 5 steps).
    expect(out[6]).toBeCloseTo(14 - (14 - 10) * (1 / 5), 8);
    expect(out[7]).toBeCloseTo(14 - (14 - 10) * (2 / 5), 8);
    expect(out[0]).toBeCloseTo(14 - (14 - 10) * (3 / 5), 8);
    expect(out[1]).toBeCloseTo(14 - (14 - 10) * (4 / 5), 8);
    expect(out[2]).toBe(10);
  });

  it("extends flat from the single accepted neighbour on an open end", () => {
    const proposed = [99, 99, 12, 12];
    const confidence = [0.1, 0.1, 0.9, 0.9];
    const out = interpolateAcrossLowConfidence(proposed, confidence, 0.6, false);

    expect(out[0]).toBe(12);
    expect(out[1]).toBe(12);
    expect(out[2]).toBe(12);
  });

  it("returns the proposal unchanged when everything is accepted or nothing is", () => {
    const proposed = [8, 9, 10];
    expect(interpolateAcrossLowConfidence(proposed, [0.9, 0.9, 0.9], 0.6, true)).toEqual(proposed);
    expect(interpolateAcrossLowConfidence(proposed, [0.1, 0.1, 0.1], 0.6, true)).toEqual(proposed);
  });

  it("rejects mismatched arrays", () => {
    expect(() => interpolateAcrossLowConfidence([1, 2], [0.9], 0.6, false))
      .toThrow(/matching lengths/);
  });
});
