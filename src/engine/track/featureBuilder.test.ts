import type * as THREE from "three";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  FEATURE_COLORS,
  FEATURE_KINDS,
  buildFeaturesGroup,
  loadFeatures,
  refineTriangleSoup,
  subdivideOutline,
  type FeaturesData,
} from "./FeatureBuilder";

describe("subdivideOutline", () => {
  it("grows the point count for an 80 m edge with an 8 m step", () => {
    const square: Array<[number, number]> = [
      [0, 0],
      [80, 0],
      [80, 80],
      [0, 80],
    ];
    const out = subdivideOutline(square, 8);
    expect(out.length).toBeGreaterThan(square.length);
    expect(out.length).toBe(40); // 4 edges x 10 pieces of 8 m

    // No edge (closing edge included) may exceed the step.
    for (let i = 0; i < out.length; i += 1) {
      const [ax, az] = out[i];
      const [bx, bz] = out[(i + 1) % out.length];
      expect(Math.hypot(bx - ax, bz - az)).toBeLessThanOrEqual(8 + 1e-9);
    }
  });

  it("leaves outlines with short edges untouched", () => {
    const triangle: Array<[number, number]> = [
      [0, 0],
      [5, 0],
      [0, 5],
    ];
    expect(subdivideOutline(triangle, 8)).toEqual(triangle);
  });
});

describe("refineTriangleSoup", () => {
  const soupArea = (soup: number[]): number => {
    let area = 0;
    for (let i = 0; i + 5 < soup.length; i += 6) {
      area += Math.abs(
        (soup[i + 2] - soup[i]) * (soup[i + 5] - soup[i + 1]) -
          (soup[i + 4] - soup[i]) * (soup[i + 3] - soup[i + 1]),
      ) / 2;
    }
    return area;
  };

  it("splits an 80 m triangle until every edge is at most 8 m, preserving area", () => {
    const soup = [0, 0, 80, 0, 0, 80];
    const refined = refineTriangleSoup(soup, 8);

    expect(refined.length).toBeGreaterThan(soup.length);
    expect(refined.length % 6).toBe(0);
    expect(soupArea(refined)).toBeCloseTo(soupArea(soup), 6);

    for (let i = 0; i + 5 < refined.length; i += 6) {
      const [ax, az, bx, bz, cx, cz] = refined.slice(i, i + 6);
      expect(Math.hypot(bx - ax, bz - az)).toBeLessThanOrEqual(8 + 1e-9);
      expect(Math.hypot(cx - bx, cz - bz)).toBeLessThanOrEqual(8 + 1e-9);
      expect(Math.hypot(ax - cx, az - cz)).toBeLessThanOrEqual(8 + 1e-9);
    }
  });

  it("returns small triangles unchanged", () => {
    const soup = [0, 0, 4, 0, 0, 4];
    expect(refineTriangleSoup(soup, 8)).toEqual(soup);
  });

  it("supports a position-dependent step (finer near x=0)", () => {
    const soup = [0, 0, 200, 0, 0, 200];
    const stepAt = (x: number) => (x < 50 ? 8 : 40);
    const refined = refineTriangleSoup(soup, stepAt);
    expect(soupArea(refined)).toBeCloseTo(soupArea(soup), 5);

    // Every remaining edge respects the local step at its midpoint.
    for (let i = 0; i + 5 < refined.length; i += 6) {
      const pts = [
        [refined[i], refined[i + 1]],
        [refined[i + 2], refined[i + 3]],
        [refined[i + 4], refined[i + 5]],
      ];
      for (let e = 0; e < 3; e += 1) {
        const [px, pz] = pts[e];
        const [qx, qz] = pts[(e + 1) % 3];
        const local = stepAt((px + qx) / 2);
        expect(Math.hypot(qx - px, qz - pz)).toBeLessThanOrEqual(local + 1e-9);
      }
    }

    // The fine zone produced strictly more triangles than a uniform-40 pass.
    const coarse = refineTriangleSoup(soup, 40);
    expect(refined.length).toBeGreaterThan(coarse.length);
  });
});

describe("FEATURE_COLORS", () => {
  it("defines a colour for every feature kind in the type", () => {
    for (const kind of FEATURE_KINDS) {
      expect(FEATURE_COLORS[kind]).toBeTypeOf("number");
    }
    expect(Object.keys(FEATURE_COLORS).sort()).toEqual([...FEATURE_KINDS].sort());
  });
});

describe("loadFeatures", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns null when fetch rejects", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    await expect(loadFeatures("/data/tracks/barber/features.json")).resolves.toBeNull();
  });

  it("returns null on a non-OK response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 404 } as Response));
    await expect(loadFeatures("/data/tracks/barber/features.json")).resolves.toBeNull();
  });

  it("returns the parsed payload on success", async () => {
    const payload: FeaturesData = { version: 1, origin: { lat: 0, lng: 0, alt: 0 }, features: [] };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => payload } as unknown as Response),
    );
    await expect(loadFeatures("/data/tracks/barber/features.json")).resolves.toEqual(payload);
  });
});

describe("buildFeaturesGroup", () => {
  const data: FeaturesData = {
    version: 1,
    origin: { lat: 0, lng: 0, alt: 0 },
    features: [
      {
        id: "way/1",
        kind: "grass",
        outline: [
          [0, 0],
          [20, 0],
          [20, 20],
          [0, 20],
        ],
      },
    ],
  };

  it("drapes every vertex 0.06 m above the supplied ground height", () => {
    const group = buildFeaturesGroup(data, () => 5);
    expect(group.name).toBe("osm-features");
    expect(group.children).toHaveLength(1);

    const mesh = group.children[0] as THREE.Mesh;
    const posAttr = mesh.geometry.getAttribute("position");
    expect(posAttr.count).toBeGreaterThanOrEqual(12); // subdivided 20 m edges
    for (let i = 0; i < posAttr.count; i += 1) {
      expect(posAttr.getY(i)).toBeCloseTo(5.06, 5);
    }
  });

  it("skips degenerate outlines with fewer than 3 points", () => {
    const degenerate: FeaturesData = {
      ...data,
      features: [{ id: "way/2", kind: "grass", outline: [[0, 0], [1, 1]] }],
    };
    expect(buildFeaturesGroup(degenerate, () => 0).children).toHaveLength(0);
  });
});
