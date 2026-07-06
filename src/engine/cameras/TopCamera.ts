import * as THREE from "three";
import { computeTopCameraFrame } from "./cameraMath";
import type { CarCameraState, ReplayCameraController } from "./types";

export class TopCamera implements ReplayCameraController {
  readonly mode = "top" as const;

  constructor(private readonly camera: THREE.PerspectiveCamera) {}

  activate(state: CarCameraState): void {
    const frame = computeTopCameraFrame(state);
    this.camera.fov = frame.fov;
    this.camera.position.copy(frame.position);
    this.camera.lookAt(frame.target);
    this.camera.updateProjectionMatrix();
  }

  deactivate(): void {
    return undefined;
  }

  update(state: CarCameraState): void {
    const frame = computeTopCameraFrame(state);
    if (Math.abs(this.camera.fov - frame.fov) > 0.1) {
      this.camera.fov = frame.fov;
      this.camera.updateProjectionMatrix();
    }
    this.camera.position.copy(frame.position);
    this.camera.lookAt(frame.target);
  }
}
