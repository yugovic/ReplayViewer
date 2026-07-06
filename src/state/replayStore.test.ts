import { describe, expect, it } from "vitest";
import { useReplayStore } from "./replayStore";

describe("scene layer flags", () => {
  it("defaults all layers to visible", () => {
    const state = useReplayStore.getState();
    expect(state.showRoad3d).toBe(true);
    expect(state.showOsmFeatures).toBe(true);
    expect(state.showDetailTexture).toBe(true);
  });

  it("toggleRoad3d flips the flag back and forth", () => {
    useReplayStore.getState().toggleRoad3d();
    expect(useReplayStore.getState().showRoad3d).toBe(false);
    useReplayStore.getState().toggleRoad3d();
    expect(useReplayStore.getState().showRoad3d).toBe(true);
  });

  it("toggleOsmFeatures flips the flag back and forth", () => {
    useReplayStore.getState().toggleOsmFeatures();
    expect(useReplayStore.getState().showOsmFeatures).toBe(false);
    useReplayStore.getState().toggleOsmFeatures();
    expect(useReplayStore.getState().showOsmFeatures).toBe(true);
  });

  it("toggleDetailTexture flips the flag back and forth", () => {
    useReplayStore.getState().toggleDetailTexture();
    expect(useReplayStore.getState().showDetailTexture).toBe(false);
    useReplayStore.getState().toggleDetailTexture();
    expect(useReplayStore.getState().showDetailTexture).toBe(true);
  });

  it("setters apply explicit values", () => {
    useReplayStore.getState().setShowRoad3d(false);
    useReplayStore.getState().setShowOsmFeatures(false);
    useReplayStore.getState().setShowDetailTexture(false);
    expect(useReplayStore.getState().showRoad3d).toBe(false);
    expect(useReplayStore.getState().showOsmFeatures).toBe(false);
    expect(useReplayStore.getState().showDetailTexture).toBe(false);

    useReplayStore.getState().setShowRoad3d(true);
    useReplayStore.getState().setShowOsmFeatures(true);
    useReplayStore.getState().setShowDetailTexture(true);
    expect(useReplayStore.getState().showRoad3d).toBe(true);
    expect(useReplayStore.getState().showOsmFeatures).toBe(true);
    expect(useReplayStore.getState().showDetailTexture).toBe(true);
  });
});

describe("satellite variant selection", () => {
  it("defaults to the 'default' variant with only itself available", () => {
    const state = useReplayStore.getState();
    expect(state.satelliteVariant).toBe("default");
    expect(state.availableSatelliteVariants).toEqual(["default"]);
  });

  it("setSatelliteVariant applies an explicit value", () => {
    useReplayStore.getState().setSatelliteVariant("bing");
    expect(useReplayStore.getState().satelliteVariant).toBe("bing");
    useReplayStore.getState().setSatelliteVariant("default");
    expect(useReplayStore.getState().satelliteVariant).toBe("default");
  });

  it("setAvailableSatelliteVariants replaces the available list", () => {
    useReplayStore.getState().setAvailableSatelliteVariants(["default", "sr", "bing"]);
    expect(useReplayStore.getState().availableSatelliteVariants).toEqual(["default", "sr", "bing"]);
    useReplayStore.getState().setAvailableSatelliteVariants(["default"]);
  });

  it("cycleSatelliteVariant advances through the available variants in SATELLITE_VARIANTS order and wraps", () => {
    useReplayStore.getState().setAvailableSatelliteVariants(["default", "sr", "bing"]);
    useReplayStore.getState().setSatelliteVariant("default");

    useReplayStore.getState().cycleSatelliteVariant();
    expect(useReplayStore.getState().satelliteVariant).toBe("sr");

    useReplayStore.getState().cycleSatelliteVariant();
    expect(useReplayStore.getState().satelliteVariant).toBe("bing");

    useReplayStore.getState().cycleSatelliteVariant();
    expect(useReplayStore.getState().satelliteVariant).toBe("default");

    // Reset for other tests sharing the module-level store instance.
    useReplayStore.getState().setAvailableSatelliteVariants(["default"]);
  });

  it("cycleSatelliteVariant is a no-op when no variants are available", () => {
    useReplayStore.getState().setAvailableSatelliteVariants([]);
    useReplayStore.getState().setSatelliteVariant("default");
    useReplayStore.getState().cycleSatelliteVariant();
    expect(useReplayStore.getState().satelliteVariant).toBe("default");
    useReplayStore.getState().setAvailableSatelliteVariants(["default"]);
  });
});
