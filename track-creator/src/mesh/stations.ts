/**
 * Stations = the per-slice description of the road the mesh builders consume:
 * position, tangent, half-width and edge elevations (camber) every road.step
 * metres. Built by resampling the centerline and interpolating the pre-fetched
 * elevation corridor onto it.
 */

import { resampleCenterline, interpByDist, interpUniform } from "../centerline.ts";
import type { Centerline } from "../centerline.ts";
import type { TrackDef, ElevationData } from "../types.ts";

export interface Station {
  x: number;
  z: number;
  dist: number;
  tx: number;
  tz: number;
  /** Lateral distance from the centerline to each paved edge (m, positive outward). */
  offsetLeft: number;
  offsetRight: number;
  /** Elevation at the paved left edge / centerline / paved right edge. */
  yLeft: number;
  yCenter: number;
  yRight: number;
}

export interface StationTrack {
  stations: Station[];
  totalLength: number;
  closed: boolean;
}

export function buildStations(
  def: TrackDef,
  elev: ElevationData,
  precomputedLine?: Centerline,
): StationTrack {
  const line: Centerline =
    precomputedLine ?? resampleCenterline(def.controlPoints, def.road.step, def.closed);
  const { points, totalLength, closed } = line;

  const eDists = elev.samples.map((s) => s.dist);
  const eL = elev.samples.map((s) => s.yLeft);
  const eC = elev.samples.map((s) => s.yCenter);
  const eR = elev.samples.map((s) => s.yRight);

  const profile = (def.road.widthProfile ?? []).slice().sort((a, b) => a.dist - b.dist);
  const wDists = profile.map((k) => k.dist);
  const wVals = profile.map((k) => k.width);

  const edges = def.road.edges;

  const stations: Station[] = points.map((p) => {
    // Per-side edge offsets: free-form edges array wins; otherwise the
    // (symmetric) width profile / base width.
    let offsetLeft: number;
    let offsetRight: number;
    if (edges && edges.left.length > 0 && edges.right.length > 0) {
      offsetLeft = interpUniform(edges.left, edges.step, p.dist, totalLength, closed);
      offsetRight = interpUniform(edges.right, edges.step, p.dist, totalLength, closed);
    } else {
      const width =
        profile.length > 0
          ? interpByDist(wDists, wVals, p.dist, totalLength, closed)
          : def.road.width;
      offsetLeft = width / 2;
      offsetRight = width / 2;
    }

    const yC = interpByDist(eDists, eC, p.dist, totalLength, closed);
    const yLc = interpByDist(eDists, eL, p.dist, totalLength, closed);
    const yRc = interpByDist(eDists, eR, p.dist, totalLength, closed);
    // Corridor edges were sampled at corridorHalfWidth; scale the cross-slope
    // linearly out to each paved edge.
    return {
      x: p.x,
      z: p.z,
      dist: p.dist,
      tx: p.tx,
      tz: p.tz,
      offsetLeft,
      offsetRight,
      yLeft: yC + (yLc - yC) * (offsetLeft / elev.corridorHalfWidth),
      yCenter: yC,
      yRight: yC + (yRc - yC) * (offsetRight / elev.corridorHalfWidth),
    };
  });

  return { stations, totalLength, closed };
}

export interface TrackSample {
  x: number;
  z: number;
  tx: number;
  tz: number;
  offsetLeft: number;
  offsetRight: number;
  yLeft: number;
  yCenter: number;
  yRight: number;
}

/**
 * Precomputes per-field arrays once so repeated arbitrary-dist sampling
 * (curb stripes, editor handles) is a binary search instead of re-mapping
 * every station per query.
 */
export function makeTrackSampler(track: StationTrack): (dist: number) => TrackSample {
  const st = track.stations;
  const n = st.length;
  const dists = new Float64Array(n);
  const xs = new Float64Array(n);
  const zs = new Float64Array(n);
  const txs = new Float64Array(n);
  const tzs = new Float64Array(n);
  const ols = new Float64Array(n);
  const ors = new Float64Array(n);
  const yls = new Float64Array(n);
  const ycs = new Float64Array(n);
  const yrs = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    dists[i] = st[i].dist;
    xs[i] = st[i].x;
    zs[i] = st[i].z;
    txs[i] = st[i].tx;
    tzs[i] = st[i].tz;
    ols[i] = st[i].offsetLeft;
    ors[i] = st[i].offsetRight;
    yls[i] = st[i].yLeft;
    ycs[i] = st[i].yCenter;
    yrs[i] = st[i].yRight;
  }
  const { totalLength, closed } = track;
  return (dist: number) => ({
    x: interpByDist(dists, xs, dist, totalLength, closed),
    z: interpByDist(dists, zs, dist, totalLength, closed),
    tx: interpByDist(dists, txs, dist, totalLength, closed),
    tz: interpByDist(dists, tzs, dist, totalLength, closed),
    offsetLeft: interpByDist(dists, ols, dist, totalLength, closed),
    offsetRight: interpByDist(dists, ors, dist, totalLength, closed),
    yLeft: interpByDist(dists, yls, dist, totalLength, closed),
    yCenter: interpByDist(dists, ycs, dist, totalLength, closed),
    yRight: interpByDist(dists, yrs, dist, totalLength, closed),
  });
}
