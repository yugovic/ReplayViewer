import { describe, expect, it } from "vitest";
import {
  buildSectorBoundaries,
  computeDeltaAtDist,
  computeSectorTimes,
  getSectorIndex,
  ghostTimeForMainTime,
  playbackTimeAtTrueAxis,
  playbackTimeAtTrueDist,
  sampleReplayWithLeadIn,
  timeAtDist,
  trueAxisOffset,
  trueTimeAtTrueDist,
} from "./delta";
import { interpolateNumberSeries, sampleReplay } from "./interpolation";
import { lapStartOffsetMeters } from "./lapClock";
import type { LapData, TrackData } from "./types";

/** Minimal LapData factory for tests (`delta` = first_sample_after_lap_start_seconds) */
function makeLap(opts: { t: number[]; dist: number[]; delta?: number }): LapData {
  const n = opts.t.length;
  return {
    meta: {
      ...(opts.delta === undefined ? {} : { first_sample_after_lap_start_seconds: opts.delta }),
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

/**
 * Records a lap the way pipeline/build_race.py does: 10 Hz samples starting
 * `delta` seconds after the timing line, t and dist counted from the first
 * sample. The physical motion is: 20 m/s for the first 100 m, then 10 m/s.
 */
function recordLap(delta: number, slowSpeed = 10): LapData {
  const fastEnd = 100 / 20; // 5 s
  const distAtTrueTime = (T: number) => (T <= fastEnd ? 20 * T : 100 + slowSpeed * (T - fastEnd));
  const t: number[] = [];
  const dist: number[] = [];
  const s0 = distAtTrueTime(delta);
  for (let k = 0; k <= 100; k += 1) {
    const T = delta + k / 10;
    t.push(k / 10);
    dist.push(distAtTrueTime(T) - s0);
  }
  return makeLap({ t, dist, delta });
}

describe("true lap basis (first_sample_after_lap_start_seconds)", () => {
  it("leaves results unchanged when δ is missing", () => {
    const main = makeLap({ t: [0, 20], dist: [0, 200] });
    const ghost = makeLap({ t: [0, 10], dist: [0, 200] });
    expect(computeDeltaAtDist(main, ghost, 100)).toBeCloseTo(5, 12);
    expect(ghostTimeForMainTime(main, ghost, 5)).toBeCloseTo(2.5, 12);
    expect(trueTimeAtTrueDist(main, 100)).toBeCloseTo(timeAtDist(main, 100), 12);
  });

  it("gives zero delta for the same physical lap recorded with different δ", () => {
    // Same motion, timing-line phase 80 ms vs 20 ms. The old raw-t/raw-dist
    // delta was (s0m − s0g)/v − (δm − δg) = 1.2/10 − 0.06 = +0.06 s in the slow part.
    const main = recordLap(0.08);
    const ghost = recordLap(0.02);
    for (const d of [0, 10, 50, 120, 140]) {
      expect(Math.abs(computeDeltaAtDist(main, ghost, d))).toBeLessThan(1e-9);
    }
    // Around the 100 m speed change the 10 Hz linear interpolation of the two
    // differently phased recordings disagrees by a few ms, never by δ-sized amounts.
    for (const d of [97, 99, 101]) {
      expect(Math.abs(computeDeltaAtDist(main, ghost, d))).toBeLessThan(0.005);
    }
    const legacySlow = timeAtDist(main, 140) - timeAtDist(ghost, 140);
    expect(legacySlow).toBeCloseTo(0.06, 9);
  });

  it("measures a real time loss in true lap time", () => {
    // Ghost is identical to main until 100 m, then 20 % faster (12 m/s vs 10 m/s).
    const main = recordLap(0.08, 10);
    const ghost = recordLap(0.02, 12);
    // At true distance S = 140 m: main 5 + 40/10 s, ghost 5 + 40/12 s → +2/3 s
    const mainRecordedDist = 140 - 20 * 0.08;
    expect(computeDeltaAtDist(main, ghost, mainRecordedDist)).toBeCloseTo(2 / 3, 9);
  });

  it("interpolates the true time between the timing line and the first sample", () => {
    const lap = recordLap(0.08); // s0 = 1.6 m, δ = 0.08 s
    expect(trueTimeAtTrueDist(lap, 0)).toBeCloseTo(0, 12);
    expect(trueTimeAtTrueDist(lap, 0.8)).toBeCloseTo(0.04, 12);
    expect(trueTimeAtTrueDist(lap, 1.6)).toBeCloseTo(0.08, 12);
    expect(trueTimeAtTrueDist(lap, 1.6 + 2)).toBeCloseTo(0.18, 12);
  });

  it("places the ghost at the same physical point", () => {
    const main = recordLap(0.08);
    const ghost = recordLap(0.02);
    // Same motion: the ghost reaches main's position at the same TRUE time,
    // i.e. ghost t = main t + δm − δg.
    for (const mainTime of [0.5, 3, 7.25]) {
      expect(ghostTimeForMainTime(main, ghost, mainTime)).toBeCloseTo(mainTime + 0.06, 9);
    }
  });

  it("starts sector 1 at the timing line, δ before the first sample", () => {
    const lap = makeLap({ t: [0, 10, 20, 30], dist: [0, 100, 200, 300], delta: 0.05 });
    const boundaries = buildSectorBoundaries(300, 3);
    const done = computeSectorTimes(lap, boundaries, 300);
    expect(done[0]).toBeCloseTo(10.05, 12);
    expect(done[1]).toBeCloseTo(10, 12);
    expect(done[2]).toBeCloseTo(10, 12);
    // Sector sum = true lap time of the last sample (t_end + δ)
    expect((done[0] ?? 0) + (done[1] ?? 0) + (done[2] ?? 0)).toBeCloseTo(30.05, 12);
    expect(computeSectorTimes(lap, boundaries, 0)[0]).toBeCloseTo(0.05, 12);
  });
});

const METERS_PER_DEGREE = 111_320;

/** Straight north-bound test track (north = −z), origin at lat/lng 0. */
function straightTrack(length = 2000): TrackData {
  const centerline = [0, length / 2, length].map((d) => ({ lat: 0, lng: 0, alt: 0, dist: d, x: 0, y: 0, z: -d }));
  return {
    version: 1,
    trackId: "test",
    trackName: "Test",
    width: 10,
    totalLength: length,
    origin: { lat: 0, lng: 0, alt: 0 },
    bounds: { lat: [0, 0], lng: [0, 0], x: [0, 0], z: [-length, 0] },
    centerline,
  };
}

/**
 * A lap on the straight track recorded like pipeline/build_race.py: 10 Hz
 * samples from δ after the timing line (at z = 0), t/dist from the first
 * sample. Motion: v0 m/s plus constant acceleration `accel` (m/s²).
 */
function straightLap(delta: number, v0: number, accel = 0, samples = 40): LapData {
  const s = (T: number) => v0 * T + 0.5 * accel * T * T;
  const s0 = s(delta);
  const t: number[] = [];
  const trueS: number[] = [];
  for (let k = 0; k < samples; k += 1) {
    t.push(k / 10);
    trueS.push(s(delta + k / 10));
  }
  const lap = makeLap({ t, dist: trueS.map((v) => v - s0), delta });
  lap.lat = trueS.map((v) => v / METERS_PER_DEGREE);
  lap.lng = trueS.map(() => 0);
  return lap;
}

describe("ghost at the lap start (before the ghost's first sample)", () => {
  // Worst Fuji pair: main δ = 7 ms, ghost δ = 97 ms on the main straight
  // (~47 m/s): the ghost's first sample is 4.26 m further past the line.
  const v = 47.4;
  const main = straightLap(0.007, v);
  const ghost = straightLap(0.097, v);
  const track = straightTrack();

  it("continues the ghost time before its first sample instead of holding it", () => {
    // Same motion: ghost t = main t + δm − δg at every main time, also at the start.
    for (const mainTime of [0, 0.03, 0.05, 0.09, 0.1, 0.5]) {
      expect(ghostTimeForMainTime(main, ghost, mainTime)).toBeCloseTo(mainTime - 0.09, 9);
    }
    // No plateau / step: monotone with the main clock through the ghost's first sample.
    let previous = ghostTimeForMainTime(main, ghost, 0);
    for (let k = 1; k <= 300; k += 1) {
      const next = ghostTimeForMainTime(main, ghost, k / 1000);
      expect(next - previous).toBeGreaterThan(0.0009);
      expect(next - previous).toBeLessThan(0.0011);
      previous = next;
    }
  });

  it("places the ghost at the main car's point from the very first sample (was 4.26 m ahead)", () => {
    let maxGap = 0;
    let maxStep = 0;
    let prevGhostZ: number | null = null;
    for (let k = 0; k <= 300; k += 1) {
      const mainTime = k / 1000;
      const m = sampleReplay(main, track, mainTime);
      const g = sampleReplayWithLeadIn(ghost, track, ghostTimeForMainTime(main, ghost, mainTime));
      maxGap = Math.max(maxGap, Math.hypot(g.x - m.x, g.z - m.z));
      if (prevGhostZ !== null) maxStep = Math.max(maxStep, Math.abs(g.z - prevGhostZ));
      prevGhostZ = g.z;
    }
    expect(maxGap).toBeLessThan(0.01);
    // 1 ms frames at 47.4 m/s move 4.7 cm; a jump would be metres.
    expect(maxStep).toBeLessThan(0.06);
    // The former clamp: ghost parked at its first sample, s0g − s0m ahead.
    const clamped = sampleReplay(ghost, track, ghostTimeForMainTime(main, ghost, 0));
    const atStart = sampleReplay(main, track, 0);
    expect(Math.hypot(clamped.x - atStart.x, clamped.z - atStart.z)).toBeCloseTo(v * 0.09, 2);
  });

  it("stays within a few cm when the car accelerates through the lead-in", () => {
    const m = straightLap(0.007, 40, 3);
    const g = straightLap(0.097, 40, 3);
    for (const mainTime of [0, 0.02, 0.06, 0.12]) {
      const a = sampleReplay(m, track, mainTime);
      const b = sampleReplayWithLeadIn(g, track, ghostTimeForMainTime(m, g, mainTime));
      expect(Math.hypot(b.x - a.x, b.z - a.z)).toBeLessThan(0.03);
    }
  });

  it("is continuous through t[0], identical to sampleReplay inside the data, and bounded by the line", () => {
    const at0 = sampleReplay(ghost, track, 0);
    const before = sampleReplayWithLeadIn(ghost, track, -1e-6);
    expect(Math.hypot(before.x - at0.x, before.z - at0.z)).toBeLessThan(1e-3);
    expect(before.heading).toBeCloseTo(at0.heading, 12);
    expect(before.telemetry.dist).toBeLessThan(at0.telemetry.dist);
    for (const time of [0, 0.05, 1.234, 99]) {
      expect(sampleReplayWithLeadIn(ghost, track, time)).toEqual(sampleReplay(ghost, track, time));
    }
    // The lead-in reaches back at most to the timing line (−δ, z = 0).
    expect(playbackTimeAtTrueDist(ghost, 0)).toBeCloseTo(-0.097, 12);
    expect(playbackTimeAtTrueDist(ghost, -5)).toBeCloseTo(-0.097, 12);
    const atLine = sampleReplayWithLeadIn(ghost, track, playbackTimeAtTrueDist(ghost, 0));
    expect(atLine.z).toBeCloseTo(0, 6);
    // Without δ nothing changes: the ghost time is clamped at its first sample.
    const noDelta = makeLap({ t: [0, 1, 2], dist: [0, 10, 20] });
    expect(ghostTimeForMainTime(noDelta, noDelta, 0)).toBe(0);
  });

  it("agrees with the HUD's true-time model: playback time + δ = true time", () => {
    const s0 = lapStartOffsetMeters(ghost);
    for (const S of [0, s0 / 2, s0, s0 + 1, 100]) {
      expect(playbackTimeAtTrueDist(ghost, S) + 0.097).toBeCloseTo(trueTimeAtTrueDist(ghost, S), 12);
    }
  });
});

describe("telemetry graph axis on the true lap basis", () => {
  const main = recordLap(0.08);
  const ghost = recordLap(0.02);

  it("offsets the recorded axes by δ (time) and s0 (distance)", () => {
    expect(trueAxisOffset(main, "time")).toBeCloseTo(0.08, 12);
    expect(trueAxisOffset(main, "dist")).toBeCloseTo(1.6, 12);
    const noDelta = makeLap({ t: [0, 1], dist: [0, 10] });
    expect(trueAxisOffset(noDelta, "time")).toBe(0);
    expect(trueAxisOffset(noDelta, "dist")).toBe(0);
  });

  it("maps every main sample back to its own playback time (seek round trip)", () => {
    for (const axis of ["time", "dist"] as const) {
      const offset = trueAxisOffset(main, axis);
      const recorded = axis === "time" ? main.t : main.dist;
      for (const i of [0, 1, 37, 100]) {
        expect(playbackTimeAtTrueAxis(main, axis, recorded[i] + offset)).toBeCloseTo(main.t[i], 9);
      }
    }
  });

  it("reads the ghost at the same true lap time / true distance as the main sample", () => {
    // Same motion recorded with different δ: at a common true-axis value both
    // laps are at the same true distance (the raw t / raw dist axes were not).
    const trueDistAt = (lap: LapData, time: number) =>
      interpolateNumberSeries(lap.t, lap.dist, time) + lapStartOffsetMeters(lap);
    for (const axis of ["time", "dist"] as const) {
      const offset = trueAxisOffset(main, axis);
      for (const i of [3, 20, 60, 90]) {
        const x = (axis === "time" ? main.t[i] : main.dist[i]) + offset;
        const mainT = playbackTimeAtTrueAxis(main, axis, x);
        const ghostT = playbackTimeAtTrueAxis(ghost, axis, x);
        expect(ghostT).toBeCloseTo(mainT + 0.06, 9);
        expect(trueDistAt(ghost, ghostT)).toBeCloseTo(trueDistAt(main, mainT), 9);
      }
    }
    // The old time axis read the ghost at the main's raw t: 60 ms early in true time.
    expect(trueDistAt(ghost, main.t[60]) - trueDistAt(main, main.t[60])).toBeCloseTo(-0.6, 9);
  });
});
