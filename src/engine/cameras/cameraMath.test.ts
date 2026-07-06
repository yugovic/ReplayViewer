import { describe, expect, it } from "vitest";
import * as THREE from "three";
import {
  computeChaseCameraFrame,
  computeCockpitCameraFrame,
  computeTopCameraFrame,
  computeTvZoomFov,
  CHASE_FOV,
  COCKPIT_FOV,
  TOP_FOV,
  TV_ZOOM_MIN_FOV,
  TV_ZOOM_MAX_FOV,
} from "./cameraMath";
import type { CarCameraState } from "./types";

function makeState(overrides: Partial<CarCameraState> = {}): CarCameraState {
  return {
    position: new THREE.Vector3(0, 0, 0),
    quaternion: new THREE.Quaternion(),
    heading: 0,
    forward: new THREE.Vector3(0, 0, 1),
    up: new THREE.Vector3(0, 1, 0),
    trackCenter: new THREE.Vector3(100, 0, 100),
    trackRadius: 300,
    ...overrides,
  };
}

describe("computeChaseCameraFrame", () => {
  it("returns correct fov", () => {
    const state = makeState();
    const frame = computeChaseCameraFrame(state);
    expect(frame.fov).toBe(CHASE_FOV);
  });

  it("positions camera behind and above car", () => {
    const state = makeState({ position: new THREE.Vector3(0, 0, 0) });
    const frame = computeChaseCameraFrame(state);
    // With default heading (forward=+Z), camera should be at negative Z offset
    expect(frame.position.y).toBeGreaterThan(0);
  });

  it("target is in front of camera", () => {
    const state = makeState({ position: new THREE.Vector3(0, 0, 0) });
    const frame = computeChaseCameraFrame(state);
    // target z > position z when heading=0 (forward = +Z)
    expect(frame.target.z).toBeGreaterThan(frame.position.z);
  });
});

describe("computeCockpitCameraFrame", () => {
  it("returns correct fov", () => {
    const state = makeState();
    const frame = computeCockpitCameraFrame(state);
    expect(frame.fov).toBe(COCKPIT_FOV);
  });

  it("eye position is near car position", () => {
    const state = makeState({ position: new THREE.Vector3(10, 0, 20) });
    const frame = computeCockpitCameraFrame(state);
    const dist = frame.position.distanceTo(state.position);
    expect(dist).toBeLessThan(2);
  });

  it("target is far in front of car", () => {
    const state = makeState({
      position: new THREE.Vector3(0, 0, 0),
      heading: 0,
      forward: new THREE.Vector3(0, 0, 1),
    });
    const frame = computeCockpitCameraFrame(state);
    expect(frame.target.z).toBeGreaterThan(50);
  });
});

describe("computeTopCameraFrame", () => {
  it("returns correct fov", () => {
    const state = makeState();
    const frame = computeTopCameraFrame(state);
    expect(frame.fov).toBe(TOP_FOV);
  });

  it("places camera above track center", () => {
    const center = new THREE.Vector3(50, 0, 80);
    const state = makeState({ trackCenter: center, trackRadius: 300 });
    const frame = computeTopCameraFrame(state);
    expect(frame.position.y).toBeGreaterThan(100);
    expect(frame.target.x).toBeCloseTo(center.x);
    expect(frame.target.z).toBeCloseTo(center.z);
  });
});

describe("computeTvZoomFov", () => {
  it("zooms in (small fov) when the car is far away", () => {
    expect(computeTvZoomFov(200)).toBe(TV_ZOOM_MIN_FOV);
  });

  it("widens (large fov) when the car is close", () => {
    expect(computeTvZoomFov(10)).toBe(TV_ZOOM_MAX_FOV);
  });

  it("frames the car at ~1/4 of the frame at mid distance", () => {
    // At 50m with default car size 4.5m: frame height = 18m → fov ≈ 20.4°
    const fov = computeTvZoomFov(50);
    expect(fov).toBeGreaterThan(15);
    expect(fov).toBeLessThan(30);
    // Verify the car actually subtends ~1/4 of the frame
    const carAngle = 2 * Math.atan(4.5 / 2 / 50) * (180 / Math.PI);
    expect(carAngle / fov).toBeCloseTo(0.25, 1);
  });

  it("decreases monotonically with distance", () => {
    let prev = Infinity;
    for (const d of [15, 30, 60, 100, 160]) {
      const fov = computeTvZoomFov(d);
      expect(fov).toBeLessThanOrEqual(prev);
      prev = fov;
    }
  });

  it("stays within clamp bounds for degenerate input", () => {
    expect(computeTvZoomFov(0)).toBeLessThanOrEqual(TV_ZOOM_MAX_FOV);
    expect(computeTvZoomFov(100000)).toBeGreaterThanOrEqual(TV_ZOOM_MIN_FOV);
  });
});
