import { describe, expect, it } from "vitest";
import { useReplayStore } from "./replayStore";

describe("scene layer flags", () => {
  it("defaults layers to visible except OSM features (covers the satellite photo)", () => {
    const state = useReplayStore.getState();
    expect(state.showRoad3d).toBe(true);
    expect(state.showOsmFeatures).toBe(false);
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
    expect(useReplayStore.getState().showOsmFeatures).toBe(true);
    useReplayStore.getState().toggleOsmFeatures();
    expect(useReplayStore.getState().showOsmFeatures).toBe(false);
  });

  it("toggleDetailTexture flips the flag back and forth", () => {
    useReplayStore.getState().toggleDetailTexture();
    expect(useReplayStore.getState().showDetailTexture).toBe(false);
    useReplayStore.getState().toggleDetailTexture();
    expect(useReplayStore.getState().showDetailTexture).toBe(true);
  });

  it("showTrackLines defaults on and toggles back and forth", () => {
    expect(useReplayStore.getState().showTrackLines).toBe(true);
    useReplayStore.getState().toggleTrackLines();
    expect(useReplayStore.getState().showTrackLines).toBe(false);
    useReplayStore.getState().toggleTrackLines();
    expect(useReplayStore.getState().showTrackLines).toBe(true);
    useReplayStore.getState().setShowTrackLines(false);
    expect(useReplayStore.getState().showTrackLines).toBe(false);
    useReplayStore.getState().setShowTrackLines(true);
  });

  it("driveOnAc defaults off and toggles", () => {
    expect(useReplayStore.getState().driveOnAc).toBe(false);
    useReplayStore.getState().toggleDriveOnAc();
    expect(useReplayStore.getState().driveOnAc).toBe(true);
    useReplayStore.getState().setDriveOnAc(false);
    expect(useReplayStore.getState().driveOnAc).toBe(false);
  });

  it("applies an imagery-only user preset and restores developer layers", () => {
    useReplayStore.getState().applyViewerModePreset(false);
    const user = useReplayStore.getState();
    expect(user.showRoad3d).toBe(false);
    expect(user.showOsmFeatures).toBe(false);
    expect(user.showFeatures3d).toBe(false);
    expect(user.showDetailTexture).toBe(false);
    expect(user.showTrackLines).toBe(false);
    expect(user.showAcOverlay).toBe(false);
    expect(user.driveOnAc).toBe(false);
    expect(user.showTrialTiles).toBe(true);

    useReplayStore.getState().applyViewerModePreset(true);
    const developer = useReplayStore.getState();
    expect(developer.showRoad3d).toBe(true);
    expect(developer.showFeatures3d).toBe(true);
    expect(developer.showDetailTexture).toBe(true);
    expect(developer.showTrackLines).toBe(true);
    expect(developer.showAcOverlay).toBe(true);
    expect(developer.showTrialTiles).toBe(true);
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
