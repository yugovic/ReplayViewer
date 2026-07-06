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
export function projectPointToCenterline(
  track: TrackData,
  x: number,
  z: number,
): { arcLength: number; lateralDistance: number; signedLateralDistance: number } {
  const points = track.centerline;
  if (points.length === 0) {
    return { arcLength: 0, lateralDistance: 0, signedLateralDistance: 0 };
  }
  if (points.length === 1) {
    const d = Math.hypot(x - points[0].x, z - points[0].z);
    return { arcLength: points[0].dist, lateralDistance: d, signedLateralDistance: d };
  }

  let bestDistSq = Number.POSITIVE_INFINITY;
  let bestArcLength = points[0].dist;
  let bestSignedLateral = 0;

  for (let i = 0; i < points.length - 1; i += 1) {
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

    if (distSq < bestDistSq) {
      bestDistSq = distSq;
      bestArcLength = a.dist + (b.dist - a.dist) * t;
      // Signed lateral = T×O with T the unit tangent (increasing dist). For an
      // interior projection O ⊥ T so |T×O| == |O|; +ve is the driver's right.
      const segLen = Math.sqrt(segmentLengthSq);
      if (segLen > 0) {
        const tx = dx / segLen;
        const tz = dz / segLen;
        bestSignedLateral = tx * offsetZ - tz * offsetX;
      } else {
        bestSignedLateral = Math.sqrt(distSq);
      }
    }
  }

  return {
    arcLength: bestArcLength,
    lateralDistance: Math.sqrt(bestDistSq),
    signedLateralDistance: bestSignedLateral,
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
  for (let i = 0; i < points.length - 1; i += 1) {
    const a = points[i];
    const b = points[i + 1];
    if (a.dist <= dist && dist <= b.dist) {
      const span = b.dist - a.dist;
      const ratio = span > 0 ? (dist - a.dist) / span : 0;
      return a.y + (b.y - a.y) * ratio;
    }
  }
  return 0;
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
  for (let i = 0; i < points.length - 1; i += 1) {
    const a = points[i];
    const b = points[i + 1];
    if (a.dist <= dist && dist <= b.dist) {
      const span = b.dist - a.dist;
      const ratio = span > 0 ? (dist - a.dist) / span : 0;
      const av = (a[key] as number) ?? a.alt;
      const bv = (b[key] as number) ?? b.alt;
      return av + (bv - av) * ratio;
    }
  }
  return 0;
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

function samplePosition(lap: LapData, track: TrackData, time: number) {
  const clampedTime = clamp(time, 0, lapDuration(lap));
  const lat = interpolateNumberSeries(lap.t, lap.lat, clampedTime);
  const lng = interpolateNumberSeries(lap.t, lap.lng, clampedTime);
  const dist = interpolateNumberSeries(lap.t, lap.dist, clampedTime);
  const projectedArcLengths = getProjectedLapArcLengths(lap, track);
  const projectedDist = interpolateNumberSeries(lap.t, projectedArcLengths, clampedTime);
  const wrappedProjectedDist = wrapTrackArcLength(projectedDist, track.totalLength);
  const local = latLngToLocal(lat, lng, track.origin);
  // Instantaneous signed lateral of the actual line from the centerline, so
  // the car sits on the correct point of the (cambered) road surface.
  const { signedLateralDistance } = projectPointToCenterline(track, local.x, local.z);
  return {
    x: local.x,
    y: sampleTrackSurface(track, wrappedProjectedDist, signedLateralDistance),
    z: local.z,
    dist,
    arcLength: wrappedProjectedDist,
  };
}

function samplePose(lap: LapData, track: TrackData, time: number) {
  const index = findSegmentIndex(lap.t, time);
  const startIndex = Math.max(0, index - 6);
  const endIndex = Math.min(lap.t.length - 1, index + 6);
  const start = latLngToLocal(lap.lat[startIndex], lap.lng[startIndex], track.origin);
  const end = latLngToLocal(lap.lat[endIndex], lap.lng[endIndex], track.origin);
  const dx = end.x - start.x;
  const dz = end.z - start.z;
  const horizontal = Math.hypot(dx, dz);
  if (horizontal < 0.001) {
    return { heading: 0, pitch: 0 };
  }
  const projectedArcLengths = getProjectedLapArcLengths(lap, track);
  const startS = wrapTrackArcLength(projectedArcLengths[startIndex] ?? 0, track.totalLength);
  const endS = wrapTrackArcLength(projectedArcLengths[endIndex] ?? 0, track.totalLength);
  const startY = sampleTrackAltitude(track, startS);
  const endY = sampleTrackAltitude(track, endS);
  const pitch = clamp(Math.atan2(endY - startY, horizontal), -MAX_VEHICLE_PITCH, MAX_VEHICLE_PITCH);
  return {
    heading: Math.atan2(dx, dz),
    pitch,
  };
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
