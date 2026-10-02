/**
 * Viewer time base + playback pose-lag evaluation (Fuji, all 10 AiM laps).
 *
 * Runs the viewer's own code (sampleReplay, PoseSmoother, delta/lapClock) in
 * Node and simulates frame-by-frame playback, so the numbers describe what the
 * browser draws, not a re-implementation.
 *
 *   npx vite-node scripts/quality/apex_kpi/pose_lag_eval.ts -- [--out <dir>] [--root <repo>] [--fps 60]
 *
 * Outputs (default: artifacts/improvement-eval-2026-10-02/after/viewer-timebase-pose):
 *   pose_lag_eval.json   heading error vs target, jitter, variants, robustness
 *   timebase_eval.json   lap clock δ, position gap at equal clock, ghost-delta truth test
 *
 * Definitions (heading = yaw of the drawn car pose, atan2(x, z)):
 *   error   |wrap(yaw_drawn − yaw_target)| per frame, target = sampleReplay(t).heading
 *   step    |yaw[k] − yaw[k−1]| per frame (deg/frame)          — first difference
 *   jitter  |yaw[k+1] − 2·yaw[k] + yaw[k−1]| per frame (deg/frame) — change of the
 *           per-frame step; spikes when the yaw rate jumps (10 Hz knots). This is
 *           the smoothness measure; lower = smoother.
 * Only local, already-published lap JSON is read; nothing is written outside --out.
 */
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import * as THREE from "three";
import { sampleReplay } from "../../../src/replay/interpolation";
import { parseGpsRegistration, withRegistration } from "../../../src/replay/gpsRegistration";
import { computeDeltaAtDist, timeAtDist } from "../../../src/replay/delta";
import { lapStartOffsetMeters, lapStartOffsetSeconds } from "../../../src/replay/lapClock";
import type { LapData, TrackData } from "../../../src/replay/types";
import {
  DEFAULT_POSE_SMOOTHING,
  LEGACY_POSE_SMOOTHING,
  PoseSmoother,
  quaternionYaw,
  type PoseSmoothingConfig,
} from "../../../src/engine/poseSmoothing";
import { renderReport, type TracePoint } from "./pose_lag_eval_report";

// ---------------------------------------------------------------- arguments
const argv = process.argv.slice(2).filter((a) => a !== "--");
function arg(name: string, fallback: string): string {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : fallback;
}
const ROOT = resolve(arg("root", process.cwd()));
const OUT = resolve(ROOT, arg("out", "artifacts/improvement-eval-2026-10-02/after/viewer-timebase-pose"));
const FPS = Number(arg("fps", "60"));
const SESSIONS = ["fuji_aim_01", "fuji_aim_2020_07_30"];
const DEG = 180 / Math.PI;

// --------------------------------------------- pose (copy of ReplayScene's)
// Identical to ReplayScene.setPoseQuaternion (private there).
const WORLD_UP = new THREE.Vector3(0, 1, 0);
const P_F = new THREE.Vector3();
const P_R = new THREE.Vector3();
const P_U = new THREE.Vector3();
const P_B = new THREE.Matrix4();
const P_LF = new THREE.Vector3(0, 0, 1);
const P_RQ = new THREE.Quaternion();
function setPoseQuaternion(out: THREE.Quaternion, heading: number, pitch: number, roll = 0): THREE.Quaternion {
  const forward = P_F.set(Math.sin(heading), 0, Math.cos(heading)).multiplyScalar(Math.cos(pitch));
  forward.y = Math.sin(pitch);
  forward.normalize();
  const right = P_R.crossVectors(WORLD_UP, forward);
  if (right.lengthSq() < 0.000001) {
    out.setFromAxisAngle(WORLD_UP, heading);
  } else {
    right.normalize();
    const up = P_U.crossVectors(forward, right).normalize();
    out.setFromRotationMatrix(P_B.makeBasis(right, up, forward));
  }
  if (roll !== 0) out.multiply(P_RQ.setFromAxisAngle(P_LF, roll));
  return out;
}

// ------------------------------------------------------------------ loading
interface LoadedLap {
  id: string;
  lap: LapData;
  registration: { enabled: boolean; verified: boolean | null } | null;
}
const sha256 = (text: string) => createHash("sha256").update(text).digest("hex");

