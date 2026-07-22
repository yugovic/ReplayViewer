import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { projectPointToCenterline } from "./interpolation";
import type { TrackData } from "./types";

// Scratch benchmark for the spatial-grid projection (delete freely).
describe("projectPointToCenterline drape-scale benchmark", () => {
  it("projects a 193x193 drape lattice over fuji in well under a second", () => {
    const track = JSON.parse(
      readFileSync(join(process.cwd(), "public/data/tracks/fuji/track.json"), "utf-8"),
    ) as TrackData;
    const [x0, x1] = track.bounds.x;
    const [z0, z1] = track.bounds.z;
    const pad = 600;
    const n = 193;
    const t0 = performance.now();
    let sum = 0;
    for (let i = 0; i < n; i += 1) {
      for (let j = 0; j < n; j += 1) {
        const x = x0 - pad + ((x1 - x0 + 2 * pad) * i) / (n - 1);
        const z = z0 - pad + ((z1 - z0 + 2 * pad) * j) / (n - 1);
        sum += projectPointToCenterline(track, x, z).lateralDistance;
      }
    }
    const ms = performance.now() - t0;
    // eslint-disable-next-line no-console
    console.log(`drape lattice: ${n * n} queries in ${ms.toFixed(1)} ms (checksum ${sum.toFixed(0)})`);
    expect(ms).toBeLessThan(1000);
  });
});
