import { latLngToLocal } from "./projection";
import type { LapData, ReplaySample, TrackData } from "./types";

export const MAX_VEHICLE_PITCH = (8 * Math.PI) / 180;
export const MAX_VEHICLE_ROLL = (10 * Math.PI) / 180;

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function interpolateNumberSeries(times: ArrayLike<number>, values: ArrayLike<number>, time: number): number {
  if (times.length === 0 || values.length === 0) {
    return 0;
  }
  if (time <= times[0]) {
    return values[0] ?? 0;
  }
  const lastIndex = Math.min(times.length, values.length) - 1;
  if (time >= times[lastIndex]) {
    return values[lastIndex] ?? 0;
  }

  let low = 0;
  let high = lastIndex;
  while (low <= high) {
    const mid = Math.floor((low + high) / 2);
    if (times[mid] <= time && time <= times[mid + 1]) {
      const start = times[mid];
      const end = times[mid + 1];
      const span = end - start;
      const ratio = span > 0 ? (time - start) / span : 0;
      return values[mid] + (values[mid + 1] - values[mid]) * ratio;
    }
    if (times[mid] < time) {
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }
  return values[lastIndex] ?? 0;
}

const projectedArcLengthCache = new WeakMap<LapData, Float64Array>();

function wrapTrackArcLength(dist: number, length: number): number {
  if (length <= 0) {
    return 0;
  }
  return ((dist % length) + length) % length;
}

/**
 * Projects a scene-space (x, z) point onto the centerline.
 *
 * `lateralDistance` is the unsigned distance to the centerline (unchanged;
 * existing callers such as computeDrapeInputs rely on it). `signedLateral
 * Distance` is added for P2 camber: it is the signed perpendicular offset in
 * the direction of travel — the 2D cross product of the local unit tangent T
 * (direction of increasing `dist`) with the offset vector O = point − projection,
 * `T×O = T.x·O.z − T.z·O.x`. POSITIVE means the point lies to the driver's
 * RIGHT, negative to the LEFT (left = up × forward = (tz, −tx); e.g. north is
 * the left of an east-heading car since north = −z). This sign convention is
 * pinned by the chirality unit tests and must match bake_road_camber.py's
 * left/right edge assignment.
 */
interface CenterlineProjection {
  arcLength: number;
  lateralDistance: number;
  signedLateralDistance: number;
}

/** Uniform grid over centerline segments. Queries expand outward ring by ring
 * and stop once the best hit provably beats every unscanned cell, so results
 * are bit-identical to the brute-force scan (ties resolve to the lowest
 * segment index, matching the old first-wins loop). Built lazily per track:
 * the satellite/feature drape projects tens of thousands of points and the
 * brute-force scan over all segments dominated scene-build time (~17 s
 * measured on fuji's 193×193 drape grid × 651 segments). */
interface CenterlineIndex {
  cellSize: number;
  minX: number;
  minZ: number;
  cols: number;
  rows: number;
  cells: Map<number, number[]>;
}

const centerlineIndexCache = new WeakMap<TrackData, CenterlineIndex>();

function buildCenterlineIndex(track: TrackData): CenterlineIndex {
  const points = track.centerline;
  let minX = Infinity;
  let minZ = Infinity;
  let maxX = -Infinity;
  let maxZ = -Infinity;
  for (const p of points) {
    if (p.x < minX) minX = p.x;
    if (p.x > maxX) maxX = p.x;
    if (p.z < minZ) minZ = p.z;
    if (p.z > maxZ) maxZ = p.z;
  }
  const totalLength = points[points.length - 1].dist - points[0].dist;
  const avgSegment = totalLength > 0 ? totalLength / (points.length - 1) : 1;
  const cellSize = Math.max(16, avgSegment * 4);
  const cols = Math.max(1, Math.ceil((maxX - minX) / cellSize));
  const rows = Math.max(1, Math.ceil((maxZ - minZ) / cellSize));
  const cells = new Map<number, number[]>();
  for (let i = 0; i < points.length - 1; i += 1) {
    const a = points[i];
    const b = points[i + 1];
    const cx0 = clamp(Math.floor((Math.min(a.x, b.x) - minX) / cellSize), 0, cols - 1);
    const cx1 = clamp(Math.floor((Math.max(a.x, b.x) - minX) / cellSize), 0, cols - 1);
    const cz0 = clamp(Math.floor((Math.min(a.z, b.z) - minZ) / cellSize), 0, rows - 1);
    const cz1 = clamp(Math.floor((Math.max(a.z, b.z) - minZ) / cellSize), 0, rows - 1);
    for (let cx = cx0; cx <= cx1; cx += 1) {
      for (let cz = cz0; cz <= cz1; cz += 1) {
        const key = cx * rows + cz;
        const bucket = cells.get(key);
        if (bucket) {
          bucket.push(i);
        } else {
          cells.set(key, [i]);
        }
      }
    }
  }
  return { cellSize, minX, minZ, cols, rows, cells };
}

/** Exact point-to-segment test shared by both search strategies. Updates and
 * returns the running best. Tie-break on equal distance goes to the LOWER
 * segment index — the brute-force loop kept the first strict improvement, and
 * grid rings visit segments out of order. */
function testSegment(
  points: TrackData["centerline"],
  i: number,
  x: number,
  z: number,
  best: { distSq: number; index: number; arcLength: number; signedLateral: number },
): void {
  const a = points[i];
  const b = points[i + 1];
  const dx = b.x - a.x;
  const dz = b.z - a.z;
  const segmentLengthSq = dx * dx + dz * dz;
  const t =
    segmentLengthSq > 0 ? clamp(((x - a.x) * dx + (z - a.z) * dz) / segmentLengthSq, 0, 1) : 0;
  const projectedX = a.x + dx * t;
  const projectedZ = a.z + dz * t;
  const offsetX = x - projectedX;
  const offsetZ = z - projectedZ;
  const distSq = offsetX * offsetX + offsetZ * offsetZ;

  if (distSq < best.distSq || (distSq === best.distSq && i < best.index)) {
    best.distSq = distSq;
    best.index = i;
    best.arcLength = a.dist + (b.dist - a.dist) * t;
    // Signed lateral = T×O with T the unit tangent (increasing dist). For an
    // interior projection O ⊥ T so |T×O| == |O|; +ve is the driver's right.
    const segLen = Math.sqrt(segmentLengthSq);
    if (segLen > 0) {
      const tx = dx / segLen;
      const tz = dz / segLen;
      best.signedLateral = tx * offsetZ - tz * offsetX;
    } else {
      best.signedLateral = Math.sqrt(distSq);
    }
  }
}

export function projectPointToCenterline(
  track: TrackData,
  x: number,
  z: number,
): CenterlineProjection {
  const points = track.centerline;
  if (points.length === 0) {
    return { arcLength: 0, lateralDistance: 0, signedLateralDistance: 0 };
  }
  if (points.length === 1) {
    const d = Math.hypot(x - points[0].x, z - points[0].z);
    return { arcLength: points[0].dist, lateralDistance: d, signedLateralDistance: d };
  }

  const best = {
    distSq: Number.POSITIVE_INFINITY,
    index: Number.MAX_SAFE_INTEGER,
    arcLength: points[0].dist,
    signedLateral: 0,
  };

  let useBruteForce = points.length - 1 < 64;
  let index: CenterlineIndex | undefined;
  let homeX = 0;
  let homeZ = 0;
  if (!useBruteForce) {
    index = centerlineIndexCache.get(track);
    if (!index) {
      index = buildCenterlineIndex(track);
      centerlineIndexCache.set(track, index);
    }
    // Unclamped home cell: correct ring-distance bounds even for points just
    // outside the grid (out-of-range cells simply miss the map).
    homeX = Math.floor((x - index.minX) / index.cellSize);
    homeZ = Math.floor((z - index.minZ) / index.cellSize);
    // Far outside the grid the ring walk degrades to O(distance²) — the
    // feature drape queries points kilometres off-track, and that pathology
    // measured ~10 s per scene build. The plain scan is O(#segments) no
    // matter where the query sits, so it wins out there.
    const margin = 8;
    if (
      homeX < -margin ||
      homeX >= index.cols + margin ||
      homeZ < -margin ||
      homeZ >= index.rows + margin
    ) {
      useBruteForce = true;
    }
  }

  if (useBruteForce) {
    for (let i = 0; i < points.length - 1; i += 1) {
      testSegment(points, i, x, z, best);
    }
  } else {
    const { cellSize, cols, rows, cells } = index!;
    const maxRing =
      Math.max(Math.abs(homeX), Math.abs(homeX - (cols - 1))) +
      Math.max(Math.abs(homeZ), Math.abs(homeZ - (rows - 1)));

    for (let ring = 0; ring <= maxRing; ring += 1) {
      const x0 = homeX - ring;
      const x1 = homeX + ring;
      const z0 = homeZ - ring;
      const z1 = homeZ + ring;
      for (let cx = x0; cx <= x1; cx += 1) {
        if (cx < 0 || cx >= cols) continue;
        const onXEdge = cx === x0 || cx === x1;
        for (let cz = z0; cz <= z1; cz += 1) {
          if (cz < 0 || cz >= rows) continue;
          if (!onXEdge && cz !== z0 && cz !== z1) continue; // ring perimeter only
          const bucket = cells.get(cx * rows + cz);
          if (!bucket) continue;
          for (const i of bucket) {
            testSegment(points, i, x, z, best);
          }
        }
      }
      // Every unscanned cell (Chebyshev ring ≥ ring+1) is at least
      // ring·cellSize from the query point, so a hit within that radius is
      // provably the global minimum.
      const safeRadius = ring * cellSize;
      if (best.distSq <= safeRadius * safeRadius) {
        break;
      }
    }
  }

  return {
    arcLength: best.arcLength,
    lateralDistance: Math.sqrt(best.distSq),
    signedLateralDistance: best.signedLateral,
  };
}

export function projectPointToCenterlineArcLength(track: TrackData, x: number, z: number): number {
  return projectPointToCenterline(track, x, z).arcLength;
}

export function getProjectedLapArcLengths(lap: LapData, track: TrackData): Float64Array {
  const cached = projectedArcLengthCache.get(lap);
  if (cached) {
    return cached;
  }

  const projectedArcLengths = new Float64Array(lap.t.length);
  const trackLength = track.totalLength;
  for (let i = 0; i < projectedArcLengths.length; i += 1) {
    const local = latLngToLocal(lap.lat[i] ?? 0, lap.lng[i] ?? 0, track.origin);
    const rawArcLength = projectPointToCenterlineArcLength(track, local.x, local.z);
    if (i === 0 || trackLength <= 0) {
      projectedArcLengths[i] = rawArcLength;
      continue;
    }

    const previous = projectedArcLengths[i - 1];
    let bestArcLength = rawArcLength;
    let bestDelta = Math.abs(bestArcLength - previous);

    for (const offset of [-trackLength, trackLength]) {
      const candidate = rawArcLength + offset;
      const delta = Math.abs(candidate - previous);
      if (delta < bestDelta) {
        bestArcLength = candidate;
        bestDelta = delta;
      }
    }

    projectedArcLengths[i] = bestArcLength;
  }

  projectedArcLengthCache.set(lap, projectedArcLengths);
  return projectedArcLengths;
}

export function lapDuration(lap: LapData): number {
  return lap.t.length > 0 ? lap.t[lap.t.length - 1] : 0;
}

export function findSegmentIndex(times: number[], time: number): number {
  if (times.length < 2) {
    return 0;
  }
  if (time <= times[0]) {
    return 0;
  }
  const lastSegment = times.length - 2;
  if (time >= times[times.length - 1]) {
    return lastSegment;
  }

  let low = 0;
  let high = lastSegment;
  while (low <= high) {
    const mid = Math.floor((low + high) / 2);
    if (times[mid] <= time && time <= times[mid + 1]) {
      return mid;
    }
    if (times[mid] < time) {
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }
  return clamp(low, 0, lastSegment);
}

/** Binary search for the centerline segment containing arc length `dist`
 * (dist values are sorted). Returns the index i with points[i].dist <= dist
 * <= points[i+1].dist. Callers guarantee dist is within [first, last]. These
 * samplers run per drape vertex and per replay frame; the previous linear
 * scans measured whole seconds per scene build. */
function centerlineSegmentAt(points: TrackData["centerline"], dist: number): number {
  let low = 0;
  let high = points.length - 2;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (points[mid].dist <= dist) {
      if (dist <= points[mid + 1].dist) {
        return mid;
      }
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }
  return Math.max(0, Math.min(points.length - 2, low));
}

export function sampleTrackAltitude(track: TrackData, dist: number): number {
  const points = track.centerline;
  if (points.length === 0) {
    return 0;
  }
  if (dist <= points[0].dist) {
    return points[0].y;
  }
  const last = points[points.length - 1];
  if (dist >= last.dist) {
    return last.y;
  }
  const i = centerlineSegmentAt(points, dist);
  const a = points[i];
  const b = points[i + 1];
  const span = b.dist - a.dist;
  const ratio = span > 0 ? (dist - a.dist) / span : 0;
  return a.y + (b.y - a.y) * ratio;
}

/** True when the track carries baked road-edge elevations (P2 camber). */
function hasCamber(track: TrackData): boolean {
  const p = track.centerline[0];
  return p != null && typeof p.altLeft === "number" && typeof p.altRight === "number";
}

/** Interpolate a road-edge absolute altitude (altLeft/altRight) by arc length. */
function sampleEdgeAlt(track: TrackData, dist: number, key: "altLeft" | "altRight"): number {
  const points = track.centerline;
  if (points.length === 0) return 0;
  if (dist <= points[0].dist) return (points[0][key] as number) ?? points[0].alt;
  const last = points[points.length - 1];
  if (dist >= last.dist) return (last[key] as number) ?? last.alt;
  const i = centerlineSegmentAt(points, dist);
  const a = points[i];
  const b = points[i + 1];
  const span = b.dist - a.dist;
  const ratio = span > 0 ? (dist - a.dist) / span : 0;
  const av = (a[key] as number) ?? a.alt;
  const bv = (b[key] as number) ?? b.alt;
  return av + (bv - av) * ratio;
}

/**
 * Road surface height (scene y) at arc length `dist` and signed lateral offset
 * `signedLateral` (positive = right, see projectPointToCenterline):
 *   surface = lerp(altLeft, altRight, (d + width/2) / width)
 * clamped to the road width. Falls back to the flat centerline profile when
 * the track has no baked camber (backward compatible). At d = 0 this returns
 * exactly sampleTrackAltitude (altLeft/altRight straddle alt symmetrically).
 */
export function sampleTrackSurface(track: TrackData, dist: number, signedLateral: number): number {
  if (!hasCamber(track)) return sampleTrackAltitude(track, dist);
  const width = track.width;
  const yLeft = sampleEdgeAlt(track, dist, "altLeft") - track.origin.alt;
  const yRight = sampleEdgeAlt(track, dist, "altRight") - track.origin.alt;
  const t = clamp((signedLateral + width / 2) / width, 0, 1);
  return yLeft + (yRight - yLeft) * t;
}

/**
 * Bank/camber roll (radians) at arc length `dist`: atan((altLeft − altRight)/
 * width), clamped to ±MAX_VEHICLE_ROLL. POSITIVE = left edge higher = car leans
 * right. 0 for tracks without baked camber. Sign pinned by the chirality tests.
 */
export function sampleTrackRoll(track: TrackData, dist: number): number {
  if (!hasCamber(track)) return 0;
  const altLeft = sampleEdgeAlt(track, dist, "altLeft");
  const altRight = sampleEdgeAlt(track, dist, "altRight");
  const roll = Math.atan((altLeft - altRight) / track.width);
  return clamp(roll, -MAX_VEHICLE_ROLL, MAX_VEHICLE_ROLL);
}

/** Per-lap motion arrays for smooth playback. 10 Hz GPS through plain linear
 * interpolation renders as a visible 100 ms stutter: velocity is constant
 * inside a segment and jumps at every knot, and the old pose window only
 * changed value when the segment index changed (piecewise-constant heading).
 * Positions are therefore sampled with a C1 cubic Hermite (finite-difference
 * tangents = non-uniform Catmull-Rom, passing exactly through every GPS
 * sample) and heading/pitch are interpolated between per-sample values
 * (headings unwrapped so the lerp never crosses the ±π seam). Cached by lap
 * identity like the projected-arc-length cache above. */
interface LapMotionArrays {
  xs: Float64Array;
  zs: Float64Array;
  headings: Float64Array;
  pitches: Float64Array;
}

const lapMotionCache = new WeakMap<LapData, LapMotionArrays>();

const POSE_WINDOW = 6;

function getLapMotionArrays(lap: LapData, track: TrackData): LapMotionArrays {
  const cached = lapMotionCache.get(lap);
  if (cached) {
    return cached;
  }
  const count = lap.t.length;
  const xs = new Float64Array(count);
  const zs = new Float64Array(count);
  for (let i = 0; i < count; i += 1) {
    const local = latLngToLocal(lap.lat[i] ?? 0, lap.lng[i] ?? 0, track.origin);
    xs[i] = local.x;
    zs[i] = local.z;
  }
  const projected = getProjectedLapArcLengths(lap, track);
  const headings = new Float64Array(count);
  const pitches = new Float64Array(count);
  let previousHeading = 0;
  for (let i = 0; i < count; i += 1) {
    const s = Math.max(0, i - POSE_WINDOW);
    const e = Math.min(count - 1, i + POSE_WINDOW);
    const dx = xs[e] - xs[s];
    const dz = zs[e] - zs[s];
    const horizontal = Math.hypot(dx, dz);
    // Stationary stretch: hold the previous pose instead of snapping to 0.
    let heading = previousHeading;
    let pitch = i > 0 ? pitches[i - 1] : 0;
    if (horizontal >= 0.001) {
      heading = Math.atan2(dx, dz);
      // Unwrap onto the previous sample so linear interpolation between
      // consecutive headings never takes the long way around the circle.
      while (heading - previousHeading > Math.PI) heading -= 2 * Math.PI;
      while (heading - previousHeading < -Math.PI) heading += 2 * Math.PI;
      const startS = wrapTrackArcLength(projected[s] ?? 0, track.totalLength);
      const endS = wrapTrackArcLength(projected[e] ?? 0, track.totalLength);
      const startY = sampleTrackAltitude(track, startS);
      const endY = sampleTrackAltitude(track, endS);
      pitch = clamp(Math.atan2(endY - startY, horizontal), -MAX_VEHICLE_PITCH, MAX_VEHICLE_PITCH);
    }
    headings[i] = heading;
    pitches[i] = pitch;
    previousHeading = heading;
  }
  const arrays: LapMotionArrays = { xs, zs, headings, pitches };
  lapMotionCache.set(lap, arrays);
  return arrays;
}

/** Finite-difference tangent at sample `i` (non-uniform Catmull-Rom). */
function sampleTangent(times: number[], values: Float64Array, i: number): number {
  const prev = Math.max(0, i - 1);
  const next = Math.min(values.length - 1, i + 1);
  const span = times[next] - times[prev];
  return span > 0 ? (values[next] - values[prev]) / span : 0;
}

/** C1 cubic Hermite between samples `index` and `index + 1` at `time`. */
function sampleHermite(times: number[], values: Float64Array, index: number, time: number): number {
  const t0 = times[index];
  const t1 = times[index + 1];
  const span = t1 - t0;
  if (!(span > 0)) {
    return values[index];
  }
  const u = clamp((time - t0) / span, 0, 1);
  const u2 = u * u;
  const u3 = u2 * u;
  const m0 = sampleTangent(times, values, index) * span;
  const m1 = sampleTangent(times, values, index + 1) * span;
  return (
    (2 * u3 - 3 * u2 + 1) * values[index] +
    (u3 - 2 * u2 + u) * m0 +
    (-2 * u3 + 3 * u2) * values[index + 1] +
    (u3 - u2) * m1
  );
}

function samplePosition(lap: LapData, track: TrackData, time: number) {
  const clampedTime = clamp(time, 0, lapDuration(lap));
  const count = lap.t.length;
  const motion = getLapMotionArrays(lap, track);
  let x = 0;
  let z = 0;
  if (count > 0) {
    if (count === 1 || clampedTime <= lap.t[0]) {
      x = motion.xs[0];
      z = motion.zs[0];
    } else if (clampedTime >= lap.t[count - 1]) {
      x = motion.xs[count - 1];
      z = motion.zs[count - 1];
    } else {
      const index = findSegmentIndex(lap.t, clampedTime);
      x = sampleHermite(lap.t, motion.xs, index, clampedTime);
      z = sampleHermite(lap.t, motion.zs, index, clampedTime);
    }
  }
  const dist = interpolateNumberSeries(lap.t, lap.dist, clampedTime);
  const projectedArcLengths = getProjectedLapArcLengths(lap, track);
  const projectedDist = interpolateNumberSeries(lap.t, projectedArcLengths, clampedTime);
  const wrappedProjectedDist = wrapTrackArcLength(projectedDist, track.totalLength);
  // Instantaneous signed lateral of the actual line from the centerline, so
  // the car sits on the correct point of the (cambered) road surface.
  const { signedLateralDistance } = projectPointToCenterline(track, x, z);
  return {
    x,
    y: sampleTrackSurface(track, wrappedProjectedDist, signedLateralDistance),
    z,
    dist,
    arcLength: wrappedProjectedDist,
  };
}

function samplePose(lap: LapData, track: TrackData, time: number) {
  if (lap.t.length === 0) {
    return { heading: 0, pitch: 0 };
  }
  const motion = getLapMotionArrays(lap, track);
  const heading = interpolateNumberSeries(lap.t, motion.headings, time);
  const pitch = interpolateNumberSeries(lap.t, motion.pitches, time);
  // Unwrapped headings can drift beyond ±π after enough same-direction turns;
  // renormalize for consumers that expect atan2 range.
  return { heading: Math.atan2(Math.sin(heading), Math.cos(heading)), pitch };
}

export function sampleReplay(lap: LapData, track: TrackData, time: number): ReplaySample {
  const duration = lapDuration(lap);
  const clampedTime = clamp(time, 0, duration);
  const position = samplePosition(lap, track, clampedTime);
  const pose = samplePose(lap, track, clampedTime);
  const roll = sampleTrackRoll(track, position.arcLength);

  return {
    time: clampedTime,
    x: position.x,
    y: position.y,
    z: position.z,
    heading: pose.heading,
    pitch: pose.pitch,
    roll,
    telemetry: {
      speed: interpolateNumberSeries(lap.t, lap.speed, clampedTime),
      aps: interpolateNumberSeries(lap.t, lap.aps, clampedTime),
      brake: interpolateNumberSeries(lap.t, lap.brake, clampedTime),
      steer: interpolateNumberSeries(lap.t, lap.steer, clampedTime),
      gear: Math.round(interpolateNumberSeries(lap.t, lap.gear, clampedTime)),
      accx: interpolateNumberSeries(lap.t, lap.accx, clampedTime),
      accy: interpolateNumberSeries(lap.t, lap.accy, clampedTime),
      dist: position.dist,
    },
  };
}
