import { describe, expect, it } from "vitest";
import {
  localToLatLng,
  latLngToLocal,
  mercX,
  mercY,
  latLngToPixelMercator,
  latLngToPixelEquirect,
  rightNormal,
  offsetPoint,
  centerlineFingerprint,
} from "./coordinates.ts";

const ORIGIN = { lat: 35.37, lng: 138.93 }; // near Fuji Speedway

describe("localToLatLng / latLngToLocal", () => {
  it("round-trips through local and geographic coordinates", () => {
    const x = 500;
    const z = -300;
    const ll = localToLatLng(ORIGIN, x, z);
    const back = latLngToLocal(ORIGIN, ll.lat, ll.lng);
    expect(back.x).toBeCloseTo(x, 1);
    expect(back.z).toBeCloseTo(z, 1);
  });

  it("origin maps to (0, 0)", () => {
    const ll = localToLatLng(ORIGIN, 0, 0);
    expect(ll.lat).toBeCloseTo(ORIGIN.lat, 6);
    expect(ll.lng).toBeCloseTo(ORIGIN.lng, 6);
  });

  it("positive z is south (lower latitude)", () => {
    const ll = localToLatLng(ORIGIN, 0, 100);
    expect(ll.lat).toBeLessThan(ORIGIN.lat);
  });

  it("positive x is east (higher longitude)", () => {
    const ll = localToLatLng(ORIGIN, 100, 0);
    expect(ll.lng).toBeGreaterThan(ORIGIN.lng);
  });
});

describe("mercX / mercY", () => {
  it("mercX(−180) = 0, mercX(180) = 1", () => {
    expect(mercX(-180)).toBeCloseTo(0, 6);
    expect(mercX(180)).toBeCloseTo(1, 6);
    expect(mercX(0)).toBeCloseTo(0.5, 6);
  });

  it("mercY is symmetric about the equator", () => {
    const yNorth = mercY(45);
    const yEquator = mercY(0);
    const ySouth = mercY(-45);
    expect(yEquator).toBeCloseTo(0.5, 6);
    expect(yNorth).toBeLessThan(0.5);
    expect(ySouth).toBeGreaterThan(0.5);
  });
});

describe("latLngToPixelMercator", () => {
  const bbox = {
    minLng: 138.91,
    maxLng: 138.94,
    minLat: 35.36,
    maxLat: 35.38,
  };
  const w = 1000;
  const h = 1000;

  it("returns null for points outside the bbox", () => {
    expect(latLngToPixelMercator(34.0, 138.0, bbox, w, h)).toBeNull();
    expect(latLngToPixelMercator(36.0, 139.0, bbox, w, h)).toBeNull();
  });

  it("maps the NW corner to near (0, 0)", () => {
    const p = latLngToPixelMercator(bbox.maxLat, bbox.minLng, bbox, w, h);
    expect(p).not.toBeNull();
    expect(p!.px).toBeCloseTo(0, 0);
    expect(p!.py).toBeCloseTo(0, 0);
  });

  it("maps the SE corner to near (w-1, h-1)", () => {
    const p = latLngToPixelMercator(bbox.minLat, bbox.maxLng, bbox, w, h);
    expect(p).not.toBeNull();
    expect(p!.px).toBeCloseTo(w - 1, 0);
    expect(p!.py).toBeCloseTo(h - 1, 0);
  });
});

describe("latLngToPixelEquirect", () => {
  const bbox = {
    minLng: 138.91,
    maxLng: 138.94,
    minLat: 35.36,
    maxLat: 35.38,
  };
  const w = 1000;
  const h = 1000;

  it("returns null for points outside the bbox", () => {
    expect(latLngToPixelEquirect(34.0, 138.0, bbox, w, h)).toBeNull();
  });

  it("maps the NW corner to (0, 0)", () => {
    const p = latLngToPixelEquirect(bbox.maxLat, bbox.minLng, bbox, w, h);
    expect(p).not.toBeNull();
    expect(p!.px).toBeCloseTo(0, 0);
    expect(p!.py).toBeCloseTo(0, 0);
  });
});

describe("rightNormal", () => {
  it("returns (-tz, tx) for a given tangent", () => {
    const r1 = rightNormal(1, 0);
    expect(r1.nx).toBeCloseTo(0, 6);
    expect(r1.nz).toBeCloseTo(1, 6);
    const r2 = rightNormal(0, 1);
    expect(r2.nx).toBeCloseTo(-1, 6);
    expect(r2.nz).toBeCloseTo(0, 6);
  });

  it("is perpendicular to the tangent", () => {
    for (const [tx, tz] of [[1, 0], [0, 1], [0.6, 0.8], [-0.3, 0.95]]) {
      const { nx, nz } = rightNormal(tx, tz);
      expect(tx * nx + tz * nz).toBeCloseTo(0, 6);
    }
  });
});

describe("offsetPoint", () => {
  it("positive offset goes to the right side", () => {
    // Tangent pointing east (tx=1, tz=0), right normal = (0, 1) → +z (south)
    const p = offsetPoint(0, 0, 1, 0, 5);
    expect(p.x).toBeCloseTo(0, 6);
    expect(p.z).toBeCloseTo(5, 6);
  });

  it("negative offset goes to the left side", () => {
    const p = offsetPoint(0, 0, 1, 0, -5);
    expect(p.x).toBeCloseTo(0, 6);
    expect(p.z).toBeCloseTo(-5, 6);
  });
});

describe("centerlineFingerprint", () => {
  it("returns a stable hex string for the same points", () => {
    const pts = [{ x: 1, z: 2 }, { x: 3, z: 4 }];
    expect(centerlineFingerprint(pts)).toBe(centerlineFingerprint(pts));
  });

  it("changes when a point moves", () => {
    const pts1 = [{ x: 1, z: 2 }];
    const pts2 = [{ x: 1.001, z: 2 }];
    expect(centerlineFingerprint(pts1)).not.toBe(centerlineFingerprint(pts2));
  });
});
