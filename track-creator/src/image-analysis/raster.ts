/**
 * Pixel sampling and colour-space conversions for the image analysis module.
 * RasterSource implementations wrap an in-memory pixel buffer (loaded from a
 * georeferenced image or GSI tiles) and expose per-local-coordinate sampling.
 */

import type { Rgb, Lab, RasterSource } from "./types.ts";
import type { GeoImageMetaLike } from "./coordinates.ts";
import { localToPixel } from "./coordinates.ts";
import type { Origin } from "./coordinates.ts";

// ── colour conversions ────────────────────────────────────────────────────────

/** sRGB byte (0..255) to linear (0..1). */
function srgbToLinear(c: number): number {
  const s = c / 255;
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

/** sRGB byte triplet to CIE XYZ (D65). */
function rgbToXyz({ r, g, b }: Rgb): { X: number; Y: number; Z: number } {
  const rl = srgbToLinear(r);
  const gl = srgbToLinear(g);
  const bl = srgbToLinear(b);
  return {
    X: 0.4124564 * rl + 0.3575761 * gl + 0.1804375 * bl,
    Y: 0.2126729 * rl + 0.7151522 * gl + 0.0721750 * bl,
    Z: 0.0193339 * rl + 0.1191920 * gl + 0.9503041 * bl,
  };
}

/** CIE XYZ to Lab (D65 reference white). */
function xyzToLab(X: number, Y: number, Z: number): Lab {
  const Xn = 0.95047, Yn = 1.0, Zn = 1.08883;
  const fx = labF(X / Xn);
  const fy = labF(Y / Yn);
  const fz = labF(Z / Zn);
  return {
    L: 116 * fy - 16,
    a: 500 * (fx - fy),
    b: 200 * (fy - fz),
  };
}

function labF(t: number): number {
  const delta = 6 / 29;
  return t > delta ** 3 ? Math.cbrt(t) : t / (3 * delta * delta) + 4 / 29;
}

/** sRGB byte triplet to CIE Lab. */
export function rgbToLab(rgb: Rgb): Lab {
  const { X, Y, Z } = rgbToXyz(rgb);
  return xyzToLab(X, Y, Z);
}

/** Euclidean distance in Lab space. */
export function labDistance(a: Lab, b: Lab): number {
  return Math.sqrt((a.L - b.L) ** 2 + (a.a - b.a) ** 2 + (a.b - b.b) ** 2);
}

// ── pixel buffer ──────────────────────────────────────────────────────────────

/**
 * In-memory RGBA pixel buffer (Uint8ClampedArray, 4 bytes per pixel).
 * Row-major, top-left origin: index = (py * width + px) * 4.
 */
export interface PixelBuffer {
  data: Uint8ClampedArray;
  width: number;
  height: number;
}

/** Sample a pixel with bilinear interpolation. Returns null if out of bounds. */
export function sampleBilinear(buf: PixelBuffer, px: number, py: number): Rgb | null {
  const { data, width, height } = buf;
  if (px < 0 || px > width - 1 || py < 0 || py > height - 1) return null;

  const x0 = Math.floor(px);
  const y0 = Math.floor(py);
  const x1 = Math.min(x0 + 1, width - 1);
  const y1 = Math.min(y0 + 1, height - 1);
  const fx = px - x0;
  const fy = py - y0;

  const i = (xx: number, yy: number) => (yy * width + xx) * 4;

  const r =
    data[i(x0, y0)] * (1 - fx) * (1 - fy) +
    data[i(x1, y0)] * fx * (1 - fy) +
    data[i(x0, y1)] * (1 - fx) * fy +
    data[i(x1, y1)] * fx * fy;
  const g =
    data[i(x0, y0) + 1] * (1 - fx) * (1 - fy) +
    data[i(x1, y0) + 1] * fx * (1 - fy) +
    data[i(x0, y1) + 1] * (1 - fx) * fy +
    data[i(x1, y1) + 1] * fx * fy;
  const b =
    data[i(x0, y0) + 2] * (1 - fx) * (1 - fy) +
    data[i(x1, y0) + 2] * fx * (1 - fy) +
    data[i(x0, y1) + 2] * (1 - fx) * fy +
    data[i(x1, y1) + 2] * fx * fy;

  return { r: Math.round(r), g: Math.round(g), b: Math.round(b) };
}

// ── GeoImageRasterSource ──────────────────────────────────────────────────────

/**
 * RasterSource backed by a georeferenced image (satellite_meta.json + pixel
 * buffer). Converts local ENU → lat/lng → pixel via the bbox metadata.
 */
export class GeoImageRasterSource implements RasterSource {
  readonly id: string;
  readonly resolutionMetersPerPixel: number;
  private buf: PixelBuffer;
  private origin: Origin;
  private meta: GeoImageMetaLike;

  constructor(
    id: string,
    buf: PixelBuffer,
    origin: Origin,
    meta: GeoImageMetaLike,
  ) {
    this.id = id;
    this.buf = buf;
    this.origin = origin;
    this.meta = meta;
    this.resolutionMetersPerPixel = computeResolution(origin, meta);
  }

  sampleLocal(x: number, z: number): Rgb | null {
    const px = localToPixel(x, z, this.origin, this.meta);
    if (!px) return null;
    return sampleBilinear(this.buf, px.px, px.py);
  }

  containsLocal(x: number, z: number): boolean {
    return localToPixel(x, z, this.origin, this.meta) !== null;
  }
}

/**
 * Estimate metres-per-pixel for a georeferenced image at a given latitude.
 * Uses the average of horizontal and vertical resolution at the bbox centre.
 */
export function computeResolution(origin: Origin, meta: GeoImageMetaLike): number {
  const { lat, lng } = { lat: (meta.bbox.minLat + meta.bbox.maxLat) / 2, lng: (meta.bbox.minLng + meta.bbox.maxLng) / 2 };
  const centerLocal = { x: 0, z: 0 }; // origin maps to (0,0)
  // Sample two points 100 m apart in x and z, convert to pixels, measure.
  const dx = 100;
  const p1 = localToPixel(0, 0, origin, meta);
  const p2 = localToPixel(dx, 0, origin, meta);
  const p3 = localToPixel(0, dx, origin, meta);
  if (!p1 || !p2 || !p3) {
    // Fallback: use bbox width / image width
    const bboxWidthM = (meta.bbox.maxLng - meta.bbox.minLng) * 111320 * Math.cos(lat * Math.PI / 180);
    return bboxWidthM / meta.imageWidth;
  }
  const pxPerMetreX = Math.abs(p2.px - p1.px) / dx;
  const pxPerMetreZ = Math.abs(p3.py - p1.py) / dx;
  const avgPxPerMetre = (pxPerMetreX + pxPerMetreZ) / 2;
  return avgPxPerMetre > 0 ? 1 / avgPxPerMetre : 0.5;
}

// ── normal-section sampler ────────────────────────────────────────────────────

/**
 * Sample pixels along a normal section at a centerline station.
 * Returns an array of { offset, rgb } pairs from left (-maxOffset) to
 * right (+maxOffset), or null where the image is unavailable.
 */
export function sampleNormalSection(
  source: RasterSource,
  cx: number,
  cz: number,
  tx: number,
  tz: number,
  minOffset: number,
  maxOffset: number,
  candidateStep: number,
): { offset: number; rgb: Rgb | null }[] {
  const { nx, nz } = { nx: -tz, nz: tx }; // right normal
  const result: { offset: number; rgb: Rgb | null }[] = [];
  for (let off = -maxOffset; off <= maxOffset + 1e-9; off += candidateStep) {
    const x = cx + nx * off;
    const z = cz + nz * off;
    result.push({ offset: off, rgb: source.sampleLocal(x, z) });
  }
  return result;
}
