/**
 * Pure height-field math for the terrain drape: no three.js, no DOM, so it
 * can be unit tested directly and reused by TerrainSampler (browser-side
 * loader) and TrackBuilder (per-vertex placement) alike.
 */

export interface Bbox {
  minLng: number;
  maxLng: number;
  minLat: number;
  maxLat: number;
}

const DEG2RAD = Math.PI / 180;

/** AWS/Mapzen Terrarium encoding: height (m) packed into RGB. */
export function decodeTerrariumHeight(r: number, g: number, b: number): number {
  return r * 256 + g + b / 256 - 32768;
}

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value));
}

function mercatorY(latDeg: number): number {
  return Math.asinh(Math.tan(latDeg * DEG2RAD));
}

/**
 * Maps (lat, lng) to a terrain-image UV using the same Mercator convention as
 * satelliteUvToLatLng (v=0 at the north edge, bbox.maxLat). Clamped to
 * [0, 1] so points just outside the bbox (projection/rounding error) sample
 * the nearest edge instead of extrapolating.
 */
export function latLngToTerrainUv(lat: number, lng: number, bbox: Bbox): { u: number; v: number } {
  const u = (lng - bbox.minLng) / (bbox.maxLng - bbox.minLng);
  const yNorth = mercatorY(bbox.maxLat);
  const ySouth = mercatorY(bbox.minLat);
  const v = (yNorth - mercatorY(lat)) / (yNorth - ySouth);
  return { u: clamp01(u), v: clamp01(v) };
}

/** Bilinear sample of a row-major heightfield; u/v are normalised [0, 1]. */
export function bilinearSample(
  heights: Float32Array,
  width: number,
  height: number,
  u: number,
  v: number,
): number {
  const px = u * (width - 1);
  const py = v * (height - 1);
  const x0 = Math.floor(px);
  const y0 = Math.floor(py);
  const x1 = Math.min(x0 + 1, width - 1);
  const y1 = Math.min(y0 + 1, height - 1);
  const fx = px - x0;
  const fy = py - y0;

  const h00 = heights[y0 * width + x0];
  const h10 = heights[y0 * width + x1];
  const h01 = heights[y1 * width + x0];
  const h11 = heights[y1 * width + x1];

  return h00 * (1 - fx) * (1 - fy) + h10 * fx * (1 - fy) + h01 * (1 - fx) * fy + h11 * fx * fy;
}

export interface CorridorBlendParams {
  roadY: number;
  terrainY: number;
  lateralDistance: number;
  halfRoadWidth: number;
}

/**
 * Blends between "just under the asphalt" (inside the road corridor) and the
 * real terrain height (out past the runoff), smoothstepped across a 18 m
 * transition band. `clearance` is how far below the road surface the corridor
 * sits: 0.6 m under the procedural road, 0.08 m when the satellite photo acts
 * as the road. A safety cap keeps the blended value from rising above
 * roadY - max(clearance / 2.4, 0.05) mid-blend, in case the terrain sits
 * locally above the road right next to the corridor.
 */
export function corridorBlendHeight(params: CorridorBlendParams, clearance = 0.6): number {
  const { roadY, terrainY, lateralDistance, halfRoadWidth } = params;
  const blendStart = halfRoadWidth + 2;
  const blendEnd = halfRoadWidth + 20;
  const corridorY = roadY - clearance;

  if (lateralDistance <= blendStart) {
    return corridorY;
  }
  if (lateralDistance >= blendEnd) {
    return terrainY;
  }

  const t = (lateralDistance - blendStart) / (blendEnd - blendStart);
  const smooth = t * t * (3 - 2 * t);
  const blended = corridorY + (terrainY - corridorY) * smooth;
  return Math.min(blended, roadY - Math.max(clearance / 2.4, 0.05));
}

/**
 * Corridor mask for the ground detail-texture blend (Plan A): 1.0 well inside
 * the paved road, smoothstepping down to 0.0 a couple of metres past the
 * asphalt edge. This drives the asphalt-grain <-> grass-mottle crossfade in
 * TrackBuilder's satellite-ground shader — a much narrower transition band
 * than corridorBlendHeight's own 18 m height blend, since this only needs to
 * track the visible paint edge, not the runoff terrain.
 *
 * Equivalent to `1.0 - smoothstep(halfRoadWidth - 1.0, halfRoadWidth + 2.0, lateralDistance)`.
 */
export function corridorMask(lateralDistance: number, halfRoadWidth: number): number {
  const innerEdge = halfRoadWidth - 1;
  const outerEdge = halfRoadWidth + 2;
  if (lateralDistance <= innerEdge) return 1;
  if (lateralDistance >= outerEdge) return 0;
  const t = (lateralDistance - innerEdge) / (outerEdge - innerEdge);
  return 1 - t * t * (3 - 2 * t);
}

/**
 * Per-vertex drape inputs captured when the satellite ground is built, so a
 * road-layer toggle can re-blend every height without redoing the centerline
 * projection or terrain sampling.
 */
export interface DrapeCache {
  roadY: Float32Array;
  terrainY: Float32Array;
  lateralDistance: Float32Array;
  halfRoadWidth: number;
}

/** Recompute all draped vertex heights from cached inputs for a clearance. */
export function reblendDrapedHeights(
  cache: DrapeCache,
  clearance: number,
  out?: Float32Array,
): Float32Array {
  const { roadY, terrainY, lateralDistance, halfRoadWidth } = cache;
  const heights = out ?? new Float32Array(roadY.length);
  for (let i = 0; i < roadY.length; i += 1) {
    heights[i] = corridorBlendHeight(
      { roadY: roadY[i], terrainY: terrainY[i], lateralDistance: lateralDistance[i], halfRoadWidth },
      clearance,
    );
  }
  return heights;
}
