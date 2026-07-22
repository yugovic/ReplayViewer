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

// ─── Material-response detail (Task 1): colored albedo + normal map ──────────
//
// The luminance-only noise above is a near-invisible ±12% brightness wobble.
// The real requirement is that the road READ AS ASPHALT at chase distance:
// aggregate grain that reacts to light. That needs (a) a tangent-space normal
// map so the lighting picks up micro-relief, and (b) a stronger, subtly
// colored albedo mottling. These generators are the procedural FALLBACK used
// when the CC0 JPGs under public/data/textures/ are missing/404 at runtime
// (see loadDetailMaterialTextures in groundDetail.ts). Both are pure DOM-free
// math so they unit-test (see detailTexture.test.ts).

/** RGB mottling around a neutral base color, mean-preserving so the distant
 * (faded) view still matches the satellite photo. Two independent noise fields
 * drive it: a shared-luminance term (all channels move together = light/dark
 * grain) and a chroma term (channels move oppositely = warm/cool or green
 * variation). Because generateDetailNoise is mean-0.5, both terms are centered
 * and every channel's mean equals `base` exactly. */
export interface ColorNoiseOptions {
  /** Neutral base color (linear 0..1) each channel is centered on. */
  base: [number, number, number];
  /** Seeded luminance-noise config (shared across channels). */
  luminance: DetailNoiseOptions;
  /** Seeded chroma-noise config (pushes channels apart). */
  chroma: DetailNoiseOptions;
  /** Peak luminance swing added to every channel (linear). */
  luminanceStrength: number;
  /** Per-channel chroma tilt (linear); positive R = warmer, positive B = cooler. */
  chromaTilt: [number, number, number];
}

/** Cool/warm gray aggregate for asphalt: neutral ~0.5 gray with a faint blue
 * base and warm/cool mottling so some patches read warmer than others. */
export const ASPHALT_COLOR_OPTIONS: ColorNoiseOptions = {
  base: [0.5, 0.5, 0.52],
  luminance: {
    seed: 0x41435031,
    octaves: [
      { cells: 8, weight: 0.3 },
      { cells: 16, weight: 0.35 },
      { cells: 32, weight: 0.35 },
    ],
    amplitude: 0.5,
  },
  chroma: {
    seed: 0x41435032,
    octaves: [
      { cells: 4, weight: 0.5 },
      { cells: 8, weight: 0.5 },
    ],
    amplitude: 0.5,
  },
  luminanceStrength: 0.16,
  chromaTilt: [0.05, 0.0, -0.05],
};

/** Green/brown mottling for grass runoff. */
export const GRASS_COLOR_OPTIONS: ColorNoiseOptions = {
  base: [0.33, 0.42, 0.22],
  luminance: {
    seed: 0x47434c31,
    octaves: [
      { cells: 4, weight: 0.45 },
      { cells: 8, weight: 0.35 },
      { cells: 16, weight: 0.2 },
    ],
    amplitude: 0.5,
  },
  chroma: {
    seed: 0x47434c32,
    octaves: [
      { cells: 4, weight: 0.6 },
      { cells: 8, weight: 0.4 },
    ],
    amplitude: 0.5,
  },
  luminanceStrength: 0.1,
  chromaTilt: [0.06, 0.03, -0.04],
};

/** Generates an RGB detail-albedo field (row-major, 3 floats per texel). Mean
 * of each channel equals `options.base[channel]` — verified by tests. */
export function generateColorNoise(size: number, options: ColorNoiseOptions): Float32Array {
  const { base, luminance, chroma, luminanceStrength, chromaTilt } = options;
  // Normalized noise fields are centered on 0.5; rescale each by its own
  // amplitude so the deviation lands in ~[-1, 1] before weighting.
  const lum = generateDetailNoise(size, luminance);
  const chr = generateDetailNoise(size, chroma);
  const invAmpL = luminance.amplitude > 1e-9 ? 1 / luminance.amplitude : 0;
  const invAmpC = chroma.amplitude > 1e-9 ? 1 / chroma.amplitude : 0;
  const out = new Float32Array(size * size * 3);
  for (let i = 0; i < size * size; i++) {
    const dl = (lum[i] - 0.5) * invAmpL;
    const dc = (chr[i] - 0.5) * invAmpC;
    const o = i * 3;
    out[o] = base[0] + dl * luminanceStrength + dc * chromaTilt[0];
    out[o + 1] = base[1] + dl * luminanceStrength + dc * chromaTilt[1];
    out[o + 2] = base[2] + dl * luminanceStrength + dc * chromaTilt[2];
  }
  return out;
}

