import * as THREE from "three";
import { describe, expect, it } from "vitest";
import {
  DEFAULT_POSE_SMOOTHING,
  LEGACY_POSE_SMOOTHING,
  PoseSmoother,
  isSmoothedPoseStep,
  linearInputWeight,
  poseSmoothingAlpha,
  quaternionYaw,
  type PoseSmoothingConfig,
} from "./poseSmoothing";

const UP = new THREE.Vector3(0, 1, 0);
const DEG = 180 / Math.PI;
const wrap = (a: number) => Math.atan2(Math.sin(a), Math.cos(a));
const yawPose = (out: THREE.Quaternion, heading: number) => out.setFromAxisAngle(UP, heading);

/** Plays `heading(t)` at a fixed frame step and returns per-frame yaw errors and yaws. */
function play(config: PoseSmoothingConfig, heading: (t: number) => number, duration: number, step = 1 / 60) {
  const smoother = new PoseSmoother(config);
  const poseAt = (out: THREE.Quaternion, time: number) => yawPose(out, heading(time));
  const out = new THREE.Quaternion();
  const target = new THREE.Quaternion();
  const errors: number[] = [];
  const yaws: number[] = [];
  let last: number | null = null;
  for (let k = 0; k * step <= duration + 1e-9; k += 1) {
    const time = k * step;
    yawPose(target, heading(time));
    smoother.update(out, target, time, last === null ? 0 : time - last, poseAt);
    last = time;
    const yaw = quaternionYaw(out);
    errors.push(Math.abs(wrap(yaw - heading(time))) * DEG);
    const prev = yaws.length ? yaws[yaws.length - 1] : yaw;
    yaws.push(prev + wrap(yaw - prev));
  }
  return { errors, yaws };
}

const maxAbsSecondDifference = (yaws: number[]) => {
  let max = 0;
  for (let k = 1; k + 1 < yaws.length; k += 1) {
    max = Math.max(max, Math.abs(yaws[k + 1] - 2 * yaws[k] + yaws[k - 1]) * DEG);
  }
  return max;
};

describe("pose smoothing helpers", () => {
  it("alpha follows 1 − exp(−Δ/τ) and is 0 without a forward step", () => {
    expect(poseSmoothingAlpha(0, 140)).toBe(0);
    expect(poseSmoothingAlpha(-0.1, 140)).toBe(0);
    expect(poseSmoothingAlpha(0.14, 140)).toBeCloseTo(1 - Math.exp(-1), 12);
  });

  it("filters only forward steps up to 0.25 s (pause, seek and loop snap)", () => {
    expect(isSmoothedPoseStep(0)).toBe(false);
    expect(isSmoothedPoseStep(-0.02)).toBe(false);
    expect(isSmoothedPoseStep(1 / 60)).toBe(true);
    expect(isSmoothedPoseStep(0.25)).toBe(true);
    expect(isSmoothedPoseStep(0.26)).toBe(false);
  });

  it("linear-input weight is ½ + Δ/(12τ) for short steps and exact for long ones", () => {
    expect(linearInputWeight(1e-7, 70)).toBeCloseTo(0.5, 6);
    expect(linearInputWeight(1 / 60, 70)).toBeCloseTo(0.5 + 1 / 60 / 0.07 / 12, 4);
    const x = 0.25 / 0.07;
    expect(linearInputWeight(0.25, 70)).toBeCloseTo(1 + 1 / Math.expm1(x) - 1 / x, 12);
    expect(linearInputWeight(0, 70)).toBe(0.5);
  });

  it("linear-input step is the exact solution for a ramp input (lag exactly τ)", () => {
    // scalar check of y1 = y0 + α(u0 + c(u1 − u0) − y0) against τẏ + y = u
    const tau = 0.07;
    const rate = 2;
    for (const step of [1 / 144, 1 / 60, 1 / 30, 0.2]) {
      const alpha = poseSmoothingAlpha(step, tau * 1000);
      const c = linearInputWeight(step, tau * 1000);
      const y0 = -rate * tau; // steady state at t = 0
      const u0 = 0;
      const u1 = rate * step;
      const y1 = y0 + alpha * (u0 + c * (u1 - u0) - y0);
      expect(y1).toBeCloseTo(u1 - rate * tau, 12);
    }
  });
});

