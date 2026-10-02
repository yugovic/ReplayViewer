import { describe, expect, it } from "vitest";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { buildCgRoadGeometry, type CgCurbRow, type CgRoadRow, type FujiCgGeometry } from "./FujiCgStudy";
import { adjustCgGeometry } from "./FujiCgEditor";
import { adjustKerbZones, offsetFromEdge, resolveKerbZones, zoneWidths, type KerbZone } from "./FujiKerbZones";
import type { TrackData } from "../../replay/types";

type Batch = { positions: number[]; uvs: number[]; indices: number[] };
const vertices = (b: Batch) => Array.from({ length: b.positions.length/3 }, (_, i) => b.positions.slice(i*3, i*3+3));
function downwardOrInvalid(b: Batch) {
  let failures = 0;
  for (let i = 0; i < b.indices.length; i += 3) {
    const [a, c, d] = b.indices.slice(i, i+3).map(j => b.positions.slice(j*3, j*3+3));
    const y = (c[2]-a[2])*(d[0]-a[0])-(c[0]-a[0])*(d[2]-a[2]);
    if (y < -1e-7 || ![...a, ...c, ...d].every(Number.isFinite)) failures++;
  }
  return failures;
}
const near = (b: Batch, x: number, z: number, tol: number) => vertices(b).some(v => Math.hypot(v[0]-x, v[2]-z) < tol);

// Straight synthetic road along +X, 0.5 m grid, right side = +Z, edges at +-5 m.
const stations = Array.from({ length: 21 }, (_, i) => i*.5);
const flatTrack = { width: 13, totalLength: 10, origin: { lat: 0, lng: 0, alt: 0 }, centerline: [
  { dist: 0, x: 0, y: 0, z: 0, alt: 0 }, { dist: 10, x: 10, y: 0, z: 0, alt: 0 },
] } as TrackData;
const kerbStations = stations.filter(d => d >= 2 && d <= 6);
const fixture: FujiCgGeometry = { version: 1, source: "test fixture", limitations: [], curbHeightMeters: .055, green: [],
  road: stations.map(d => [d, d, 0, 0, 1, -5, 5] as CgRoadRow),
  curbs: [{ side: "right", rows: kerbStations.map((d, j) => [d, d, 5, d, 6.2, 5, 6.2, j < 4 ? 1 : 0, 1] as CgCurbRow) }] };
const zoneFor = (over: Record<string, unknown> = {}) => ({ id: "k0", side: "right", addedOutsideCg: false, station: kerbStations,
  paint: kerbStations.map(d => [d, 4.6]), block: kerbStations.map(d => [d, 5]), outer: kerbStations.map(d => [d, 5.9]), ...over });
const file = (kerbs: unknown[], over: Record<string, unknown> = {}) => ({ version: 3, trackId: "fuji", source: "fixture", license: "CC BY 4.0", surveyTruth: false, kerbs, ...over });

