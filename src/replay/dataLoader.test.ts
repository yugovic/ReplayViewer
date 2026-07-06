import { describe, expect, it } from "vitest";
import { resolveDataSources } from "./dataLoader";

describe("resolveDataSources", () => {
  it("defaults to the Barber round-1 data set with no query params", () => {
    expect(resolveDataSources("")).toEqual({
      trackId: "barber",
      raceId: "barber_r1",
      trackUrl: "/data/tracks/barber/track.json",
      raceBaseUrl: "/data/races/barber_r1",
      satVariant: "default",
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

  it("selects the sr satellite variant only for sat=sr", () => {
    expect(resolveDataSources("?sat=sr").satVariant).toBe("sr");
  });

  it("selects the bing satellite variant for sat=bing", () => {
    expect(resolveDataSources("?sat=bing").satVariant).toBe("bing");
  });

  it("selects the bing_sr satellite variant for sat=bing_sr", () => {
    expect(resolveDataSources("?sat=bing_sr").satVariant).toBe("bing_sr");
  });

  it("falls back to the default satellite variant for any other sat value", () => {
    expect(resolveDataSources("?sat=hd").satVariant).toBe("default");
    expect(resolveDataSources("?sat=").satVariant).toBe("default");
    expect(resolveDataSources("").satVariant).toBe("default");
  });

  it("resolves all params together", () => {
    const result = resolveDataSources("?track=barber&race=barber_r2&sat=sr");
    expect(result).toEqual({
      trackId: "barber",
      raceId: "barber_r2",
      trackUrl: "/data/tracks/barber/track.json",
      raceBaseUrl: "/data/races/barber_r2",
      satVariant: "sr",
    });
  });
});
