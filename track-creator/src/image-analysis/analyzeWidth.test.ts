import { describe, expect, it } from "vitest";
import { analyzeWidth } from "./analyzeWidth.ts";
import type { RasterSource, Rgb, WidthAnalysisOptions } from "./types.ts";
import { DEFAULT_WIDTH_OPTIONS } from "./types.ts";

/** Synthetic raster: road surface (grey) on a green background. */
class SyntheticRoadSource implements RasterSource {
  readonly id = "synthetic";
  readonly resolutionMetersPerPixel = 0.5;
  private roadHalfWidth: number;

  constructor(roadHalfWidth: number) {
    this.roadHalfWidth = roadHalfWidth;
  }

  sampleLocal(x: number, z: number): Rgb | null {
    // Road runs along x-axis, width is in z direction
    if (Math.abs(z) <= this.roadHalfWidth) {
      return { r: 80, g: 80, b: 85 }; // asphalt grey
    }
    return { r: 60, g: 120, b: 50 }; // green grass
  }

  containsLocal(_x: number, _z: number): boolean {
    return true;
  }
}

/**
 * Synthetic raster with painted edge lines: asphalt continues past the racing
 * surface (paved run-off) to `asphaltHalfWidth`, with a bright white line at
 * `lineOffset` on each side. Grass beyond the asphalt.
 */
class PaintedRoadSource implements RasterSource {
  readonly id = "synthetic-painted";
  readonly resolutionMetersPerPixel = 0.5;

  constructor(
    private lineOffset: number,
    private lineWidth: number,
    private asphaltHalfWidth: number,
  ) {}

  sampleLocal(_x: number, z: number): Rgb | null {
    const d = Math.abs(z);
    if (Math.abs(d - this.lineOffset) <= this.lineWidth / 2) {
      return { r: 225, g: 225, b: 228 }; // painted white line
    }
    if (d <= this.asphaltHalfWidth) {
      return { r: 80, g: 80, b: 85 }; // asphalt (racing surface + paved run-off)
    }
    return { r: 60, g: 120, b: 50 }; // grass
  }

  containsLocal(_x: number, _z: number): boolean {
    return true;
  }
}

const options: WidthAnalysisOptions = {
  ...DEFAULT_WIDTH_OPTIONS,
  step: 10,
  minOffset: 2,
  maxOffset: 15,
  candidateStep: 0.5,
  smoothness: 1.0,
  slopePenalty: 2.0,
  priorStrength: 0.1,
  confidenceThreshold: 0.5,
};