function loadLaps(track: TrackData): LoadedLap[] {
  const laps: LoadedLap[] = [];
  for (const race of SESSIONS) {
    const dir = join(ROOT, "public/data/races", race);
    const regText = readFileSync(join(dir, "gps_registration.json"), "utf-8");
    const file = parseGpsRegistration(JSON.parse(regText), race, track.trackId);
    const index = JSON.parse(readFileSync(join(dir, "laps.json"), "utf-8")) as { laps: { lap: number; data_file?: string }[] };
    for (const record of index.laps) {
      if (!record.data_file) continue;
      const text = readFileSync(join(dir, record.data_file), "utf-8");
      const raw = JSON.parse(text) as LapData;
      const lap = withRegistration(raw, file, true, sha256(text));
      laps.push({
        id: `${race}/L${raw.meta.lap}`,
        lap,
        registration: lap.registration
          ? { enabled: lap.registration.enabled, verified: lap.registration.verified } : null,
      });
    }
  }
  return laps;
}

// ------------------------------------------------------------------ helpers
function quantile(values: number[], q: number): number {
  if (values.length === 0) return Number.NaN;
  const sorted = Float64Array.from(values).sort();
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}
const r3 = (v: number) => Math.round(v * 1000) / 1000;
const r4 = (v: number) => Math.round(v * 10000) / 10000;
const wrap = (a: number) => Math.atan2(Math.sin(a), Math.cos(a));
function stats(values: number[], digits = r3) {
  return {
    n: values.length,
    median: digits(quantile(values, 0.5)),
    p95: digits(quantile(values, 0.95)),
    p99: digits(quantile(values, 0.99)),
    max: digits(values.reduce((m, v) => Math.max(m, v), 0)),
  };
}

// ------------------------------------------------------- playback simulation
interface Frame { time: number; heading: number; dist: number; target: THREE.Quaternion }

/** Frame times for one lap: wall-clock dt × rate, optional seeded dt jitter. */
function frameTimes(duration: number, fps: number, rate: number, jitter = 0, seed = 1, pauseEvery = 0): number[] {
  const times = [0];
  let state = seed >>> 0;
  const rand = () => {
    state = (1664525 * state + 1013904223) >>> 0;
    return state / 0x100000000;
  };
  let t = 0;
  let nextPause = pauseEvery > 0 ? pauseEvery : Number.POSITIVE_INFINITY;
  while (t < duration) {
    const scale = jitter > 0 ? 1 + jitter * (2 * rand() - 1) : 1;
    t = Math.min(duration, t + (rate / fps) * scale);
    times.push(t);
    if (t >= nextPause) {
      // paused for 10 frames (Δ = 0 → snap), then playback resumes
      for (let i = 0; i < 10; i += 1) times.push(t);
      nextPause += pauseEvery;
    }
  }
  return times;
}

function buildFrames(lap: LapData, track: TrackData, times: number[]): Frame[] {
  return times.map((time) => {
    const s = sampleReplay(lap, track, time);
    return {
      time: s.time,
      heading: s.heading,
      dist: s.telemetry.dist,
      target: setPoseQuaternion(new THREE.Quaternion(), s.heading, s.pitch, s.roll),
    };
  });
}

interface RunResult {
  errorsDeg: number[];
  stepsDeg: number[];
  jitterDeg: number[];
  rateJitterDegPerSec: number[];
  worst: { lap: string; time: number; dist: number; errorDeg: number };
}

