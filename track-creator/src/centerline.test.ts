import { describe, expect, it } from "vitest";
import { resampleCenterline, interpByDist, wrapDist } from "./centerline.ts";

const square = [
  { x: 0, z: 0 },
  { x: 100, z: 0 },
  { x: 100, z: 100 },
  { x: 0, z: 100 },
];

describe("resampleCenterline", () => {
  it("produces near-uniform spacing on a closed loop, no duplicate end point", () => {
    const { points, totalLength } = resampleCenterline(square, 5, true);
    expect(totalLength).toBeGreaterThan(300); // a rounded square is > its chord loop… sanity
    const step = totalLength / points.length;
    for (let i = 1; i < points.length; i++) {
      const d = Math.hypot(points[i].x - points[i - 1].x, points[i].z - points[i - 1].z);
      expect(d).toBeGreaterThan(step * 0.8);
      expect(d).toBeLessThan(step * 1.2);
    }
    // Last station is one step before the start, not on top of it.
    const last = points[points.length - 1];
    const gap = Math.hypot(last.x - points[0].x, last.z - points[0].z);
    expect(gap).toBeGreaterThan(step * 0.5);
  });

  it("dist is monotonic and tangents are unit length", () => {
    const { points } = resampleCenterline(square, 5, true);
    for (let i = 0; i < points.length; i++) {
      if (i > 0) expect(points[i].dist).toBeGreaterThan(points[i - 1].dist);
      expect(Math.hypot(points[i].tx, points[i].tz)).toBeCloseTo(1, 6);
    }
  });

  it("open polyline starts and ends at the terminal control points", () => {
    const { points } = resampleCenterline(square, 5, false);
    expect(points[0].x).toBeCloseTo(0, 6);
    expect(points[0].z).toBeCloseTo(0, 6);
    const last = points[points.length - 1];
    expect(last.x).toBeCloseTo(0, 4);
    expect(last.z).toBeCloseTo(100, 4);
  });
});

describe("interpByDist", () => {
  const dists = [0, 10, 20];
  const values = [0, 100, 50];

  it("interpolates linearly inside the range", () => {
    expect(interpByDist(dists, values, 5, 30, false)).toBeCloseTo(50);
    expect(interpByDist(dists, values, 15, 30, false)).toBeCloseTo(75);
  });

  it("clamps on open tracks, wraps on closed tracks", () => {
    expect(interpByDist(dists, values, 25, 30, false)).toBeCloseTo(50);
    // Closed: between last key (dist 20, v 50) and first key (dist 0≡30, v 0).
    expect(interpByDist(dists, values, 25, 30, true)).toBeCloseTo(25);
    expect(interpByDist(dists, values, -5, 30, true)).toBeCloseTo(25); // -5 ≡ 25
  });
});

describe("wrapDist", () => {
  it("wraps negatives and overshoot into [0, total)", () => {
    expect(wrapDist(-3, 10)).toBeCloseTo(7);
    expect(wrapDist(13, 10)).toBeCloseTo(3);
    expect(wrapDist(10, 10)).toBeCloseTo(0);
  });
});
