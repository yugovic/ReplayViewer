/**
 * Step 1 of the pipeline: sample elevation along the centerline corridor and
 * store it in <trackDir>/elevation.json. Run this BEFORE `npm run build`.
 *
 *   npm run elevation -- tracks/demo
 *
 * For each station (every elevation.step metres) three points are sampled:
 * centerline, left corridor edge, right corridor edge — enough to reconstruct
 * camber (cross-slope) at build time. A circular moving average smooths
 * sensor/tile noise so the road doesn't ripple.
 */

import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { resampleCenterline } from "../centerline.ts";
import { loadTrack, corridorHalfWidth, elevationPath } from "../io.ts";
import { FlatProvider, SyntheticProvider, GsiProvider } from "../elevation/providers.ts";
import type { ElevationProvider } from "../elevation/providers.ts";
import type { ElevationData, ElevationSample } from "../types.ts";

/** Moving average; wraps around for closed tracks, clamps at the ends otherwise. */
function smooth(values: number[], window: number, closed: boolean): number[] {
  if (window <= 1) return values;
  const half = Math.floor(window / 2);
  const n = values.length;
  const out = new Array<number>(n);
  for (let i = 0; i < n; i++) {
    let sum = 0;
    let cnt = 0;
    for (let k = -half; k <= half; k++) {
      let j = i + k;
      if (closed) j = ((j % n) + n) % n;
      else j = Math.min(n - 1, Math.max(0, j));
      sum += values[j];
      cnt++;
    }
    out[i] = sum / cnt;
  }
  return out;
}

async function main() {
  const { dir, def } = loadTrack(process.argv[2]);
  const cfg = def.elevation;

  let provider: ElevationProvider;
  switch (cfg.source) {
    case "imported":
      throw new Error(
        `${def.name}: elevation.source is "imported" — elevation.json came from an importer and must not be overwritten by this command`,
      );
    case "flat":
      provider = new FlatProvider();
      break;
    case "synthetic":
      provider = new SyntheticProvider();
      break;
    case "gsi": {
      if (!def.origin) throw new Error('elevation.source "gsi" requires track.json origin {lat,lng}');
      provider = new GsiProvider(def.origin, join(dir, "cache"));
      break;
    }
    default:
      throw new Error(`unknown elevation.source: ${(cfg as { source: string }).source}`);
  }

  const { points, totalLength } = resampleCenterline(def.controlPoints, cfg.step, def.closed);
  const half = corridorHalfWidth(def);
  console.log(
    `[elevation] ${def.name}: ${points.length} stations / ${totalLength.toFixed(1)} m, corridor ±${half.toFixed(1)} m, source=${cfg.source}`,
  );

  const yC: number[] = [];
  const yL: number[] = [];
  const yR: number[] = [];
  for (const p of points) {
    // normal = (-tz, 0, tx); +normal is the driver's right.
    const nx = -p.tz;
    const nz = p.tx;
    yC.push(await provider.sample(p.x, p.z));
    yL.push(await provider.sample(p.x - nx * half, p.z - nz * half));
    yR.push(await provider.sample(p.x + nx * half, p.z + nz * half));
  }

  // Rebase GSI's ASL values so local y = 0 sits at the origin altitude.
  let originAlt: number | undefined;
  if (cfg.source === "gsi") {
    originAlt = def.origin?.alt ?? yC[0];
    for (let i = 0; i < yC.length; i++) {
      yC[i] -= originAlt;
      yL[i] -= originAlt;
      yR[i] -= originAlt;
    }
  }

  const window = cfg.smooth ?? 9;
  const sC = smooth(yC, window, def.closed);
  const sL = smooth(yL, window, def.closed);
  const sR = smooth(yR, window, def.closed);

  const samples: ElevationSample[] = points.map((p, i) => ({
    dist: Math.round(p.dist * 100) / 100,
    x: Math.round(p.x * 1000) / 1000,
    z: Math.round(p.z * 1000) / 1000,
    yLeft: Math.round(sL[i] * 1000) / 1000,
    yCenter: Math.round(sC[i] * 1000) / 1000,
    yRight: Math.round(sR[i] * 1000) / 1000,
  }));

  const data: ElevationData = {
    source: cfg.source,
    step: cfg.step,
    corridorHalfWidth: half,
    totalLength: Math.round(totalLength * 100) / 100,
    ...(originAlt !== undefined ? { originAlt } : {}),
    samples,
  };
  const outPath = elevationPath(dir);
  writeFileSync(outPath, JSON.stringify(data, null, 1));

  const min = Math.min(...sC);
  const max = Math.max(...sC);
  console.log(
    `[elevation] wrote ${outPath} — y range ${min.toFixed(2)}..${max.toFixed(2)} m (Δ${(max - min).toFixed(2)} m)` +
      (originAlt !== undefined ? `, origin alt ${originAlt.toFixed(2)} m ASL` : ""),
  );
}

main().catch((e) => {
  console.error(e instanceof Error ? e.message : e);
  process.exit(1);
});
