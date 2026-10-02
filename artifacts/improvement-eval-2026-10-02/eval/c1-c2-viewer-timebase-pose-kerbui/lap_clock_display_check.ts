/**
 * Evaluator check (C1 lap clock / ghost delta). Read-only on repo data.
 *   npx vite-node artifacts/improvement-eval-2026-10-02/eval/c1-c2-viewer-timebase-pose-kerbui/lap_clock_display_check.ts
 * Writes lap_clock_display_check.json + delta_ts_dump.json next to this file.
 *
 * BEFORE = the baseline snapshot's own code (baseline/src/replay/delta.ts and
 * the formatLapTime/formatClock bodies copied verbatim from baseline Hud.tsx /
 * ReplayControls.tsx, which were module-private). AFTER = current src.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { LapData } from "../../../../src/replay/types";
import { formatLapClock, formatTransportClock, lapStartOffsetMeters, lapStartOffsetSeconds, trueLapTime } from "../../../../src/replay/lapClock";
import { computeDeltaAtDist, ghostTimeForMainTime } from "../../../../src/replay/delta";
import {
  computeDeltaAtDist as baselineDelta,
  ghostTimeForMainTime as baselineGhostTime,
} from "../../baseline/src/replay/delta";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "../../../..");

// verbatim from baseline src/ui/Hud.tsx
function formatLapTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rem = seconds - minutes * 60;
  const remStr = rem.toFixed(3).padStart(6, "0");
  return `${String(minutes).padStart(2, "0")}:${remStr}`;
}
// verbatim from baseline src/ui/ReplayControls.tsx
function formatClock(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rem = Math.floor(seconds - minutes * 60);
  return `${String(minutes).padStart(2, "0")}:${String(rem).padStart(2, "0")}`;
}

const laps: { id: string; lap: LapData }[] = [];
for (const race of ["fuji_aim_01", "fuji_aim_2020_07_30"]) {
  const dir = join(ROOT, "public/data/races", race);
  const index = JSON.parse(readFileSync(join(dir, "laps.json"), "utf-8")) as { laps: { data_file?: string }[] };
  for (const rec of index.laps) {
    if (!rec.data_file) continue;
    const lap = JSON.parse(readFileSync(join(dir, rec.data_file), "utf-8")) as LapData;
    laps.push({ id: `${race}/L${lap.meta.lap}`, lap });
  }
}

const clock = laps.map(({ id, lap }) => {
  const t0 = lap.t[0];
  const tEnd = lap.t[lap.t.length - 1];
  return {
    lap: id,
    delta: lapStartOffsetSeconds(lap.meta),
    s0: lapStartOffsetMeters(lap),
    firstSample: {
      hudBefore: formatLapTime(t0),
      hudAfter: formatLapClock(trueLapTime(t0, lap.meta)),
      transportBefore: formatClock(t0),
      transportAfter: formatTransportClock(t0 + lapStartOffsetSeconds(lap.meta)),
    },
    lastSample: {
      hudBefore: formatLapTime(tEnd),
      hudAfter: formatLapClock(trueLapTime(tEnd, lap.meta)),
      official: lap.meta.lap_time_seconds,
    },
  };
});

// Delta dump for the Python port cross-check: every 5th main sample, all ordered pairs.
const dump: { main: string; ghost: string; d: number[]; before: number[]; after: number[]; ghostTBefore: number[]; ghostTAfter: number[] }[] = [];
for (const m of laps) {
  for (const g of laps) {
    if (m === g) continue;
    const d: number[] = [];
    const before: number[] = [];
    const after: number[] = [];
    const gtb: number[] = [];
    const gta: number[] = [];
    for (let k = 0; k < m.lap.dist.length; k += 5) {
      d.push(m.lap.dist[k]);
      before.push(baselineDelta(m.lap, g.lap, m.lap.dist[k]));
      after.push(computeDeltaAtDist(m.lap, g.lap, m.lap.dist[k]));
      gtb.push(baselineGhostTime(m.lap, g.lap, m.lap.t[k]));
      gta.push(ghostTimeForMainTime(m.lap, g.lap, m.lap.t[k]));
    }
    dump.push({ main: m.id, ghost: g.id, d, before, after, ghostTBefore: gtb, ghostTAfter: gta });
  }
}

writeFileSync(join(HERE, "lap_clock_display_check.json"), JSON.stringify({ clock }, null, 2) + "\n");
writeFileSync(join(HERE, "delta_ts_dump.json"), JSON.stringify(dump) + "\n");
for (const c of clock) console.log(c.lap, c.delta, c.s0.toFixed(3), JSON.stringify(c.firstSample), JSON.stringify(c.lastSample));
console.log(`pairs dumped: ${dump.length}`);