describe("PoseSmoother", () => {
  const yawRate = 40 / DEG; // 40 °/s, a brisk steady corner

  it("legacy 140 ms filter trails a steady turn by ≈ yawRate × τ", () => {
    const { errors } = play(LEGACY_POSE_SMOOTHING, (t) => yawRate * t, 3);
    const settled = errors.slice(120);
    // discrete lag at 60 fps = 131.6 ms → 5.3°
    expect(Math.min(...settled)).toBeGreaterThan(5);
  });

  it("shipped filter has no steady-state lag in a steady turn", () => {
    const { errors } = play(DEFAULT_POSE_SMOOTHING, (t) => yawRate * t, 3);
    expect(Math.max(...errors)).toBeLessThan(0.01);
  });

  it("stays lag-free at other frame rates and playback speeds", () => {
    for (const step of [1 / 30, 1 / 144, 4 / 60]) {
      const { errors } = play(DEFAULT_POSE_SMOOTHING, (t) => yawRate * t, 3, step);
      expect(Math.max(...errors)).toBeLessThan(0.01);
    }
  });

  it("keeps a steady turn smooth with irregular frame times", () => {
    // ±40 % random frame durations: the drawn yaw rate must stay ≈ constant.
    const smoother = new PoseSmoother();
    const poseAt = (out: THREE.Quaternion, time: number) => yawPose(out, yawRate * time);
    const out = new THREE.Quaternion();
    const target = new THREE.Quaternion();
    let seed = 7;
    const rand = () => ((seed = (1664525 * seed + 1013904223) >>> 0) / 0x100000000);
    let time = 0;
    let last: number | null = null;
    let prevYaw = 0;
    let worstRateError = 0;
    let worstError = 0;
    for (let k = 0; k < 300; k += 1) {
      smoother.update(out, yawPose(target, yawRate * time), time, last === null ? 0 : time - last, poseAt);
      const yaw = quaternionYaw(out);
      if (last !== null) {
        const rate = wrap(yaw - prevYaw) / (time - last);
        worstRateError = Math.max(worstRateError, Math.abs(rate - yawRate) * DEG);
      }
      worstError = Math.max(worstError, Math.abs(wrap(yaw - yawRate * time)) * DEG);
      prevYaw = yaw;
      last = time;
      time += (1 / 60) * (1 + 0.4 * (2 * rand() - 1));
    }
    expect(worstError).toBeLessThan(0.01);
    expect(worstRateError).toBeLessThan(0.05); // °/s, vs a 40 °/s turn
  });

  it("snaps exactly to the target while paused or after a seek", () => {
    const smoother = new PoseSmoother();
    const poseAt = (out: THREE.Quaternion, time: number) => yawPose(out, yawRate * time);
    const out = yawPose(new THREE.Quaternion(), 0);
    const target = yawPose(new THREE.Quaternion(), 1.234);
    smoother.update(out, target, 10, 0, poseAt);
    expect(out.angleTo(target)).toBeLessThan(1e-6);
    yawPose(target, -0.5);
    smoother.update(out, target, 2, 1.5, poseAt); // seek jump > 0.25 s
    expect(out.angleTo(target)).toBeLessThan(1e-6);
  });

  it("pre-charges the cascade so resuming from pause does not trail", () => {
    // Paused mid-corner (Δ = 0 frames), then playback resumes.
    const smoother = new PoseSmoother();
    const poseAt = (out: THREE.Quaternion, time: number) => yawPose(out, yawRate * time);
    const out = new THREE.Quaternion();
    const target = new THREE.Quaternion();
    for (let i = 0; i < 5; i += 1) smoother.update(out, yawPose(target, yawRate * 2), 2, 0, poseAt);
    let worst = 0;
    for (let k = 1; k <= 30; k += 1) {
      const time = 2 + k / 60;
      smoother.update(out, yawPose(target, yawRate * time), time, 1 / 60, poseAt);
      worst = Math.max(worst, Math.abs(wrap(quaternionYaw(out) - yawRate * time)) * DEG);
    }
    expect(worst).toBeLessThan(0.05);
  });

  it("smooths 10 Hz yaw-rate steps at least as well as the legacy filter", () => {
    // Piecewise-linear heading: the yaw rate alternates 30/50 °/s every 100 ms,
    // like the per-sample interpolated replay target.
    const knots = Array.from({ length: 41 }, (_, i) => i / 10);
    const values: number[] = [0];
    for (let i = 1; i < knots.length; i += 1) values.push(values[i - 1] + ((i % 2 ? 30 : 50) / DEG) * 0.1);
    const heading = (t: number) => {
      const i = Math.min(knots.length - 2, Math.max(0, Math.floor(t * 10)));
      const u = Math.min(1, Math.max(0, (t - knots[i]) * 10));
      return values[i] + (values[i + 1] - values[i]) * u;
    };
    const raw = play({ stages: 1, timeConstantMs: 1e-6, lookahead: false, linearInput: false }, heading, 3.5);
    const legacy = play(LEGACY_POSE_SMOOTHING, heading, 3.5);
    const shipped = play(DEFAULT_POSE_SMOOTHING, heading, 3.5);
    const tail = (y: number[]) => y.slice(60);
    expect(maxAbsSecondDifference(tail(shipped.yaws))).toBeLessThan(maxAbsSecondDifference(tail(raw.yaws)) / 3);
    expect(maxAbsSecondDifference(tail(shipped.yaws))).toBeLessThanOrEqual(maxAbsSecondDifference(tail(legacy.yaws)) * 1.0001);
    // and it tracks the heading far better than the lagging legacy filter
    // (the residual is the ±0.5° zig-zag of the target itself, smoothed away)
    expect(Math.max(...shipped.errors.slice(60))).toBeLessThan(0.6);
    expect(Math.max(...legacy.errors.slice(60))).toBeGreaterThan(4);
  });
});
