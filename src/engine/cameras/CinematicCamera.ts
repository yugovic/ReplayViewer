import type * as THREE from "three";
import { ChaseCamera } from "./ChaseCamera";
import { CockpitCamera } from "./CockpitCamera";
import { TvCamera } from "./TvCamera";
import { cutDuration, selectShot, type ShotType } from "./director";
import type { CarCameraState, ReplayCameraController } from "./types";

/**
 * Cinematic auto-director camera (mode 6).
 *
 * Delegates to internal TV / low-TV / chase / cockpit controllers and hard-cuts
 * between them every 4–8 seconds based on what the car is doing (see director.ts).
 */
export class CinematicCamera implements ReplayCameraController {
  readonly mode = "cinematic" as const;

  private readonly shots: Record<ShotType, ReplayCameraController>;
  private active: ReplayCameraController | null = null;
  private currentShot: ShotType | null = null;
  private cutIndex = 0;
  private timeSinceCut = 0;
  private currentCutLength = 5;

  constructor(camera: THREE.PerspectiveCamera, centerline: THREE.Vector3[]) {
    this.shots = {
      tv: new TvCamera(camera, centerline, 12),
      // Low, ground-hugging TV pan for high-speed sections
      tv_low: new TvCamera(camera, centerline, 12, { sideOffset: 14, heightOffset: 3 }),
      chase: new ChaseCamera(camera),
      cockpit: new CockpitCamera(camera),
    };
  }

  /** True while the current shot is the cockpit (the car model must be hidden) */
  get isCockpitShot(): boolean {
    return this.currentShot === "cockpit";
  }

  activate(state: CarCameraState): void {
    this.cutIndex = 0;
    this.cut(state);
  }

  deactivate(): void {
    this.active?.deactivate();
    this.active = null;
    this.currentShot = null;
  }

  update(state: CarCameraState, dt: number): void {
    this.timeSinceCut += dt;
    if (this.active === null || this.timeSinceCut >= this.currentCutLength) {
      this.cut(state);
    }
    this.active?.update(state, dt);
  }

  /** Hard cut: pick the next shot from channel values and snap to it */
  private cut(state: CarCameraState): void {
    const t = state.telemetry;
    const shot = selectShot({
      dist: t?.dist ?? 0,
      speedKmh: t?.speedKmh ?? 0,
      aps: t?.aps ?? 0,
      brake: t?.brake ?? 0,
      cutIndex: this.cutIndex,
    });
    this.currentCutLength = cutDuration(this.cutIndex);
    this.cutIndex += 1;
    this.timeSinceCut = 0;

    this.active?.deactivate();
    this.currentShot = shot;
    this.active = this.shots[shot];
    // activate() snaps the shared camera → hard cut, no fade
    this.active.activate(state);
  }
}
