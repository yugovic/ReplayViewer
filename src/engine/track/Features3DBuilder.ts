/**
 * Features3DBuilder – renders the volumetric OSM/LiDAR features produced by
 * pipeline/build_features3d.py (public/data/tracks/<id>/features3d.json):
 * trees, buildings and barriers, placed on the SAME "post-drape" ground
 * height the satellite photo uses (via the groundHeightAt callback that
 * TrackBuilder builds from createFeatureDrape), so nothing floats or sinks at
 * the road-corridor boundary.
 *
 * Draw-call budget (P3b, ≤10 added total):
 *  - trees:     2  (trunk InstancedMesh + crown InstancedMesh)
 *  - buildings: 1  (all footprints extruded and merged into one BufferGeometry)
 *  - barriers:  ≤2 (opaque guard_rail/wall merge + translucent fence merge)
 *
 * All variation (crown hue, Y-rotation, building tint) comes from a
 * deterministic index hash — never Math.random — so screenshot regressions
 * stay byte-stable. Nothing here touches the DOM, so the pure logic is unit
 * testable under vitest's node environment (like FeatureBuilder).
 */

import * as THREE from "three";

// ─── Schema (mirrors specs/p3a_features3d_extraction.md) ────────────────────

export interface Tree3D {
  x: number;
  z: number;
  height: number;
  crownRadius: number;
}

export interface Building3D {
  footprint: Array<[number, number]>;
  height: number;
  tags?: Record<string, string>;
}

export interface Barrier3D {
  type: string;
  points: Array<[number, number]>;
  height: number;
}

export interface Features3DData {
  version: number;
  origin: { lat: number; lng: number; alt: number };
  source: string;
  trees: Tree3D[];
  buildings: Building3D[];
  barriers: Barrier3D[];
}

/** Post-drape ground height at a local XZ position, supplied by TrackBuilder. */
export type GroundHeightFn = (x: number, z: number) => number;

export interface Features3DStats {
  drawCalls: number;
  vertices: number;
  trees: number;
  buildings: number;
  barriers: number;
}

// ─── Deterministic hashing (no Math.random) ─────────────────────────────────

/**
 * Deterministic hash of an integer index + salt → [0, 1). Two salts give two
 * independent-looking but fully reproducible streams for the same index, so
 * per-instance variation never changes between runs (screenshot stability).
 */
