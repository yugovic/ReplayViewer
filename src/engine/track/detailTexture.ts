/**
 * Tileable grayscale value-noise for ground-photo detail blending (Plan A).
 *
 * The satellite photo is draped at ~0.30 m/px and looks blurry up close. This
 * module generates procedural luminance-only noise that TrackBuilder mixes
 * into the photo's albedo near the camera (see buildSatelliteGround()'s
 * onBeforeCompile) — it must never invent fake color or geometric detail,
 * only modulate brightness.
 *
 * The generator (generateDetailNoise) is pure DOM-free math so it runs
 * identically under Vitest (Node) and the browser, and is deterministic
 * (mulberry32 seed) so tests are stable. buildDetailTexture() is the
 * browser-side wrapper that turns the data into a THREE.DataTexture.
 */

import * as THREE from "three";

/** Seeded 32-bit PRNG — same algorithm as TrackBuilder's asphalt grain noise. */
function mulberry32(seed: number) {
  let s = seed >>> 0;
  return () => {
    s += 0x6d2b79f5;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t ^= t + Math.imul(t ^ (t >>> 7), 61 | t);
    return ((t ^ (t >>> 14)) >>> 0) / 0xffffffff;
  };
}

function smoothstep01(t: number): number {
  return t * t * (3 - 2 * t);
}

export interface DetailNoiseOctave {
  /** Lattice cells per axis for this octave (need not divide `size`). */
  cells: number;
  /** Contribution weight before normalization. */
  weight: number;
}

export interface DetailNoiseOptions {
  seed: number;
  octaves: DetailNoiseOctave[];
  /** Max deviation from 0.5 after normalization (e.g. 0.12 => output in [0.38, 0.62]). */
  amplitude: number;
}

/** Fine, high-frequency grain for asphalt — subtle, close-up road texture. */
export const ASPHALT_DETAIL_OPTIONS: DetailNoiseOptions = {
  seed: 0x41535048, // "ASPH"
  octaves: [
    { cells: 8, weight: 0.3 },
    { cells: 16, weight: 0.35 },
    { cells: 32, weight: 0.35 },
  ],
  amplitude: 0.12,
};

/** Lower-frequency mottling plus a little fine grain, for grass/dirt runoff. */
export const GRASS_DETAIL_OPTIONS: DetailNoiseOptions = {
  seed: 0x47524153, // "GRAS"
  octaves: [
    { cells: 4, weight: 0.45 },
    { cells: 8, weight: 0.35 },
    { cells: 16, weight: 0.2 },
  ],
  amplitude: 0.16,
};

/** Builds one `cells` x `cells` lattice of seeded values in [0, 1). */
function buildLattice(cells: number, rng: () => number): Float32Array {
  const lattice = new Float32Array(cells * cells);
  for (let i = 0; i < lattice.length; i++) lattice[i] = rng();
  return lattice;
}

/**
 * Bilinear, wrap-around sample of a `cells` x `cells` lattice at a continuous
 * (x, y) given in lattice units. Because lattice indices wrap modulo `cells`,
 * this function is exactly periodic with period `cells` in both axes — so the
 * noise field tiles seamlessly regardless of how `cells` relates to the
 * output texture's pixel size.
 */
function sampleLatticeWrapped(lattice: Float32Array, cells: number, x: number, y: number): number {
  const x0 = Math.floor(x);
  const y0 = Math.floor(y);
  const fx = smoothstep01(x - x0);
  const fy = smoothstep01(y - y0);
  const x0w = ((x0 % cells) + cells) % cells;
  const y0w = ((y0 % cells) + cells) % cells;
  const x1w = (x0w + 1) % cells;
  const y1w = (y0w + 1) % cells;

  const v00 = lattice[y0w * cells + x0w];
  const v10 = lattice[y0w * cells + x1w];
  const v01 = lattice[y1w * cells + x0w];
  const v11 = lattice[y1w * cells + x1w];

  const a = v00 * (1 - fx) + v10 * fx;
  const b = v01 * (1 - fx) + v11 * fx;
  return a * (1 - fy) + b * fy;
}

/**
 * Generates a tileable grayscale value-noise field, normalized so the mean is
 * exactly 0.5 (a mean of 0.5 means the shader's `detail.r * 2.0` modulation
 * averages to a no-op — no invented brightness where octaves cancel out or
 * the effect fades with distance) and rescaled so its peak deviation exactly
 * matches `options.amplitude`, keeping the look subtle rather than dirty.
 */
export function generateDetailNoise(size: number, options: DetailNoiseOptions): Float32Array {
  const { seed, octaves, amplitude } = options;
  const rng = mulberry32(seed);
  const totalWeight = octaves.reduce((sum, o) => sum + o.weight, 0) || 1;
  const lattices = octaves.map((o) => buildLattice(o.cells, rng));

  const raw = new Float32Array(size * size);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let value = 0;
      for (let oi = 0; oi < octaves.length; oi++) {
        const { cells, weight } = octaves[oi];
        value += sampleLatticeWrapped(lattices[oi], cells, (x / size) * cells, (y / size) * cells) * weight;
      }
      raw[y * size + x] = value / totalWeight;
    }
  }

  let sum = 0;
  for (let i = 0; i < raw.length; i++) sum += raw[i];
  const mean = sum / raw.length;

  let maxAbs = 0;
  for (let i = 0; i < raw.length; i++) {
    const centered = Math.abs(raw[i] - mean);
    if (centered > maxAbs) maxAbs = centered;
  }
  const scale = maxAbs > 1e-9 ? amplitude / maxAbs : 0;

  const out = new Float32Array(raw.length);
  for (let i = 0; i < raw.length; i++) {
    out[i] = 0.5 + (raw[i] - mean) * scale;
  }
  return out;
}

/** Output texture resolution — power of two so mipmaps generate cleanly. */
export const DETAIL_TEXTURE_SIZE = 256;

/**
 * Browser-side wrapper: builds a THREE.DataTexture from the noise field.
 * RGBA (not RedFormat) for broad WebGL1/2 compatibility; only the red
 * channel is read by the shader (see TrackBuilder's onBeforeCompile).
 */
export function buildDetailTexture(size: number, options: DetailNoiseOptions): THREE.DataTexture {
  const values = generateDetailNoise(size, options);
  const data = new Uint8Array(size * size * 4);
  for (let i = 0; i < values.length; i++) {
    const v = Math.max(0, Math.min(255, Math.round(values[i] * 255)));
    const o = i * 4;
    data[o] = v;
    data[o + 1] = v;
    data[o + 2] = v;
    data[o + 3] = 255;
  }

  const texture = new THREE.DataTexture(data, size, size, THREE.RGBAFormat, THREE.UnsignedByteType);
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.generateMipmaps = true;
  texture.needsUpdate = true;
  return texture;
}

export function buildAsphaltDetailTexture(): THREE.DataTexture {
  return buildDetailTexture(DETAIL_TEXTURE_SIZE, ASPHALT_DETAIL_OPTIONS);
}

export function buildGrassDetailTexture(): THREE.DataTexture {
  return buildDetailTexture(DETAIL_TEXTURE_SIZE, GRASS_DETAIL_OPTIONS);
}
