import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  getProjectedLapArcLengths,
  interpolateNumberSeries,
  MAX_VEHICLE_ROLL,
  projectPointToCenterline,
  projectPointToCenterlineArcLength,
  sampleReplay,
  sampleTrackRoll,
  sampleTrackSurface,
} from "./interpolation";
import type { LapData, TrackData, TrackPoint } from "./types";

const METERS_PER_DEGREE = 111_320;

function makeTrack(points: Array<{ x: number; z: number; dist: number; y?: number }>): TrackData {
  const centerline: TrackPoint[] = points.map((point) => ({
    lat: 0,
    lng: 0,
    alt: point.y ?? 0,
    dist: point.dist,
    x: point.x,
    y: point.y ?? 0,
    z: point.z,
  }));

  return {
    version: 1,
    trackId: "test",
    trackName: "Test",
    width: 10,
    totalLength: centerline[centerline.length - 1]?.dist ?? 0,
    origin: { lat: 0, lng: 0, alt: 0 },
    bounds: {
      lat: [0, 0],
      lng: [0, 0],
      x: [Math.min(...points.map((point) => point.x)), Math.max(...points.map((point) => point.x))],
      z: [Math.min(...points.map((point) => point.z)), Math.max(...points.map((point) => point.z))],
    },
    centerline,
  };
}

/** Inverse of src/replay/projection.ts's latLngToLocal (north = -z). */
function localToLatLng(track: TrackData, x: number, z: number) {
  const lngScale = METERS_PER_DEGREE * Math.cos((track.origin.lat * Math.PI) / 180);
  return {
    lat: track.origin.lat - z / METERS_PER_DEGREE,
    lng: track.origin.lng + x / lngScale,
  };
}

function makeLapFromLocalPoints(track: TrackData, points: Array<{ x: number; z: number }>): LapData {
  const latLngPoints = points.map((point) => localToLatLng(track, point.x, point.z));
  const zeros = Array.from({ length: points.length }, () => 0);

  return {
    meta: {
      version: 1,
      track_id: track.trackId,
      race_id: "test",
      vehicle_id: "test",
      vehicle_number: "0",
      lap: 1,
      lap_time_seconds: points.length - 1,
      lap_time: null,
      point_count: points.length,
      raw_gps_points: points.length,
      gps_outliers_removed: 0,
      total_distance_m: track.totalLength,
      source_file: "test",
      time_column: "t",
      first_sample_time: "0",
      last_sample_time: String(points.length - 1),
    },
    t: points.map((_, index) => index),
    lat: latLngPoints.map((point) => point.lat),
    lng: latLngPoints.map((point) => point.lng),
    speed: zeros.slice(),
    aps: zeros.slice(),
    brake: zeros.slice(),
    steer: zeros.slice(),
    gear: zeros.slice(),
    accx: zeros.slice(),
    accy: zeros.slice(),
    dist: points.map((_, index) => index),
  };
}

function readJson<T>(relativePath: string): T {
  return JSON.parse(readFileSync(join(process.cwd(), relativePath), "utf8")) as T;
}

function nearestCenterlineYByXZ(track: TrackData, x: number, z: number): number {
  let nearestY = track.centerline[0]?.y ?? 0;
  let bestDistSq = Number.POSITIVE_INFINITY;

  for (let i = 0; i < track.centerline.length - 1; i += 1) {
    const a = track.centerline[i];
    const b = track.centerline[i + 1];
    const segmentX = b.x - a.x;
    const segmentZ = b.z - a.z;
    const segmentLengthSq = segmentX * segmentX + segmentZ * segmentZ;
    const t =
      segmentLengthSq > 0
        ? Math.min(1, Math.max(0, ((x - a.x) * segmentX + (z - a.z) * segmentZ) / segmentLengthSq))
        : 0;
    const projectedX = a.x + segmentX * t;
    const projectedZ = a.z + segmentZ * t;
    const dx = x - projectedX;
    const dz = z - projectedZ;
    const distSq = dx * dx + dz * dz;
    if (distSq < bestDistSq) {
      bestDistSq = distSq;
      nearestY = a.y + (b.y - a.y) * t;
    }
  }

  return nearestY;
}

