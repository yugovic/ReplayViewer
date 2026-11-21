import * as THREE from "three";
import type { CarCameraState } from "./types";

/** Square-pixel pinhole camera in the source video coordinate system.
 * Mount axes: right = car local -X, up = +Y, forward = +Z.
 * Camera rotations match camproj.py: Rz(roll) Rx(pitch) Ry(-yaw), mapping
 * car (right, down, forward) into image (right, down, forward).
 * Positive pitch looks down; positive yaw looks right. Roll follows that
 * matrix convention (positive roll rotates image coordinates clockwise).
 */
export interface OnboardCameraProfile {
  id: string;
  label: string;
  calibrationStatus: "estimated";
  referenceWidth: number;
  referenceHeight: number;
  f: number;
  cx: number;
  cy: number;
  rightMeters: number;
  upMeters: number;
  forwardMeters: number;
  pitchDeg: number;
  yawDeg: number;
  rollDeg: number;
}

/** Joint fit of four global parameters on 3 wet-video frames (2026-10-03).
 * Mount dimensions remain fixed assumptions; this is an image alignment
 * estimate, not physical camera calibration or GPS ground truth. */
export const FUJI_ONBOARD_CAMERA_PROFILE: Readonly<OnboardCameraProfile> = Object.freeze({
  "id": "fuji-smartycam-camera-fit-2026-10-03",
  "label": "動画比較用・推定カメラ",
  "calibrationStatus": "estimated",
  "referenceWidth": 1280,
  "referenceHeight": 720,
  "f": 472.3330956945948,
  "cx": 640,
  "cy": 360,
  "rightMeters": 0,
  "upMeters": 1.25,
  "forwardMeters": -0.55,
  "pitchDeg": 18.20854664390703,
  "yawDeg": -0.6934100796703133,
  "rollDeg": 1.8433014912273966
});

export function validateOnboardCameraProfile(profile: OnboardCameraProfile): void {
  const numbers = [profile.referenceWidth, profile.referenceHeight, profile.f,
    profile.cx, profile.cy, profile.rightMeters, profile.upMeters,
    profile.forwardMeters, profile.pitchDeg, profile.yawDeg, profile.rollDeg];
  if (!numbers.every(Number.isFinite) || profile.referenceWidth <= 0 ||
      profile.referenceHeight <= 0 || profile.f <= 0 || profile.upMeters <= 0 ||
      Math.abs(profile.pitchDeg) >= 89 || Math.abs(profile.yawDeg) >= 89 ||
      Math.abs(profile.rollDeg) >= 89) {
    throw new Error("Invalid onboard camera profile");
  }
}

export interface OnboardCameraFrame {
  position: THREE.Vector3;
  quaternion: THREE.Quaternion;
  fov: number;
}

const CAR_FROM_IMAGE_AXES = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), Math.PI);
const IMAGE_FROM_THREE_AXES = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), Math.PI);

export function computeOnboardCameraFrame(
  state: CarCameraState,
  profile: OnboardCameraProfile,
): OnboardCameraFrame {
  const rotation = new THREE.Matrix4()
    .makeRotationZ(THREE.MathUtils.degToRad(profile.rollDeg))
    .multiply(new THREE.Matrix4().makeRotationX(THREE.MathUtils.degToRad(profile.pitchDeg)))
    .multiply(new THREE.Matrix4().makeRotationY(-THREE.MathUtils.degToRad(profile.yawDeg)));
  const inverseImageRotation = new THREE.Quaternion().setFromRotationMatrix(rotation).invert();
  const quaternion = state.quaternion.clone()
    .multiply(CAR_FROM_IMAGE_AXES)
    .multiply(inverseImageRotation)
    .multiply(IMAGE_FROM_THREE_AXES).normalize();
  const position = new THREE.Vector3(-profile.rightMeters, profile.upMeters, profile.forwardMeters)
    .applyQuaternion(state.quaternion).add(state.position);
  return {
    position,
    quaternion,
    fov: THREE.MathUtils.radToDeg(2 * Math.atan(profile.referenceHeight / (2 * profile.f))),
  };
}

/** Preserve source pixels per image height. Wider/narrower viewports extend
 * or crop horizontally; exact pixel calibration uses the reference aspect. */
export function applyOnboardCameraProjection(
  camera: THREE.PerspectiveCamera,
  profile: OnboardCameraProfile,
): void {
  camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(profile.referenceHeight / (2 * profile.f)));
  const halfHeight = camera.near * profile.referenceHeight / (2 * profile.f);
  const halfWidth = halfHeight * camera.aspect;
  const shiftX = (profile.cx - profile.referenceWidth / 2) * camera.near / profile.f;
  const shiftY = (profile.cy - profile.referenceHeight / 2) * camera.near / profile.f;
  camera.projectionMatrix.makePerspective(
    -halfWidth - shiftX, halfWidth - shiftX,
    halfHeight + shiftY, -halfHeight + shiftY,
    camera.near, camera.far, camera.coordinateSystem,
  );
  camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
}

/** Independent source-pixel projection for fitting and deterministic audits.
 * This changes no replay position, time, track geometry, or telemetry. */
export function projectOnboardPoint(
  point: THREE.Vector3,
  state: CarCameraState,
  profile: OnboardCameraProfile,
): { x: number; y: number; depth: number } {
  const frame = computeOnboardCameraFrame(state, profile);
  const p = point.clone().sub(frame.position).applyQuaternion(frame.quaternion.clone().invert());
  const depth = -p.z;
  return { x: profile.cx + profile.f * p.x / depth,
    y: profile.cy - profile.f * p.y / depth, depth };
}