describe("kerb zones file validation", () => {
  it("treats a missing file as absent and a foreign or old file as invalid, without zones", () => {
    expect(resolveKerbZones(null, fixture)).toMatchObject({ status: "absent", zones: [] });
    expect(resolveKerbZones(file([zoneFor()], { version: 2 }), fixture)).toMatchObject({ status: "invalid", zones: [] });
    expect(resolveKerbZones(file([zoneFor()], { trackId: "suzuka" }), fixture)).toMatchObject({ status: "invalid", zones: [] });
    expect(resolveKerbZones("<!doctype html>", fixture)).toMatchObject({ status: "invalid", zones: [] });
  });
  it("maps kN to CG kerb N only when side and station range match", () => {
    const ok = resolveKerbZones(file([zoneFor()]), fixture);
    expect(ok.zones.map(z => [z.id, z.replaces, z.redSource])).toEqual([["k0", 0, "cg"]]);
    expect(ok.zones[0].red).toEqual(fixture.curbs[0].rows.map(r => r[7]));
    for (const bad of [{ side: "left" }, { id: "k1" }, { station: kerbStations.map(d => d+.5) }]) {
      const r = resolveKerbZones(file([zoneFor(bad)]), fixture);
      expect(r.zones).toEqual([]); expect(r.rejected).toHaveLength(1);
    }
  });
  it("rejects malformed lines and implausible widths (the CG kerb is kept instead)", () => {
    const cases = [
      { paint: kerbStations.map((d, j) => [d, j === 3 ? NaN : 4.6]) },
      { block: kerbStations.slice(1).map(d => [d, 5]) },
      { station: kerbStations.map(d => d+.25), id: "kX", addedOutsideCg: true },
      { paint: kerbStations.map(d => [d, 3.0]) },        // 2 m white strip
      { outer: kerbStations.map(d => [d, 5.05]) },       // 5 cm block band
      { paint: kerbStations.map(d => [d, 5.3]) },        // paint outside the block
    ];
    for (const c of cases) expect(resolveKerbZones(file([zoneFor(c)]), fixture).zones).toEqual([]);
  });
  it("accepts an added kerb only where the CG has none, never claims survey truth, and keeps observed red flags", () => {
    const added = { id: "kNew", side: "right", addedOutsideCg: true, station: [7, 7.5, 8], paint: [[7, 4.6], [7.5, 4.6], [8, 4.6]],
      block: [[7, 5], [7.5, 5], [8, 5]], outer: [[7, 5.9], [7.5, 5.9], [8, 5.9]] };
    const r = resolveKerbZones(file([zoneFor(), added, { ...added, id: "kClash", station: kerbStations.slice(0, 3), paint: added.paint, block: added.block, outer: added.outer }], { surveyTruth: true }), fixture);
    expect(r.zones.map(z => [z.id, z.replaces])).toEqual([["k0", 0], ["kNew", null]]);
    expect(r.rejected.map(x => x.id)).toEqual(["kClash"]);
    expect(r.surveyTruth).toBe(false);
    expect(r.zones[1].redSource).toBe("illustrative");
    const observed = resolveKerbZones(file([{ ...added, red: [1, 0, 1] }]), fixture);
    expect(observed.zones[0]).toMatchObject({ red: [1, 0, 1], redSource: "zone" });
    const twice = resolveKerbZones(file([zoneFor(), zoneFor()]), fixture);
    expect(twice.zones).toHaveLength(1); expect(twice.rejected).toHaveLength(1);
  });
});

describe("kerb zone rendering (synthetic road)", () => {
  const zones = resolveKerbZones(file([zoneFor({ paint: kerbStations.map((d, j) => [d, j < 2 ? 5.2 : 4.6]), block: kerbStations.map((d, j) => [d, j < 2 ? 5.6 : 5]), outer: kerbStations.map((d, j) => [d, j < 2 ? 6.4 : 5.9]) })]), fixture).zones;
  const before = buildCgRoadGeometry(flatTrack, fixture), after = buildCgRoadGeometry(flatTrack, fixture, zones);
  const segments = kerbStations.length-1;
  it("replaces the CG kerb (no double kerb) and keeps one batch per material", () => {
    expect(Object.keys(after)).toEqual(Object.keys(before));
    expect(near(before.red, 2, 6.2, 1e-6)).toBe(true);
    for (const b of [after.red, after.white]) expect(vertices(b).some(v => Math.abs(v[2]-6.2) < 1e-6)).toBe(false);
    expect(after.red.positions.length+after.white.positions.length).toBe(segments*12*3);
    expect(near(after.red, 3, 5.9, 1e-6)).toBe(true);
  });
  it("draws the white strip at paint height and drops the edge marking it replaces", () => {
    const edgeMarks = (b: Batch) => vertices(b).filter(v => Math.abs(v[2]-4.84) < 1e-6 && v[0] > 2 && v[0] < 6).length;
    expect(edgeMarks(before.paint)).toBeGreaterThan(0);
    expect(edgeMarks(after.paint)).toBe(0);
    const strip = vertices(after.paint).filter(v => v[0] >= 2 && v[0] <= 6 && (Math.abs(v[2]-4.6) < 1e-6 || Math.abs(v[2]-5) < 1e-6));
    expect(strip.length).toBeGreaterThanOrEqual(segments*2);
    for (const v of strip) expect(v[1]).toBeCloseTo(.012, 6);
    // The left edge marking is untouched.
    expect(vertices(after.paint).filter(v => Math.abs(v[2]+4.84) < 1e-6).length).toBe(vertices(before.paint).filter(v => Math.abs(v[2]+4.84) < 1e-6).length);
  });
  it("faces the outer kerb skirt away from the road on both sides", () => {
    const left = { id: "kL", side: "left", addedOutsideCg: true, station: [7, 7.5, 8, 8.5],
      paint: [7, 7.5, 8, 8.5].map(d => [d, -4.6]), block: [7, 7.5, 8, 8.5].map(d => [d, -5]), outer: [7, 7.5, 8, 8.5].map(d => [d, -5.9]) };
    const built = buildCgRoadGeometry(flatTrack, fixture, resolveKerbZones(file([zoneFor(), left]), fixture).zones);
    const walls = { left: 0, right: 0 };
    for (const b of [built.red, built.white]) for (let i = 0; i < b.indices.length; i += 3) {
      const [a, c, d] = b.indices.slice(i, i+3).map(j => b.positions.slice(j*3, j*3+3));
      const u = [c[0]-a[0], c[1]-a[1], c[2]-a[2]], v = [d[0]-a[0], d[1]-a[1], d[2]-a[2]];
      const n = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]];
      if (Math.abs(n[1]) > 1e-9) { expect(n[1]).toBeGreaterThan(0); continue; }
      const side = a[2] > 0 ? "right" : "left";
      walls[side]++;
      expect(side === "right" ? n[2] : -n[2]).toBeGreaterThan(0);
    }
    expect(walls).toEqual({ left: 3*2, right: segments*2 });
  });
  it("fills asphalt where the traced paint starts outside the road edge", () => {
    expect(after.road.positions.length).toBeGreaterThan(before.road.positions.length);
    expect(near(after.road, 2, 5.2, 1e-6)).toBe(true);
    for (const b of Object.values(after)) expect(downwardOrInvalid(b)).toBe(0);
  });
});