describe("analyzeWidth", () => {
  it("produces arrays of the correct length", () => {
    const source = new SyntheticRoadSource(7);
    const stations = Array.from({ length: 10 }, (_, i) => ({
      x: i * 10,
      z: 0,
      tx: 1,
      tz: 0,
      dist: i * 10,
    }));
    const currentLeft = new Array(10).fill(7);
    const currentRight = new Array(10).fill(7);

    const proposal = analyzeWidth({
      source,
      stations,
      closed: false,
      options,
      currentLeft,
      currentRight,
    });

    expect(proposal.left.length).toBe(10);
    expect(proposal.right.length).toBe(10);
    expect(proposal.confidenceLeft.length).toBe(10);
    expect(proposal.confidenceRight.length).toBe(10);
    expect(proposal.diagnostics.stationCount).toBe(10);
    expect(proposal.diagnostics.missingCount).toBe(0);
  });

  it("detects road edges near the correct half-width", () => {
    const halfWidth = 7;
    const source = new SyntheticRoadSource(halfWidth);
    const stations = Array.from({ length: 20 }, (_, i) => ({
      x: i * 10,
      z: 0,
      tx: 1,
      tz: 0,
      dist: i * 10,
    }));
    const currentLeft = new Array(20).fill(8);
    const currentRight = new Array(20).fill(8);

    const proposal = analyzeWidth({
      source,
      stations,
      closed: false,
      options,
      currentLeft,
      currentRight,
    });

    // The road edge should be near 7 m. Allow ±2 m tolerance.
    const avgLeft = proposal.left.reduce((a, b) => a + b, 0) / proposal.left.length;
    const avgRight = proposal.right.reduce((a, b) => a + b, 0) / proposal.right.length;
    expect(Math.abs(avgLeft - halfWidth)).toBeLessThan(2.5);
    expect(Math.abs(avgRight - halfWidth)).toBeLessThan(2.5);
  });

  it("stripeFirst snaps to the painted line; boundary mode keeps the asphalt edge", () => {
    // Racing surface line at 6 m, asphalt (paved run-off) out to 12 m.
    const source = new PaintedRoadSource(6, 0.5, 12);
    const stations = Array.from({ length: 20 }, (_, i) => ({
      x: i * 10,
      z: 0,
      tx: 1,
      tz: 0,
      dist: i * 10,
    }));
    const currentLeft = new Array(20).fill(7);
    const currentRight = new Array(20).fill(7);

    const boundary = analyzeWidth({
      source, stations, closed: false,
      options: { ...options, objective: "boundary" },
      currentLeft, currentRight,
    });
    const stripeFirst = analyzeWidth({
      source, stations, closed: false,
      options: { ...options, objective: "stripeFirst" },
      currentLeft, currentRight,
    });

    const avg = (a: number[]) => a.reduce((s, v) => s + v, 0) / a.length;
    // Boundary mode: paint is penalised, the asphalt/grass edge at 12 m wins.
    expect(avg(boundary.left)).toBeGreaterThan(9);
    expect(avg(boundary.right)).toBeGreaterThan(9);
    // Stripe-first: the painted line at 6 m wins on both sides.
    expect(Math.abs(avg(stripeFirst.left) - 6)).toBeLessThan(1.2);
    expect(Math.abs(avg(stripeFirst.right) - 6)).toBeLessThan(1.2);
  });

  it("stripeFirst falls back to the boundary where no line is painted", () => {
    // No painted line at all — both modes should agree on the asphalt edge.
    const source = new SyntheticRoadSource(9);
    const stations = Array.from({ length: 12 }, (_, i) => ({
      x: i * 10,
      z: 0,
      tx: 1,
      tz: 0,
      dist: i * 10,
    }));
    const currentLeft = new Array(12).fill(8);
    const currentRight = new Array(12).fill(8);

    const stripeFirst = analyzeWidth({
      source, stations, closed: false,
      options: { ...options, objective: "stripeFirst" },
      currentLeft, currentRight,
    });

    const avg = (a: number[]) => a.reduce((s, v) => s + v, 0) / a.length;
    expect(Math.abs(avg(stripeFirst.left) - 9)).toBeLessThan(2.5);
    expect(Math.abs(avg(stripeFirst.right) - 9)).toBeLessThan(2.5);
  });

  it("handles missing image data gracefully", () => {
    const source: RasterSource = {
      id: "empty",
      resolutionMetersPerPixel: 0.5,
      sampleLocal: () => null,
      containsLocal: () => false,
    };
    const stations = Array.from({ length: 5 }, (_, i) => ({
      x: i * 10,
      z: 0,
      tx: 1,
      tz: 0,
      dist: i * 10,
    }));

    const proposal = analyzeWidth({
      source,
      stations,
      closed: false,
      options,
      currentLeft: [5, 5, 5, 5, 5],
      currentRight: [5, 5, 5, 5, 5],
    });

    expect(proposal.diagnostics.missingCount).toBe(5);
    // Offsets should fall back to current values
    for (const off of proposal.left) expect(off).toBe(5);
    for (const off of proposal.right) expect(off).toBe(5);
    // Confidence should be zero for all missing stations
    for (const c of proposal.confidenceLeft) expect(c).toBe(0);
    for (const c of proposal.confidenceRight) expect(c).toBe(0);
  });
});
