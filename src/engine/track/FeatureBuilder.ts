/**
 * FeatureBuilder – OSM surface features (gravel traps, grass runoff, pit
 * lane, woods, water …) rendered as flat draped polygons that sit just above
 * the satellite ground and below the procedural road.
 *
 * Data comes from public/data/tracks/barber/features.json, produced by
 * pipeline/fetch_osm_features.py (outlines already projected to local XZ
 * metres with the viewer's equirectangular projection).
 */

import * as THREE from "three";

export const FEATURE_KINDS = ["gravel", "sand", "grass", "paved", "pit_lane", "wood", "water"] as const;
export type FeatureKind = (typeof FEATURE_KINDS)[number];

export interface FeatureRecord {
  id: string;
  kind: FeatureKind;
  outline: Array<[number, number]>;
}

export interface FeaturesData {
  version: number;
  origin: { lat: number; lng: number; alt: number };
  features: FeatureRecord[];
}

export const FEATURE_COLORS: Record<FeatureKind, number> = {
  gravel: 0xcdc0a5,
  sand: 0xded7bd,
  grass: 0x4d6b3c,
  paved: 0x3a3d44,
  pit_lane: 0x33363d,
  wood: 0x37543a,
  water: 0x3a5a74,
};

/** Features float this far above the draped ground (still below the road). */
const FEATURE_LIFT = 0.06;
const SUBDIVIDE_STEP = 8; // metres

/** Ground height (drape) at a local XZ position, supplied by TrackBuilder. */
export type GroundHeightFn = (x: number, z: number) => number;

/** Fetch features.json; resolves null on any failure (missing file is fine). */
export async function loadFeatures(url: string): Promise<FeaturesData | null> {
  try {
    const response = await fetch(url);
    if (!response.ok) {
      return null;
    }
    const data = (await response.json()) as FeaturesData;
    return Array.isArray(data.features) ? data : null;
  } catch {
    return null;
  }
}

/**
 * Split every outline edge (closing edge included) into equal pieces no
 * longer than the local step in metres (evaluated at the edge midpoint), so
 * large polygons follow the terrain drape.
 */
export function subdivideOutline(
  outline: Array<[number, number]>,
  step: number | ((x: number, z: number) => number) = SUBDIVIDE_STEP,
): Array<[number, number]> {
  const stepAt = typeof step === "number" ? () => step : step;
  const out: Array<[number, number]> = [];
  for (let i = 0; i < outline.length; i += 1) {
    const [ax, az] = outline[i];
    const [bx, bz] = outline[(i + 1) % outline.length];
    const local = stepAt((ax + bx) / 2, (az + bz) / 2);
    const pieces = Math.max(1, Math.ceil(Math.hypot(bx - ax, bz - az) / local));
    for (let k = 0; k < pieces; k += 1) {
      const t = k / pieces;
      out.push([ax + (bx - ax) * t, az + (bz - az) * t]);
    }
  }
  return out;
}

/** Drop consecutive outline points closer than 1 cm (earcut hygiene). */
function dedupeOutline(outline: Array<[number, number]>): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  for (const point of outline) {
    const prev = out[out.length - 1];
    if (!prev || Math.hypot(point[0] - prev[0], point[1] - prev[1]) > 0.01) {
      out.push(point);
    }
  }
  const first = out[0];
  const last = out[out.length - 1];
  if (out.length > 1 && Math.hypot(first[0] - last[0], first[1] - last[1]) <= 0.01) {
    out.pop();
  }
  return out;
}

/**
 * Recursively split triangle edges at their midpoints until every edge is
 * shorter than the local step, on a flat XZ triangle soup
 * [ax, az, bx, bz, cx, cz, …].
 *
 * Outline subdivision alone is not enough on hilly terrain: earcut places no
 * interior vertices, so a large polygon's interior triangles span between
 * outline points and hang in the air wherever the ground dips between them
 * (the sky-smear bug: a paved paddock lid floating 9 m over the S/F
 * straight). `step` may be a function of position so callers can sample
 * finely near the road corridor and coarsely elsewhere. The split decision
 * is made per edge at the edge midpoint, so an edge shared by two triangles
 * splits identically in both — no cracks.
 */
