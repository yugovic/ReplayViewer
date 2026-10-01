import { describe, expect, it } from "vitest";
import { useReplayStore } from "./replayStore";
import type { LapData, LapIndexRecord } from "../replay/types";

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
    // 3D features stay on for users: the extruded boxes are the only
    // building representation since the building/terrain fix.
    expect(user.showFeatures3d).toBe(true);
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

describe("GPS registration toggle", () => {
  const meta = { race_id: "r", vehicle_id: "v", lap: 1 } as LapData["meta"];
  const stamped = (lap: number): LapData => ({
    meta: { ...meta, lap }, t: [0], lat: [0], lng: [0], speed: [0], aps: [0], brake: [0], steer: [0], gear: [0], accx: [0], accy: [0], dist: [0],
    registration: { offsetMeters: [-1.6, 1.6], sigmaMeters: null, vehicleWidthMeters: 1.7, antennaMeters: null, verified: true, enabled: true },
  });
  const record = (lap: number) => ({ vehicle_id: "v", lap }) as LapIndexRecord;

  it("defaults on, flips main and ghost together, and new laps follow the current choice", () => {
    const main = stamped(1), ghost = stamped(2);
    expect(useReplayStore.getState().gpsRegistrationEnabled).toBe(true);
    useReplayStore.getState().setActiveLap(record(1), main);
    useReplayStore.getState().setGhostLap(record(2), ghost);
    expect(useReplayStore.getState().activeLap).toBe(main);

    useReplayStore.getState().setGpsRegistrationEnabled(false);
    const off = useReplayStore.getState();
    expect(off.activeLap).not.toBe(main); // new identity: position caches must miss
    expect(off.activeLap?.registration?.enabled).toBe(false);
    expect(off.ghostLap?.registration?.enabled).toBe(false);
    expect(main.registration?.enabled).toBe(true); // inputs are never mutated

    useReplayStore.getState().setActiveLap(record(3), stamped(3));
    expect(useReplayStore.getState().activeLap?.registration?.enabled).toBe(false);

    useReplayStore.getState().setGpsRegistrationEnabled(true);
    expect(useReplayStore.getState().activeLap?.registration?.enabled).toBe(true);
    expect(useReplayStore.getState().ghostLap?.registration?.enabled).toBe(true);
    useReplayStore.getState().clearGhost();
  });

  it("leaves laps without a registration untouched", () => {
    const plain = { ...stamped(4), registration: undefined };
    useReplayStore.getState().setActiveLap(record(4), plain);
    useReplayStore.getState().setGpsRegistrationEnabled(false);
    expect(useReplayStore.getState().activeLap).toBe(plain);
    useReplayStore.getState().setGpsRegistrationEnabled(true);
  });
});
