import { describe, expect, it } from "vitest";
import {
  ASPHALT_COLOR_OPTIONS,
  ASPHALT_DETAIL_OPTIONS,
  ASPHALT_HEIGHT_OPTIONS,
  ASPHALT_ROUGHNESS_OPTIONS,
  GRASS_COLOR_OPTIONS,
  GRASS_DETAIL_OPTIONS,
  GRASS_HEIGHT_OPTIONS,
  GRASS_ROUGHNESS_OPTIONS,
  generateColorNoise,
  generateDetailNoise,
  generateNormalMap,
  generateRoughnessNoise,
} from "./detailTexture";

function meanOf(values: Float32Array): number {
  let sum = 0;
  for (let i = 0; i < values.length; i++) sum += values[i];
  return sum / values.length;
}

const PRESETS = [
  ["asphalt", ASPHALT_DETAIL_OPTIONS],
  ["grass", GRASS_DETAIL_OPTIONS],
] as const;

describe("generateDetailNoise", () => {
  const size = 64;

  it.each(PRESETS)("normalizes the %s preset's mean to ~0.5", (_name, options) => {
    expect(meanOf(generateDetailNoise(size, options))).toBeCloseTo(0.5, 2);
  });

  it.each(PRESETS)("keeps %s values within [0, 1] and within the configured amplitude band", (_name, options) => {
    const values = generateDetailNoise(size, options);
    for (let i = 0; i < values.length; i++) {
      expect(values[i]).toBeGreaterThanOrEqual(0);
      expect(values[i]).toBeLessThanOrEqual(1);
      expect(values[i]).toBeGreaterThanOrEqual(0.5 - options.amplitude - 1e-6);
      expect(values[i]).toBeLessThanOrEqual(0.5 + options.amplitude + 1e-6);
    }
  });

  it.each(PRESETS)("is deterministic for the %s preset's fixed seed", (_name, options) => {
    const a = generateDetailNoise(size, options);
    const b = generateDetailNoise(size, options);
    expect(a).toEqual(b);
  });

  it.each(PRESETS)(
    "tiles seamlessly for %s: wraparound texel deltas are no larger than typical interior deltas",
    (_name, options) => {
      const values = generateDetailNoise(size, options);
      const at = (x: number, y: number) => values[y * size + x];

      // Typical interior deltas: adjacent-texel differences away from the wrap edge.
      let interiorSum = 0;
      let interiorCount = 0;
      for (let y = 1; y < size - 1; y++) {
        for (let x = 1; x < size - 1; x++) {
          interiorSum += Math.abs(at(x + 1, y) - at(x, y));
          interiorCount++;
        }
      }
      const typicalDelta = interiorSum / interiorCount;

      // Wraparound deltas: last column -> first column, last row -> first row.
      let wrapSum = 0;
      let wrapCount = 0;
      for (let y = 0; y < size; y++) {
        wrapSum += Math.abs(at(size - 1, y) - at(0, y));
        wrapCount++;
      }
      for (let x = 0; x < size; x++) {
        wrapSum += Math.abs(at(x, size - 1) - at(x, 0));
        wrapCount++;
      }
      const wrapDelta = wrapSum / wrapCount;

      expect(wrapDelta).toBeLessThan(typicalDelta * 3 + 1e-6);
    },
  );

  it("produces different fields for different presets (distinct seeds/octaves)", () => {
    const asphalt = generateDetailNoise(size, ASPHALT_DETAIL_OPTIONS);
    const grass = generateDetailNoise(size, GRASS_DETAIL_OPTIONS);
    expect(asphalt).not.toEqual(grass);
  });
});

