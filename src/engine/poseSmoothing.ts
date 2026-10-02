import * as THREE from "three";

/**
 * Lag-compensated pose smoothing for the replayed car (and the cockpit
 * camera, which inherits the car pose).
 *
 * The target pose comes from per-sample chord headings interpolated linearly
 * between 10 Hz samples, so the target yaw RATE steps every 100 ms. A low-pass
 * filter hides those steps, but a plain first-order filter with time constant
 * τ also delays the pose by yawRate × τ in steady cornering (the previous
 * 140 ms filter: p95 ≈ 4° heading error, ~6–7.6° in the Dunlop chicane).
 *
 * Because the whole lap is known in advance, the filter can chase the pose it
 * will reach one group delay ahead instead of the current one. For a cascade
 * of N first-order stages the group delay is N·τ, so feeding pose(t + N·τ)
 * cancels the steady-state lag for a constant yaw rate; the residual is
 * ≈ N/2 · τ² · yaw-acceleration (the s-term of e^{Nτs}/(1+τs)^N vanishes).
 * Two 70 ms stages keep the 140 ms window of the old filter but roll off the
 * 10 Hz yaw-rate steps at −40 dB/decade, so the drawn yaw is smoother too.
 *
 * Each stage is stepped with the exact solution of τẏ + y = u for an input
 * that varies LINEARLY over the frame (first-order hold) instead of the usual
 * y += α(u − y), which assumes the input was constant over the frame. With
 * the constant-input update a ramp lags by Δ/(e^{Δ/τ} − 1) ≈ τ − Δ/2, i.e. the
 * lag (and the drawn yaw rate) would depend on every frame's duration; with
 * the linear-input update it is exactly τ for any frame step, so the
 * look-ahead is a constant N·τ and irregular frame times add no yaw noise.
 *
 * All times are REPLAY seconds (playbackDelta = change of sample time), so the
 * behaviour is identical at 0.5×–4× playback and any display frame rate.
 */

/** Steps outside (0, MAX] snap to the target (pause, seek, loop, stall). */
export const POSE_SMOOTHING_MAX_STEP_SECONDS = 0.25;

export interface PoseSmoothingConfig {
  /** Number of cascaded first-order stages (≥ 1). */
  stages: number;
  /** Time constant of each stage, milliseconds (> 0). */
  timeConstantMs: number;
  /** Chase pose(t + stages·τ) instead of pose(t). false = lagging filter. */
  lookahead: boolean;
  /** Linear-input (first-order-hold) stage update; false = y += α(u − y). */
  linearInput: boolean;
}

/** Shipped setting: 2 × 70 ms stages, 140 ms look-ahead, linear-input steps. */
export const DEFAULT_POSE_SMOOTHING: PoseSmoothingConfig = Object.freeze({
  stages: 2,
  timeConstantMs: 70,
  lookahead: true,
  linearInput: true,
});

/** Pre-2026-10-02 behaviour (one 140 ms stage chasing the current pose). */
export const LEGACY_POSE_SMOOTHING: PoseSmoothingConfig = Object.freeze({
  stages: 1,
  timeConstantMs: 140,
  lookahead: false,
  linearInput: false,
});

/** Writes the target pose quaternion for replay time `time` into `out`. */
export type PoseAtTime = (out: THREE.Quaternion, time: number) => THREE.Quaternion;

/** Exponential smoothing factor of one stage for a step of `playbackDelta` s. */
export function poseSmoothingAlpha(playbackDelta: number, timeConstantMs: number): number {
  if (!(playbackDelta > 0)) return 0;
  if (!(timeConstantMs > 0)) return 1;
  return -Math.expm1(-(playbackDelta * 1000) / timeConstantMs);
}

/** True when a step should be filtered; false means snap to the target. */
export function isSmoothedPoseStep(playbackDelta: number): boolean {
  return playbackDelta > 0 && playbackDelta <= POSE_SMOOTHING_MAX_STEP_SECONDS;
}

/**
 * First-order-hold input weight c: for an input moving linearly from u0 to u1
 * during the step, the exact stage update equals y1 = y0 + α(u_c − y0) with
 * u_c = u0 + c·(u1 − u0), c = 1 + 1/(e^{x} − 1) − 1/x, x = Δ/τ (→ ½ + x/12).
 */
export function linearInputWeight(playbackDelta: number, timeConstantMs: number): number {
  const x = (playbackDelta * 1000) / timeConstantMs;
  if (!(x > 0)) return 0.5;
  if (x < 1e-4) return 0.5 + x / 12;
  return 1 + 1 / Math.expm1(x) - 1 / x;
}