/** Wrap-around index into a `size`-length axis. */
function wrapIndex(i: number, size: number): number {
  return ((i % size) + size) % size;
}

/**
 * Tangent-space normal map (row-major, 3 floats per texel in [0, 1]) from a
 * tileable scalar height field, via central differences with wrap-around so
 * the map tiles seamlessly. The +Z (blue) component is the surface up-axis and
 * is always > 0.5 after encoding (nz = 1/‖·‖ > 0) — asserted by tests. `scale`
 * controls bump steepness (height units are the field's own 0..1 range).
 */
export function generateNormalMap(height: Float32Array, size: number, scale: number): Float32Array {
  const out = new Float32Array(size * size * 3);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const hL = height[y * size + wrapIndex(x - 1, size)];
      const hR = height[y * size + wrapIndex(x + 1, size)];
      const hD = height[wrapIndex(y - 1, size) * size + x];
      const hU = height[wrapIndex(y + 1, size) * size + x];
      // Surface normal tilts against the height gradient.
      const nx = -(hR - hL) * scale;
      const ny = -(hU - hD) * scale;
      const nz = 1;
      const inv = 1 / Math.hypot(nx, ny, nz);
      const o = (y * size + x) * 3;
      out[o] = nx * inv * 0.5 + 0.5;
      out[o + 1] = ny * inv * 0.5 + 0.5;
      out[o + 2] = nz * inv * 0.5 + 0.5;
    }
  }
  return out;
}

/** Height field for the procedural asphalt normal map — reuses the seeded,
 * tileable value-noise generator (a deterministic stand-in for the CC0
 * NormalGL map, weighted toward high octaves for fine aggregate grain). */
export const ASPHALT_HEIGHT_OPTIONS: DetailNoiseOptions = {
  seed: 0x41485448, // "AHTH"
  octaves: [
    { cells: 16, weight: 0.25 },
    { cells: 32, weight: 0.35 },
    { cells: 64, weight: 0.4 },
  ],
  amplitude: 0.5,
};

/** Softer, broader height field for grass blades and uneven soil. */
export const GRASS_HEIGHT_OPTIONS: DetailNoiseOptions = {
  seed: 0x47485448, // "GHTH"
  octaves: [
    { cells: 8, weight: 0.35 },
    { cells: 16, weight: 0.4 },
    { cells: 32, weight: 0.25 },
  ],
  amplitude: 0.5,
};

function clamp255(v: number): number {
  return Math.max(0, Math.min(255, Math.round(v * 255)));
}

function configureTiling(tex: THREE.DataTexture): void {
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.generateMipmaps = true;
  tex.needsUpdate = true;
}

/** Browser-side: RGBA sRGB DataTexture from an RGB color-noise field. */
export function buildColorTexture(size: number, options: ColorNoiseOptions): THREE.DataTexture {
  const rgb = generateColorNoise(size, options);
  const data = new Uint8Array(size * size * 4);
  for (let i = 0; i < size * size; i++) {
    const o = i * 4;
    const s = i * 3;
    data[o] = clamp255(rgb[s]);
    data[o + 1] = clamp255(rgb[s + 1]);
    data[o + 2] = clamp255(rgb[s + 2]);
    data[o + 3] = 255;
  }
  const tex = new THREE.DataTexture(data, size, size, THREE.RGBAFormat, THREE.UnsignedByteType);
  tex.colorSpace = THREE.SRGBColorSpace;
  configureTiling(tex);
  return tex;
}

/** Browser-side: RGBA linear (NoColorSpace) normal-map DataTexture. */
export function buildNormalTexture(
  size: number,
  heightOptions: DetailNoiseOptions,
  scale: number,
): THREE.DataTexture {
  const height = generateDetailNoise(size, heightOptions);
  const rgb = generateNormalMap(height, size, scale);
  const data = new Uint8Array(size * size * 4);
  for (let i = 0; i < size * size; i++) {
    const o = i * 4;
    const s = i * 3;
    data[o] = clamp255(rgb[s]);
    data[o + 1] = clamp255(rgb[s + 1]);
    data[o + 2] = clamp255(rgb[s + 2]);
    data[o + 3] = 255;
  }
  const tex = new THREE.DataTexture(data, size, size, THREE.RGBAFormat, THREE.UnsignedByteType);
  tex.colorSpace = THREE.NoColorSpace;
  configureTiling(tex);
  return tex;
}