describe("interpolateNumberSeries", () => {
  it("linearly interpolates between neighboring samples", () => {
    expect(interpolateNumberSeries([0, 10, 20], [0, 100, 200], 5)).toBe(50);
    expect(interpolateNumberSeries([0, 10, 20], [0, 100, 200], 15)).toBe(150);
  });

  it("clamps outside the source time range", () => {
    expect(interpolateNumberSeries([2, 4], [20, 40], 0)).toBe(20);
    expect(interpolateNumberSeries([2, 4], [20, 40], 8)).toBe(40);
  });
});

describe("projectPointToCenterlineArcLength", () => {
  it("returns projected arc length and lateral distance for an offset point", () => {
    const track = makeTrack([
      { x: 0, z: 0, dist: 0 },
      { x: 10, z: 0, dist: 10 },
      { x: 10, z: 10, dist: 20 },
      { x: 0, z: 10, dist: 30 },
      { x: 0, z: 0, dist: 40 },
    ]);

    const projection = projectPointToCenterline(track, 5, 3);

    expect(projection.arcLength).toBeCloseTo(5, 6);
    expect(projection.lateralDistance).toBeCloseTo(3, 6);
  });

  it("projects a laterally offset point to the nearest segment arc length", () => {
    const track = makeTrack([
      { x: 0, z: 0, dist: 0 },
      { x: 10, z: 0, dist: 10 },
      { x: 10, z: 10, dist: 20 },
      { x: 0, z: 10, dist: 30 },
      { x: 0, z: 0, dist: 40 },
    ]);

    expect(projectPointToCenterlineArcLength(track, 5, 3)).toBeCloseTo(5, 6);
  });
});

describe("getProjectedLapArcLengths", () => {
  it("unwraps a closed-loop lap crossing start/finish into a continuous series", () => {
    const track = makeTrack([
      { x: 0, z: 0, dist: 0 },
      { x: 10, z: 0, dist: 10 },
      { x: 10, z: 10, dist: 20 },
      { x: 0, z: 10, dist: 30 },
      { x: 0, z: 0, dist: 40 },
    ]);
    const lap = makeLapFromLocalPoints(track, [
      { x: 0, z: 2 },
      { x: 0, z: 1 },
      { x: 1, z: 0 },
      { x: 2, z: 0 },
    ]);

    const projectedArcLengths = getProjectedLapArcLengths(lap, track);

    expect(Array.from(projectedArcLengths)).toEqual([
      expect.closeTo(38, 6),
      expect.closeTo(39, 6),
      expect.closeTo(41, 6),
      expect.closeTo(42, 6),
    ]);
    for (let i = 1; i < projectedArcLengths.length; i += 1) {
      expect(Math.abs(projectedArcLengths[i] - projectedArcLengths[i - 1])).toBeLessThan(5);
    }
  });
});

// ─── P2 camber: chirality (left/right sign) — MANDATORY per spec ──────────────
//
// Scene convention: east → +x, north → −z, up → +y. The driver's LEFT =
// up × forward = (tz, 0, −tx); for an east-heading car forward = +x so left =
// −z = NORTH. altLeft/altRight are the road-edge elevations on those sides.
// These tests pin the sign so the mirror-bug (north = −z) class can never
// silently swap left/right again.

/** Build a track with baked altLeft/altRight camber for the chirality tests. */
function makeCamberTrack(
  points: Array<{ x: number; z: number; dist: number; alt: number; altLeft: number; altRight: number }>,
  width: number,
): TrackData {
  const centerline: TrackPoint[] = points.map((p) => ({
    lat: 0,
    lng: 0,
    alt: p.alt,
    dist: p.dist,
    x: p.x,
    y: p.alt, // origin.alt = 0 below, so y == alt
    z: p.z,
    altLeft: p.altLeft,
    altRight: p.altRight,
  }));
  return {
    version: 1,
    trackId: "camber-test",
    trackName: "Camber Test",
    width,
    totalLength: centerline[centerline.length - 1]?.dist ?? 0,
    origin: { lat: 0, lng: 0, alt: 0 },
    bounds: { lat: [0, 0], lng: [0, 0], x: [0, 0], z: [0, 0] },
    centerline,
  };
}

