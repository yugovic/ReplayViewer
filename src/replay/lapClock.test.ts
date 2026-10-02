import { describe, expect, it } from "vitest";
import {
  formatLapClock,
  formatTransportClock,
  lapStartOffsetMeters,
  lapStartOffsetSeconds,
  trueLapTime,
} from "./lapClock";
import type { LapData, LapMeta } from "./types";

function meta(delta?: unknown): LapMeta {
  const base = {
    version: 1,
    track_id: "test",
    race_id: "test",
    vehicle_id: "test",
    vehicle_number: "0",
    lap: 1,
    lap_time_seconds: 10,
    lap_time: null,
    point_count: 3,
    raw_gps_points: 3,
    gps_outliers_removed: 0,
    total_distance_m: 200,
    source_file: "test",
    time_column: "t",
    first_sample_time: "0",
    last_sample_time: "10",
  };
  return (delta === undefined ? base : { ...base, first_sample_after_lap_start_seconds: delta }) as LapMeta;
}

function lap(delta: unknown, t: number[], dist: number[], speed: number[] = t.map(() => 0)) {
  return { meta: meta(delta), t, dist, speed } as Pick<LapData, "meta" | "t" | "dist" | "speed">;
}

describe("lapStartOffsetSeconds", () => {
  it("reads first_sample_after_lap_start_seconds", () => {
    expect(lapStartOffsetSeconds(meta(0.097))).toBe(0.097);
    expect(lapStartOffsetSeconds(meta(0))).toBe(0);
  });

  it("treats missing, non-numeric and implausible values as 0", () => {
    expect(lapStartOffsetSeconds(meta())).toBe(0);
    expect(lapStartOffsetSeconds(null)).toBe(0);
    expect(lapStartOffsetSeconds(undefined)).toBe(0);
    expect(lapStartOffsetSeconds(meta(null))).toBe(0);
    expect(lapStartOffsetSeconds(meta("0.05"))).toBe(0);
    expect(lapStartOffsetSeconds(meta(Number.NaN))).toBe(0);
    expect(lapStartOffsetSeconds(meta(-0.02))).toBe(0);
    expect(lapStartOffsetSeconds(meta(5))).toBe(0);
  });
});

describe("lapStartOffsetMeters", () => {
  it("is δ times the speed of the first recorded segment", () => {
    // 48 m/s over the first 0.1 s segment, δ = 0.05 s → 2.4 m before the first sample
    expect(lapStartOffsetMeters(lap(0.05, [0, 0.1, 0.2], [0, 4.8, 9.6]))).toBeCloseTo(2.4, 9);
  });

  it("falls back to the speed channel (km/h) for a one-sample lap", () => {
    expect(lapStartOffsetMeters(lap(0.05, [0], [0], [180]))).toBeCloseTo(2.5, 9);
  });

  it("is 0 without δ or without motion", () => {
    expect(lapStartOffsetMeters(lap(undefined, [0, 0.1], [0, 4.8]))).toBe(0);
    expect(lapStartOffsetMeters(lap(0.05, [0, 0.1], [0, 0]))).toBe(0);
  });
});

describe("trueLapTime", () => {
  it("adds δ to the recorded playback time", () => {
    expect(trueLapTime(0, meta(0.016))).toBeCloseTo(0.016, 12);
    expect(trueLapTime(141.3, meta(0.016))).toBeCloseTo(141.316, 12);
  });

  it("is the recorded time when δ is missing", () => {
    expect(trueLapTime(12.34, meta())).toBe(12.34);
    expect(trueLapTime(12.34, null)).toBe(12.34);
  });
});

describe("formatLapClock (HUD TIME)", () => {
  it("formats MM:SS.mmm", () => {
    expect(formatLapClock(0)).toBe("00:00.000");
    expect(formatLapClock(141.385)).toBe("02:21.385");
    expect(formatLapClock(5.25)).toBe("00:05.250");
  });

  it("shows the true lap time once δ is added", () => {
    // first GPS sample of Fuji 7/30 L4 is 97 ms after the timing line
    expect(formatLapClock(trueLapTime(0, meta(0.097)))).toBe("00:00.097");
    expect(formatLapClock(trueLapTime(130.7, meta(0.097)))).toBe("02:10.797");
  });

  it("carries a rounded 60th second into the minutes", () => {
    expect(formatLapClock(59.9996)).toBe("01:00.000");
    expect(formatLapClock(119.9999)).toBe("02:00.000");
  });

  it("never prints a negative or NaN clock", () => {
    expect(formatLapClock(-0.2)).toBe("00:00.000");
    expect(formatLapClock(Number.NaN)).toBe("00:00.000");
  });
});

describe("formatTransportClock", () => {
  it("truncates to whole seconds", () => {
    expect(formatTransportClock(0)).toBe("00:00");
    expect(formatTransportClock(59.99)).toBe("00:59");
    expect(formatTransportClock(141.316)).toBe("02:21");
  });
});
