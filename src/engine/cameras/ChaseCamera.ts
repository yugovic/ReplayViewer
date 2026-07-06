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

  update(state: CarCameraState): void {
    const frame = computeChaseCameraFrame(state);
    if (Math.abs(this.camera.fov - frame.fov) > 0.1) {
      this.camera.fov = frame.fov;
      this.camera.updateProjectionMatrix();
    }
    this.camera.position.lerp(frame.position, 0.15);
    this.camera.lookAt(frame.target);
  }
}
