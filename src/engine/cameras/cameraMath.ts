import * as THREE from "three";
import type { CameraFrame, CarCameraState } from "./types";

// Closer chase: 9 m back, 3.5 m high – car fills more of the frame
export const CHASE_OFFSET = new THREE.Vector3(0, 3.5, -9);
export const CHASE_LOOK_OFFSET = new THREE.Vector3(0, 1.2, 12);
export const COCKPIT_EYE_OFFSET = new THREE.Vector3(-0.28, 0.78, -0.55);
export const COCKPIT_LOOK_DISTANCE = 70;
export const CHASE_FOV = 60;
export const COCKPIT_FOV = 58;
export const TOP_FOV = 60;

export function computeChaseCameraFrame(state: CarCameraState): CameraFrame {
  const position = state.position.clone().add(CHASE_OFFSET.clone().applyQuaternion(state.quaternion));
  const target = state.position.clone().add(CHASE_LOOK_OFFSET.clone().applyQuaternion(state.quaternion));
  return { position, target, fov: CHASE_FOV };
}

export function computeCockpitCameraFrame(state: CarCameraState): CameraFrame {
  const cosY = Math.cos(state.heading);
  const sinY = Math.sin(state.heading);
  const eyeX = COCKPIT_EYE_OFFSET.x * cosY + COCKPIT_EYE_OFFSET.z * sinY;
  const eyeZ = -COCKPIT_EYE_OFFSET.x * sinY + COCKPIT_EYE_OFFSET.z * cosY;
  const position = state.position.clone().add(new THREE.Vector3(eyeX, COCKPIT_EYE_OFFSET.y, eyeZ));
  const target = position.clone().add(state.forward.clone().multiplyScalar(COCKPIT_LOOK_DISTANCE));
  return { position, target, fov: COCKPIT_FOV };
}

export function computeTopCameraFrame(state: CarCameraState): CameraFrame {
  const altitude = Math.max(200, state.trackRadius);
  const position = new THREE.Vector3(state.trackCenter.x, state.trackCenter.y + altitude, state.trackCenter.z);
  return { position, target: state.trackCenter.clone(), fov: TOP_FOV };
}

// ── TV zoom ───────────────────────────────────────────────────────────────────

export const TV_ZOOM_MIN_FOV = 16;
export const TV_ZOOM_MAX_FOV = 48;
/** Apparent car size (metres) used for framing – slightly larger than the car */
export const TV_ZOOM_CAR_SIZE_M = 4.5;
/** Target fraction of the frame the car should occupy (~1/4) */
export const TV_ZOOM_FRAME_FRACTION = 0.25;

/**
 * Compute the vertical FOV (degrees) a fixed TV camera should use so that the
 * car occupies roughly TV_ZOOM_FRAME_FRACTION of the frame at the given
 * distance. Emulates a broadcast operator zooming with the car.
 * Pure function – clamped to [TV_ZOOM_MIN_FOV, TV_ZOOM_MAX_FOV].
 */
export function computeTvZoomFov(
  distanceM: number,
  frameFraction = TV_ZOOM_FRAME_FRACTION,
  carSizeM = TV_ZOOM_CAR_SIZE_M,
): number {
  const safeDist = Math.max(1, distanceM);
  const frameHeightM = carSizeM / frameFraction;
  const fovRad = 2 * Math.atan(frameHeightM / (2 * safeDist));
  const fovDeg = (fovRad * 180) / Math.PI;
  return Math.min(TV_ZOOM_MAX_FOV, Math.max(TV_ZOOM_MIN_FOV, fovDeg));
}