describe("projectPointToCenterline signed lateral distance", () => {
  // East-heading straight (x increases, z constant). Tangent = +x.
  const track = makeTrack([
    { x: 0, z: 0, dist: 0 },
    { x: 10, z: 0, dist: 10 },
    { x: 20, z: 0, dist: 20 },
  ]);

  it("is POSITIVE for a point to the driver's RIGHT (south, +z, for east heading)", () => {
    const { signedLateralDistance, lateralDistance } = projectPointToCenterline(track, 10, 3);
    expect(signedLateralDistance).toBeCloseTo(3, 6);
    expect(lateralDistance).toBeCloseTo(3, 6);
  });

  it("is NEGATIVE for a point to the driver's LEFT (north, −z, for east heading)", () => {
    const { signedLateralDistance, lateralDistance } = projectPointToCenterline(track, 10, -3);
    expect(signedLateralDistance).toBeCloseTo(-3, 6);
    expect(lateralDistance).toBeCloseTo(3, 6);
  });

  it("keeps the unsigned lateralDistance backward compatible (existing callers)", () => {
    const { lateralDistance } = projectPointToCenterline(track, 5, -4);
    expect(lateralDistance).toBeCloseTo(4, 6);
  });
});

describe("sampleTrackSurface + sampleTrackRoll chirality", () => {
  const width = 12;
  // Left edge higher than right by 1.2 m over 12 m → cross-slope +0.10.
  const track = makeCamberTrack(
    [
      { x: 0, z: 0, dist: 0, alt: 100, altLeft: 100.6, altRight: 99.4 },
      { x: 10, z: 0, dist: 10, alt: 100, altLeft: 100.6, altRight: 99.4 },
      { x: 20, z: 0, dist: 20, alt: 100, altLeft: 100.6, altRight: 99.4 },
    ],
    width,
  );

  it("returns the LEFT edge height at signed lateral −width/2 and RIGHT at +width/2", () => {
    // origin.alt = 0 so surface y == absolute alt here.
    expect(sampleTrackSurface(track, 10, -width / 2)).toBeCloseTo(100.6, 6); // left edge
    expect(sampleTrackSurface(track, 10, width / 2)).toBeCloseTo(99.4, 6); // right edge
    expect(sampleTrackSurface(track, 10, 0)).toBeCloseTo(100, 6); // centerline == alt
  });

  it("roll is POSITIVE when the LEFT edge is higher (car leans right)", () => {
    const roll = sampleTrackRoll(track, 10);
    expect(roll).toBeGreaterThan(0);
    expect(roll).toBeCloseTo(Math.atan((100.6 - 99.4) / width), 6);
  });

  it("roll flips sign when the RIGHT edge is higher", () => {
    const flipped = makeCamberTrack(
      [
        { x: 0, z: 0, dist: 0, alt: 100, altLeft: 99.4, altRight: 100.6 },
        { x: 20, z: 0, dist: 20, alt: 100, altLeft: 99.4, altRight: 100.6 },
      ],
      width,
    );
    expect(sampleTrackRoll(flipped, 10)).toBeLessThan(0);
  });

  it("clamps roll to ±MAX_VEHICLE_ROLL for an extreme cross-slope", () => {
    const steep = makeCamberTrack(
      [
        { x: 0, z: 0, dist: 0, alt: 100, altLeft: 110, altRight: 90 },
        { x: 20, z: 0, dist: 20, alt: 100, altLeft: 110, altRight: 90 },
      ],
      width,
    );
    expect(sampleTrackRoll(steep, 10)).toBeCloseTo(MAX_VEHICLE_ROLL, 6);
  });

  it("falls back to a flat surface / zero roll without baked camber", () => {
    const flat = makeTrack([
      { x: 0, z: 0, dist: 0, y: 5 },
      { x: 20, z: 0, dist: 20, y: 5 },
    ]);
    expect(sampleTrackRoll(flat, 10)).toBe(0);
    expect(sampleTrackSurface(flat, 10, 3)).toBeCloseTo(5, 6); // = centerline y at any lateral
  });

  it("ties the road-mesh edge geometry to the surface: physical-left edge point projects to −width/2 → altLeft", () => {
    // The physical LEFT direction for an east tangent is (tz, −tx) = (0, −1) = −z.
    // A point on the left edge is centerline + halfWidth*(0,−1).
    const leftEdgePoint = { x: 10, z: -width / 2 };
    const { signedLateralDistance } = projectPointToCenterline(track, leftEdgePoint.x, leftEdgePoint.z);
    expect(signedLateralDistance).toBeCloseTo(-width / 2, 6);
    expect(sampleTrackSurface(track, 10, signedLateralDistance)).toBeCloseTo(100.6, 6); // altLeft
    // And the right edge point → +width/2 → altRight.
    const rightProj = projectPointToCenterline(track, 10, width / 2);
    expect(rightProj.signedLateralDistance).toBeCloseTo(width / 2, 6);
    expect(sampleTrackSurface(track, 10, rightProj.signedLateralDistance)).toBeCloseTo(99.4, 6);
  });
});

