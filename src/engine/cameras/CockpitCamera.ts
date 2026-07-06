import * as THREE from "three";
import { computeCockpitCameraFrame } from "./cameraMath";
import type { CarCameraState, ReplayCameraController } from "./types";

export class CockpitCamera implements ReplayCameraController {
  readonly mode = "cockpit" as const;

  constructor(private readonly camera: THREE.PerspectiveCamera) {}

  activate(state: CarCameraState): void {
    const frame = computeCockpitCameraFrame(state);
    this.camera.fov = frame.fov;
    this.camera.position.copy(frame.position);
    this.camera.lookAt(frame.target);
    this.camera.updateProjectionMatrix();
  }

  deactivate(): void {
    return undefined;
  }

  update(state: CarCameraState): void {
    const frame = computeCockpitCameraFrame(state);
    if (Math.abs(this.camera.fov - frame.fov) > 0.1) {
      this.camera.fov = frame.fov;
      this.camera.updateProjectionMatrix();
    }
    this.camera.position.copy(frame.position);
    this.camera.lookAt(frame.target);
  }
}
