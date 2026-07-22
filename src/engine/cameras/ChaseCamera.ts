import * as THREE from "three";
import { computeChaseCameraFrame } from "./cameraMath";
import type { CarCameraState, ReplayCameraController } from "./types";

export class ChaseCamera implements ReplayCameraController {
  readonly mode = "chase" as const;

  constructor(private readonly camera: THREE.PerspectiveCamera) {}

  activate(state: CarCameraState): void {
    const frame = computeChaseCameraFrame(state);
    this.camera.fov = frame.fov;
    this.camera.position.copy(frame.position);
    this.camera.lookAt(frame.target);
    this.camera.updateProjectionMatrix();
  }

  deactivate(): void {
    return undefined;
  }

  update(state: CarCameraState, dt: number): void {
    const frame = computeChaseCameraFrame(state);
    if (Math.abs(this.camera.fov - frame.fov) > 0.1) {
      this.camera.fov = frame.fov;
      this.camera.updateProjectionMatrix();
    }
    // Frame-rate independent smoothing (τ ≈ 100 ms; equals the old fixed
    // 0.15/frame at 60 fps). A fixed per-frame factor made the camera lag
    // vary with frame time, which reads as the car surging back and forth
    // inside the frame.
    const alpha = dt > 0 ? 1 - Math.exp(-dt / 0.1) : 1;
    this.camera.position.lerp(frame.position, alpha);
    this.camera.lookAt(frame.target);
  }
}
