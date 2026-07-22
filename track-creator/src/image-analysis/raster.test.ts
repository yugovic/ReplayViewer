import { describe, expect, it } from "vitest";
import { rgbToLab, labDistance, sampleBilinear } from "./raster.ts";
import type { Rgb } from "./types.ts";

describe("rgbToLab", () => {
  it("white maps to L≈100, a≈0, b≈0", () => {
    const lab = rgbToLab({ r: 255, g: 255, b: 255 });
    expect(lab.L).toBeCloseTo(100, 0);
    expect(Math.abs(lab.a)).toBeLessThan(3);
    expect(Math.abs(lab.b)).toBeLessThan(3);
  });

  it("black maps to L≈0", () => {
    const lab = rgbToLab({ r: 0, g: 0, b: 0 });
    expect(lab.L).toBeCloseTo(0, 0);
  });

  it("red has positive a", () => {
    const lab = rgbToLab({ r: 200, g: 50, b: 50 });
    expect(lab.a).toBeGreaterThan(20);
  });
});

describe("labDistance", () => {
  it("is zero for identical colours", () => {
    const c: Rgb = { r: 100, g: 150, b: 200 };
    const lab = rgbToLab(c);
    expect(labDistance(lab, lab)).toBeCloseTo(0, 4);
  });

  it("is larger for more different colours", () => {
    const lab1 = rgbToLab({ r: 200, g: 200, b: 200 });
    const lab2 = rgbToLab({ r: 50, g: 50, b: 50 });
    const lab3 = rgbToLab({ r: 180, g: 180, b: 180 });
    expect(labDistance(lab1, lab2)).toBeGreaterThan(labDistance(lab1, lab3));
  });
});

describe("sampleBilinear", () => {
  const buf = {
    data: new Uint8ClampedArray([
      0, 0, 0, 255, 255, 0, 0, 255,
      0, 255, 0, 255, 0, 0, 255, 255,
    ]),
    width: 2,
    height: 2,
  };

  it("returns null outside the buffer", () => {
    expect(sampleBilinear(buf, -1, 0)).toBeNull();
    expect(sampleBilinear(buf, 0, -1)).toBeNull();
    expect(sampleBilinear(buf, 2, 0)).toBeNull();
    expect(sampleBilinear(buf, 0, 2)).toBeNull();
  });

  it("returns exact pixel at integer coordinates", () => {
    const tl = sampleBilinear(buf, 0, 0);
    expect(tl).toEqual({ r: 0, g: 0, b: 0 });
    const tr = sampleBilinear(buf, 1, 0);
    expect(tr).toEqual({ r: 255, g: 0, b: 0 });
  });

  it("interpolates at the centre", () => {
    const centre = sampleBilinear(buf, 0.5, 0.5);
    expect(centre).not.toBeNull();
    // Average of all 4 pixels: (0+255+0+0)/4, (0+0+255+0)/4, (0+0+0+255)/4
    expect(centre!.r).toBeCloseTo(64, 0);
    expect(centre!.g).toBeCloseTo(64, 0);
    expect(centre!.b).toBeCloseTo(64, 0);
  });
});
