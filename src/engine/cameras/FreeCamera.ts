import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { CarCameraState, ReplayCameraController } from "./types";

export class FreeCamera implements ReplayCameraController {
  readonly mode = "free" as const;
  private firstUpdate = true;

  constructor(
    private readonly camera: THREE.PerspectiveCamera,
    private readonly controls: OrbitControls
  ) {}

  activate(state: CarCameraState): void {
    this.camera.fov = 60;
    this.camera.updateProjectionMatrix();
    this.controls.enabled = true;
    this.controls.target.copy(state.position);
    if (this.firstUpdate) {
      this.camera.position.copy(state.position).add(new THREE.Vector3(30, 24, -44));
      this.firstUpdate = false;
    }
    this.controls.update();
  }

  deactivate(): void {
    this.controls.enabled = false;
  }

  update(): void {
    this.controls.update();
  }

  dispose(): void {
    this.controls.dispose();
  }
}
