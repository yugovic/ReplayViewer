/**
 * T4 — dump the pose the viewer DRAWS for every Fuji AiM lap, using the
 * viewer's own code (origin/master src/replay + src/engine/poseSmoothing; the
 * imported files are byte-identical to origin/master, checked by the caller).
 *
 *   npx vite-node artifacts/kerb-event-truth-2026-10-22/T4-cg-traces/dump_viewer_pose.ts -- --out <dir> [--hz 100] [--fps2 60]
 *
 * Reproduces ReplayScene.update/updateCar for continuous 1x playback from the
 * lap start (seek to t=0 = snap, then fixed playback steps):
 *   position  = sampleReplay(lap, track, t).{x,z}   (registered GPS, C1 Hermite)
 *   target    = sampleReplay(...).heading/pitch/roll -> setPoseQuaternion (copy of ReplayScene's)
 *   drawn yaw = PoseSmoother (DEFAULT_POSE_SMOOTHING: 2 x 70 ms, 140 ms look-ahead,
 *               linear-input) stepped with playbackDelta = 1/hz, quaternionYaw(pose)
 * Registration: gps_registration.json via withRegistration with the lap, limits
 * and reference-track SHA-256 exactly as dataLoader.ts (enabled unless any hash mismatches).
 * Times: t = recorded lap time (GPS base, playback clock); true lap time = t + delta
 * (meta.first_sample_after_lap_start_seconds, lapClock.ts).
 * A second run at --fps2 frames/s (default 60) gives the frame-rate sensitivity
 * of the drawn yaw (interpolated onto the --hz grid).
 * Writes <out>/viewer_pose_<race>_L<n>.json. Reads only public/data.
 */
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import * as THREE from "three";
import { sampleReplay } from "../../../src/replay/interpolation";
import { parseGpsRegistration, withRegistration } from "../../../src/replay/gpsRegistration";
import { lapStartOffsetSeconds } from "../../../src/replay/lapClock";
import type { LapData, TrackData } from "../../../src/replay/types";
import { DEFAULT_POSE_SMOOTHING, PoseSmoother, quaternionYaw } from "../../../src/engine/poseSmoothing";

const argv = process.argv.slice(2).filter((a) => a !== "--");
function arg(name: string, fallback: string): string {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : fallback;
}
const ROOT = resolve(arg("root", process.cwd()));
const OUT = resolve(arg("out", "."));
const HZ = Number(arg("hz", "100"));
const FPS2 = Number(arg("fps2", "60"));
const SESSIONS = ["fuji_aim_01", "fuji_aim_2020_07_30"];

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

const sha256 = (text: string) => createHash("sha256").update(text).digest("hex");
const wrap = (a: number) => Math.atan2(Math.sin(a), Math.cos(a));

/** Drawn yaw (unwrapped) at the given frame times, simulating updateCar's pose block. */
function drawnYaw(lap: LapData, track: TrackData, times: number[]) {
  const smoother = new PoseSmoother(DEFAULT_POSE_SMOOTHING);
  const poseAt = (out: THREE.Quaternion, time: number) => {
    const s = sampleReplay(lap, track, time);
    return setPoseQuaternion(out, s.heading, s.pitch, s.roll);
  };
  const pose = new THREE.Quaternion();
  const target = new THREE.Quaternion();
  const scratch = new THREE.Vector3();
  let lastTime: number | null = null;
  const yaw: number[] = [];
  for (const time of times) {
    const s = sampleReplay(lap, track, time);
    setPoseQuaternion(target, s.heading, s.pitch, s.roll);
    const playbackDelta = lastTime === null ? 0 : s.time - lastTime;
    lastTime = s.time;
    smoother.update(pose, target, s.time, playbackDelta, poseAt);
    const y = quaternionYaw(pose, scratch);
    const prev = yaw.length ? yaw[yaw.length - 1] : y;
    yaw.push(prev + wrap(y - prev));
  }
  return yaw;
}