describe("sampleReplay roll (end-to-end, same path drives car and ghost)", () => {
  it("produces positive roll on an east-heading lap where the left (north) edge is higher", () => {
    const width = 12;
    const track = makeCamberTrack(
      [
        { x: 0, z: 0, dist: 0, alt: 100, altLeft: 100.6, altRight: 99.4 },
        { x: 20, z: 0, dist: 20, alt: 100, altLeft: 100.6, altRight: 99.4 },
        { x: 40, z: 0, dist: 40, alt: 100, altLeft: 100.6, altRight: 99.4 },
      ],
      width,
    );
    const lap = makeLapFromLocalPoints(track, [
      { x: 5, z: 0 },
      { x: 15, z: 0 },
      { x: 25, z: 0 },
    ]);
    const sample = sampleReplay(lap, track, 1);
    expect(sample.roll).toBeGreaterThan(0);
    expect(sample.roll).toBeCloseTo(Math.atan((100.6 - 99.4) / width), 6);
  });
});

describe("sampleReplay", () => {
  it("keeps real Barber replay altitude close to the nearest centerline height", () => {
    const track = readJson<TrackData>("public/data/tracks/barber/track.json");
    const lap = readJson<LapData>("public/data/races/barber_r1/GR86-006-7_lap_023.json");
    const duration = lap.t[lap.t.length - 1] ?? 0;
    const sampleCount = 200;

    for (let i = 0; i < sampleCount; i += 1) {
      const time = (duration * i) / (sampleCount - 1);
      const sample = sampleReplay(lap, track, time);
      const nearestY = nearestCenterlineYByXZ(track, sample.x, sample.z);

      expect(Math.abs(sample.y - nearestY), `altitude mismatch at t=${time.toFixed(3)}s`).toBeLessThan(1);
    }
  });

  // P2 verification item 2: the replayed car must sit ON the cambered road
  // surface. sample.y is computed by sampleReplay; here we independently
  // recompute the surface height at the car's own (x, z) — projecting to get
  // the signed lateral and arc length directly — and require the two to agree
  // to within 0.05 m across every frame of a real lap.
  it("keeps the car on the cambered road surface within 0.05 m over a real Barber lap", () => {
    const track = readJson<TrackData>("public/data/tracks/barber/track.json");
    const lap = readJson<LapData>("public/data/races/barber_r1/GR86-006-7_lap_023.json");
    const duration = lap.t[lap.t.length - 1] ?? 0;
    const sampleCount = 400;
    let maxErr = 0;

    for (let i = 0; i < sampleCount; i += 1) {
      const time = (duration * i) / (sampleCount - 1);
      const sample = sampleReplay(lap, track, time);
      const { arcLength, signedLateralDistance } = projectPointToCenterline(track, sample.x, sample.z);
      const independentSurface = sampleTrackSurface(track, arcLength, signedLateralDistance);
      maxErr = Math.max(maxErr, Math.abs(sample.y - independentSurface));
    }

    expect(maxErr, `max grounding error ${maxErr.toFixed(4)} m`).toBeLessThanOrEqual(0.05);
  });
});
