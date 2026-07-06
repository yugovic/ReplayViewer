import type * as THREE from "three";

export type CameraMode = "chase" | "cockpit" | "top" | "free" | "tv" | "cinematic";

/** Channel values used by the cinematic director to pick shots */
export interface CameraTelemetry {
  speedKmh: number;
  aps: number;
  brake: number;
  dist: number;
}

export interface CarCameraState {
  position: THREE.Vector3;
  quaternion: THREE.Quaternion;
  heading: number;
  forward: THREE.Vector3;
  up: THREE.Vector3;
  trackCenter: THREE.Vector3;
  trackRadius: number;
  /** Optional live channel values (speed/throttle/brake/dist) */
  telemetry?: CameraTelemetry;
}

export interface CameraFrame {
  position: THREE.Vector3;
  target: THREE.Vector3;
  fov: number;
}

export interface ReplayCameraController {
  readonly mode: CameraMode;
  activate: (state: CarCameraState) => void;
  deactivate: () => void;
  update: (state: CarCameraState, dt: number) => void;
  dispose?: () => void;
}
