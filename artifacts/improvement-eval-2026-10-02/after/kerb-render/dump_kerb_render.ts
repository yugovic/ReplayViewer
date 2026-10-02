/**
 * Evaluation helper for TASK B2 (kerb zones in the Fuji CG study).
 * Builds the CG road batches exactly as the viewer does, once without and once
 * with public/data/tracks/fuji/cg_study/kerb_zones.json, and writes:
 *   $KERB_RENDER_WORK/tris_{before,after}.json  top-face triangles (XZ) per batch, for rasterising
 *   <this dir>/render_metrics.json              counts, draw calls and line deviations
 * Run from the repo root:
 *   KERB_RENDER_WORK=<scratch dir> npx vite-node artifacts/improvement-eval-2026-10-02/after/kerb-render/dump_kerb_render.ts
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { buildCgRoadGeometry, type FujiCgGeometry } from "../../../../src/engine/track/FujiCgStudy";
import { offsetFromEdge, outwardAt, resolveKerbZones, zoneWidths, roadEdgeAt } from "../../../../src/engine/track/FujiKerbZones";
import type { TrackData } from "../../../../src/replay/types";

const here = dirname(fileURLToPath(import.meta.url));
const work = process.env.KERB_RENDER_WORK ?? join(here, "work");
mkdirSync(work, { recursive: true });
const track = JSON.parse(readFileSync("public/data/tracks/fuji/track.json", "utf8")) as TrackData;
const geometry = JSON.parse(readFileSync("public/data/tracks/fuji/cg_study/geometry.json", "utf8")) as FujiCgGeometry;
const zoneSet = resolveKerbZones(JSON.parse(readFileSync("public/data/tracks/fuji/cg_study/kerb_zones.json", "utf8")), geometry);

const t0 = performance.now();
const before = buildCgRoadGeometry(track, geometry);
const t1 = performance.now();
const after = buildCgRoadGeometry(track, geometry, zoneSet.zones);
const t2 = performance.now();

type Batch = { positions: number[]; indices: number[] };
const r3 = (v: number) => Math.round(v*1000)/1000;
/** Upward (top) faces only, as flat [x0,z0,x1,z1,x2,z2,...]. */
function tops(b: Batch, fromTriangle = 0) {
  const out: number[] = [];
  for (let i = fromTriangle*3; i < b.indices.length; i += 3) {
    const [a, c, d] = b.indices.slice(i, i+3).map(j => b.positions.slice(j*3, j*3+3));
    const y = (c[2]-a[2])*(d[0]-a[0])-(c[0]-a[0])*(d[2]-a[2]);
    if (y > 1e-9) out.push(r3(a[0]), r3(a[2]), r3(c[0]), r3(c[2]), r3(d[0]), r3(d[2]));
  }
  return out;
}
writeFileSync(join(work, "tris_before.json"), JSON.stringify({ paint: tops(before.paint), red: tops(before.red), white: tops(before.white), roadFill: [] }));
writeFileSync(join(work, "tris_after.json"), JSON.stringify({ paint: tops(after.paint), red: tops(after.red), white: tops(after.white),
  roadFill: tops(after.road, before.road.indices.length/3) }));

const count = (b: Batch) => ({ vertices: b.positions.length/3, triangles: b.indices.length/3 });
const counts = (g: typeof before) => Object.fromEntries(Object.entries(g).map(([k, b]) => [k, count(b)]));
const median = (v: number[]) => { const s = [...v].sort((a, b) => a-b); return s.length ? (s[(s.length-1) >> 1]+s[s.length >> 1])/2 : NaN; };
const q = (v: number[], p: number) => { const s = [...v].sort((a, b) => a-b); return s[Math.min(s.length-1, Math.max(0, Math.round((s.length-1)*p)))]; };
const stats = (v: number[]) => ({ median: +median(v).toFixed(3), p05: +q(v, .05).toFixed(3), p95: +q(v, .95).toFixed(3), maxAbs: +Math.max(...v.map(Math.abs)).toFixed(3) });