/** Simulate updateCar's pose block (config = null → raw target, no filter). */
function simulate(laps: LoadedLap[], track: TrackData, framesByLap: Frame[][], config: PoseSmoothingConfig | null): RunResult {
  const errorsDeg: number[] = [];
  const stepsDeg: number[] = [];
  const jitterDeg: number[] = [];
  const rateJitterDegPerSec: number[] = [];
  let worst = { lap: "", time: 0, dist: 0, errorDeg: -1 };
  const scratch = new THREE.Vector3();
  laps.forEach(({ id, lap }, li) => {
    const frames = framesByLap[li];
    const smoother = config ? new PoseSmoother(config) : null;
    const poseAt = (out: THREE.Quaternion, time: number) => {
      const s = sampleReplay(lap, track, time);
      return setPoseQuaternion(out, s.heading, s.pitch, s.roll);
    };
    const pose = new THREE.Quaternion();
    let lastTime: number | null = null;
    const yaws: number[] = [];
    const times: number[] = [];
    for (const frame of frames) {
      const playbackDelta = lastTime === null ? 0 : frame.time - lastTime;
      lastTime = frame.time;
      if (smoother) smoother.update(pose, frame.target, frame.time, playbackDelta, poseAt);
      else pose.copy(frame.target);
      const yaw = quaternionYaw(pose, scratch);
      const error = Math.abs(wrap(yaw - frame.heading)) * DEG;
      errorsDeg.push(error);
      if (error > worst.errorDeg) worst = { lap: id, time: r3(frame.time), dist: r3(frame.dist), errorDeg: r3(error) };
      // unwrap onto the previous yaw for differences
      const prev = yaws.length ? yaws[yaws.length - 1] : yaw;
      yaws.push(prev + wrap(yaw - prev));
      times.push(frame.time);
    }
    for (let k = 1; k < yaws.length; k += 1) stepsDeg.push(Math.abs(yaws[k] - yaws[k - 1]) * DEG);
    for (let k = 1; k + 1 < yaws.length; k += 1) {
      jitterDeg.push(Math.abs(yaws[k + 1] - 2 * yaws[k] + yaws[k - 1]) * DEG);
      // frame-time-normalised: change of the drawn yaw RATE between frames
      const d1 = times[k] - times[k - 1];
      const d2 = times[k + 1] - times[k];
      if (d1 > 0 && d2 > 0) {
        rateJitterDegPerSec.push(Math.abs((yaws[k + 1] - yaws[k]) / d2 - (yaws[k] - yaws[k - 1]) / d1) * DEG);
      }
    }
  });
  return { errorsDeg, stepsDeg, jitterDeg, rateJitterDegPerSec, worst };
}

function summarize(result: RunResult) {
  const lateral = (d: number) => result.errorsDeg.map((e) => d * Math.tan(e / DEG));
  return {
    headingErrorDeg: stats(result.errorsDeg),
    lateralShiftAt20mM: stats(lateral(20)),
    lateralShiftAt50mM: stats(lateral(50)),
    stepDegPerFrame: stats(result.stepsDeg, r4),
    jitterDegPerFrame: stats(result.jitterDeg, r4),
    yawRateChangeDegPerSecPerFrame: stats(result.rateJitterDegPerSec),
    worst: result.worst,
  };
}

/** Signed heading error (drawn − target, deg) of BEFORE/AFTER in a window of one lap. */
function traceWindow(lap: LapData, track: TrackData, frames: Frame[], fromTime: number, toTime: number): TracePoint[] {
  const poseAt = (out: THREE.Quaternion, time: number) => {
    const s = sampleReplay(lap, track, time);
    return setPoseQuaternion(out, s.heading, s.pitch, s.roll);
  };
  const run = (config: PoseSmoothingConfig) => {
    const smoother = new PoseSmoother(config);
    const pose = new THREE.Quaternion();
    let lastTime: number | null = null;
    return frames.map((frame) => {
      smoother.update(pose, frame.target, frame.time, lastTime === null ? 0 : frame.time - lastTime, poseAt);
      lastTime = frame.time;
      return wrap(quaternionYaw(pose) - frame.heading) * DEG;
    });
  };
  const before = run(LEGACY_POSE_SMOOTHING);
  const after = run(DEFAULT_POSE_SMOOTHING);
  const points: TracePoint[] = [];
  frames.forEach((frame, i) => {
    if (frame.time < fromTime || frame.time > toTime || i % 2 !== 0) return;
    points.push({ time: r3(frame.time), dist: r3(frame.dist), headingDeg: r3(frame.heading * DEG),
      beforeDeg: r3(before[i]), afterDeg: r4(after[i]) });
  });
  return points;
}

const label = (c: PoseSmoothingConfig | null) =>
  c ? `${c.stages}x${c.timeConstantMs}ms${c.lookahead ? "+lookahead" : ""}${c.linearInput ? "+linearInput" : ""}` : "raw-target";

