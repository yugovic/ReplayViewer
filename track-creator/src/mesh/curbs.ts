/**
 * Curb ribbons with red/white stripes BAKED as vertex colors (COLOR_0), so the
 * exported GLB shows stripes in any glTF viewer with a plain white material —
 * no custom shader needed. Each stripe is emitted as its own vertex run
 * (duplicated boundary vertices) so the color change is crisp.
 *
 * Same edge conventions as curbBuilder.ts in the main repo: the inner edge
 * tucks slightly under the paved edge, the ribbon extends outward by
 * span.width and drops a little for drainage.
 */

import { wrapDist } from "../centerline.ts";
import { computeNormals } from "./normals.ts";
import { makeTrackSampler } from "./stations.ts";
import type { TrackSample } from "./stations.ts";
import type { CurbSpanDef } from "../types.ts";
import type { MeshData } from "./road.ts";
import type { StationTrack } from "./stations.ts";

const RED: [number, number, number] = [0.72, 0.06, 0.06];
const WHITE: [number, number, number] = [0.92, 0.92, 0.92];

export interface CurbOptions {
  /** How far the inner edge tucks under the paved edge (m). */
  overlap?: number;
  /** Station spacing within a stripe (m). */
  step?: number;
  /** Inner-edge lift above the road surface (m). */
  yOffset?: number;
  /** Outer-edge drop below the inner edge (m). */
  outerDrop?: number;
}

interface Arrays {
  positions: number[];
  uvs: number[];
  colors: number[];
  indices: number[];
}

function emitStripe(
  out: Arrays,
  track: StationTrack,
  sampler: (dist: number) => TrackSample,
  span: CurbSpanDef,
  start: number,
  end: number,
  color: [number, number, number],
  opts: Required<CurbOptions>,
) {
  const sideSign = span.side === "right" ? 1 : -1;
  const stations = Math.max(2, Math.ceil((end - start) / opts.step) + 1);
  const base = out.positions.length / 3;

  for (let s = 0; s < stations; s++) {
    const t = s / (stations - 1);
    const dist = start + (end - start) * t;
    const c = sampler(track.closed ? wrapDist(dist, track.totalLength) : dist);
    const tl = Math.hypot(c.tx, c.tz) || 1;
    const nx = -c.tz / tl;
    const nz = c.tx / tl;
    const edgeOff = span.side === "right" ? c.offsetRight : c.offsetLeft;
    const innerOff = edgeOff - opts.overlap;
    const outerOff = innerOff + span.width;
    const edgeY = span.side === "right" ? c.yRight : c.yLeft;
    const yIn = edgeY + opts.yOffset;
    const yOut = yIn - opts.outerDrop;

    out.positions.push(
      c.x + nx * sideSign * innerOff, yIn, c.z + nz * sideSign * innerOff,
      c.x + nx * sideSign * outerOff, yOut, c.z + nz * sideSign * outerOff,
    );
    out.uvs.push(0, dist, 1, dist);
    out.colors.push(...color, ...color);

    if (s < stations - 1) {
      const a = base + s * 2;
      const b = a + 1;
      const c2 = a + 2;
      const d = a + 3;
      // Mirrored left-side ribbons flip handedness; reverse winding to keep +Y.
      if (sideSign > 0) out.indices.push(a, b, c2, b, d, c2);
      else out.indices.push(a, c2, b, b, c2, d);
    }
  }
}

/** Build every curb span into one striped mesh. Returns null when there are no spans. */
export function buildCurbsMesh(
  track: StationTrack,
  spans: CurbSpanDef[],
  options: CurbOptions = {},
): MeshData | null {
  if (spans.length === 0 || track.stations.length < 2) return null;
  const opts: Required<CurbOptions> = {
    overlap: options.overlap ?? 0.1,
    step: options.step ?? 1.5,
    yOffset: options.yOffset ?? 0.025,
    outerDrop: options.outerDrop ?? 0.05,
  };

  const out: Arrays = { positions: [], uvs: [], colors: [], indices: [] };
  const sampler = makeTrackSampler(track);
  for (const span of spans) {
    const len = span.endDist - span.startDist;
    if (len <= 0 || span.stripeLength <= 0) continue;
    const stripes = Math.max(1, Math.ceil(len / span.stripeLength));
    for (let k = 0; k < stripes; k++) {
      const s0 = span.startDist + k * span.stripeLength;
      const s1 = Math.min(s0 + span.stripeLength, span.endDist);
      emitStripe(out, track, sampler, span, s0, s1, k % 2 === 0 ? RED : WHITE, opts);
    }
  }
  if (out.indices.length === 0) return null;

  const positions = new Float32Array(out.positions);
  const indices = new Uint32Array(out.indices);
  return {
    positions,
    normals: computeNormals(positions, indices),
    uvs: new Float32Array(out.uvs),
    colors: new Float32Array(out.colors),
    indices,
  };
}