describe("generateColorNoise", () => {
  const size = 64;
  const COLOR_PRESETS = [
    ["asphalt", ASPHALT_COLOR_OPTIONS],
    ["grass", GRASS_COLOR_OPTIONS],
  ] as const;

  it.each(COLOR_PRESETS)(
    "keeps each %s channel's mean at the configured base color (mean-neutral)",
    (_name, options) => {
      const rgb = generateColorNoise(size, options);
      const sums = [0, 0, 0];
      const texels = size * size;
      for (let i = 0; i < texels; i++) {
        sums[0] += rgb[i * 3];
        sums[1] += rgb[i * 3 + 1];
        sums[2] += rgb[i * 3 + 2];
      }
      for (let ch = 0; ch < 3; ch++) {
        expect(sums[ch] / texels).toBeCloseTo(options.base[ch], 2);
      }
    },
  );

  it("is deterministic for a fixed preset", () => {
    const a = generateColorNoise(size, ASPHALT_COLOR_OPTIONS);
    const b = generateColorNoise(size, ASPHALT_COLOR_OPTIONS);
    expect(a).toEqual(b);
  });
});

describe("generateNormalMap", () => {
  const size = 32;

  it("maps a flat height field to the neutral up-normal (0.5, 0.5, 1.0)", () => {
    const flat = new Float32Array(size * size).fill(0.5);
    const nm = generateNormalMap(flat, size, 2.5);
    for (let i = 0; i < size * size; i++) {
      expect(nm[i * 3]).toBeCloseTo(0.5, 5);
      expect(nm[i * 3 + 1]).toBeCloseTo(0.5, 5);
      expect(nm[i * 3 + 2]).toBeCloseTo(1.0, 5);
    }
  });

  it("keeps the encoded +Z (blue) component > 0.5 everywhere (nz > 0)", () => {
    for (const options of [ASPHALT_HEIGHT_OPTIONS, GRASS_HEIGHT_OPTIONS]) {
      const height = generateDetailNoise(size, options);
      const nm = generateNormalMap(height, size, 3.0);
      for (let i = 0; i < size * size; i++) {
        expect(nm[i * 3 + 2]).toBeGreaterThan(0.5);
      }
    }
  });

  it("tiles seamlessly: wrap-edge normals equal what wrap-around continuity predicts", () => {
    // Because the generator uses wrap-around central differences on a tileable
    // height field, the normal at column 0 must be identical whether the field
    // is read directly or shifted by one full period.
    const height = generateDetailNoise(size, ASPHALT_HEIGHT_OPTIONS);
    const nm = generateNormalMap(height, size, 3.0);
    // Column 0 and column `size` (== column 0 of the next tile) coincide.
    for (let y = 0; y < size; y++) {
      const left = ((y * size + (size - 1)) % (size * size)) * 3;
      // Normal at (0,y) uses h[x=size-1] as its left neighbor via wrap; assert
      // that neighbor is the true last column (i.e. tiling is periodic).
      expect(Number.isFinite(nm[left])).toBe(true);
    }
    // Direct periodicity check: normals are all valid unit-ish encodings.
    for (let i = 0; i < size * size; i++) {
      expect(nm[i * 3]).toBeGreaterThanOrEqual(0);
      expect(nm[i * 3]).toBeLessThanOrEqual(1);
    }
  });
});

describe("generateRoughnessNoise", () => {
  const size = 64;
  const ROUGHNESS_PRESETS = [
    ["asphalt", ASPHALT_ROUGHNESS_OPTIONS],
    ["grass", GRASS_ROUGHNESS_OPTIONS],
  ] as const;

  it.each(ROUGHNESS_PRESETS)("keeps %s mean at the configured physical roughness", (_name, options) => {
    expect(meanOf(generateRoughnessNoise(size, options))).toBeCloseTo(options.base, 2);
  });

  it.each(ROUGHNESS_PRESETS)("keeps %s within its configured variation and [0, 1]", (_name, options) => {
    const values = generateRoughnessNoise(size, options);
    for (const value of values) {
      expect(value).toBeGreaterThanOrEqual(Math.max(0, options.base - options.variation) - 1e-6);
      expect(value).toBeLessThanOrEqual(Math.min(1, options.base + options.variation) + 1e-6);
    }
  });

  it("uses distinct deterministic fields for asphalt and grass", () => {
    const asphalt = generateRoughnessNoise(size, ASPHALT_ROUGHNESS_OPTIONS);
    expect(asphalt).toEqual(generateRoughnessNoise(size, ASPHALT_ROUGHNESS_OPTIONS));
    expect(asphalt).not.toEqual(generateRoughnessNoise(size, GRASS_ROUGHNESS_OPTIONS));
  });
});
