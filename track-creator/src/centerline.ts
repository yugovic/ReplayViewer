/**
 * Centerline resampling: a Catmull-Rom spline through the control points,
 * densely tessellated then re-walked at (near-)uniform arc-length steps.
 * Pure math — no THREE, no IO — so it is unit-testable.
 */

export interface Vec2 {
  x: number;
  z: number;
}

export interface CenterlinePoint {
  x: number;
  z: number;
  /** Cumulative arc-length from the start, metres. */
  dist: number;
  /** Unit tangent (direction of increasing dist). */
  tx: number;
  tz: number;
}

export interface Centerline {
  points: CenterlinePoint[];
  /** Full loop length for closed tracks; end-to-end length for open ones. */
  totalLength: number;
  closed: boolean;
}

function catmullRom(p0: Vec2, p1: Vec2, p2: Vec2, p3: Vec2, t: number): Vec2 {
  const t2 = t * t;
  const t3 = t2 * t;
  const f = (a: number, b: number, c: number, d: number) =>
    0.5 *
    (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2 + (-a + 3 * b - 3 * c + d) * t3);
  return { x: f(p0.x, p1.x, p2.x, p3.x), z: f(p0.z, p1.z, p2.z, p3.z) };
}

/** Tessellate the spline into a dense polyline (`subdiv` points per segment). */
export function densePolyline(control: Vec2[], closed: boolean, subdiv = 64): Vec2[] {
  const n = control.length;
  if (n < 2) throw new Error("centerline needs at least 2 control points");
  const pts: Vec2[] = [];
  const segCount = closed ? n : n - 1;
  for (let i = 0; i < segCount; i++) {
    const p0 = control[closed ? (i - 1 + n) % n : Math.max(i - 1, 0)];
    const p1 = control[i];
    const p2 = control[(i + 1) % n];
    const p3 = control[closed ? (i + 2) % n : Math.min(i + 2, n - 1)];
    for (let s = 0; s < subdiv; s++) {
      pts.push(catmullRom(p0, p1, p2, p3, s / subdiv));
    }
  }
  if (!closed) pts.push({ ...control[n - 1] });
  return pts;
}

/**
 * Resample at uniform arc-length spacing. The requested `step` is adjusted so
 * an integer number of stations fits exactly; for closed tracks the last
 * station is one step before the start (the loop wraps, no duplicate point).
 */
export function resampleCenterline(control: Vec2[], step: number, closed: boolean): Centerline {
  const dense = densePolyline(control, closed);
  const n = dense.length;

  const cum = new Float64Array(n);
  for (let i = 1; i < n; i++) {
    cum[i] = cum[i - 1] + Math.hypot(dense[i].x - dense[i - 1].x, dense[i].z - dense[i - 1].z);
  }
  const closeLen = closed
    ? Math.hypot(dense[0].x - dense[n - 1].x, dense[0].z - dense[n - 1].z)
    : 0;
  const totalLength = cum[n - 1] + closeLen;

  const count = closed
    ? Math.max(3, Math.round(totalLength / step))
    : Math.max(2, Math.round(totalLength / step) + 1);
  const actualStep = closed ? totalLength / count : totalLength / (count - 1);

  const points: CenterlinePoint[] = [];
  let seg = 0;
  for (let k = 0; k < count; k++) {
    const d = k * actualStep;
    // Walk forward through the dense polyline (d is monotonic).
    while (seg < n - 1 && cum[seg + 1] < d) seg++;
    let x: number;
    let z: number;
    if (seg >= n - 1) {
      // Past the last dense point: on the closing edge (closed tracks only).
      const t = closeLen > 0 ? (d - cum[n - 1]) / closeLen : 0;
      x = dense[n - 1].x + (dense[0].x - dense[n - 1].x) * t;
      z = dense[n - 1].z + (dense[0].z - dense[n - 1].z) * t;
    } else {
      const segLen = cum[seg + 1] - cum[seg];
      const t = segLen > 0 ? (d - cum[seg]) / segLen : 0;
      x = dense[seg].x + (dense[seg + 1].x - dense[seg].x) * t;
      z = dense[seg].z + (dense[seg + 1].z - dense[seg].z) * t;
    }
    points.push({ x, z, dist: d, tx: 0, tz: 0 });
  }

  // Central-difference tangents (wrapping for closed tracks).
  const m = points.length;
  for (let i = 0; i < m; i++) {
    const prev = closed ? points[(i - 1 + m) % m] : points[Math.max(i - 1, 0)];
    const next = closed ? points[(i + 1) % m] : points[Math.min(i + 1, m - 1)];
    const dx = next.x - prev.x;
    const dz = next.z - prev.z;
    const len = Math.hypot(dx, dz) || 1;
    points[i].tx = dx / len;
    points[i].tz = dz / len;
  }

  return { points, totalLength, closed };
}

/** Wrap an arc-length into [0, total). */
export function wrapDist(d: number, total: number): number {
  return ((d % total) + total) % total;
}

/**
 * Linear interpolation over values sampled uniformly every `step` metres
 * (sample i at dist = i * step). Closed tracks wrap from the last sample back
 * to sample 0; open tracks clamp at both ends.
 */
export function interpUniform(
  values: ArrayLike<number>,
  step: number,
  d: number,
  totalLength: number,
  closed: boolean,
): number {
  const n = values.length;
  if (n === 0) throw new Error("interpUniform: empty values");
  if (n === 1) return values[0];
  if (closed) d = wrapDist(d, totalLength);
  const f = d / step;
  const i = Math.floor(f);
  const t = f - i;
  if (i < 0) return values[0];
  if (i >= n - 1) {
    if (!closed) return values[n - 1];
    // Between the last sample and sample 0 (through totalLength).
    const span = totalLength - (n - 1) * step;
    const tt = span > 0 ? (d - (n - 1) * step) / span : 0;
    return i >= n ? values[0] : values[n - 1] + (values[0] - values[n - 1]) * tt;
  }
  return values[i] + (values[i + 1] - values[i]) * t;
}

/**
 * Linear interpolation over values keyed by ascending `dists`. For closed
 * tracks the gap between the last key and the first (through totalLength)
 * is interpolated too.
 */
export function interpByDist(
  dists: ArrayLike<number>,
  values: ArrayLike<number>,
  d: number,
  totalLength: number,
  closed: boolean,
): number {
  const n = dists.length;
  if (n === 0) throw new Error("interpByDist: empty keys");
  if (n === 1) return values[0];
  if (closed) d = wrapDist(d, totalLength);

  if (d <= dists[0]) {
    if (!closed) return values[0];
    // Between last key (wrapped) and first key.
    const span = dists[0] + (totalLength - dists[n - 1]);
    const t = span > 0 ? (d + (totalLength - dists[n - 1])) / span : 0;
    return values[n - 1] + (values[0] - values[n - 1]) * t;
  }
  if (d >= dists[n - 1]) {
    if (!closed) return values[n - 1];
    const span = dists[0] + (totalLength - dists[n - 1]);
    const t = span > 0 ? (d - dists[n - 1]) / span : 0;
    return values[n - 1] + (values[0] - values[n - 1]) * t;
  }

  // Binary search for the bracketing pair.
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (dists[mid] <= d) lo = mid;
    else hi = mid;
  }
  const span = dists[hi] - dists[lo];
  const t = span > 0 ? (d - dists[lo]) / span : 0;
  return values[lo] + (values[hi] - values[lo]) * t;
}
