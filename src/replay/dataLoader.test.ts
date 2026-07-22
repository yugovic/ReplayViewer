import { describe, expect, it } from "vitest";
import { getVehicleBestLap, resolveDataSources } from "./dataLoader";
import type { LapIndexRecord, LapsIndex } from "./types";

describe("resolveDataSources", () => {
  it("defaults to the Barber round-1 data set with no query params", () => {
    expect(resolveDataSources("")).toEqual({
      trackId: "barber",
      raceId: "barber_r1",
      trackUrl: "/data/tracks/barber/track.json",
      raceBaseUrl: "/data/races/barber_r1",
      satVariant: "default",
      developerMode: false,
    });
  });

  it("resolves track and race ids from the query string", () => {
    const result = resolveDataSources("?track=laguna_seca&race=laguna_r2");
    expect(result.trackId).toBe("laguna_seca");
    expect(result.raceId).toBe("laguna_r2");
    expect(result.trackUrl).toBe("/data/tracks/laguna_seca/track.json");
    expect(result.raceBaseUrl).toBe("/data/races/laguna_r2");
  });

  it("accepts uppercase letters, digits, underscores and hyphens", () => {
    const result = resolveDataSources("?track=Track-2_B&race=Race-1_A");
    expect(result.trackId).toBe("Track-2_B");
    expect(result.raceId).toBe("Race-1_A");
  });

  it("falls back to defaults for track/race values with disallowed characters", () => {
    const result = resolveDataSources("?track=../../etc&race=foo bar");
    expect(result.trackId).toBe("barber");
    expect(result.raceId).toBe("barber_r1");
  });

  it("falls back to defaults for empty track/race values", () => {
    const result = resolveDataSources("?track=&race=");
    expect(result.trackId).toBe("barber");
    expect(result.raceId).toBe("barber_r1");
  });

  it("keeps the user-facing viewer on the track's single curated texture", () => {
    const result = resolveDataSources("?sat=sr");
    expect(result.developerMode).toBe(false);
    expect(result.satVariant).toBe("default");
  });

  it("selects Shizuoka 20cm SR for Fuji in user mode and ignores sat overrides", () => {
    const result = resolveDataSources("?track=fuji&race=fuji_aim_01&sat=bing");
    expect(result.developerMode).toBe(false);
    expect(result.satVariant).toBe("shizuoka_x2");
  });

  it("selects the sr satellite variant only in developer mode", () => {
    expect(resolveDataSources("?dev=1&sat=sr").satVariant).toBe("sr");
  });

  it("selects the bing satellite variant in developer mode", () => {
    expect(resolveDataSources("?dev=1&sat=bing").satVariant).toBe("bing");
  });

  it("selects the bing_sr satellite variant in developer mode", () => {
    expect(resolveDataSources("?dev=1&sat=bing_sr").satVariant).toBe("bing_sr");
  });

  it("falls back to the default satellite variant for any other sat value", () => {
    expect(resolveDataSources("?sat=hd").satVariant).toBe("default");
    expect(resolveDataSources("?sat=").satVariant).toBe("default");
    expect(resolveDataSources("").satVariant).toBe("default");
  });

  it("resolves all params together", () => {
    const result = resolveDataSources("?track=barber&race=barber_r2&dev=1&sat=sr");
    expect(result).toEqual({
      trackId: "barber",
      raceId: "barber_r2",
      trackUrl: "/data/tracks/barber/track.json",
      raceBaseUrl: "/data/races/barber_r2",
      satVariant: "sr",
      developerMode: true,
    });
  });
});

function bestLapRec(overrides: Partial<LapIndexRecord>): LapIndexRecord {
  return {
    vehicle_id: "car_a",
    vehicle_number: "1",
    lap: 1,
    lap_time_seconds: 100,
    lap_time: "01:40.000",
    start_time: "",
    end_time: "",
    outing: "0",
    is_best_vehicle: false,
    is_overall_best: false,
    ...overrides,
  };
}

function lapsIndexOf(laps: LapIndexRecord[]): LapsIndex {
  return { version: 1, race_id: "r", track_id: "t", selected: [], laps };
}

describe("getVehicleBestLap", () => {
  it("prefers the record flagged is_best_vehicle", () => {
    const index = lapsIndexOf([
      bestLapRec({ lap: 1, lap_time_seconds: 144.556 }),
      bestLapRec({ lap: 3, lap_time_seconds: 141.385, is_best_vehicle: true }),
      bestLapRec({ lap: 4, lap_time_seconds: 164.717 }),
    ]);
    expect(getVehicleBestLap(index, "car_a")?.lap).toBe(3);
  });

  it("falls back to the smallest positive lap time when nothing is flagged", () => {
    const index = lapsIndexOf([
      bestLapRec({ lap: 1, lap_time_seconds: 144.556 }),
      bestLapRec({ lap: 2, lap_time_seconds: 141.385 }),
      bestLapRec({ lap: 3, lap_time_seconds: 164.717 }),
    ]);
    expect(getVehicleBestLap(index, "car_a")?.lap).toBe(2);
  });

  it("only considers the requested vehicle", () => {
    const index = lapsIndexOf([
      bestLapRec({ vehicle_id: "car_b", lap: 9, lap_time_seconds: 90, is_best_vehicle: true }),
      bestLapRec({ lap: 2, lap_time_seconds: 141.385 }),
    ]);
    expect(getVehicleBestLap(index, "car_a")?.lap).toBe(2);
  });

  it("ignores untimed laps and returns null when none are timed", () => {
    const index = lapsIndexOf([
      bestLapRec({ lap: 0, lap_time_seconds: 0 }),
      bestLapRec({ lap: 1, lap_time_seconds: 141.385 }),
    ]);
    expect(getVehicleBestLap(index, "car_a")?.lap).toBe(1);
    expect(getVehicleBestLap(lapsIndexOf([bestLapRec({ lap_time_seconds: 0 })]), "car_a")).toBeNull();
    expect(getVehicleBestLap(lapsIndexOf([]), "car_a")).toBeNull();
  });
});
