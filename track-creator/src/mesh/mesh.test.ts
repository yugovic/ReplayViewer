import { describe, expect, it } from "vitest";
import { buildStations, makeTrackSampler } from "./stations.ts";
import { buildRoadMesh } from "./road.ts";
import { buildCurbsMesh } from "./curbs.ts";
import type { TrackDef, ElevationData } from "../types.ts";

/** Small closed test track with a synthetic pre-fetched elevation file. */
function fixture(): { def: TrackDef; elev: ElevationData } {
  const def: TrackDef = {
    name: "t",
    closed: true,
    controlPoints: [
      { x: 0, z: 0 },
      { x: 200, z: 0 },
      { x: 200, z: 150 },
      { x: 0, z: 150 },
    ],
    elevation: { source: "flat", step: 10 },
    road: { width: 10, step: 5 },
  };
  // Fake corridor: center at y = 2, left edge +0.5, right edge -0.5 (constant camber).
  const totalLength = 700; // roughly; only used as interp domain
  const samples = [];
  for (let d = 0; d < totalLength; d += 10) {
    samples.push({ dist: d, x: 0, z: 0, yLeft: 2.5, yCenter: 2, yRight: 1.5 });
  }
  const elev: ElevationData = {
    source: "flat",
    step: 10,
    corridorHalfWidth: 7,
    totalLength,
    samples,
  };
  return { def, elev };
}

describe("buildStations", () => {
  it("scales corridor camber down to the paved edge", () => {
    const { def, elev } = fixture();
    const track = buildStations(def, elev);
    const s = track.stations[3];
    // offset 5, corridor 7 → edge camber = ±0.5 * 5/7
    expect(s.yCenter).toBeCloseTo(2, 3);
    expect(s.yLeft).toBeCloseTo(2 + 0.5 * (5 / 7), 3);
    expect(s.yRight).toBeCloseTo(2 - 0.5 * (5 / 7), 3);
    expect(s.offsetLeft).toBeCloseTo(5, 6);
    expect(s.offsetRight).toBeCloseTo(5, 6);
  });

  it("applies the width profile with wrap-around interpolation", () => {
    const { def, elev } = fixture();
    def.road.widthProfile = [
      { dist: 0, width: 10 },
      { dist: 100, width: 14 },
      { dist: 200, width: 10 },
    ];
    const track = buildStations(def, elev);
    const near100 = track.stations.reduce((a, b) =>
      Math.abs(b.dist - 100) < Math.abs(a.dist - 100) ? b : a,
    );
    expect(near100.offsetLeft + near100.offsetRight).toBeGreaterThan(13);
    // Far side of the loop (past the last key) wraps toward the first key's 10 m.
    const last = track.stations[track.stations.length - 1];
    expect(last.offsetLeft + last.offsetRight).toBeLessThan(10.5);
  });

  it("includes the interpolated center elevation in arbitrary-distance samples", () => {
    const { def, elev } = fixture();
    const track = buildStations(def, elev);
    const sampler = makeTrackSampler(track);
    const sample = sampler(track.totalLength * 0.25);

    expect(Number.isFinite(sample.yCenter)).toBe(true);
    expect(sample.yCenter).toBeCloseTo(
      (sample.yLeft + sample.yRight) / 2,
      8,
    );
  });

  it("road.edges overrides the width profile with per-side offsets", () => {
    const { def, elev } = fixture();
    const track0 = buildStations(def, elev);
    const total = track0.totalLength;
    // Uniform arrays every 10 m: left constant 5, right bulging to 9 at sample 10.
    const n = Math.ceil(total / 10);
    const left = new Array<number>(n).fill(5);
    const right = new Array<number>(n).fill(5);
    right[10] = 9;
    def.road.width = 99; // must be ignored
    def.road.edges = { step: 10, left, right };

    const track = buildStations(def, elev);
    const at100 = track.stations.reduce((a, b) =>
      Math.abs(b.dist - 100) < Math.abs(a.dist - 100) ? b : a,
    );
    expect(at100.offsetLeft).toBeCloseTo(5, 2);
    expect(at100.offsetRight).toBeGreaterThan(8);
    // Away from the bulge both sides sit at 5 (width 99 ignored).
    const at0 = track.stations[0];
    expect(at0.offsetLeft).toBeCloseTo(5, 2);
    expect(at0.offsetRight).toBeCloseTo(5, 2);
  });
});

