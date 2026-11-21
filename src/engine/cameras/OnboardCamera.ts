import * as THREE from "three";
import { CockpitCamera } from "./CockpitCamera";
import { applyOnboardCameraProjection, computeOnboardCameraFrame } from "./onboardCameraMath";
import type { OnboardCameraProfile } from "./onboardCameraMath";
import type { CarCameraState, ReplayCameraController } from "./types";

/** Opt-in source-video viewpoint; unsupported tracks keep the cockpit view. */
export class OnboardCamera implements ReplayCameraController {
  readonly mode = "onboard" as const;
  private readonly fallback: CockpitCamera;

  constructor(
    private readonly camera: THREE.PerspectiveCamera,
    private readonly getProfile: () => OnboardCameraProfile | null,
  ) {
    this.fallback = new CockpitCamera(camera);
  }

  activate(state: CarCameraState): void {
    if (!this.getProfile()) this.fallback.activate(state);
    else this.update(state);
  }

  deactivate(): void {
    // Standard cameras own fov/aspect on activate. Clear the custom principal
    // point now so no calibrated projection leaks into another view.
    this.camera.updateProjectionMatrix();
  }

  update(state: CarCameraState): void {
    const profile = this.getProfile();
    if (!profile) { this.fallback.update(state); return; }
    const frame = computeOnboardCameraFrame(state, profile);
    this.camera.position.copy(frame.position);
    this.camera.quaternion.copy(frame.quaternion);
    applyOnboardCameraProjection(this.camera, profile);
  }
}