export function refineTriangleSoup(
  triangles: number[],
  step: number | ((x: number, z: number) => number) = SUBDIVIDE_STEP,
  maxDepth = 64,
): number[] {
  const stepAt = typeof step === "number" ? () => step : step;
  const longEdge = (px: number, pz: number, qx: number, qz: number): boolean => {
    const local = stepAt((px + qx) / 2, (pz + qz) / 2);
    return (qx - px) ** 2 + (qz - pz) ** 2 > local * local;
  };

  const out: number[] = [];
  type Tri = [number, number, number, number, number, number, number]; // + depth
  const stack: Tri[] = [];
  for (let i = 0; i + 5 < triangles.length; i += 6) {
    stack.push([
      triangles[i], triangles[i + 1], triangles[i + 2],
      triangles[i + 3], triangles[i + 4], triangles[i + 5], 0,
    ]);
  }

  while (stack.length > 0) {
    let [ax, az, bx, bz, cx, cz, depth] = stack.pop()!;
    let longAb = longEdge(ax, az, bx, bz);
    let longBc = longEdge(bx, bz, cx, cz);
    let longCa = longEdge(cx, cz, ax, az);

    if (depth >= maxDepth || (!longAb && !longBc && !longCa)) {
      out.push(ax, az, bx, bz, cx, cz);
      continue;
    }

    // Rotate a→b→c→a until the long-edge pattern is canonical: 1 long edge →
    // AB long; 2 long edges → CA the short one. Rotation preserves winding.
    const rotate = () => {
      [ax, az, bx, bz, cx, cz] = [bx, bz, cx, cz, ax, az];
      [longAb, longBc, longCa] = [longBc, longCa, longAb];
    };
    const longCount = Number(longAb) + Number(longBc) + Number(longCa);
    if (longCount === 1) {
      while (!longAb) rotate();
    } else if (longCount === 2) {
      while (longCa) rotate();
    }

    const d = depth + 1;
    const m1x = (ax + bx) / 2;
    const m1z = (az + bz) / 2;
    if (longCount === 1) {
      stack.push([ax, az, m1x, m1z, cx, cz, d], [m1x, m1z, bx, bz, cx, cz, d]);
      continue;
    }
    const m2x = (bx + cx) / 2;
    const m2z = (bz + cz) / 2;
    if (longCount === 2) {
      stack.push(
        [ax, az, m1x, m1z, cx, cz, d],
        [m1x, m1z, m2x, m2z, cx, cz, d],
        [m1x, m1z, bx, bz, m2x, m2z, d],
      );
      continue;
    }
    const m3x = (cx + ax) / 2;
    const m3z = (cz + az) / 2;
    stack.push(
      [ax, az, m1x, m1z, m3x, m3z, d],
      [m1x, m1z, bx, bz, m2x, m2z, d],
      [m3x, m3z, m2x, m2z, cx, cz, d],
      [m1x, m1z, m2x, m2z, m3x, m3z, d],
    );
  }
  return out;
}

/**
 * Build the "osm-features" group: one draped triangulated mesh per polygon.
 * `refineStepAt` controls interior sampling density (metres) by position;
 * the default samples everything at 8 m.
 */
export function buildFeaturesGroup(
  data: FeaturesData,
  groundHeightAt: GroundHeightFn,
  refineStepAt: number | ((x: number, z: number) => number) = SUBDIVIDE_STEP,
): THREE.Group {
  const group = new THREE.Group();
  group.name = "osm-features";
  const materials = new Map<FeatureKind, THREE.MeshStandardMaterial>();

  const materialFor = (kind: FeatureKind): THREE.MeshStandardMaterial => {
    let material = materials.get(kind);
    if (!material) {
      material = new THREE.MeshStandardMaterial({
        color: FEATURE_COLORS[kind],
        roughness: 0.95,
        metalness: 0.0,
        polygonOffset: true,
        polygonOffsetFactor: -1,
        polygonOffsetUnits: -1,
        side: THREE.DoubleSide,
      });
      materials.set(kind, material);
    }
    return material;
  };

  let warnedNonFinite = false;

  for (const feature of data.features) {
    if (!(feature.kind in FEATURE_COLORS) || feature.outline.length < 3) {
      continue;
    }

    // Triangulate in shape XY with worldZ = -shapeY (keeps +Y normals), then
    // refine the soup so interior triangles follow the terrain drape too.
    const outline = subdivideOutline(dedupeOutline(feature.outline), refineStepAt);
    if (outline.length < 3) {
      continue;
    }
    const shape = new THREE.Shape(outline.map(([x, z]) => new THREE.Vector2(x, -z)));
    const shapeGeo = new THREE.ShapeGeometry(shape);
    const shapePos = shapeGeo.attributes["position"] as THREE.BufferAttribute;
    const index = shapeGeo.getIndex();
    const soup: number[] = [];
    const count = index ? index.count : shapePos.count;
    for (let i = 0; i < count; i += 1) {
      const v = index ? index.getX(i) : i;
      soup.push(shapePos.getX(v), -shapePos.getY(v));
    }
    shapeGeo.dispose();

    const refined = refineTriangleSoup(soup, refineStepAt);
    const vertexCount = refined.length / 2;
    const positions = new Float32Array(vertexCount * 3);
    let nonFinite = false;
    for (let v = 0; v < vertexCount; v += 1) {
      const x = refined[v * 2];
      const z = refined[v * 2 + 1];
      const height = groundHeightAt(x, z);
      if (!Number.isFinite(x) || !Number.isFinite(z) || !Number.isFinite(height)) {
        nonFinite = true;
        break;
      }
      positions[v * 3] = x;
      positions[v * 3 + 1] = height + FEATURE_LIFT;
      positions[v * 3 + 2] = z;
    }
    if (nonFinite) {
      // A single bad vertex smears triangles across clip space; drop the mesh.
      if (!warnedNonFinite) {
        console.warn(`OSM feature ${feature.id} produced a non-finite drape height; mesh skipped`);
        warnedNonFinite = true;
      }
      continue;
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.computeVertexNormals();
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();

    const mesh = new THREE.Mesh(geometry, materialFor(feature.kind));
    mesh.receiveShadow = true;
    mesh.renderOrder = 0; // above the satellite ground (-1), below the road
    mesh.name = `feature-${feature.kind}`;
    group.add(mesh);
  }
  return group;
}