describe("buildRoadMesh", () => {
  it("closes the loop with a duplicated final row and correct counts", () => {
    const { def, elev } = fixture();
    const track = buildStations(def, elev);
    const mesh = buildRoadMesh(track);
    const rows = track.stations.length + 1;
    expect(mesh.positions.length).toBe(rows * 3 * 3);
    expect(mesh.indices.length).toBe((rows - 1) * 2 * 6);
    // Duplicated closing row sits exactly on the first row.
    const firstY = mesh.positions[1];
    const lastRowStart = (rows - 1) * 3 * 3;
    expect(mesh.positions[lastRowStart + 1]).toBeCloseTo(firstY, 5);
    // Road faces up: average normal Y strongly positive.
    let sumY = 0;
    for (let v = 0; v < mesh.normals.length; v += 3) sumY += mesh.normals[v + 1];
    expect(sumY / (mesh.normals.length / 3)).toBeGreaterThan(0.9);
  });
});

describe("buildCurbsMesh", () => {
  it("bakes alternating red/white stripes as vertex colors", () => {
    const { def, elev } = fixture();
    const track = buildStations(def, elev);
    const mesh = buildCurbsMesh(track, [
      { startDist: 20, endDist: 44, side: "right", width: 1.2, stripeLength: 4 },
    ]);
    expect(mesh).not.toBeNull();
    const colors = mesh!.colors!;
    // Collect distinct colors: must be exactly red and white.
    const seen = new Set<string>();
    for (let i = 0; i < colors.length; i += 3) {
      seen.add(`${colors[i].toFixed(2)},${colors[i + 1].toFixed(2)},${colors[i + 2].toFixed(2)}`);
    }
    expect(seen.size).toBe(2);
    // First stripe red, and a vertex ~5 m in (second stripe) is white.
    expect(colors[0]).toBeCloseTo(0.72, 2);
    // Curbs face up too.
    let sumY = 0;
    for (let v = 0; v < mesh!.normals.length; v += 3) sumY += mesh!.normals[v + 1];
    expect(sumY / (mesh!.normals.length / 3)).toBeGreaterThan(0.8);
  });

  it("returns null when there are no spans", () => {
    const { def, elev } = fixture();
    const track = buildStations(def, elev);
    expect(buildCurbsMesh(track, [])).toBeNull();
  });

  it("places right-side curbs outside the paved edge", () => {
    const { def, elev } = fixture();
    const track = buildStations(def, elev);
    const span = { startDist: 20, endDist: 30, side: "right" as const, width: 1.0, stripeLength: 100 };
    const mesh = buildCurbsMesh(track, [span])!;
    // Every curb vertex must sit outside the paved edge (≈ halfWidth − overlap
    // laterally from the centerline) and on the +normal (driver's right) side.
    for (let v = 0; v < mesh.positions.length; v += 3) {
      const vx = mesh.positions[v];
      const vz = mesh.positions[v + 2];
      let best = track.stations[0];
      let bestD2 = Infinity;
      for (const s of track.stations) {
        const d2 = (s.x - vx) ** 2 + (s.z - vz) ** 2;
        if (d2 < bestD2) {
          bestD2 = d2;
          best = s;
        }
      }
      expect(Math.sqrt(bestD2)).toBeGreaterThan(4.0); // ≳ halfWidth − overlap, minus curvature slack
      // Signed lateral: positive = right of the centerline.
      const lateral = (vx - best.x) * -best.tz + (vz - best.z) * best.tx;
      expect(lateral).toBeGreaterThan(0);
    }
  });
});