// Rendered lines vs the B1 zone lines, signed along the outward normal (+ = outward).
// before: white track-limit marking = road edge - 0.16 m (its road-side edge),
//         kerb block start = CG kerb inner, outer = CG kerb outer.
// after:  strip road-side edge = zone paint, block start = zone block, outer = zone outer (by construction).
const perKerb = zoneSet.zones.map(zone => {
  const rows = zone.station.map(st => geometry.road[Math.round(st/.5)]);
  const cg = zone.replaces === null ? null : geometry.curbs[zone.replaces].rows;
  const lineBefore = { paint: [] as number[], block: [] as number[], outer: [] as number[], whiteWidth: [] as number[] };
  zone.station.forEach((_, j) => {
    const row = rows[j], n = outwardAt(row, zone.side), e = roadEdgeAt(row, zone.side);
    const along = (p: [number, number]) => (p[0]-e[0])*n[0]+(p[1]-e[1])*n[1];
    lineBefore.paint.push(-.16 - along(zone.paint[j]));
    if (cg) {
      lineBefore.block.push(along([cg[j][1], cg[j][2]]) - along(zone.block[j]));
      lineBefore.outer.push(along([cg[j][3], cg[j][4]]) - along(zone.outer[j]));
    }
  });
  // Lateral jump of the white line where the zone starts/ends and the 0.16 m edge marking resumes.
  const ends = [0, zone.station.length-1].map(j => +(-.16 - offsetFromEdge(rows[j], zone.side, zone.paint[j])).toFixed(3));
  const w = zoneWidths(zone);
  return { id: zone.id, side: zone.side, replaces: zone.replaces, redSource: zone.redSource,
    stations: [zone.station[0], zone.station.at(-1)], segments: zone.station.length-1,
    whiteWidthBefore: cg ? .16 : 0, whiteWidthAfter: +w.strip.toFixed(3), bandWidthAfter: +w.band.toFixed(3),
    bandWidthBefore: cg ? +median(cg.map(r => Math.hypot(r[3]-r[1], r[4]-r[2]))).toFixed(3) : 0,
    beforeMinusZone: { paint: stats(lineBefore.paint), block: cg ? stats(lineBefore.block) : null, outer: cg ? stats(lineBefore.outer) : null },
    whiteLineJumpAtEnds: ends };
});

const metrics = {
  generatedBy: "artifacts/improvement-eval-2026-10-02/after/kerb-render/dump_kerb_render.ts",
  zonesFile: "public/data/tracks/fuji/cg_study/kerb_zones.json",
  zoneStatus: zoneSet.status, zones: zoneSet.zones.length, rejected: zoneSet.rejected,
  replacedCgKerbs: zoneSet.zones.filter(z => z.replaces !== null).length,
  addedKerbs: zoneSet.zones.filter(z => z.replaces === null).map(z => z.id),
  illustrativeRedPattern: zoneSet.zones.filter(z => z.redSource === "illustrative").map(z => z.id),
  drawCalls: { before: Object.keys(before).length, after: Object.keys(after).length,
    note: "buildFujiCgTrack makes one mesh per batch (cg-road, cg-edge-paint, cg-curbs-red, cg-curbs-white, cg-green-runoff)" },
  counts: { before: counts(before), after: counts(after) },
  buildMs: { before: +(t1-t0).toFixed(0), after: +(t2-t1).toFixed(0) },
  definitions: {
    beforeMinusZone: "rendered line minus the B1 kerb_zones.json line along the outward road normal (m, + = rendered line lies outward); after = 0 by construction",
    whiteLineJumpAtEnds: "lateral step (m) from the zone paint line to the 0.16 m road-edge marking at the zone's first/last station",
  },
  perKerb,
};
writeFileSync(join(here, "render_metrics.json"), JSON.stringify(metrics, null, 1)+"\n");
console.log(JSON.stringify({ drawCalls: metrics.drawCalls, counts: metrics.counts, buildMs: metrics.buildMs }, null, 1));