// -------------------------------------------------------------- time base
/** Continuous "truth" lap from a real lap: T(S) = t(dist), line at S=0, T=0. */
function synthesizeRecordedLap(source: LapData, delta: number, sampleHz = 10): LapData {
  const duration = source.t[source.t.length - 1];
  const distAt = (T: number) => {
    // invert t → dist on the source (linear)
    const { t, dist } = source;
    if (T <= t[0]) return dist[0];
    if (T >= t[t.length - 1]) return dist[dist.length - 1];
    let lo = 0;
    let hi = t.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (t[mid] <= T) lo = mid; else hi = mid;
    }
    return dist[lo] + ((dist[hi] - dist[lo]) * (T - t[lo])) / (t[hi] - t[lo]);
  };
  const t: number[] = [];
  const dist: number[] = [];
  const speed: number[] = [];
  const s0 = distAt(delta);
  for (let T = delta; T <= duration + 1e-9; T += 1 / sampleHz) {
    t.push(Math.round((T - delta) * 1e6) / 1e6);
    dist.push(distAt(T) - s0);
    speed.push(((distAt(T + 0.05) - distAt(T - 0.05)) / 0.1) * 3.6);
  }
  const n = t.length;
  const zeros = Array(n).fill(0);
  return {
    meta: { ...source.meta, first_sample_after_lap_start_seconds: delta } as LapData["meta"],
    t, dist, speed, lat: zeros, lng: zeros, aps: zeros, brake: zeros, steer: zeros, gear: zeros, accx: zeros, accy: zeros,
  };
}

/** Pre-change computeDeltaAtDist (raw t/dist, δ ignored). */
const legacyDelta = (main: LapData, ghost: LapData, d: number) => timeAtDist(main, d) - timeAtDist(ghost, d);

