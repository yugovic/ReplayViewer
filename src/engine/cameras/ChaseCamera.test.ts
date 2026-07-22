import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { ChaseCamera } from "./ChaseCamera";
import type { CarCameraState } from "./types";

function carStateAt(x: number): CarCameraState {
  return {
    position: new THREE.Vector3(x, 0, 0),
    quaternion: new THREE.Quaternion(),
    heading: 0,
    forward: new THREE.Vector3(0, 0, 1),
    up: new THREE.Vector3(0, 1, 0),
    trackCenter: new THREE.Vector3(),
    trackRadius: 100,
  };
}

describe("ChaseCamera smoothing", () => {
  it("is frame-rate independent: two half-steps equal one full step", () => {
    const run = (dts: number[]) => {
      const camera = new THREE.PerspectiveCamera();
      const chase = new ChaseCamera(camera);
      chase.activate(carStateAt(0));
      const target = carStateAt(100);
      for (const dt of dts) {
        chase.update(target, dt);
      }
      return camera.position.clone();
    };
    // Exponential smoothing composes exactly: 1-(1-a(dt/2))^2 == a(dt).
    const twoSmallSteps = run([1 / 120, 1 / 120]);
    const oneBigStep = run([1 / 60]);
    expect(twoSmallSteps.distanceTo(oneBigStep)).toBeLessThan(1e-9);
  });

  it("still converges onto the chase frame", () => {
    const camera = new THREE.PerspectiveCamera();
    const chase = new ChaseCamera(camera);
    chase.activate(carStateAt(0));
    const target = carStateAt(100);
    for (let i = 0; i < 120; i += 1) {
      chase.update(target, 1 / 60);
    }
    const settled = camera.position.clone();
    // Reference: activate() snaps a fresh camera directly onto the frame.
    const reference = new THREE.PerspectiveCamera();
    new ChaseCamera(reference).activate(carStateAt(100));
    // After 2 s (20 time constants) the smoothed camera sits on that frame.
    expect(settled.distanceTo(reference.position)).toBeLessThan(1e-3);
  });
});
