import { describe, expect, it } from "vitest";
import { trailColor, rgbToHex, clampUnit, selectTvCamera } from "./trailColor";
import type { TvCamera } from "./trailColor";

describe("clampUnit", () => {
  it("clamps values to [0, 1]", () => {
    expect(clampUnit(-0.5)).toBe(0);
    expect(clampUnit(0)).toBe(0);
    expect(clampUnit(0.5)).toBe(0.5);
    expect(clampUnit(1)).toBe(1);
    expect(clampUnit(1.5)).toBe(1);
  });
});

describe("trailColor", () => {
  it("returns red-ish colour when brake is high", () => {
    const col = trailColor({ speedKmh: 100, brake: 80 });
    expect(col.r).toBeGreaterThan(0.8);
    expect(col.g).toBeLessThan(0.5);
    expect(col.b).toBeLessThan(0.3);
  });

  it("returns cyan-ish colour at full speed with no brake", () => {
    const col = trailColor({ speedKmh: 200, brake: 0, maxSpeedKmh: 200 });
    expect(col.r).toBeLessThan(0.2);
    expect(col.b).toBeGreaterThan(0.7);
  });

  it("returns orange-ish colour at low speed with no brake", () => {
    const col = trailColor({ speedKmh: 0, brake: 0 });
    expect(col.r).toBeGreaterThan(0.7);
    expect(col.b).toBeLessThan(0.2);
  });

  it("brake below 5% does not trigger brake colour", () => {
    const col = trailColor({ speedKmh: 100, brake: 4 });
    // Should be speed-based, not red
    expect(col.g).toBeGreaterThan(0.3);
  });

  it("uses custom maxSpeedKmh", () => {
    const col = trailColor({ speedKmh: 100, brake: 0, maxSpeedKmh: 100 });
    // At max speed → cyan
    expect(col.r).toBeLessThan(0.2);
    expect(col.b).toBeGreaterThan(0.7);
  });
});

describe("rgbToHex", () => {
  it("packs red correctly", () => {
    expect(rgbToHex({ r: 1, g: 0, b: 0 })).toBe(0xff0000);
  });

  it("packs white correctly", () => {
    expect(rgbToHex({ r: 1, g: 1, b: 1 })).toBe(0xffffff);
  });

  it("packs black correctly", () => {
    expect(rgbToHex({ r: 0, g: 0, b: 0 })).toBe(0x000000);
  });

  it("clamps out-of-range values", () => {
    // r=2 clamps to 1.0 → 0xff, g=-1 clamps to 0.0 → 0x00, b=0.5 → Math.round(127.5)=128 → 0x80
    expect(rgbToHex({ r: 2, g: -1, b: 0.5 })).toBe(0xff0080);
  });
});

describe("selectTvCamera", () => {
  const makeCameras = (): TvCamera[] => [
    { x: 100, z: 0, fwdX: -1, fwdZ: 0 }, // faces -X
    { x: -100, z: 0, fwdX: 1, fwdZ: 0 }, // faces +X
    { x: 0, z: 100, fwdX: 0, fwdZ: -1 }, // faces -Z
  ];

  it("returns index 0 for integer type", () => {
    const idx = selectTvCamera(0, 0, makeCameras());
    expect(typeof idx).toBe("number");
    expect(idx).toBeGreaterThanOrEqual(0);
    expect(idx).toBeLessThan(makeCameras().length);
  });

  it("excludes cameras beyond maxDist", () => {
    const cameras: TvCamera[] = [{ x: 0, z: 500, fwdX: 0, fwdZ: -1 }];
    const idx = selectTvCamera(0, 0, cameras, 20, 220);
    // Only one camera, which is out of range – returns 0 (default)
    expect(idx).toBe(0);
  });

  it("prefers the camera facing toward the car", () => {
    // Car at (50, 0, 0)
    // Camera A at (0,0,0) facing +X (toward car) – should win
    // Camera B at (0,0,100) facing +X (not toward car in Z axis)
    const cameras: TvCamera[] = [
      { x: 0, z: 0, fwdX: 1, fwdZ: 0 }, // A: faces car at (50,0,0)
      { x: 0, z: 80, fwdX: 0, fwdZ: -1 }, // B: faces -Z, car is in wrong direction
    ];
    const idx = selectTvCamera(50, 0, cameras, 5, 300);
    expect(idx).toBe(0);
  });
});