function timebaseEval(laps: LoadedLap[]) {
  // 1) Lap clock: at the same displayed lap time X, how far is the drawn car
  //    from where the car really was at lap time X? (before: shows t = X)
  const clock = laps.map(({ id, lap }) => {
    const delta = lapStartOffsetSeconds(lap.meta);
    const gaps: number[] = [];
    const duration = lap.t[lap.t.length - 1];
    for (let X = delta; X <= duration; X += 0.05) {
      // before: playback t = X  (true time X + δ); truth: t = X − δ
      const before = timeAtDistInverse(lap, X);
      const truth = timeAtDistInverse(lap, X - delta);
      gaps.push(Math.abs(before - truth));
    }
    return {
      lap: id,
      deltaSeconds: delta,
      startOffsetMeters: r3(lapStartOffsetMeters(lap)),
      lastSampleTrueTime: r3(lap.t[lap.t.length - 1] + delta),
      officialLapTime: lap.meta.lap_time_seconds,
      beforeAheadMeters: stats(gaps),
      afterAheadMeters: 0,
    };
  });

  // 2) Ghost delta vs ground truth: synthetic recordings of real speed
  //    profiles with known timing-line phase δ (truth = continuous T(S)).
  const pairs: { main: string; ghost: string; dm: number; dg: number; legacy: number[]; fixed: number[]; naive: number[]; startLegacy: number; startFixed: number; startNaive: number }[] = [];
  const truthT = (source: LapData, S: number) => timeAtDist(source, S);
  for (let i = 0; i < laps.length; i += 1) {
    for (let j = 0; j < laps.length; j += 1) {
      if (i === j) continue;
      const srcM = laps[i].lap;
      const srcG = laps[j].lap;
      const dm = lapStartOffsetSeconds(srcM.meta);
      const dg = lapStartOffsetSeconds(srcG.meta);
      const recM = synthesizeRecordedLap(srcM, dm);
      const recG = synthesizeRecordedLap(srcG, dg);
      const s0m = truthDistAt(srcM, dm);
      const s0g = truthDistAt(srcG, dg);
      const legacy: number[] = [];
      const fixed: number[] = [];
      const naive: number[] = [];
      // Compare only where BOTH recordings have samples (the last sample is up
      // to one interval before the source end; beyond it every method clamps).
      const maxS = Math.min(recM.dist[recM.dist.length - 1] + s0m, recG.dist[recG.dist.length - 1] + s0g);
      let first = true;
      let startLegacy = 0;
      let startFixed = 0;
      let startNaive = 0;
      for (let k = 0; k < recM.dist.length; k += 1) {
        const d = recM.dist[k];
        const S = d + s0m; // true distance of this main sample
        if (S > maxS) break;
        const truth = truthT(srcM, S) - truthT(srcG, S);
        const eLegacy = legacyDelta(recM, recG, d) - truth;
        const eFixed = computeDeltaAtDist(recM, recG, d) - truth;
        const eNaive = legacyDelta(recM, recG, d) + dm - dg - truth;
        legacy.push(Math.abs(eLegacy));
        fixed.push(Math.abs(eFixed));
        naive.push(Math.abs(eNaive));
        if (first) { startLegacy = eLegacy; startFixed = eFixed; startNaive = eNaive; first = false; }
      }
      pairs.push({ main: laps[i].id, ghost: laps[j].id, dm, dg, legacy, fixed, naive, startLegacy, startFixed, startNaive });
    }
  }
  const all = (key: "legacy" | "fixed" | "naive") => pairs.flatMap((p) => p[key]).map((v) => v * 1000);
  const perPairMax = (key: "legacy" | "fixed" | "naive") => pairs.map((p) => Math.max(...p[key]) * 1000);
  const worstPair = pairs.reduce((w, p) => (Math.max(...p.legacy) > Math.max(...w.legacy) ? p : w), pairs[0]);

  // 3) Real data: how much the shipped HUD delta changes (no truth available).
  const changeMs: number[] = [];
  for (let i = 0; i < laps.length; i += 1) {
    for (let j = 0; j < laps.length; j += 1) {
      if (i === j) continue;
      const m = laps[i].lap;
      const g = laps[j].lap;
      for (const d of m.dist) changeMs.push(Math.abs(computeDeltaAtDist(m, g, d) - legacyDelta(m, g, d)) * 1000);
    }
  }

  return {
    clock,
    clockSummary: {
      beforeAheadMetersMedianOfLapMedians: r3(quantile(clock.map((c) => c.beforeAheadMeters.median), 0.5)),
      beforeAheadMetersMax: Math.max(...clock.map((c) => c.beforeAheadMeters.max)),
      afterAheadMeters: 0,
      note: "after: HUD/transport clock show t + δ, so at displayed lap time X the drawn car is the sample at true lap time X (exact by construction)",
    },
    ghostDeltaTruthTest: {
      method: "Each real lap's (t, dist) is taken as a continuous truth T(S) from the timing line; it is re-sampled at 10 Hz starting δ after the line (its real δ) with dist from the first sample, exactly like the pipeline. 90 ordered main/ghost pairs; error = computed − true delta at each main sample.",
      errorMs: { legacy: stats(all("legacy")), naiveAddDeltaOnly: stats(all("naive")), fixed: stats(all("fixed")) },
      perPairMaxErrorMs: { legacy: stats(perPairMax("legacy")), naiveAddDeltaOnly: stats(perPairMax("naive")), fixed: stats(perPairMax("fixed")) },
      atFirstMainSampleMs: {
        legacy: stats(pairs.map((p) => Math.abs(p.startLegacy) * 1000)),
        naiveAddDeltaOnly: stats(pairs.map((p) => Math.abs(p.startNaive) * 1000)),
        fixed: stats(pairs.map((p) => Math.abs(p.startFixed) * 1000)),
      },
      worstLegacyPair: { main: worstPair.main, ghost: worstPair.ghost, deltaMain: worstPair.dm, deltaGhost: worstPair.dg,
        legacyMaxMs: r3(Math.max(...worstPair.legacy) * 1000), fixedMaxMs: r3(Math.max(...worstPair.fixed) * 1000) },
    },
    realDataHudDeltaChangeMs: stats(changeMs),
  };
}

