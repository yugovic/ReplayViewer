import * as THREE from "three";
import { selectTvCamera, type TvCamera as TvCameraDesc } from "../trailColor";
import { computeTvZoomFov } from "./cameraMath";
import type { CarCameraState, ReplayCameraController } from "./types";

const SWITCH_INTERVAL_S = 5; // minimum seconds before switching camera
const SWITCH_DISTANCE_THRESHOLD = 90; // switch early if car is farther than this
const PICK_MIN_DIST = 12; // metres – don't pick a camera the car is on top of
const PICK_MAX_DIST = 130; // metres – keep cameras close enough for a good frame

export interface TvCameraOptions {
  /** Lateral offset from the track centreline in metres (15–25 m looks like TV) */
  sideOffset?: number;
  /** Camera height above the track surface in metres */
  heightOffset?: number;
}

/**
 * Generate TV-style fixed camera positions around the track centerline.
 * Places cameras at STEP-ed positions, offset outward from the track, facing inward.
 */
export function generateTvCameraPoints(
  centerline: THREE.Vector3[],
  count = 12,
  sideOffset = 18,
  heightOffset = 6,
): { position: THREE.Vector3; target: THREE.Vector3; desc: TvCameraDesc }[] {
  const step = Math.max(1, Math.floor(centerline.length / count));
  const result: { position: THREE.Vector3; target: THREE.Vector3; desc: TvCameraDesc }[] = [];

  for (let i = 0; i < count; i++) {
    const idx = (i * step) % centerline.length;
    const prev = centerline[(idx - 1 + centerline.length) % centerline.length];
    const curr = centerline[idx];
    const next = centerline[(idx + 1) % centerline.length];

    // Tangent direction along track
    const tangent = new THREE.Vector3(next.x - prev.x, 0, next.z - prev.z).normalize();
    // Normal (perpendicular, choose alternating sides for variety)
    const side = i % 2 === 0 ? 1 : -1;
    const normal = new THREE.Vector3(-tangent.z * side, 0, tangent.x * side);

    const position = curr.clone().addScaledVector(normal, sideOffset);
    // Camera height above the local track surface (min 2 m so it never sits on the ground)
    position.y = curr.y + Math.max(2, heightOffset);

    // Camera faces the track centreline point
    const target = curr.clone();
    target.y = curr.y + 1;

    // Forward vector from camera toward original track point
    const fwdRaw = target.clone().sub(position);
    const fwdLen = Math.hypot(fwdRaw.x, fwdRaw.z);

    result.push({
      position,
      target,
      desc: {
        x: position.x,
        z: position.z,
        fwdX: fwdLen > 0 ? fwdRaw.x / fwdLen : 0,
        fwdZ: fwdLen > 0 ? fwdRaw.z / fwdLen : 0,
      },
    });
  }
  return result;
}

export class TvCamera implements ReplayCameraController {
  readonly mode = "tv" as const;

  private cameraPoints: ReturnType<typeof generateTvCameraPoints> = [];
  private currentIndex = 0;
  private timeSinceSwitch = 0;
  private readonly smoothTarget = new THREE.Vector3();
  private readonly smoothPosition = new THREE.Vector3();
  private readonly lookTarget = new THREE.Vector3();
  private initialized = false;

  constructor(
    private readonly camera: THREE.PerspectiveCamera,
    centerline: THREE.Vector3[],
    count = 12,
    options: TvCameraOptions = {},
  ) {
    if (centerline.length >= 3) {
      this.cameraPoints = generateTvCameraPoints(
        centerline,
        count,
        options.sideOffset ?? 18,
        options.heightOffset ?? 6,
      );
    }
  }

  activate(state: CarCameraState): void {
    this.currentIndex = this.pickBestCamera(state);
    this.timeSinceSwitch = 0;
    this.initialized = false;
    this.applyZoom(state, true);
  }

  deactivate(): void {
    return undefined;
  }

  update(state: CarCameraState, dt: number): void {
    if (this.cameraPoints.length === 0) {
      return;
    }

    this.timeSinceSwitch += dt;

    // Check if we should switch cameras
    const cam = this.cameraPoints[this.currentIndex];
    const distToCam = Math.hypot(state.position.x - cam.position.x, state.position.z - cam.position.z);

    const shouldSwitch =
      this.timeSinceSwitch >= SWITCH_INTERVAL_S ||
      distToCam > SWITCH_DISTANCE_THRESHOLD;

    if (shouldSwitch) {
      const next = this.pickBestCamera(state);
      if (next !== this.currentIndex) {
        this.currentIndex = next;
        this.timeSinceSwitch = 0;
        this.initialized = false;
      } else {
        this.timeSinceSwitch = SWITCH_INTERVAL_S * 0.5;
      }
    }

    const current = this.cameraPoints[this.currentIndex];

    if (!this.initialized) {
      this.smoothPosition.copy(current.position);
      this.smoothTarget.copy(state.position).setY(state.position.y + 1);
      this.initialized = true;
      this.applyZoom(state, true);
    }

    // Smoothly interpolate camera position (fixed) and pan to follow car
    const lerpAlpha = Math.min(1, dt * 3.0);
    this.smoothPosition.lerp(current.position, lerpAlpha);
    // Never sink below the camera point's own height
    if (this.smoothPosition.y < current.position.y) {
      this.smoothPosition.y = current.position.y;
    }

    this.lookTarget.copy(state.position);
    this.lookTarget.y += 1.0;
    this.smoothTarget.lerp(this.lookTarget, Math.min(1, dt * 4.0));

    this.camera.position.copy(this.smoothPosition);
    this.camera.lookAt(this.smoothTarget);

    this.applyZoom(state, false, dt);
  }

  /** Broadcast-style zoom: interpolate FOV so the car keeps a similar frame size */
  private applyZoom(state: CarCameraState, snap: boolean, dt = 0): void {
    if (this.cameraPoints.length === 0) return;
    const cam = this.cameraPoints[this.currentIndex];
    const dist = Math.hypot(state.position.x - cam.position.x, state.position.z - cam.position.z);
    const targetFov = computeTvZoomFov(dist);
    const nextFov = snap
      ? targetFov
      : THREE.MathUtils.lerp(this.camera.fov, targetFov, Math.min(1, dt * 2.5));
    if (Math.abs(this.camera.fov - nextFov) > 0.05) {
      this.camera.fov = nextFov;
      this.camera.updateProjectionMatrix();
    }
  }

  private pickBestCamera(state: CarCameraState): number {
    if (this.cameraPoints.length === 0) {
      return 0;
    }
    const descs = this.cameraPoints.map((p) => p.desc);
    return selectTvCamera(state.position.x, state.position.z, descs, PICK_MIN_DIST, PICK_MAX_DIST);
  }
}