function interp(xs: number[], ys: number[], x: number): number {
  if (x <= xs[0]) return ys[0];
  if (x >= xs[xs.length - 1]) return ys[ys.length - 1];
  let lo = 0, hi = xs.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (xs[mid] <= x) lo = mid; else hi = mid;
  }
  const u = (x - xs[lo]) / (xs[hi] - xs[lo]);
  return ys[lo] + (ys[hi] - ys[lo]) * u;
}

const r6 = (v: number) => Math.round(v * 1e6) / 1e6;

mkdirSync(OUT, { recursive: true });
const trackText = readFileSync(join(ROOT, "public/data/tracks/fuji/track.json"), "utf-8");
const track = JSON.parse(trackText) as TrackData;
track.trackId = "fuji";
track.visualProfile = "cg"; // ?look=cg; no alignment= study -> no replayAlignment
const manifest: Record<string, unknown>[] = [];
for (const race of SESSIONS) {
  const dir = join(ROOT, "public/data/races", race);
  const regText = readFileSync(join(dir, "gps_registration.json"), "utf-8");
  const file = parseGpsRegistration(JSON.parse(regText), race, "fuji");
  if (!file) throw new Error(`registration rejected for ${race}`);
  const limitsText = readFileSync(join(ROOT, file.limits!.source), "utf-8");
  const index = JSON.parse(readFileSync(join(dir, "laps.json"), "utf-8")) as { laps: { lap: number; data_file?: string }[] };
  for (const record of index.laps) {
    if (!record.data_file) continue;
    const text = readFileSync(join(dir, record.data_file), "utf-8");
    const raw = JSON.parse(text) as LapData;
    const lap = withRegistration(raw, file, true, sha256(text), sha256(limitsText), sha256(trackText));
    const reg = lap.registration!;
    const duration = lap.t[lap.t.length - 1];
    const n = Math.floor(duration * HZ + 1e-9) + 1;
    const times = Array.from({ length: n }, (_, k) => k / HZ);
    const x: number[] = [], z: number[] = [], yawT: number[] = [], pitch: number[] = [], roll: number[] = [], arc: number[] = [];
    let prevT = 0;
    for (const time of times) {
      const s = sampleReplay(lap, track, time);
      x.push(r6(s.x)); z.push(r6(s.z));
      const h = yawT.length ? prevT + wrap(s.heading - prevT) : s.heading;
      prevT = h;
      yawT.push(r6(h)); pitch.push(r6(s.pitch)); roll.push(r6(s.roll)); arc.push(r6(s.arcLength));
    }
    const yawD = drawnYaw(lap, track, times).map(r6);
    // frame-rate sensitivity: same playback at FPS2
    const n2 = Math.floor(duration * FPS2 + 1e-9) + 1;
    const times2 = Array.from({ length: n2 }, (_, k) => k / FPS2);
    const yaw2 = drawnYaw(lap, track, times2);
    const yawD2 = times.map((t) => r6(interp(times2, yaw2, t)));
    const out = {
      race, lap: lap.meta.lap, hz: HZ, fps2: FPS2,
      deltaSeconds: lapStartOffsetSeconds(lap.meta),
      durationSeconds: duration,
      registration: { enabled: reg.enabled, verified: reg.verified, inputVerification: reg.inputVerification,
        offsetMeters: reg.offsetMeters, antennaMeters: reg.antennaMeters },
      vehicleGeometry: reg.vehicleGeometry,
      lapSha256: sha256(text),
      t: times.map(r6), x, z, yawTarget: yawT, yawDrawn: yawD, yawDrawnFps2: yawD2, pitch, roll, viewerArcLength: arc,
    };
    const name = `viewer_pose_${race}_L${lap.meta.lap}.json`;
    writeFileSync(join(OUT, name), JSON.stringify(out));
    manifest.push({ race, lap: lap.meta.lap, file: name, samples: n, enabled: reg.enabled, verified: reg.verified });
    console.log(race, lap.meta.lap, n, "enabled", reg.enabled, "verified", reg.verified);
  }
}
writeFileSync(join(OUT, "viewer_pose_manifest.json"), JSON.stringify(manifest, null, 1));
