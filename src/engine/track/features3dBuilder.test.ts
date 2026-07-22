import * as THREE from "three";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildFeatures3dGroup,
  computeBuildingBaseY,
  computeTreePlacement,
  hash01,
  isFeatures3dData,
  loadFeatures3d,
  signedArea,
  treeCrownColor,
  type Features3DData,
} from "./Features3DBuilder";
import { computeFootprintOrientation } from "./LandmarkBuilder";

describe("hash01", () => {
  it("is deterministic for the same index/salt", () => {
    expect(hash01(42, 1)).toBe(hash01(42, 1));
    expect(hash01(0, 0)).toBe(hash01(0, 0));
  });

  it("stays within [0, 1)", () => {
    for (let i = 0; i < 5000; i += 1) {
      const v = hash01(i, (i % 7) + 1);
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThan(1);
    }
  });

  it("decorrelates index and salt streams", () => {
    expect(hash01(10, 1)).not.toBe(hash01(10, 2));
    expect(hash01(10, 1)).not.toBe(hash01(11, 1));
  });
});

describe("computeTreePlacement", () => {
  it("makes the crown top reach the measured height above the trunk base", () => {
    const groundY = 12;
    const height = 20;
    const p = computeTreePlacement({ x: 3, z: -4, height, crownRadius: 5 }, 7, groundY);
    const crownTop = p.baseY + p.crownCenterY + p.crownHalfHeight;
    expect(crownTop - p.baseY).toBeCloseTo(height, 4); // full silhouette height
    expect(crownTop).toBeCloseTo(groundY + height - 0.15, 4); // sits on ground (minus sink)
    expect(p.x).toBe(3);
    expect(p.z).toBe(-4);
  });

  it("sits the trunk base just below the ground (grounded, not floating)", () => {
    const p = computeTreePlacement({ x: 0, z: 0, height: 10, crownRadius: 4 }, 0, 30);
    expect(p.baseY).toBeLessThan(30);
    expect(p.baseY).toBeGreaterThan(30 - 0.5);
  });

  it("keeps the trunk radius modest even for wide crowns", () => {
    const p = computeTreePlacement({ x: 0, z: 0, height: 25, crownRadius: 12 }, 0, 0);
    expect(p.trunkRadius).toBeLessThanOrEqual(0.5);
  });

  it("is fully deterministic (no Math.random)", () => {
    const a = computeTreePlacement({ x: 1, z: 2, height: 15, crownRadius: 5 }, 99, 5);
    const b = computeTreePlacement({ x: 1, z: 2, height: 15, crownRadius: 5 }, 99, 5);
    expect(a).toEqual(b);
  });
});

describe("treeCrownColor", () => {
  it("stays in the green phase (hue 0.24‥0.33)", () => {
    const color = new THREE.Color();
    const hsl = { h: 0, s: 0, l: 0 };
    for (let i = 0; i < 2000; i += 1) {
      treeCrownColor(i, color).getHSL(hsl);
      expect(hsl.h).toBeGreaterThanOrEqual(0.24 - 1e-6);
      expect(hsl.h).toBeLessThanOrEqual(0.33 + 1e-6);
      // green channel dominates a green-phase colour
      expect(color.g).toBeGreaterThanOrEqual(color.b);
    }
  });
});

describe("signedArea", () => {
  it("is positive for CCW and negative for CW polygons", () => {
    const ccw: Array<[number, number]> = [
      [0, 0],
      [10, 0],
      [10, 10],
      [0, 10],
    ];
    expect(signedArea(ccw)).toBeGreaterThan(0);
    expect(signedArea([...ccw].reverse())).toBeLessThan(0);
    expect(Math.abs(signedArea(ccw))).toBeCloseTo(100, 6);
  });
});

describe("computeBuildingBaseY", () => {
  const footprint: Array<[number, number]> = [
    [0, 0],
    [10, 0],
    [10, 10],
    [0, 10],
  ];

  it("uses the lowest footprint ground height minus 0.5 m", () => {
    // ground rises with x: min is at x=0 → 4.0, so base = 4.0 - 0.5
    const groundAt = (x: number) => 4 + x * 0.1;
    expect(computeBuildingBaseY(footprint, groundAt)).toBeCloseTo(3.5, 6);
  });

  it("returns NaN when no vertex has a finite ground height", () => {
    expect(Number.isNaN(computeBuildingBaseY(footprint, () => NaN))).toBe(true);
  });
});

describe("isFeatures3dData", () => {
  it("accepts a well-formed object and rejects malformed ones", () => {
    expect(isFeatures3dData({ trees: [], buildings: [], barriers: [] })).toBe(true);
    expect(isFeatures3dData({ trees: [], buildings: [] })).toBe(false);
    expect(isFeatures3dData(null)).toBe(false);
    expect(isFeatures3dData("nope")).toBe(false);
    expect(isFeatures3dData({ trees: "x", buildings: [], barriers: [] })).toBe(false);
  });
});

