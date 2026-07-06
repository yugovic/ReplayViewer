import { describe, expect, it } from "vitest";
import {
  buildSectorBoundaries,
  computeDeltaAtDist,
  computeSectorTimes,
  getSectorIndex,
  ghostTimeForMainTime,
  timeAtDist,
} from "./delta";
import type { LapData } from "./types";

/** Minimal LapData factory for tests */
function makeLap(opts: { t: number[]; dist: number[] }): LapData {
  const n = opts.t.length;
  return {
    meta: {
      version: 1,
      track_id: "test",
      race_id: "test",
      vehicle_id: "test",
      vehicle_number: "0",
      lap: 1,
      lap_time_seconds: opts.t[n - 1],
      lap_time: null,
      point_count: n,
      raw_gps_points: n,
      gps_outliers_removed: 0,
      total_distance_m: opts.dist[n - 1],
      source_file: "test",
      time_column: "t",
      first_sample_time: "0",
      last_sample_time: String(opts.t[n - 1]),
    },
    t: opts.t,
    dist: opts.dist,
    lat: Array(n).fill(0),
    lng: Array(n).fill(0),
    speed: Array(n).fill(0),
    aps: Array(n).fill(0),
    brake: Array(n).fill(0),
    steer: Array(n).fill(0),
    gear: Array(n).fill(1),
    accx: Array(n).fill(0),
    accy: Array(n).fill(0),
  };
}

describe("timeAtDist", () => {
  it("returns start time when dist is 0", () => {
    const lap = makeLap({ t: [0, 10, 20], dist: [0, 100, 200] });
    expect(timeAtDist(lap, 0)).toBe(0);
  });

  it("interpolates correctly at midpoint", () => {
    const lap = makeLap({ t: [0, 10, 20], dist: [0, 100, 200] });
    expect(timeAtDist(lap, 50)).toBeCloseTo(5);
    expect(timeAtDist(lap, 150)).toBeCloseTo(15);
  });

  it("clamps to end when dist exceeds total", () => {
    const lap = makeLap({ t: [0, 10, 20], dist: [0, 100, 200] });
    expect(timeAtDist(lap, 999)).toBe(20);
  });

  it("handles single-point lap", () => {
    const lap = makeLap({ t: [5], dist: [0] });
    expect(timeAtDist(lap, 0)).toBe(5);
    expect(timeAtDist(lap, 100)).toBe(5);
  });
});

describe("computeDeltaAtDist", () => {
  it("returns 0 when both laps have identical timing", () => {
    const lap = makeLap({ t: [0, 10, 20], dist: [0, 100, 200] });
    expect(computeDeltaAtDist(lap, lap, 100)).toBeCloseTo(0);
  });

  it("positive delta when main is slower than ghost", () => {
    // main takes 20s for 200m; ghost takes 10s for 200m
    const main = makeLap({ t: [0, 20], dist: [0, 200] });
    const ghost = makeLap({ t: [0, 10], dist: [0, 200] });
    // At dist=100: main reaches at t=10, ghost at t=5 => delta=+5
    expect(computeDeltaAtDist(main, ghost, 100)).toBeCloseTo(5);
  });

  it("negative delta when main is faster than ghost", () => {
    const main = makeLap({ t: [0, 10], dist: [0, 200] });
    const ghost = makeLap({ t: [0, 20], dist: [0, 200] });
    expect(computeDeltaAtDist(main, ghost, 100)).toBeCloseTo(-5);
  });
});

describe("ghostTimeForMainTime", () => {
  it("maps main time to ghost time through shared distance", () => {
    // main: 200m in 10s → 20m/s
    const main = makeLap({ t: [0, 10], dist: [0, 200] });
    // ghost: 200m in 20s → 10m/s
    const ghost = makeLap({ t: [0, 20], dist: [0, 200] });
    // At main t=5, dist=100; ghost reaches dist=100 at t=10
    expect(ghostTimeForMainTime(main, ghost, 5)).toBeCloseTo(10);
  });
});

describe("buildSectorBoundaries", () => {
  it("creates equal-length sectors", () => {
    const b = buildSectorBoundaries(300, 3);
    expect(b).toEqual([0, 100, 200, 300]);
  });

  it("works with 2 sectors", () => {
    const b = buildSectorBoundaries(1000, 2);
    expect(b).toEqual([0, 500, 1000]);
  });
});

describe("getSectorIndex", () => {
  it("places distance in correct sector", () => {
    const b = buildSectorBoundaries(300, 3); // [0,100,200,300]
    expect(getSectorIndex(0, b)).toBe(0);
    expect(getSectorIndex(50, b)).toBe(0);
    expect(getSectorIndex(100, b)).toBe(1);
    expect(getSectorIndex(200, b)).toBe(2);
    expect(getSectorIndex(299, b)).toBe(2);
  });
});

describe("computeSectorTimes", () => {
  it("returns elapsed times for completed sectors", () => {
    // 300m in 30s, uniform pace
    const lap = makeLap({ t: [0, 10, 20, 30], dist: [0, 100, 200, 300] });
    const boundaries = buildSectorBoundaries(300, 3);
    const times = computeSectorTimes(lap, boundaries, 300);
    expect(times[0]).toBeCloseTo(10); // S1 ends at dist=100 → t=10
    expect(times[1]).toBeCloseTo(10); // S2: dist 100→200 = 10s elapsed in sector
    expect(times[2]).toBeCloseTo(10);
  });

  it("returns undefined for sectors not yet reached", () => {
    const lap = makeLap({ t: [0, 10, 20, 30], dist: [0, 100, 200, 300] });
    const boundaries = buildSectorBoundaries(300, 3);
    const times = computeSectorTimes(lap, boundaries, 50);
    // Only sector 0 is in-progress
    expect(times[0]).toBeDefined();
    expect(times[1]).toBeUndefined();
    expect(times[2]).toBeUndefined();
  });
});