describe("delivered Fuji kerb zones", () => {
  const track = JSON.parse(readFileSync("public/data/tracks/fuji/track.json", "utf8")) as TrackData;
  const geometryText = readFileSync("public/data/tracks/fuji/cg_study/geometry.json", "utf8");
  const geometry = JSON.parse(geometryText) as FujiCgGeometry;
  const raw = JSON.parse(readFileSync("public/data/tracks/fuji/cg_study/kerb_zones.json", "utf8"));
  const set = resolveKerbZones(raw, geometry);
  const byId = new Map(set.zones.map(z => [z.id, z]));
  const segs = (zs: readonly KerbZone[]) => zs.reduce((n, z) => n+z.station.length-1, 0);

  it("was traced against this geometry.json and resolves every kerb", () => {
    // B1 hashed the working-tree bytes; accept either line-ending checkout.
    const sha = (text: string) => createHash("sha256").update(text).digest("hex");
    const lf = geometryText.replace(/\r\n/g, "\n");
    expect([sha(lf), sha(lf.replace(/\n/g, "\r\n"))]).toContain(raw.geometrySha256);
    expect(set.status).toBe("loaded");
    expect(set.rejected).toEqual([]);
    expect(set.zones).toHaveLength(26);
    expect(set.zones.filter(z => z.replaces !== null).map(z => z.replaces).sort((a, b) => a!-b!)).toEqual(geometry.curbs.map((_, i) => i));
    expect(set.license).toBe("CC BY 4.0");
    expect(set.surveyTruth).toBe(false);
  });
  it("adds the Dunlop-R kerb missing from the CG", () => {
    const dunlop = byId.get("kDunlopR")!;
    expect(dunlop).toMatchObject({ side: "right", replaces: null });
    expect(dunlop.station[0]).toBe(2962.5); expect(dunlop.station.at(-1)).toBe(2997);
  });
  it("keeps the white strip at 0.3-0.5 m and the block band plausible", () => {
    for (const zone of set.zones) {
      const { strip, band } = zoneWidths(zone);
      expect(strip, zone.id).toBeGreaterThanOrEqual(.3); expect(strip, zone.id).toBeLessThanOrEqual(.5);
      expect(band, zone.id).toBeGreaterThanOrEqual(.5); expect(band, zone.id).toBeLessThanOrEqual(2);
    }
  });

  const before = buildCgRoadGeometry(track, geometry);
  const after = buildCgRoadGeometry(track, geometry, set.zones);
  it("renders without downward or non-finite triangles and with sane vertex counts", () => {
    for (const name of ["road", "paint", "red", "white"] as const) expect(downwardOrInvalid(after[name]), name).toBe(0);
    const cgSegs = geometry.curbs.reduce((n, c) => n+c.rows.length-1, 0);
    const kerbVerts = (b: ReturnType<typeof buildCgRoadGeometry>) => (b.red.positions.length+b.white.positions.length)/3;
    expect(kerbVerts(before)).toBe(cgSegs*12);
    expect(kerbVerts(after)).toBe(segs(set.zones)*12);
    expect(kerbVerts(after)-kerbVerts(before)).toBe((byId.get("kDunlopR")!.station.length-1)*12);
    // One strip quad per zone segment replaces exactly one 0.16 m edge-marking
    // quad on that side, so the paint batch keeps its size.
    expect(after.paint.positions.length).toBe(before.paint.positions.length);
    // Asphalt fill only at the few kerb ends where the paint lies outside the CG road edge.
    const fill = (after.road.positions.length-before.road.positions.length)/3;
    expect(fill).toBeGreaterThan(0); expect(fill).toBeLessThan(400);
    expect(before.road.indices.length/3+fill/3).toBe(after.road.indices.length/3);
  });
  it("draws Dunlop-R where the CG had no kerb", () => {
    const dunlop = byId.get("kDunlopR")!, kerb = (b: typeof after) => [...vertices(b.red), ...vertices(b.white)];
    const mid = Math.floor(dunlop.station.length/2);
    const [ox, oz] = dunlop.outer[mid], [bx, bz] = dunlop.block[mid];
    expect(kerb(before).some(v => Math.hypot(v[0]-ox, v[2]-oz) < .5)).toBe(false);
    expect(kerb(after).some(v => Math.hypot(v[0]-ox, v[2]-oz) < 1e-3)).toBe(true);
    expect(kerb(after).some(v => Math.hypot(v[0]-bx, v[2]-bz) < 1e-3)).toBe(true);
    expect(near(after.paint, ...dunlop.paint[mid], 1e-3)).toBe(true);
  });
  it("follows the corrected lines at kerbs 15 and 23 instead of the old CG kerb", () => {
    for (const id of ["k15", "k23"]) {
      const zone = byId.get(id)!, rows = geometry.curbs[zone.replaces!].rows;
      const kerb = [...vertices(after.red), ...vertices(after.white)];
      let checked = 0;
      zone.station.forEach((st, j) => {
        if (j%2) return;
        const row = geometry.road[Math.round(st/.5)];
        // Where the corrected block start lies well inside the CG kerb inner
        // (= road edge), the old kerb's inner and bevel vertices must be gone.
        if (offsetFromEdge(row, zone.side, zone.block[j]) > -.5) return;
        const r = rows[j], bevel = [r[1]+(r[3]-r[1])*.18, r[2]+(r[4]-r[2])*.18];
        expect(kerb.some(v => Math.hypot(v[0]-zone.block[j][0], v[2]-zone.block[j][1]) < 1e-3), `${id}@${st}`).toBe(true);
        expect(kerb.some(v => Math.hypot(v[0]-r[1], v[2]-r[2]) < .05), `${id}@${st} old inner`).toBe(false);
        expect(kerb.some(v => Math.hypot(v[0]-bevel[0], v[2]-bevel[1]) < .05), `${id}@${st} old bevel`).toBe(false);
        checked++;
      });
      expect(checked, id).toBeGreaterThan(15);
    }
  });
  it("moves zone kerbs with the interactive CG kerb editor", () => {
    const change = { index: 17, offsetMeters: .3, widthScale: 1.2 };
    const moved = adjustKerbZones(set.zones, geometry, [change]);
    const zone = set.zones.find(z => z.replaces === 17)!, shifted = moved.find(z => z.replaces === 17)!;
    zone.station.forEach((_, j) => {
      const r = geometry.curbs[17].rows[j], w = Math.hypot(r[3]-r[1], r[4]-r[2]);
      const shift = ((shifted.paint[j][0]-zone.paint[j][0])*(r[3]-r[1])+(shifted.paint[j][1]-zone.paint[j][1])*(r[4]-r[2]))/w;
      expect(shift).toBeCloseTo(.3, 6);
      const bandBefore = Math.hypot(zone.outer[j][0]-zone.block[j][0], zone.outer[j][1]-zone.block[j][1]);
      expect(Math.hypot(shifted.outer[j][0]-shifted.block[j][0], shifted.outer[j][1]-shifted.block[j][1])).toBeCloseTo(bandBefore*1.2, 6);
    });
    expect(moved.filter(z => z !== set.zones.find(s => s.id === z.id))).toHaveLength(1);
    for (const offset of [-.5, .5]) for (const scale of [.7, 1.3]) {
      const edits = [{ index: 17, offsetMeters: offset, widthScale: scale }];
      const built = buildCgRoadGeometry(track, adjustCgGeometry(geometry, edits), adjustKerbZones(set.zones, geometry, edits));
      for (const name of ["road", "paint", "red", "white"] as const) expect(downwardOrInvalid(built[name]), `${name} ${offset} ${scale}`).toBe(0);
    }
  });
});
