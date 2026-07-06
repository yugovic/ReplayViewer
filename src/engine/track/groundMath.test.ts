import { describe, expect, it } from "vitest";
import {
  bilinearSample,
  corridorBlendHeight,
  corridorMask,
  decodeTerrariumHeight,
  latLngToTerrainUv,
  reblendDrapedHeights,
} from "./groundMath";

describe("decodeTerrariumHeight", () => {
  it("decodes the terrarium zero-height reference pixel", () => {
    expect(decodeTerrariumHeight(128, 0, 0)).toBe(0);
  });

  it("decodes nontrivial RGB combinations", () => {
    expect(decodeTerrariumHeight(128, 10, 0)).toBeCloseTo(10, 6);
    expect(decodeTerrariumHeight(133, 0, 128)).toBeCloseTo(1280.5, 6);
    expect(decodeTerrariumHeight(127, 255, 0)).toBeCloseTo(-1, 6);
  });
});

describe("latLngToTerrainUv", () => {
  const bbox = { minLng: -87, maxLng: -86, minLat: 33, maxLat: 34 };

  it("maps the bbox corners to the UV corners (v=0 at the north edge)", () => {
    expect(latLngToTerrainUv(34, -87, bbox)).toEqual({ u: 0, v: 0 });
    const southEast = latLngToTerrainUv(33, -86, bbox);
    expect(southEast.u).toBeCloseTo(1, 6);
    expect(southEast.v).toBeCloseTo(1, 6);
  });

  it("clamps points outside the bbox to [0, 1]", () => {
    expect(latLngToTerrainUv(40, -100, bbox)).toEqual({ u: 0, v: 0 });
  });
});

describe("bilinearSample", () => {
  it("returns exact corners and the center average on a synthetic 2x2 grid", () => {
    const heights = new Float32Array([10, 20, 30, 40]); // (x0,y0) (x1,y0) (x0,y1) (x1,y1)
    expect(bilinearSample(heights, 2, 2, 0, 0)).toBe(10);
    expect(bilinearSample(heights, 2, 2, 1, 0)).toBe(20);
    expect(bilinearSample(heights, 2, 2, 0, 1)).toBe(30);
    expect(bilinearSample(heights, 2, 2, 1, 1)).toBe(40);
    expect(bilinearSample(heights, 2, 2, 0.5, 0.5)).toBe(25);
  });
});

describe("corridorBlendHeight", () => {
  const base = { roadY: 100, terrainY: 90, halfRoadWidth: 6 };

  it("stays at roadY - 0.6 inside the corridor", () => {
    expect(corridorBlendHeight({ ...base, lateralDistance: 0 })).toBeCloseTo(99.4, 6);
    expect(corridorBlendHeight({ ...base, lateralDistance: 8 })).toBeCloseTo(99.4, 6); // blendStart
  });

  it("returns terrainY exactly at and beyond blendEnd", () => {
    expect(corridorBlendHeight({ ...base, lateralDistance: 26 })).toBe(90); // blendEnd
    expect(corridorBlendHeight({ ...base, lateralDistance: 100 })).toBe(90);
  });

  it("is continuous across both blend boundaries", () => {
    const eps = 1e-6;
    const nearTerrain = { roadY: 100, terrainY: 98, halfRoadWidth: 6 };

    const atStart = corridorBlendHeight({ ...nearTerrain, lateralDistance: 8 });
    const justAfterStart = corridorBlendHeight({ ...nearTerrain, lateralDistance: 8 + eps });
    expect(Math.abs(atStart - justAfterStart)).toBeLessThan(1e-6);

    const justBeforeEnd = corridorBlendHeight({ ...nearTerrain, lateralDistance: 26 - eps });
    const atEnd = corridorBlendHeight({ ...nearTerrain, lateralDistance: 26 });
    expect(Math.abs(atEnd - justBeforeEnd)).toBeLessThan(1e-6);
  });

  it("caps the blend when terrain sits well above the road mid-blend", () => {
    const result = corridorBlendHeight({ roadY: 100, terrainY: 1000, halfRoadWidth: 6, lateralDistance: 17 });
    // Uncapped lerp at t=0.5 would be ~549.7; the cap holds it at roadY - 0.25.
    expect(result).toBeCloseTo(99.75, 6);
  });

  it("sits clearance below the road inside the corridor for clearance 0.08", () => {
    expect(corridorBlendHeight({ ...base, lateralDistance: 0 }, 0.08)).toBeCloseTo(99.92, 6);
    expect(corridorBlendHeight({ ...base, lateralDistance: 8 }, 0.08)).toBeCloseTo(99.92, 6); // blendStart
    expect(corridorBlendHeight({ ...base, lateralDistance: 100 }, 0.08)).toBe(90);
  });

  it("scales the mid-blend cap with clearance, flooring at 0.05", () => {
    const spike = { roadY: 100, terrainY: 1000, halfRoadWidth: 6, lateralDistance: 17 };
    expect(corridorBlendHeight(spike, 0.6)).toBeCloseTo(99.75, 6); // 0.6 / 2.4
    expect(corridorBlendHeight(spike, 0.08)).toBeCloseTo(99.95, 6); // 0.08 / 2.4 < 0.05 floor
  });

  it("matches the explicit 0.6 argument when the clearance is omitted", () => {
    for (const lateralDistance of [0, 8, 12, 17, 26, 40]) {
      expect(corridorBlendHeight({ ...base, lateralDistance })).toBe(
        corridorBlendHeight({ ...base, lateralDistance }, 0.6),
      );
    }
  });
});