/** Recorded distance at recorded time t (linear in the lap's own arrays). */
function timeAtDistInverse(lap: LapData, time: number): number {
  const { t, dist } = lap;
  if (time <= t[0]) return dist[0] + (time - t[0]) * ((dist[1] - dist[0]) / (t[1] - t[0]));
  if (time >= t[t.length - 1]) return dist[dist.length - 1];
  let lo = 0;
  let hi = t.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (t[mid] <= time) lo = mid; else hi = mid;
  }
  return dist[lo] + ((dist[hi] - dist[lo]) * (time - t[lo])) / (t[hi] - t[lo]);
}
const truthDistAt = (source: LapData, T: number) => timeAtDistInverse(source, T);

// --------------------------------------------------------------------- main
function main() {
  const t0 = Date.now();
  const track = JSON.parse(readFileSync(join(ROOT, "public/data/tracks/fuji/track.json"), "utf-8")) as TrackData;
  const laps = loadLaps(track);
  if (laps.length !== 10) throw new Error(`expected 10 Fuji laps, found ${laps.length}`);

  const variants: (PoseSmoothingConfig | null)[] = [
    LEGACY_POSE_SMOOTHING,
    { stages: 1, timeConstantMs: 40, lookahead: false, linearInput: false },
    { stages: 1, timeConstantMs: 140, lookahead: true, linearInput: false },
    { stages: 1, timeConstantMs: 140, lookahead: true, linearInput: true },
    { stages: 2, timeConstantMs: 70, lookahead: true, linearInput: false },
    DEFAULT_POSE_SMOOTHING,
    { stages: 2, timeConstantMs: 50, lookahead: true, linearInput: true },
    { stages: 2, timeConstantMs: 100, lookahead: true, linearInput: true },
    { stages: 3, timeConstantMs: 47, lookahead: true, linearInput: true },
    null,
  ];
  const baseFrames = laps.map(({ lap }) => buildFrames(lap, track, frameTimes(lap.t[lap.t.length - 1], FPS, 1)));
  const variantResults: Record<string, ReturnType<typeof summarize>> = {};
  for (const v of variants) {
    variantResults[label(v)] = summarize(simulate(laps, track, baseFrames, v));
    console.log(label(v), JSON.stringify(variantResults[label(v)].headingErrorDeg), "jitter p99", variantResults[label(v)].jitterDegPerFrame.p99);
  }

  // Robustness: other frame rates, playback rates, irregular frame times.
  const scenarios = [
    { name: "30fps-1x", fps: 30, rate: 1, jitter: 0 },
    { name: "144fps-1x", fps: 144, rate: 1, jitter: 0 },
    { name: "60fps-2x", fps: 60, rate: 2, jitter: 0 },
    { name: "60fps-4x", fps: 60, rate: 4, jitter: 0 },
    { name: "60fps-0.5x", fps: 60, rate: 0.5, jitter: 0 },
    { name: "60fps-1x-dtJitter40%", fps: 60, rate: 1, jitter: 0.4 },
    { name: "60fps-1x-pause10framesEvery3s", fps: 60, rate: 1, jitter: 0, pauseEvery: 3 },
  ];
  const robustness: Record<string, Record<string, ReturnType<typeof summarize>>> = {};
  for (const sc of scenarios) {
    const frames = laps.map(({ lap }, i) =>
      buildFrames(lap, track, frameTimes(lap.t[lap.t.length - 1], sc.fps, sc.rate, sc.jitter, 17 + i, sc.pauseEvery ?? 0)));
    robustness[sc.name] = {};
    for (const v of [LEGACY_POSE_SMOOTHING, DEFAULT_POSE_SMOOTHING]) {
      robustness[sc.name][label(v)] = summarize(simulate(laps, track, frames, v));
    }
    const b = robustness[sc.name][label(LEGACY_POSE_SMOOTHING)];
    const a = robustness[sc.name][label(DEFAULT_POSE_SMOOTHING)];
    console.log(sc.name, "p95/max before", b.headingErrorDeg.p95, b.headingErrorDeg.max, "after", a.headingErrorDeg.p95, a.headingErrorDeg.max,
      "| jitter p99 before", b.jitterDegPerFrame.p99, "after", a.jitterDegPerFrame.p99,
      "| yaw-rate change p99 before", b.yawRateChangeDegPerSecPerFrame.p99, "after", a.yawRateChangeDegPerSecPerFrame.p99);
  }

  const before = variantResults[label(LEGACY_POSE_SMOOTHING)];
  const after = variantResults[label(DEFAULT_POSE_SMOOTHING)];
  const pose = {
    generatedAt: new Date().toISOString(),
    command: "npx vite-node scripts/quality/apex_kpi/pose_lag_eval.ts",
    laps: laps.map((l) => ({ id: l.id, samples: l.lap.t.length, durationSeconds: l.lap.t[l.lap.t.length - 1], registration: l.registration })),
    simulation: { fps: FPS, playbackRate: 1, frames: baseFrames.reduce((n, f) => n + f.length, 0),
      definitions: {
        headingErrorDeg: "|wrap(yaw(drawn pose) − sampleReplay(t).heading)| per frame",
        stepDegPerFrame: "|yaw[k] − yaw[k−1]|",
        jitterDegPerFrame: "|yaw[k+1] − 2 yaw[k] + yaw[k−1]| (change of the per-frame step; smoothness)",
        yawRateChangeDegPerSecPerFrame: "|Δyaw[k+1]/Δt[k+1] − Δyaw[k]/Δt[k]| (frame-time-normalised smoothness; use for irregular frame times)",
        lateralShiftAtDm: "d · tan(headingError): sideways displacement of scenery d metres ahead in the cockpit view",
      } },
    before: { config: LEGACY_POSE_SMOOTHING, ...before },
    after: { config: DEFAULT_POSE_SMOOTHING, ...after },
    targets: {
      p95BelowOneDegree: after.headingErrorDeg.p95 < 1,
      jitterP99NotWorse: after.jitterDegPerFrame.p99 <= before.jitterDegPerFrame.p99,
      jitterMaxNotWorse: after.jitterDegPerFrame.max <= before.jitterDegPerFrame.max,
      yawRateChangeP99NotWorse:
        after.yawRateChangeDegPerSecPerFrame.p99 <= before.yawRateChangeDegPerSecPerFrame.p99,
      robustnessP95BelowOneDegree: Object.values(robustness).every((r) =>
        r[label(DEFAULT_POSE_SMOOTHING)].headingErrorDeg.p95 < 1),
      robustnessYawRateChangeP99NotWorse: Object.values(robustness).every((r) =>
        r[label(DEFAULT_POSE_SMOOTHING)].yawRateChangeDegPerSecPerFrame.p99 <=
          r[label(LEGACY_POSE_SMOOTHING)].yawRateChangeDegPerSecPerFrame.p99),
    },
    variants: variantResults,
    robustness,
    elapsedSeconds: (Date.now() - t0) / 1000,
  };
  const timebase = { generatedAt: pose.generatedAt, ...timebaseEval(laps) };
  // Trace around the worst BEFORE frame (Dunlop chicane) for the report figure.
  const worstIndex = laps.findIndex((l) => l.id === before.worst.lap);
  const trace = traceWindow(laps[worstIndex].lap, track, baseFrames[worstIndex],
    before.worst.time - 6, before.worst.time + 6);
  mkdirSync(OUT, { recursive: true });
  writeFileSync(join(OUT, "pose_lag_trace.json"), JSON.stringify({ lap: before.worst.lap, points: trace }, null, 1) + "\n");
  writeFileSync(join(OUT, "report.html"), renderReport({ pose, timebase, trace, traceLap: before.worst.lap }));
  writeFileSync(join(OUT, "pose_lag_eval.json"), JSON.stringify(pose, null, 2) + "\n");
  writeFileSync(join(OUT, "timebase_eval.json"), JSON.stringify(timebase, null, 2) + "\n");
  console.log("before", JSON.stringify(before.headingErrorDeg), "jitter", JSON.stringify(before.jitterDegPerFrame));
  console.log("after ", JSON.stringify(after.headingErrorDeg), "jitter", JSON.stringify(after.jitterDegPerFrame));
  console.log("targets", JSON.stringify(pose.targets));
  console.log("timebase clock", JSON.stringify(timebase.clockSummary));
  console.log("ghost delta truth", JSON.stringify(timebase.ghostDeltaTruthTest.errorMs));
  console.log(`wrote ${OUT} in ${pose.elapsedSeconds}s`);
}

main();