export function hash01(index: number, salt: number): number {
  let h = (Math.imul(index >>> 0, 0x9e3779b1) + Math.imul(salt >>> 0, 0x85ebca77)) >>> 0;
  h ^= h >>> 15;
  h = Math.imul(h, 0x2c1b3c6d) >>> 0;
  h ^= h >>> 12;
  h = Math.imul(h, 0x297a2d39) >>> 0;
  h ^= h >>> 15;
  return (h >>> 0) / 0x1_0000_0000;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

// ─── Loader (graceful degradation, like loadFeatures) ───────────────────────

/**
 * Fetch features3d.json; resolves null on ANY failure (missing file, HTTP
 * error, malformed JSON, wrong shape). A null result makes the whole 3D layer
 * a no-op — the scene is never broken by absent/corrupt data (P3b constraint).
 */
export async function loadFeatures3d(url: string): Promise<Features3DData | null> {
  try {
    const response = await fetch(url);
    if (!response.ok) {
      return null;
    }
    const data = (await response.json()) as Features3DData;
    return isFeatures3dData(data) ? data : null;
  } catch {
    return null;
  }
}

/** Minimal structural validation: the three feature arrays must be present. */
export function isFeatures3dData(data: unknown): data is Features3DData {
  if (typeof data !== "object" || data === null) return false;
  const d = data as Partial<Features3DData>;
  return Array.isArray(d.trees) && Array.isArray(d.buildings) && Array.isArray(d.barriers);
}

// ─── Trees ──────────────────────────────────────────────────────────────────

export interface TreePlacement {
  x: number;
  z: number;
  /** Ground contact height for the trunk base (drape height, slightly sunk). */
  baseY: number;
  rotationY: number;
  trunkRadius: number;
  trunkHeight: number;
  crownRadius: number;
  /** Height above baseY where the crown ellipsoid centre sits. */
  crownCenterY: number;
  /** Vertical half-extent of the crown ellipsoid. */
  crownHalfHeight: number;
}

/** Small downward bias so a tiny drape mismatch reads as grounded, not floating. */
const TREE_SINK = 0.15;

/**
 * Pure placement math for one tree instance. Proportions are derived from the
 * measured `height`/`crownRadius`; the trunk radius is decoupled from the crown
 * radius (thick crowns must not imply comically thick trunks). The crown top
 * reaches ~groundY + height, so the silhouette matches the measured tree height.
 */
export function computeTreePlacement(tree: Tree3D, index: number, groundY: number): TreePlacement {
  const height = clamp(tree.height, 1.5, 60);
  const crownRadius = clamp(tree.crownRadius, 0.5, 12);
  const trunkHeight = clamp(height * 0.28, 0.5, height * 0.6);
  const trunkRadius = clamp(height * 0.035, 0.08, 0.5);
  const crownBase = trunkHeight * 0.7; // crown overlaps the trunk top (no gap)
  const crownHeight = Math.max(height - crownBase, 0.8);
  const baseY = groundY - TREE_SINK;
  return {
    x: tree.x,
    z: tree.z,
    baseY,
    rotationY: hash01(index, 1) * Math.PI * 2,
    trunkRadius,
    trunkHeight,
    crownRadius,
    crownCenterY: crownBase + crownHeight / 2,
    crownHalfHeight: crownHeight / 2,
  };
}

/** Deterministic green-phase crown colour (hue/saturation/lightness vary only
 * within a green band — never leaves "green", per spec). */
export function treeCrownColor(index: number, target: THREE.Color): THREE.Color {
  const h = 0.24 + hash01(index, 2) * 0.09; // 0.24‥0.33 (green → yellow-green)
  const s = 0.36 + hash01(index, 3) * 0.18; // 0.36‥0.54
  const l = 0.22 + hash01(index, 4) * 0.13; // 0.22‥0.35
  return target.setHSL(h, s, l);
}

function buildTrees(trees: Tree3D[], groundHeightAt: GroundHeightFn): THREE.InstancedMesh[] {
  if (trees.length === 0) return [];

  // Unit geometries (base at y=0, extent 1) reused across every instance.
  const trunkGeo = new THREE.CylinderGeometry(0.8, 1.0, 1, 5, 1);
  trunkGeo.translate(0, 0.5, 0);
  const crownGeo = new THREE.IcosahedronGeometry(1, 0); // 12 verts, faceted blob

  const trunkMat = new THREE.MeshStandardMaterial({
    color: 0x5a4334,
    roughness: 0.95,
    metalness: 0.0,
    flatShading: true,
  });
  const crownMat = new THREE.MeshStandardMaterial({
    color: 0xffffff, // multiplied by per-instance colour
    roughness: 0.9,
    metalness: 0.0,
    flatShading: true,
  });

  const trunkMesh = new THREE.InstancedMesh(trunkGeo, trunkMat, trees.length);
  const crownMesh = new THREE.InstancedMesh(crownGeo, crownMat, trees.length);
  trunkMesh.name = "features3d-tree-trunks";
  crownMesh.name = "features3d-tree-crowns";
  trunkMesh.castShadow = false;
  crownMesh.castShadow = false;
  trunkMesh.receiveShadow = false;
  crownMesh.receiveShadow = false;

  const matrix = new THREE.Matrix4();
  const pos = new THREE.Vector3();
  const quat = new THREE.Quaternion();
  const scale = new THREE.Vector3();
  const up = new THREE.Vector3(0, 1, 0);
  const color = new THREE.Color();

  for (let i = 0; i < trees.length; i += 1) {
    const tree = trees[i];
    const groundY = groundHeightAt(tree.x, tree.z);
    const p = computeTreePlacement(tree, i, Number.isFinite(groundY) ? groundY : 0);
    quat.setFromAxisAngle(up, p.rotationY);

    // Trunk: base at baseY, spans trunkHeight.
    pos.set(p.x, p.baseY, p.z);
    scale.set(p.trunkRadius, p.trunkHeight, p.trunkRadius);
    matrix.compose(pos, quat, scale);
    trunkMesh.setMatrixAt(i, matrix);

    // Crown: ellipsoid centred above the trunk, top ≈ baseY + height.
    pos.set(p.x, p.baseY + p.crownCenterY, p.z);
    scale.set(p.crownRadius, p.crownHalfHeight, p.crownRadius);
    matrix.compose(pos, quat, scale);
    crownMesh.setMatrixAt(i, matrix);
    crownMesh.setColorAt(i, treeCrownColor(i, color));
  }

  trunkMesh.instanceMatrix.needsUpdate = true;
  crownMesh.instanceMatrix.needsUpdate = true;
  if (crownMesh.instanceColor) crownMesh.instanceColor.needsUpdate = true;
  // Instancing culls per-mesh by one bounding sphere; the trees blanket the
  // whole map, so compute proper bounds rather than risk a stale empty sphere.
  trunkMesh.computeBoundingSphere();
  crownMesh.computeBoundingSphere();

  return [trunkMesh, crownMesh];
}

// ─── Buildings ────────────────────────────────────────────────────────────────

/** Signed area of a 2D polygon (shape space); >0 is counter-clockwise. */
export function signedArea(points: Array<[number, number]>): number {
  let area = 0;
  for (let i = 0; i < points.length; i += 1) {
    const [ax, ay] = points[i];
    const [bx, by] = points[(i + 1) % points.length];
    area += ax * by - bx * ay;
  }
  return area / 2;
}

/**
 * Building floor height: the lowest post-drape ground height under the
 * footprint vertices, minus 0.5 m, so no corner floats on a slope (P3b).
 */
export function computeBuildingBaseY(
  footprint: Array<[number, number]>,
  groundHeightAt: GroundHeightFn,
): number {
  let minGround = Infinity;
  for (const [x, z] of footprint) {
    const g = groundHeightAt(x, z);
    if (Number.isFinite(g) && g < minGround) minGround = g;
  }
  if (!Number.isFinite(minGround)) return NaN;
  return minGround - 0.5;
}

const BUILDING_WALL = new THREE.Color(0x9a9a95);
const BUILDING_ROOF = new THREE.Color(0xb0b1ac);
/** Wall vertices darken toward the ground by this factor (cheap baked AO). */
const BUILDING_BASE_AO = 0.72;
const BUILDING_OUTLINE_COLOR = 0x41464a;
/** Crease threshold for the silhouette lines (degrees). */
const BUILDING_OUTLINE_ANGLE = 25;

function buildBuildings(buildings: Building3D[], groundHeightAt: GroundHeightFn): THREE.Group | null {
  const positions: number[] = [];
  const colors: number[] = [];
  const wall = new THREE.Color();
  const roof = new THREE.Color();
  const scratch = new THREE.Vector3();

  let built = 0;
  for (let b = 0; b < buildings.length; b += 1) {
    const building = buildings[b];
    const footprint = building.footprint;
    if (!Array.isArray(footprint) || footprint.length < 3) continue;
    const height = clamp(building.height ?? 6, 1.5, 120);
    const baseY = computeBuildingBaseY(footprint, groundHeightAt);
    if (!Number.isFinite(baseY)) continue;

    // Shape space uses (x, -z) so a CCW world-XZ footprint stays consistent
    // through ExtrudeGeometry (extrudes +Z) → rotateX(-90°) (maps +Z → world
    // +Y and shape-Y → world +Z). Normalise the shape to CCW (+area) so the
    // extruded side walls face outward.
    const shapePts: Array<[number, number]> = footprint.map(([x, z]) => [x, -z]);
    if (signedArea(shapePts) < 0) shapePts.reverse();

    const shape = new THREE.Shape(shapePts.map(([x, y]) => new THREE.Vector2(x, y)));
    const extruded = new THREE.ExtrudeGeometry(shape, { depth: height, bevelEnabled: false });
    extruded.rotateX(-Math.PI / 2);
    extruded.translate(0, baseY, 0);
    const flat = extruded.toNonIndexed();
    extruded.dispose();

    // Per-building deterministic tint, subtle so the block stays neutral grey.
    const tint = 0.92 + hash01(b, 5) * 0.14; // 0.92‥1.06
    wall.copy(BUILDING_WALL).multiplyScalar(tint);
    roof.copy(BUILDING_ROOF).multiplyScalar(tint);

    const posAttr = flat.getAttribute("position") as THREE.BufferAttribute;
    const normAttr = flat.getAttribute("normal") as THREE.BufferAttribute;
    for (let i = 0; i < posAttr.count; i += 1) {
      const y = posAttr.getY(i);
      positions.push(posAttr.getX(i), y, posAttr.getZ(i));
      // Roof faces point up (world +Y); walls are ~horizontal.
      const ny = normAttr ? normAttr.getY(i) : 0;
      const isRoof = ny > 0.5;
      const c = isRoof ? roof : wall;
      // Baked AO: wall bases sink toward the ground shade so the block reads
      // as sitting on the terrain instead of floating on it.
      const t = clamp((y - baseY) / height, 0, 1);
      const ao = isRoof ? 1 : BUILDING_BASE_AO + (1 - BUILDING_BASE_AO) * t;
      colors.push(c.r * ao, c.g * ao, c.b * ao);
      void scratch;
    }
    flat.dispose();
    built += 1;
  }

  if (built === 0) return null;

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  geometry.computeVertexNormals();
  geometry.computeBoundingSphere();

  const material = new THREE.MeshStandardMaterial({
    vertexColors: true,
    roughness: 0.82,
    metalness: 0.0,
    flatShading: true,
    // The scene's ambient light is very low (0.15); without a floor the
    // sun-averted walls of tall buildings (main grandstand) render near
    // black. This constant lift keeps shaded faces readable while the sun
    // still models the volume.
    emissive: new THREE.Color(0x868782),
    emissiveIntensity: 0.35,
  });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.name = "features3d-buildings";
  mesh.castShadow = true;
  mesh.receiveShadow = false;

  // Toon-style silhouette: crease/border edges as one extra draw call.
  const edges = new THREE.EdgesGeometry(geometry, BUILDING_OUTLINE_ANGLE);
  const outline = new THREE.LineSegments(
    edges,
    new THREE.LineBasicMaterial({ color: BUILDING_OUTLINE_COLOR }),
  );
  outline.name = "features3d-building-outline";
  outline.renderOrder = 2;

  const group = new THREE.Group();
  group.name = "features3d-buildings-group";
  group.add(mesh);
  group.add(outline);
  return group;
}

// ─── Barriers ─────────────────────────────────────────────────────────────────

const BARRIER_SINK = 0.1;
const BARRIER_THICKNESS = 0.14;

const BARRIER_COLORS: Record<string, THREE.Color> = {
  guard_rail: new THREE.Color(0xb9bbbf),
  wall: new THREE.Color(0x9c968a),
  retaining_wall: new THREE.Color(0x9c968a),
  fence: new THREE.Color(0xc2c7ca),
};
const BARRIER_DEFAULT_COLOR = new THREE.Color(0xa9adb0);

function isTranslucentBarrier(type: string): boolean {
  return type === "fence";
}

/**
 * Append one polyline barrier as a thin vertical box ribbon (a wall with
 * BARRIER_THICKNESS depth) following the ground, into the given position/color
 * soups. Height comes from the record; the top tracks ground + height.
 */
function appendBarrierRibbon(
  barrier: Barrier3D,
  groundHeightAt: GroundHeightFn,
  positions: number[],
  colors: number[],
): boolean {
  const pts = barrier.points;
  if (!Array.isArray(pts) || pts.length < 2) return false;
  const height = clamp(barrier.height ?? 1, 0.3, 6);
  const half = BARRIER_THICKNESS / 2;
  const color = BARRIER_COLORS[barrier.type] ?? BARRIER_DEFAULT_COLOR;

  // Precompute the four longitudinal rails (bottomA/B, topA/B) per node,
  // offset ±half along the horizontal segment normal.
  type Rail = { ax: number; az: number; bx: number; bz: number; gy: number };
  const rails: Rail[] = [];
  let anyFinite = false;
  for (let i = 0; i < pts.length; i += 1) {
    const [x, z] = pts[i];
    const prev = pts[Math.max(0, i - 1)];
    const next = pts[Math.min(pts.length - 1, i + 1)];
    let tx = next[0] - prev[0];
    let tz = next[1] - prev[1];
    const len = Math.hypot(tx, tz) || 1;
    tx /= len;
    tz /= len;
    const nx = -tz;
    const nz = tx;
    const gy = groundHeightAt(x, z);
    if (Number.isFinite(gy)) anyFinite = true;
    rails.push({
      ax: x + nx * half,
      az: z + nz * half,
      bx: x - nx * half,
      bz: z - nz * half,
      gy: Number.isFinite(gy) ? gy : 0,
    });
  }
  if (!anyFinite) return false;

  const pushTri = (
    x1: number, y1: number, z1: number,
    x2: number, y2: number, z2: number,
    x3: number, y3: number, z3: number,
  ) => {
    positions.push(x1, y1, z1, x2, y2, z2, x3, y3, z3);
    for (let k = 0; k < 3; k += 1) colors.push(color.r, color.g, color.b);
  };

  for (let i = 0; i < rails.length - 1; i += 1) {
    const r0 = rails[i];
    const r1 = rails[i + 1];
    const b0 = r0.gy - BARRIER_SINK;
    const b1 = r1.gy - BARRIER_SINK;
    const t0 = r0.gy + height;
    const t1 = r1.gy + height;

    // Side A
    pushTri(r0.ax, b0, r0.az, r0.ax, t0, r0.az, r1.ax, b1, r1.az);
    pushTri(r0.ax, t0, r0.az, r1.ax, t1, r1.az, r1.ax, b1, r1.az);
    // Side B
    pushTri(r0.bx, b0, r0.bz, r1.bx, b1, r1.bz, r0.bx, t0, r0.bz);
    pushTri(r0.bx, t0, r0.bz, r1.bx, b1, r1.bz, r1.bx, t1, r1.bz);
    // Top cap
    pushTri(r0.ax, t0, r0.az, r0.bx, t0, r0.bz, r1.ax, t1, r1.az);
    pushTri(r0.bx, t0, r0.bz, r1.bx, t1, r1.bz, r1.ax, t1, r1.az);
  }
  return true;
}

function buildBarrierMesh(
  positions: number[],
  colors: number[],
  translucent: boolean,
  name: string,
): THREE.Mesh | null {
  if (positions.length === 0) return null;
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  geometry.computeVertexNormals();
  geometry.computeBoundingSphere();
  const material = new THREE.MeshStandardMaterial({
    vertexColors: true,
    roughness: 0.7,
    metalness: 0.1,
    side: THREE.DoubleSide,
    flatShading: true,
    transparent: translucent,
    opacity: translucent ? 0.55 : 1,
    depthWrite: !translucent,
  });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.name = name;
  mesh.castShadow = false;
  mesh.receiveShadow = false;
  return mesh;
}

function buildBarriers(barriers: Barrier3D[], groundHeightAt: GroundHeightFn): THREE.Mesh[] {
  const opaquePos: number[] = [];
  const opaqueCol: number[] = [];
  const fencePos: number[] = [];
  const fenceCol: number[] = [];

  for (const barrier of barriers) {
    if (isTranslucentBarrier(barrier.type)) {
      appendBarrierRibbon(barrier, groundHeightAt, fencePos, fenceCol);
    } else {
      appendBarrierRibbon(barrier, groundHeightAt, opaquePos, opaqueCol);
    }
  }

  const meshes: THREE.Mesh[] = [];
  const opaque = buildBarrierMesh(opaquePos, opaqueCol, false, "features3d-barriers-solid");
  if (opaque) meshes.push(opaque);
  const fence = buildBarrierMesh(fencePos, fenceCol, true, "features3d-barriers-fence");
  if (fence) {
    fence.renderOrder = 1; // draw translucent fences after opaque geometry
    meshes.push(fence);
  }
  return meshes;
}

// ─── Public API ───────────────────────────────────────────────────────────────

/**
 * Build the "features3d" group (trees + buildings + barriers). Returns the
 * group and measured stats (draw calls = one per mesh / instanced mesh;
 * vertices = unique geometry vertices, i.e. what is uploaded — instancing
 * reuses the trunk/crown geometry across all trees).
 *
 * Empty / missing categories are simply skipped, so an all-empty dataset
 * yields an empty group with drawCalls 0 (a visual no-op).
 */
export function buildFeatures3dGroup(
  data: Features3DData,
  groundHeightAt: GroundHeightFn,
): { group: THREE.Group; stats: Features3DStats } {
  const group = new THREE.Group();
  group.name = "features3d";

  const trees = Array.isArray(data.trees) ? data.trees : [];
  const buildings = Array.isArray(data.buildings) ? data.buildings : [];
  const barriers = Array.isArray(data.barriers) ? data.barriers : [];

  const treeMeshes = buildTrees(trees, groundHeightAt);
  const buildingGroup = buildBuildings(buildings, groundHeightAt);
  const barrierMeshes = buildBarriers(barriers, groundHeightAt);

  treeMeshes.forEach((m) => group.add(m));
  if (buildingGroup) group.add(buildingGroup);
  barrierMeshes.forEach((m) => group.add(m));

  let vertices = 0;
  group.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    const geo = mesh.geometry as THREE.BufferGeometry | undefined;
    const posAttr = geo?.getAttribute?.("position") as THREE.BufferAttribute | undefined;
    if (posAttr) vertices += posAttr.count;
  });

  const drawCalls = treeMeshes.length + (buildingGroup ? 2 : 0) + barrierMeshes.length;

  return {
    group,
    stats: {
      drawCalls,
      vertices,
      trees: trees.length,
      buildings: buildingGroup ? buildings.filter((b) => Array.isArray(b.footprint) && b.footprint.length >= 3).length : 0,
      barriers: barriers.length,
    },
  };
}
