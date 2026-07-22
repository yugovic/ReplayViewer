/** Shared IO helpers for the CLIs. */

import { readFileSync, existsSync } from "node:fs";
import { join, resolve } from "node:path";
import type { TrackDef, ElevationData } from "./types.ts";

export interface TrackDir {
  dir: string;
  def: TrackDef;
}

/** Resolve the track directory from argv and load its track.json. */
export function loadTrack(argvDir: string | undefined): TrackDir {
  if (!argvDir) {
    throw new Error("usage: pass the track directory, e.g.  npm run build -- tracks/demo");
  }
  const dir = resolve(argvDir);
  const defPath = join(dir, "track.json");
  if (!existsSync(defPath)) throw new Error(`not found: ${defPath}`);
  const def = JSON.parse(readFileSync(defPath, "utf8")) as TrackDef;
  if (!def.controlPoints || def.controlPoints.length < 3) {
    throw new Error("track.json: controlPoints needs at least 3 points");
  }
  return { dir, def };
}

export function elevationPath(dir: string): string {
  return join(dir, "elevation.json");
}

export function loadElevation(dir: string): ElevationData {
  const p = elevationPath(dir);
  if (!existsSync(p)) {
    throw new Error(`not found: ${p} — run  npm run elevation -- <trackDir>  first`);
  }
  return JSON.parse(readFileSync(p, "utf8")) as ElevationData;
}

/** Corridor half-width: room for the widest road plus the widest curb. */
export function corridorHalfWidth(def: TrackDef): number {
  let maxWidth = def.road.width;
  for (const k of def.road.widthProfile ?? []) maxWidth = Math.max(maxWidth, k.width);
  let maxCurb = 0;
  for (const c of def.curbs ?? []) maxCurb = Math.max(maxCurb, c.width);
  return maxWidth / 2 + maxCurb + 0.5;
}