describe("corridorMask", () => {
  const halfRoadWidth = 6;

  it("is 1 deep inside the road", () => {
    expect(corridorMask(0, halfRoadWidth)).toBe(1);
    expect(corridorMask(halfRoadWidth - 1, halfRoadWidth)).toBe(1); // inner edge
  });

  it("is 0 far outside the road", () => {
    expect(corridorMask(halfRoadWidth + 2, halfRoadWidth)).toBe(0); // outer edge
    expect(corridorMask(100, halfRoadWidth)).toBe(0);
  });

  it("is monotonically non-increasing across the transition band", () => {
    const samples = [];
    for (let d = halfRoadWidth - 1; d <= halfRoadWidth + 2; d += 0.25) {
      samples.push(corridorMask(d, halfRoadWidth));
    }
    for (let i = 1; i < samples.length; i++) {
      expect(samples[i]).toBeLessThanOrEqual(samples[i - 1] + 1e-9);
    }
    expect(samples[0]).toBe(1);
    expect(samples[samples.length - 1]).toBe(0);
  });

  it("matches 1 - smoothstep(halfRoadWidth - 1, halfRoadWidth + 2, x) at the midpoint", () => {
    const mid = halfRoadWidth + 0.5; // midpoint of the [halfRoadWidth-1, halfRoadWidth+2] band
    const t = (mid - (halfRoadWidth - 1)) / 3;
    const smoothstep = t * t * (3 - 2 * t);
    expect(corridorMask(mid, halfRoadWidth)).toBeCloseTo(1 - smoothstep, 6);
  });
});

describe("reblendDrapedHeights", () => {
  // One vertex inside the corridor, one mid-blend (t=0.5), one past blendEnd.
  const cache = {
    roadY: new Float32Array([100, 100, 100]),
    terrainY: new Float32Array([90, 90, 90]),
    lateralDistance: new Float32Array([0, 17, 30]),
    halfRoadWidth: 6,
  };

  it("recomputes per-vertex heights from cached inputs for clearance 0.6", () => {
    const heights = reblendDrapedHeights(cache, 0.6);
    expect(heights[0]).toBeCloseTo(99.4, 4);
    expect(heights[1]).toBeCloseTo(94.7, 4); // corridorY + (terrainY - corridorY) * 0.5
    expect(heights[2]).toBeCloseTo(90, 4);
  });

  it("raises the corridor to roadY - 0.08 for clearance 0.08", () => {
    const heights = reblendDrapedHeights(cache, 0.08);
    expect(heights[0]).toBeCloseTo(99.92, 4);
    expect(heights[1]).toBeCloseTo(94.96, 4);
    expect(heights[2]).toBeCloseTo(90, 4);
  });

  it("writes into and returns the provided output array", () => {
    const out = new Float32Array(3);
    const heights = reblendDrapedHeights(cache, 0.6, out);
    expect(heights).toBe(out);
    expect(out[0]).toBeCloseTo(99.4, 4);
  });
});
