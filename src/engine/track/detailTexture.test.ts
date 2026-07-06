import { describe, expect, it } from "vitest";
import { ASPHALT_DETAIL_OPTIONS, GRASS_DETAIL_OPTIONS, generateDetailNoise } from "./detailTexture";

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