describe("loadFeatures3d (graceful degradation)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns null when fetch rejects", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    await expect(loadFeatures3d("/data/tracks/barber/features3d.json")).resolves.toBeNull();
  });

  it("returns null on a non-OK response (missing file)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 404 } as Response));
    await expect(loadFeatures3d("/x.json")).resolves.toBeNull();
  });

  it("returns null on a malformed payload (missing arrays)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ version: 1 }) } as unknown as Response),
    );
    await expect(loadFeatures3d("/x.json")).resolves.toBeNull();
  });

  it("returns the parsed payload on success", async () => {
    const payload: Features3DData = {
      version: 1,
      origin: { lat: 0, lng: 0, alt: 0 },
      source: "test",
      trees: [],
      buildings: [],
      barriers: [],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => payload } as unknown as Response),
    );
    await expect(loadFeatures3d("/x.json")).resolves.toEqual(payload);
  });
});

describe("buildFeatures3dGroup", () => {
  const groundY = () => 0;

  it("emits 2 instanced tree meshes with one instance per tree", () => {
    const data: Features3DData = {
      version: 1,
      origin: { lat: 0, lng: 0, alt: 0 },
      source: "test",
      trees: [
        { x: 0, z: 0, height: 12, crownRadius: 4 },
        { x: 10, z: 5, height: 8, crownRadius: 3 },
        { x: -20, z: 30, height: 20, crownRadius: 6 },
      ],
      buildings: [],
      barriers: [],
    };
    const { group, stats } = buildFeatures3dGroup(data, groundY);
    const instanced = group.children.filter(
      (c): c is THREE.InstancedMesh => c instanceof THREE.InstancedMesh,
    );
    expect(instanced).toHaveLength(2);
    for (const mesh of instanced) expect(mesh.count).toBe(3);
    expect(stats.trees).toBe(3);
    expect(stats.drawCalls).toBe(2);
  });

  it("merges all buildings into a single mesh + one outline (two draw calls)", () => {
    const data: Features3DData = {
      version: 1,
      origin: { lat: 0, lng: 0, alt: 0 },
      source: "test",
      trees: [],
      buildings: [
        { footprint: [[0, 0], [10, 0], [10, 10], [0, 10]], height: 8 },
        { footprint: [[40, 40], [50, 40], [50, 50], [40, 50]], height: 12 },
      ],
      barriers: [],
    };
    const { group, stats } = buildFeatures3dGroup(data, groundY);
    const buildingGroup = group.children.find((c) => c.name === "features3d-buildings-group");
    expect(buildingGroup).toBeInstanceOf(THREE.Group);
    const meshes = (buildingGroup as THREE.Group).children.filter(
      (c) => c instanceof THREE.Mesh && !(c instanceof THREE.InstancedMesh),
    );
    const outlines = (buildingGroup as THREE.Group).children.filter(
      (c) => c instanceof THREE.LineSegments,
    );
    expect(meshes).toHaveLength(1);
    expect(outlines).toHaveLength(1);
    expect((meshes[0] as THREE.Mesh).castShadow).toBe(true);
    expect(stats.buildings).toBe(2);
    expect(stats.drawCalls).toBe(2);
  });

  it("splits barriers into opaque + translucent meshes (≤2 draw calls)", () => {
    const data: Features3DData = {
      version: 1,
      origin: { lat: 0, lng: 0, alt: 0 },
      source: "test",
      trees: [],
      buildings: [],
      barriers: [
        { type: "guard_rail", points: [[0, 0], [10, 0], [20, 5]], height: 0.75 },
        { type: "fence", points: [[0, 30], [30, 40]], height: 2 },
        { type: "wall", points: [[0, -30], [30, -30]], height: 1.5 },
      ],
    };
    const { group, stats } = buildFeatures3dGroup(data, groundY);
    expect(group.children.length).toBe(2); // one opaque (rail+wall), one fence
    expect(stats.drawCalls).toBe(2);
    expect(stats.barriers).toBe(3);
  });

  it("is a visual no-op for an all-empty dataset", () => {
    const data: Features3DData = {
      version: 1,
      origin: { lat: 0, lng: 0, alt: 0 },
      source: "test",
      trees: [],
      buildings: [],
      barriers: [],
    };
    const { group, stats } = buildFeatures3dGroup(data, groundY);
    expect(group.children).toHaveLength(0);
    expect(stats.drawCalls).toBe(0);
    expect(stats.vertices).toBe(0);
  });

  const boxBuilding: Array<[number, number]> = [[0, 0], [20, 0], [20, 8], [0, 8]];

  function buildingMeshVertexCount(data: Features3DData): number {
    const { group } = buildFeatures3dGroup(data, groundY);
    const bg = group.children.find((c) => c.name === "features3d-buildings-group") as
      | THREE.Group
      | undefined;
    const mesh = bg?.children.find(
      (c) => c instanceof THREE.Mesh && !(c instanceof THREE.InstancedMesh),
    ) as THREE.Mesh | undefined;
    const pos = mesh?.geometry.getAttribute("position") as THREE.BufferAttribute | undefined;
    return pos ? pos.count : 0;
  }

  function meshBounds(data: Features3DData): { maxY: number; minY: number } {
    const { group } = buildFeatures3dGroup(data, groundY);
    const bg = group.children.find((c) => c.name === "features3d-buildings-group") as THREE.Group;
    const mesh = bg.children.find(
      (c) => c instanceof THREE.Mesh && !(c instanceof THREE.InstancedMesh),
    ) as THREE.Mesh;
    const pos = mesh.geometry.getAttribute("position") as THREE.BufferAttribute;
    let maxY = -Infinity;
    let minY = Infinity;
    for (let i = 0; i < pos.count; i += 1) {
      const y = pos.getY(i);
      if (y > maxY) maxY = y;
      if (y < minY) minY = y;
    }
    return { maxY, minY };
  }

  it("builds a stepped grandstand (more verts than a box, top at height)", () => {
    const height = 15;
    const grandstand: Features3DData = {
      version: 1,
      origin: { lat: 0, lng: 0, alt: 0 },
      source: "test",
      trees: [],
      buildings: [{ footprint: boxBuilding, height, tags: { building: "grandstand" } }],
      barriers: [],
    };
    const plainBox: Features3DData = { ...grandstand, buildings: [{ footprint: boxBuilding, height }] };

    const gsVerts = buildingMeshVertexCount(grandstand);
    const boxVerts = buildingMeshVertexCount(plainBox);
    // Tiers + wall + roof + columns → far more geometry than one extruded box.
    expect(gsVerts).toBeGreaterThan(boxVerts);

    // Roof slab reaches the measured height (baseY = -0.5 here, so top ≈ 14.5).
    const { maxY } = meshBounds(grandstand);
    expect(maxY).toBeCloseTo(height - 0.5, 1);
  });

  it("orients the grandstand along the footprint's long axis", () => {
    // Long axis along X (length 40) vs short along Z (width 6). The front hint
    // sits on the -Z side, so tiers must rise toward +Z, not along X.
    const footprint: Array<[number, number]> = [[0, 0], [40, 0], [40, 6], [0, 6]];
    const o = computeFootprintOrientation(footprint, { x: 20, z: -50 });
    expect(Math.abs(o.ux)).toBeGreaterThan(Math.abs(o.uz)); // long axis ≈ X
    expect(o.halfLen).toBeCloseTo(20, 3);
    expect(o.halfWid).toBeCloseTo(3, 3);
    // +v (rising / back) points away from the front hint (toward +Z).
    expect(o.vz).toBeGreaterThan(0);
  });

  it("builds a wall-less canopy for a roof tag (little vertical side area)", () => {
    const height = 6;
    const roof: Features3DData = {
      version: 1,
      origin: { lat: 0, lng: 0, alt: 0 },
      source: "test",
      trees: [],
      buildings: [{ footprint: boxBuilding, height, tags: { building: "roof" } }],
      barriers: [],
    };
    const grandstand: Features3DData = {
      ...roof,
      buildings: [{ footprint: boxBuilding, height, tags: { building: "grandstand" } }],
    };
    // Canopy = thin slab + slim columns: fewer verts than the tiered grandstand.
    expect(buildingMeshVertexCount(roof)).toBeLessThan(buildingMeshVertexCount(grandstand));
    // Roof slab sits at the top; the columns are the only full-height geometry.
    const { maxY } = meshBounds(roof);
    expect(maxY).toBeCloseTo(height - 0.5, 1);
  });

  it("keeps untagged buildings as plain extruded boxes (24 verts)", () => {
    const data: Features3DData = {
      version: 1,
      origin: { lat: 0, lng: 0, alt: 0 },
      source: "test",
      trees: [],
      buildings: [{ footprint: boxBuilding, height: 8 }],
      barriers: [],
    };
    // One rectangular prism = 12 tris × 3 = 36 verts (ExtrudeGeometry caps + walls).
    // Assert it is unchanged and modest vs. the grandstand.
    const boxVerts = buildingMeshVertexCount(data);
    expect(boxVerts).toBeGreaterThan(0);
    expect(boxVerts).toBeLessThan(200);
  });

  it("skips buildings with degenerate footprints", () => {
    const data: Features3DData = {
      version: 1,
      origin: { lat: 0, lng: 0, alt: 0 },
      source: "test",
      trees: [],
      buildings: [{ footprint: [[0, 0], [1, 1]], height: 5 }],
      barriers: [],
    };
    const { group, stats } = buildFeatures3dGroup(data, groundY);
    expect(group.children).toHaveLength(0);
    expect(stats.buildings).toBe(0);
  });
});