export class PoseSmoother {
  readonly config: PoseSmoothingConfig;
  /** Seconds ahead of the sample time that the first stage chases. */
  readonly lookaheadSeconds: number;
  /** Intermediate stage states (stages − 1 of them; the last stage is `out`). */
  private readonly stageStates: THREE.Quaternion[];
  /** Previous input of the first stage (for the linear-input update). */
  private readonly previousInput = new THREE.Quaternion();
  private readonly input = new THREE.Quaternion();
  private readonly held = new THREE.Quaternion();
  private readonly before = new THREE.Quaternion();
  /** Replay time of the last snap; the stages are charged on the next step. */
  private snapTime = Number.NaN;
  private stagesCharged = false;

  constructor(config: PoseSmoothingConfig = DEFAULT_POSE_SMOOTHING) {
    const stages = Math.max(1, Math.round(config.stages));
    const timeConstantMs = config.timeConstantMs > 0 ? config.timeConstantMs : 1;
    this.config = { stages, timeConstantMs, lookahead: config.lookahead, linearInput: config.linearInput };
    this.lookaheadSeconds = config.lookahead ? (stages * timeConstantMs) / 1000 : 0;
    this.stageStates = Array.from({ length: stages - 1 }, () => new THREE.Quaternion());
  }

  /**
   * Advance the filter. `out` holds the previous output on entry and the new
   * pose on return; `target` is the (already computed) pose at `time`.
   */
  update(
    out: THREE.Quaternion,
    target: THREE.Quaternion,
    time: number,
    playbackDelta: number,
    poseAt: PoseAtTime,
  ): THREE.Quaternion {
    if (!isSmoothedPoseStep(playbackDelta)) {
      return this.reset(out, target, time);
    }
    if (!this.stagesCharged) this.chargeStages(out, poseAt);
    const alpha = poseSmoothingAlpha(playbackDelta, this.config.timeConstantMs);
    const c = linearInputWeight(playbackDelta, this.config.timeConstantMs);
    const input = this.lookaheadSeconds > 0
      ? poseAt(this.input, time + this.lookaheadSeconds)
      : this.input.copy(target);
    // Equivalent constant input of stage 1 over this step.
    if (this.config.linearInput) this.held.slerpQuaternions(this.previousInput, input, c);
    else this.held.copy(input);
    this.previousInput.copy(input);
    for (let i = 0; i <= this.stageStates.length; i += 1) {
      const stage = i < this.stageStates.length ? this.stageStates[i] : out;
      this.before.copy(stage);
      stage.slerp(this.held, alpha);
      // The next stage sees this stage's output moving from `before` to `stage`.
      if (this.config.linearInput) this.held.slerpQuaternions(this.before, stage, c);
      else this.held.copy(stage);
    }
    return out;
  }

  /** Snap the output to `target` (pause, seek, loop); stages recharge on the next step. */
  reset(out: THREE.Quaternion, target: THREE.Quaternion, time: number): THREE.Quaternion {
    out.copy(target);
    this.snapTime = time;
    this.stagesCharged = false;
    return out;
  }

  /**
   * Pre-charge the stages with their steady-state value for a constant turn
   * at the snap time: stage k of N lags the input by k·τ, so it holds
   * pose(t + (N−k)·τ), and the previous first-stage input is pose(t + N·τ).
   * Without this the cascade would start from rest and trail for ~N·τ after
   * every pause or seek.
   */
  private chargeStages(out: THREE.Quaternion, poseAt: PoseAtTime): void {
    const n = this.config.stages;
    const tau = this.config.timeConstantMs / 1000;
    const canSample = this.config.lookahead && Number.isFinite(this.snapTime);
    this.stageStates.forEach((stage, index) => {
      if (canSample) poseAt(stage, this.snapTime + (n - (index + 1)) * tau);
      else stage.copy(out);
    });
    if (canSample) poseAt(this.previousInput, this.snapTime + this.lookaheadSeconds);
    else this.previousInput.copy(out);
    this.stagesCharged = true;
  }
}

/** Yaw (radians, atan2(x, z) convention of the replay) of a pose quaternion. */
export function quaternionYaw(q: THREE.Quaternion, scratch = new THREE.Vector3()): number {
  scratch.set(0, 0, 1).applyQuaternion(q);
  return Math.atan2(scratch.x, scratch.z);
}