export function buildProceduralAsphaltAlbedo(): THREE.DataTexture {
  return buildColorTexture(DETAIL_TEXTURE_SIZE, ASPHALT_COLOR_OPTIONS);
}

export function buildProceduralGrassAlbedo(): THREE.DataTexture {
  return buildColorTexture(DETAIL_TEXTURE_SIZE, GRASS_COLOR_OPTIONS);
}

export function buildProceduralAsphaltNormal(): THREE.DataTexture {
  return buildNormalTexture(DETAIL_TEXTURE_SIZE, ASPHALT_HEIGHT_OPTIONS, 2.5);
}

export function buildProceduralGrassNormal(): THREE.DataTexture {
  return buildNormalTexture(DETAIL_TEXTURE_SIZE, GRASS_HEIGHT_OPTIONS, 1.8);
}

// -- Close-range roughness response ------------------------------------------

export interface RoughnessNoiseOptions {
  /** Mean physical roughness in [0, 1]. */
  base: number;
  /** Maximum deviation from base. */
  variation: number;
  /** Tileable source field; its configured amplitude is normalized away. */
  noise: DetailNoiseOptions;
}

/** Fine aggregate alternates between slightly polished and coarse asphalt. */
export const ASPHALT_ROUGHNESS_OPTIONS: RoughnessNoiseOptions = {
  base: 0.82,
  variation: 0.14,
  noise: {
    seed: 0x41524f55, // "AROU"
    octaves: [
      { cells: 16, weight: 0.25 },
      { cells: 32, weight: 0.35 },
      { cells: 64, weight: 0.4 },
    ],
    amplitude: 0.5,
  },
};

/** Grass stays broadly matte, with lower-frequency moisture/soil variation. */
export const GRASS_ROUGHNESS_OPTIONS: RoughnessNoiseOptions = {
  base: 0.91,
  variation: 0.07,
  noise: {
    seed: 0x47524f55, // "GROU"
    octaves: [
      { cells: 8, weight: 0.5 },
      { cells: 16, weight: 0.3 },
      { cells: 32, weight: 0.2 },
    ],
    amplitude: 0.5,
  },
};

/** Mean-preserving physical roughness field. It is independent of the ortho,
 * so baked shadows and markings can never become false glossy geometry. */
export function generateRoughnessNoise(size: number, options: RoughnessNoiseOptions): Float32Array {
  const source = generateDetailNoise(size, options.noise);
  const invAmplitude = options.noise.amplitude > 1e-9 ? 1 / options.noise.amplitude : 0;
  const out = new Float32Array(source.length);
  for (let i = 0; i < source.length; i++) {
    const normalized = (source[i] - 0.5) * invAmplitude;
    out[i] = Math.max(0, Math.min(1, options.base + normalized * options.variation));
  }
  return out;
}

/** Browser-side linear grayscale roughness map. */
export function buildRoughnessTexture(size: number, options: RoughnessNoiseOptions): THREE.DataTexture {
  const values = generateRoughnessNoise(size, options);
  const data = new Uint8Array(size * size * 4);
  for (let i = 0; i < values.length; i++) {
    const v = clamp255(values[i]);
    const o = i * 4;
    data[o] = v;
    data[o + 1] = v;
    data[o + 2] = v;
    data[o + 3] = 255;
  }
  const tex = new THREE.DataTexture(data, size, size, THREE.RGBAFormat, THREE.UnsignedByteType);
  tex.colorSpace = THREE.NoColorSpace;
  configureTiling(tex);
  return tex;
}

export function buildProceduralAsphaltRoughness(): THREE.DataTexture {
  return buildRoughnessTexture(DETAIL_TEXTURE_SIZE, ASPHALT_ROUGHNESS_OPTIONS);
}

export function buildProceduralGrassRoughness(): THREE.DataTexture {
  return buildRoughnessTexture(DETAIL_TEXTURE_SIZE, GRASS_ROUGHNESS_OPTIONS);
}
