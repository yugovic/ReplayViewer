/**
 * Runs the viewer's BEFORE (git HEAD, identical to baseline/src/replay/delta.ts)
 * and AFTER (working tree) lap-clock / ghost-delta code on real and synthetic laps.
 * Read-only for the repo; writes harness_out.json next to this file.
 *   npx vite-node artifacts/improvement-eval-2026-10-02/eval/c1c2-timebase-pose-kerbui-r2/timebase_harness.ts
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as after from "../../../../src/replay/delta";
import * as before from "../../baseline/src/replay/delta";
import { formatLapClock, lapStartOffsetMeters, trueLapTime } from "../../../../src/replay/lapClock";
import type { LapData } from "../../../../src/replay/types";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "../../../..");
const SESSIONS = ["fuji_aim_01", "fuji_aim_2020_07_30"];

/** Pre-change HUD formatter (git HEAD src/ui/Hud.tsx formatLapTime). */
function oldFormatLapTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rem = seconds - minutes * 60;
  const remStr = rem.toFixed(3).padStart(6, "0");
  return `${String(minutes).padStart(2, "0")}:${remStr}`;
}

const interp = (x: number, xs: number[], ys: number[]) => {
  if (x <= xs[0]) return ys[0];
  if (x >= xs[xs.length - 1]) return ys[ys.length - 1];
  let lo = 0, hi = xs.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (xs[mid] <= x) lo = mid; else hi = mid; }
  return ys[lo] + ((ys[hi] - ys[lo]) * (x - xs[lo])) / (xs[hi] - xs[lo]);
};
const r6 = (v: number) => Math.round(v * 1e6) / 1e6;

// ------------------------------------------------------------- real laps
const real: { id: string; race: string; lap: LapData }[] = [];
for (const race of SESSIONS) {
  const dir = join(ROOT, "public/data/races", race);
  const index = JSON.parse(readFileSync(join(dir, "laps.json"), "utf-8")) as { laps: { data_file?: string }[] };
  for (const rec of index.laps) {
    if (!rec.data_file) continue;
    const lap = JSON.parse(readFileSync(join(dir, rec.data_file), "utf-8")) as LapData;
    real.push({ id: `${race}/L${lap.meta.lap}`, race, lap });
  }
}

const clock: Record<string, unknown> = {};
for (const { id, lap } of real) {
  const t0 = lap.t[0];
  const delta = (lap.meta as { first_sample_after_lap_start_seconds?: number }).first_sample_after_lap_start_seconds ?? NaN;
  clock[id] = {
    beforeHud: oldFormatLapTime(t0),
    afterHud: formatLapClock(trueLapTime(t0, lap.meta)),
    expectedFromDelta: `00:00.${String(Math.round(delta * 1000)).padStart(3, "0")}`,
    s0: r6(lapStartOffsetMeters(lap)),
  };
}

const realPairs: unknown[] = [];
for (const m of real) {
  for (const g of real) {
    if (m === g || m.race !== g.race) continue; // the ghost picker lists laps of the loaded race
    const dist: number[] = [], b: number[] = [], a: number[] = [];
    for (const d of m.lap.dist) {
      dist.push(r6(d));
      b.push(r6(before.computeDeltaAtDist(m.lap, g.lap, d)));
      a.push(r6(after.computeDeltaAtDist(m.lap, g.lap, d)));
    }
    realPairs.push({ main: m.id, ghost: g.id, s0Main: lapStartOffsetMeters(m.lap), s0Ghost: lapStartOffsetMeters(g.lap), dist, before: b, after: a });
  }
}

// -------------------------------------------------------- synthetic laps
interface Syn { source: string; delta: number; s0: number; t: number[]; dist: number[]; speed: number[] }
const syn = JSON.parse(readFileSync(join(HERE, "synthetic_laps.json"), "utf-8")) as Syn[];
const toLap = (s: Syn): LapData => {
  const zeros = s.t.map(() => 0);
  return {
    meta: { race_id: "syn", track_id: "fuji", lap: 0, first_sample_after_lap_start_seconds: s.delta } as unknown as LapData["meta"],
    t: s.t, dist: s.dist, speed: s.speed, lat: zeros, lng: zeros, aps: zeros, brake: zeros, steer: zeros, gear: zeros, accx: zeros, accy: zeros,
  } as LapData;
};
const synOut: unknown[] = [];
const sources = [...new Set(syn.map((s) => s.source))];
for (const src of sources) {
  const recs = syn.filter((s) => s.source === src);
  for (const sm of recs) {
    for (const sg of recs) {
      if (sm === sg) continue;
      const main = toLap(sm), ghost = toLap(sg);
      const maxTrue = Math.min(sm.dist[sm.dist.length - 1] + sm.s0, sg.dist[sg.dist.length - 1] + sg.s0) - 1;
      const b: number[] = [], a: number[] = [], pb: number[] = [], pa: number[] = [];
      for (let k = 0; k < sm.t.length; k += 1) {
        const d = sm.dist[k];
        if (d + sm.s0 > maxTrue) break;
        b.push(r6(before.computeDeltaAtDist(main, ghost, d))); // truth = 0
        a.push(r6(after.computeDeltaAtDist(main, ghost, d)));
        const trueMain = d + sm.s0;
        const tgB = before.ghostTimeForMainTime(main, ghost, sm.t[k]);
        const tgA = after.ghostTimeForMainTime(main, ghost, sm.t[k]);
        pb.push(r6(interp(tgB, sg.t, sg.dist) + sg.s0 - trueMain));
        pa.push(r6(interp(tgA, sg.t, sg.dist) + sg.s0 - trueMain));
      }
      synOut.push({ source: src, deltaMain: sm.delta, deltaGhost: sg.delta, before: b, after: a, ghostPosBeforeM: pb, ghostPosAfterM: pa,
        s0TrueMain: sm.s0, s0CodeMain: lapStartOffsetMeters(main) });
    }
  }
}

writeFileSync(join(HERE, "harness_out.json"), JSON.stringify({ clock, real: realPairs, synthetic: synOut }));
console.log("clock", JSON.stringify(clock));
console.log(`real pairs ${realPairs.length}, synthetic pairs ${synOut.length}`);
