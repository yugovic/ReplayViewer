/**
 * One-shot importer: builds tracks/fuji/ from the real Fuji data already in
 * the repo's tracktools-kit (logger-derived centerline with LiDAR-grade edge
 * elevations, plus the driven lap lines for the editor's reference overlay).
 *
 *   npx tsx src/cli/importFuji.ts
 *
 * Sources (all in ../tracktools-kit/fuji/):
 *   centerline.json  → track.json (controlPoints) + elevation.json (yLeft/yRight camber)
 *   build.json       → origin lat/lng/alt (for the GSI photo overlay)
 *   logger_overlay/make_logger_overlay_blender.py → lap polylines → refs.json
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import type { TrackDef, ElevationData, RefsData, RefLine } from "../types.ts";

const here = dirname(fileURLToPath(import.meta.url));
const kitDir = resolve(here, "../../../tracktools-kit/fuji");
const outDir = resolve(here, "../../tracks/fuji");

interface KitCenterline {
  width: number;
  totalLength: number;
  closed: boolean;
  points: { x: number; y: number; z: number; dist: number; yLeft: number; yRight: number }[];
}

const cl = JSON.parse(readFileSync(join(kitDir, "centerline.json"), "utf8")) as KitCenterline;
const build = JSON.parse(readFileSync(join(kitDir, "build.json"), "utf8")) as {
  origin: { lat: number; lng: number; alt: number };
};

mkdirSync(outDir, { recursive: true });

// ── track.json ───────────────────────────────────────────────────────────────
const def: TrackDef = {
  name: "fuji",
  closed: cl.closed,
  origin: build.origin,
  controlPoints: cl.points.map((p) => ({
    x: Math.round(p.x * 1000) / 1000,
    z: Math.round(p.z * 1000) / 1000,
  })),
  elevation: { source: "imported", step: 7 },
  road: { width: cl.width, step: 3 },
  curbs: [],
};
writeFileSync(join(outDir, "track.json"), JSON.stringify(def, null, 1));

// ── elevation.json ───────────────────────────────────────────────────────────
const elev: ElevationData = {
  source: "imported",
  step: 7,
  corridorHalfWidth: cl.width / 2,
  totalLength: cl.totalLength,
  originAlt: build.origin.alt,
  samples: cl.points.map((p) => ({
    dist: p.dist,
    x: p.x,
    z: p.z,
    yLeft: p.yLeft,
    yCenter: Math.round(((p.yLeft + p.yRight) / 2) * 1000) / 1000,
    yRight: p.yRight,
  })),
};
writeFileSync(join(outDir, "elevation.json"), JSON.stringify(elev, null, 1));

// ── refs.json: extract lap polylines from the Blender overlay script ─────────
const py = readFileSync(join(kitDir, "logger_overlay/make_logger_overlay_blender.py"), "utf8");
const colors = ["#4da3ff", "#ffd24d", "#ff4d6b", "#7dffa1", "#c58bff"];
const lines: RefLine[] = [];
const curveRe = /mk_curve\("([^"]+)",\s*\[(.*?)\]\s*,\s*cyclic=(True|False)/gs;
const tupleRe = /\(np\.float64\((-?[\d.eE+]+)\),\s*np\.float64\((-?[\d.eE+]+)\),\s*np\.float64\((-?[\d.eE+]+)\)\)/g;
let m: RegExpExecArray | null;
while ((m = curveRe.exec(py)) !== null) {
  const [, name, body, cyclic] = m;
  if (name.toLowerCase().startsWith("centerline")) continue; // already have it as controlPoints
  const points: [number, number, number][] = [];
  let t: RegExpExecArray | null;
  tupleRe.lastIndex = 0;
  while ((t = tupleRe.exec(body)) !== null) {
    points.push([Number(t[1]), Number(t[2]), Number(t[3])]);
  }
  if (points.length < 2) continue;
  lines.push({ name, color: colors[lines.length % colors.length], closed: cyclic === "True", points });
}
const refs: RefsData = { lines };
writeFileSync(join(outDir, "refs.json"), JSON.stringify(refs));

console.log(
  `[import] fuji: ${def.controlPoints.length} control points, ${cl.totalLength.toFixed(0)} m, width ${cl.width} m`,
);
console.log(`[import] refs: ${lines.map((l) => `${l.name}(${l.points.length})`).join(", ") || "none"}`);
console.log(`[import] wrote ${outDir}\\{track,elevation,refs}.json`);
