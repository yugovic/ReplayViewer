/**
 * TrackBuilder – creates the full track visual group for Phase 3.
 *
 * Components:
 *  - Asphalt ribbon with UV coordinates and procedural roughness normal-map
 *  - Start/finish chequered line
 *  - 100 m / 50 m braking markers at major corners
 *  - Satellite imagery as ground plane
 */

import * as THREE from "three";
import type { TrackData } from "../../replay/types";
import { latLngToLocal, localToLatLng } from "../../replay/projection";
import { projectPointToCenterline, sampleTrackSurface } from "../../replay/interpolation";
import { corridorBlendHeight, corridorMask, reblendDrapedHeights, type DrapeCache } from "./groundMath";
import { TerrainSampler } from "./TerrainSampler";
import { buildFeaturesGroup, loadFeatures } from "./FeatureBuilder";
import { buildFeatures3dGroup, loadFeatures3d, type Features3DStats } from "./Features3DBuilder";
import {
  attachGroundDetailShader,
  createGroundDetailUniforms,
  type GroundDetailUniforms,
} from "./groundDetail";
import { loadOverlays } from "./overlays";
import { buildCurbsGroup, type CurbCenterlinePoint } from "./curbBuilder";
import { satelliteFilename, type SatVariantId } from "../../replay/satelliteVariants";
import { EnhancedCorridorGround } from "./EnhancedCorridorGround";
import { buildRoadEdgeLinesGroup, loadRoadEdgeProfile } from "./RoadEdgeLines";
import { buildFujiReferenceRoad } from "./FujiReferenceStudy";
import { buildFujiCgTrack } from "./FujiCgStudy";

const ELEVATION_SCALE = 1;

/** Minimal shape TrackBuilder needs from a terrain heightfield; satisfied by
 * TerrainSampler and by lightweight test doubles alike. */
interface HeightSampler {
  heightAt(lat: number, lng: number): number | null;
}

function wrapArcLength(dist: number, length: number): number {
  if (length <= 0) return 0;
  return ((dist % length) + length) % length;
}

/**
 * The projection/sampling half of the drape: centerline projection, road
 * altitude and terrain height for one vertex. Cached per satellite-ground
 * vertex so a road toggle can re-blend heights without repeating this work.
 */
export function computeDrapeInputs(
  track: TrackData,
  x: number,
  z: number,
  lat: number,
  lng: number,
  sampler: HeightSampler | null,
): { roadY: number; terrainY: number; lateralDistance: number } {
  const { arcLength, lateralDistance, signedLateralDistance } = projectPointToCenterline(track, x, z);
  // P2: sample the cambered road surface at this vertex's signed lateral offset
  // so the draped ground under the corridor tilts with the road ribbon (no
  // step at the asphalt edge). Falls back to the flat centerline profile for
  // tracks without baked camber (sampleTrackSurface handles that internally).
  const roadY =
    sampleTrackSurface(track, wrapArcLength(arcLength, track.totalLength), signedLateralDistance) * ELEVATION_SCALE;
  const terrainAlt = sampler?.heightAt(lat, lng) ?? null;
  const terrainY = terrainAlt != null ? (terrainAlt - track.origin.alt) * ELEVATION_SCALE : roadY;
  return { roadY, terrainY, lateralDistance };
}

/**
 * Ground height for one drape vertex: the road surface just under the
 * asphalt inside the corridor, blending out to the real terrain heightfield
 * (when available) past the runoff — see corridorBlendHeight().
 */
export function computeDrapedGroundHeight(
  track: TrackData,
  x: number,
  z: number,
  lat: number,
  lng: number,
  sampler: HeightSampler | null,
): number {
  const { roadY, terrainY, lateralDistance } = computeDrapeInputs(track, x, z, lat, lng, sampler);
  return corridorBlendHeight({ roadY, terrainY, lateralDistance, halfRoadWidth: track.width / 2 });
}

// ─── Asphalt ──────────────────────────────────────────────────────────────────

function buildAsphaltMaterial(): THREE.MeshStandardMaterial {
  // Procedural "noise" normal map for asphalt grain (256×256 canvas)
  const size = 256;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d")!;

  // Fill with neutral normal (0.5, 0.5, 1.0) → flat
  ctx.fillStyle = "#8080ff";
  ctx.fillRect(0, 0, size, size);

  // Sprinkle small bumps
  const rng = mulberry32(0xdeadbeef);
  for (let i = 0; i < 8000; i++) {
    const x = Math.floor(rng() * size);
    const y = Math.floor(rng() * size);
    const r = 1 + Math.floor(rng() * 2);
    const ang = rng() * Math.PI * 2;
    const strength = 0.12 + rng() * 0.12;
    const nx = 0.5 + Math.cos(ang) * strength;
    const ny = 0.5 + Math.sin(ang) * strength;
    ctx.fillStyle = `rgb(${Math.round(nx * 255)},${Math.round(ny * 255)},255)`;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
  }

  const normalMap = new THREE.CanvasTexture(canvas);
  normalMap.wrapS = THREE.RepeatWrapping;
  normalMap.wrapT = THREE.RepeatWrapping;
  normalMap.repeat.set(40, 2);

  return new THREE.MeshStandardMaterial({
    color: 0x252830,
    roughness: 0.82,
    metalness: 0.01,
    normalMap,
    normalScale: new THREE.Vector2(0.35, 0.35),
    side: THREE.DoubleSide,
  });
}

/** Seeded 32-bit mulberry RNG – deterministic noise, no Math.random() dependency */
function mulberry32(seed: number) {
  let s = seed >>> 0;
  return () => {
    s += 0x6d2b79f5;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t ^= t + Math.imul(t ^ (t >>> 7), 61 | t);
    return ((t ^ (t >>> 14)) >>> 0) / 0xffffffff;
  };
}

/**
 * Scene-space y of the road's LEFT and RIGHT edges at centerline index `i`,
 * from the baked altLeft/altRight (P2 camber). LEFT/RIGHT are the driver's
 * left/right in the direction of increasing dist. Falls back to the flat
 * centerline height when the track has no camber data (backward compatible).
 */
function roadEdgeY(track: TrackData, i: number, fallbackY: number): { left: number; right: number } {
  const p = track.centerline[i];
  if (p && typeof p.altLeft === "number" && typeof p.altRight === "number") {
    return {
      left: (p.altLeft - track.origin.alt) * ELEVATION_SCALE,
      right: (p.altRight - track.origin.alt) * ELEVATION_SCALE,
    };
  }
  return { left: fallbackY, right: fallbackY };
}

function buildRoadMesh(track: TrackData, points: THREE.Vector3[]): THREE.Mesh {
  const vertices: number[] = [];
  const uvs: number[] = [];
  const indices: number[] = [];
  const halfWidth = track.width / 2;
  let accDist = 0;

  for (let i = 0; i < points.length; i++) {
    const point = points[i];
    const prev = points[Math.max(0, i - 1)];
    const next = points[Math.min(points.length - 1, i + 1)];

    const tangent = new THREE.Vector3(next.x - prev.x, 0, next.z - prev.z);
    if (tangent.lengthSq() < 0.0001) tangent.set(0, 0, 1);
    tangent.normalize();
    // `normal` = (-tz, 0, tx). The +normal side is the driver's RIGHT
    // (physical left = up × forward = (tz, 0, -tx) = -normal), so the first
    // vertex below (at +normal) takes the RIGHT edge height and the second
    // (at -normal) the LEFT edge height. Camber tilts the ribbon accordingly;
    // vertex geometry/winding is unchanged from the flat version.
    const normal = new THREE.Vector3(-tangent.z, 0, tangent.x);
    const edge = roadEdgeY(track, i, point.y);

    const rightVertex = point.clone().addScaledVector(normal, halfWidth);
    rightVertex.y = edge.right;
    const leftVertex = point.clone().addScaledVector(normal, -halfWidth);
    leftVertex.y = edge.left;

    if (i > 0) {
      accDist += points[i].distanceTo(points[i - 1]);
    }
    const uv = accDist / track.width; // tile along track length

    vertices.push(rightVertex.x, rightVertex.y, rightVertex.z, leftVertex.x, leftVertex.y, leftVertex.z);
    uvs.push(0, uv, 1, uv);
  }

  for (let i = 0; i < points.length - 1; i++) {
    const a = i * 2;
    indices.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(vertices, 3));
  geometry.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();

  const mesh = new THREE.Mesh(geometry, buildAsphaltMaterial());
  mesh.receiveShadow = true;
  mesh.castShadow = false;
  return mesh;
}

// ─── Centerline curvature (drives braking-marker placement) ────────────────────

/**
 * Returns curvature magnitude at each centerline point (0 = straight).
 *
 * The raw turn angle between consecutive segments scales with point spacing
 * (θ ∝ spacing at constant corner radius, and 1-cos θ ≈ θ²/2), so the value
 * is normalised to a 17 m reference spacing – the spacing the CURV_THRESHOLD
 * constants were originally tuned for – to stay threshold-compatible when the
 * centerline is resampled more densely (e.g. the ~7 m OSM-based centerline).
 */
function computeCurvatures(points: THREE.Vector3[]): number[] {
  const REF_SPACING = 17; // metres
  const curvatures: number[] = new Array(points.length).fill(0);
  for (let i = 1; i < points.length - 1; i++) {
    const a = points[i - 1];
    const b = points[i];
    const c = points[i + 1];
    const ab = new THREE.Vector3(b.x - a.x, 0, b.z - a.z);
    const bc = new THREE.Vector3(c.x - b.x, 0, c.z - b.z);
    const spacing = (ab.length() + bc.length()) / 2;
    if (spacing < 0.001) continue;
    ab.normalize();
    bc.normalize();
    const raw = 1 - ab.dot(bc); // 0 for straight, ~2 for 180° turn
    curvatures[i] = raw * (REF_SPACING / spacing) ** 2;
  }
  return curvatures;
}

// NOTE: the old curvature-triggered red/white box kerbs were removed
// (2026-07-13): sampled per centerline point they rendered as sparse floating
// plates beside the road (persona-test P0-4). Proper curbs come from
// overlays.json via curbBuilder (the "Track lines" layer).

// ─── Start/Finish chequered line ───────────────────────────────────────────────

function buildStartFinishLine(track: TrackData, points: THREE.Vector3[]): THREE.Mesh {
  const p0 = points[0];
  const p1 = points[1] ?? points[0];
  const tangent = new THREE.Vector3(p1.x - p0.x, 0, p1.z - p0.z).normalize();

  const COLS = 10;
  const ROWS = 2;
  const tileW = track.width / COLS;
  const tileD = 0.9;

  const canvas = document.createElement("canvas");
  canvas.width = COLS * 32;
  canvas.height = ROWS * 32;
  const ctx = canvas.getContext("2d")!;
  for (let row = 0; row < ROWS; row++) {
    for (let col = 0; col < COLS; col++) {
      ctx.fillStyle = (row + col) % 2 === 0 ? "#ffffff" : "#000000";
      ctx.fillRect(col * 32, row * 32, 32, 32);
    }
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;

  const geo = new THREE.PlaneGeometry(track.width, tileD * ROWS);
  const mat = new THREE.MeshStandardMaterial({ map: tex, roughness: 0.55, metalness: 0.0 });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.rotation.x = -Math.PI / 2;
  mesh.position.set(p0.x, p0.y + 0.02, p0.z);
  mesh.rotation.z = -Math.atan2(tangent.x, tangent.z);
  mesh.receiveShadow = true;
  return mesh;
}

// ─── Braking markers ──────────────────────────────────────────────────────────

function buildBrakingMarkers(track: TrackData, points: THREE.Vector3[]): THREE.Group {
  const group = new THREE.Group();
  const curvatures = computeCurvatures(points);
  const CURV_THRESHOLD = 0.07; // only major corners
  const MARKER_DISTS = [100, 50]; // metres before corner
  const halfRoad = track.width / 2;

  // Accumulate distances along centerline
  const dists: number[] = [0];
  for (let i = 1; i < points.length; i++) {
    dists.push(dists[i - 1] + points[i].distanceTo(points[i - 1]));
  }

  // Find apex indices (local curvature maxima above threshold)
  const apexIndices: number[] = [];
  for (let i = 2; i < points.length - 2; i++) {
    if (
      curvatures[i] >= CURV_THRESHOLD &&
      curvatures[i] >= curvatures[i - 1] &&
      curvatures[i] >= curvatures[i + 1]
    ) {
      // Ensure we're not too close to a previous apex (distance-based so the
      // rule is independent of centerline point spacing; 8 points ≈ 136 m at
      // the original 17 m spacing)
      const last = apexIndices[apexIndices.length - 1];
      if (last === undefined || dists[i] - dists[last] > 136) {
        apexIndices.push(i);
      }
    }
  }

  const paintMat100 = buildPaintMaterial("100");
  const paintMat50 = buildPaintMaterial("50");
  // Small road-paint marker: 1 m wide × 2 m long, lying flat on the asphalt
  const paintGeo = new THREE.PlaneGeometry(1.0, 2.0);

  for (const apexIdx of apexIndices) {
    const apexDist = dists[apexIdx];
    for (const markerDist of MARKER_DISTS) {
      const targetDist = apexDist - markerDist;
      if (targetDist < 0) continue;

      // Find index with closest dist
      let closest = 0;
      let minDiff = Infinity;
      for (let i = 0; i < dists.length; i++) {
        const diff = Math.abs(dists[i] - targetDist);
        if (diff < minDiff) { minDiff = diff; closest = i; }
      }
      const pt = points[closest];
      const prev2 = points[Math.max(0, closest - 1)];
      const next2 = points[Math.min(points.length - 1, closest + 1)];
      const tangent = new THREE.Vector3(next2.x - prev2.x, 0, next2.z - prev2.z).normalize();
      const normal = new THREE.Vector3(-tangent.z, 0, tangent.x);

      // Painted on the edge of the asphalt, 2 cm above the surface
      const paintPos = pt.clone().addScaledVector(normal, halfRoad - 0.8);
      paintPos.y = pt.y + 0.02;

      const mat = markerDist === 100 ? paintMat100 : paintMat50;
      const paint = new THREE.Mesh(paintGeo, mat);
      paint.position.copy(paintPos);
      // Lie flat and align the long side with the direction of travel
      paint.rotation.x = -Math.PI / 2;
      paint.rotation.z = -Math.atan2(tangent.x, tangent.z);
      paint.receiveShadow = true;
      group.add(paint);
    }
  }
  return group;
}

/** Road-paint style distance marker texture: white numeral on a red patch */
function buildPaintMaterial(label: string): THREE.MeshStandardMaterial {
  const canvas = document.createElement("canvas");
  canvas.width = 128;
  canvas.height = 256;
  const ctx = canvas.getContext("2d")!;
  // Red paint patch with slightly rounded look
  ctx.fillStyle = "#b32014";
  ctx.fillRect(6, 6, 116, 244);
  // White border stripe like painted lines
  ctx.strokeStyle = "#f2f2f2";
  ctx.lineWidth = 8;
  ctx.strokeRect(10, 10, 108, 236);
  // Numeral along the travel direction
  ctx.fillStyle = "#f2f2f2";
  ctx.font = "bold 64px monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(label, 64, 128);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return new THREE.MeshStandardMaterial({
    map: tex,
    roughness: 0.75,
    metalness: 0.0,
    polygonOffset: true,
    polygonOffsetFactor: -1,
    polygonOffsetUnits: -1,
  });
}

// ─── Satellite ground plane ────────────────────────────────────────────────────

interface SatelliteMeta {
  imageFile: string;
  imageWidth: number;
  imageHeight: number;
  bbox: { minLng: number; maxLng: number; minLat: number; maxLat: number };
  mercator: boolean;
}

const DEG2RAD = Math.PI / 180;
const RAD2DEG = 180 / Math.PI;

/**
 * Maps a satellite-texture UV coordinate to the geographic (lat, lng) of the
 * ground it depicts.
 *
 * The satellite image is a stitched Web-Mercator tile mosaic
 * (see replay-viewer/fetch_satellite_tiles.py): pixel x is linear in
 * longitude, but pixel y is linear in Mercator y = asinh(tan(lat)) — NOT in
 * latitude. With flipY=false, v=0 samples the top image row (north edge,
 * maxLat) and v=1 the bottom row (south edge, minLat).
 *
 * Exported for quantitative alignment verification in tests/scripts.
 */
/**
 * Applies the ground-texture conventions every satellite variant must share
 * so a runtime variant swap (setSatelliteVariant) looks identical to the
 * initial load: flipY=false matches the vertex-placement convention in
 * buildSatelliteGround (see satelliteUvToLatLng's doc comment), sRGB decoding
 * for a photo, and max anisotropy since racing cameras view the ground at
 * grazing angles almost every frame.
 */
function applySatelliteTextureSettings(texture: THREE.Texture): void {
  texture.flipY = false;
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 16;
}

export function satelliteUvToLatLng(
  u: number,
  v: number,
  bbox: SatelliteMeta["bbox"],
  mercator: boolean,
): { lat: number; lng: number } {
  const lng = bbox.minLng + u * (bbox.maxLng - bbox.minLng); // Mercator x is linear in lng
  if (!mercator) {
    return { lat: bbox.maxLat + v * (bbox.minLat - bbox.maxLat), lng };
  }
  const yNorth = Math.asinh(Math.tan(bbox.maxLat * DEG2RAD));
  const ySouth = Math.asinh(Math.tan(bbox.minLat * DEG2RAD));
  const lat = Math.atan(Math.sinh(yNorth + v * (ySouth - yNorth))) * RAD2DEG;
  return { lat, lng };
}

function buildSatelliteGround(
  track: TrackData,
  meta: SatelliteMeta,
  texture: THREE.Texture,
  sampler: HeightSampler | null,
): { mesh: THREE.Mesh; cache: DrapeCache; detailUniforms: GroundDetailUniforms } {
  const { bbox } = meta;
  const origin = track.origin;

  // Grid-warp placement: rather than stretching the (Web-Mercator) image
  // linearly between its bbox corners in the local equirectangular scene
  // space, every vertex of a subdivided plane is placed by converting its
  // UV → (lng, lat) with the exact inverse Mercator mapping and then through
  // the very same latLngToLocal() projection used for telemetry, centerline
  // and the road mesh. The image ground position and the scene coordinate
  // system therefore agree by construction, absorbing any projection
  // difference in one step (and staying correct for larger bboxes where the
  // Mercator/equirectangular divergence grows quadratically).
  const segsX = 192;
  const segsZ = 192;
  const geo = new THREE.PlaneGeometry(1, 1, segsX, segsZ);
  geo.rotateX(-Math.PI / 2);

  // PlaneGeometry's default index winding is only correct for a +Y-up normal
  // when v increases as local Z decreases (true for the plain rotateX(-90°)
  // idiom, since its untouched vertex grid ties v to -y before rotation).
  // Every vertex below is then relocated to its real lat/lng-derived (x, z)
  // via latLngToLocal, whose north→−Z convention makes v increase as z
  // INCREASES instead (v runs from the north edge, v=0, down to the south
  // edge, v=1 — see satelliteUvToLatLng — and north is now -Z). That is a
  // mirror of the grid's original layout along Z, which flips triangle
  // winding, so computeVertexNormals() below would otherwise yield -Y
  // (downward) normals — an invisible/backface-culled ground. Reversing the
  // last two indices of every triangle compensates, restoring +Y normals
  // without touching the (correct) vertex positions or the UV↔latlng gluing.
  const flipIndex = geo.getIndex();
  if (flipIndex) {
    const arr = flipIndex.array as Uint16Array | Uint32Array;
    for (let t = 0; t < arr.length; t += 3) {
      const tmp = arr[t + 1];
      arr[t + 1] = arr[t + 2];
      arr[t + 2] = tmp;
    }
    flipIndex.needsUpdate = true;
  }

  // Drape every satellite vertex just under the asphalt inside the road
  // corridor, blending out to the real terrain heightfield (if loaded)
  // beyond the runoff — see corridorBlendHeight(). The per-vertex blend
  // inputs are cached so the road toggle can re-blend without re-projecting.
  const posAttr = geo.attributes["position"] as THREE.BufferAttribute;
  const uvAttr = geo.attributes["uv"] as THREE.BufferAttribute;
  const halfRoadWidth = track.width / 2;
  const cache: DrapeCache = {
    roadY: new Float32Array(posAttr.count),
    terrainY: new Float32Array(posAttr.count),
    lateralDistance: new Float32Array(posAttr.count),
    halfRoadWidth,
  };
  // Baked per-vertex mask for the detail-texture blend below: 1 inside the
  // paved road, 0 past the edge — see corridorMask() in groundMath.ts.
  const corridorMaskAttr = new Float32Array(posAttr.count);
  for (let i = 0; i < posAttr.count; i++) {
    const u = uvAttr.getX(i);
    const v = uvAttr.getY(i);
    const { lat, lng } = satelliteUvToLatLng(u, v, bbox, meta.mercator !== false);
    const local = latLngToLocal(lat, lng, origin);
    const { roadY, terrainY, lateralDistance } = computeDrapeInputs(track, local.x, local.z, lat, lng, sampler);
    cache.roadY[i] = roadY;
    cache.terrainY[i] = terrainY;
    cache.lateralDistance[i] = lateralDistance;
    corridorMaskAttr[i] = corridorMask(lateralDistance, halfRoadWidth);
    const vertexY = corridorBlendHeight({ roadY, terrainY, lateralDistance, halfRoadWidth });
    posAttr.setXYZ(i, local.x, vertexY, local.z);
  }
  posAttr.needsUpdate = true;
  geo.setAttribute("corridorMask", new THREE.Float32BufferAttribute(corridorMaskAttr, 1));
  // Per-vertex lateral distance to the centerline (baked from the DrapeCache),
  // consumed by the white edge-line shader (Task 2a, attachGroundDetailShader).
  geo.setAttribute("lateralDistance", new THREE.Float32BufferAttribute(cache.lateralDistance.slice(), 1));
  geo.computeVertexNormals();
  geo.computeBoundingBox();
  geo.computeBoundingSphere();

  // v=0 samples the top image row (north edge), matching the
  // satelliteUvToLatLng() convention used for vertex placement above.
  applySatelliteTextureSettings(texture);

  const mat = new THREE.MeshStandardMaterial({
    map: texture,
    roughness: 0.92,
    metalness: 0.0,
    // Extra insurance against Z-fighting: push satellite polygons further back
    polygonOffset: true,
    polygonOffsetFactor: 4,
    polygonOffsetUnits: 4,
  });
  // Shared close-range detail response (see groundDetail.ts): this ground
  // uses the baked per-vertex corridor mask and keeps the legacy edge-line
  // band for tracks without a measured edge profile.
  const detailUniforms = createGroundDetailUniforms(halfRoadWidth);
  attachGroundDetailShader(mat, detailUniforms, {
    maskMode: "vertexCorridor",
    edgeLines: true,
    cacheKey: "satellite-ground-detail-v3",
  });
  const mesh = new THREE.Mesh(geo, mat);
  // Vertices are already in world (scene-local) coordinates via latLngToLocal.
  mesh.position.set(0, 0, 0);
  mesh.receiveShadow = true;
  mesh.renderOrder = -1;
  mesh.name = "satellite-ground";
  return { mesh, cache, detailUniforms };
}

// ─── Fallback ground ──────────────────────────────────────────────────────────

function buildFallbackGround(): THREE.Mesh {
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(2000, 2000),
    new THREE.MeshStandardMaterial({
      color: 0x263421,
      roughness: 0.95,
      polygonOffset: true,
      polygonOffsetFactor: 4,
      polygonOffsetUnits: 4,
    }),
  );
  ground.rotation.x = -Math.PI / 2;
  ground.position.set(120, -18.2, 210);
  ground.receiveShadow = true;
  ground.renderOrder = -1;
  ground.name = "fallback-ground";
  return ground;
}

// ─── Public API ───────────────────────────────────────────────────────────────

export interface TrackBuildResult {
  group: THREE.Group;
  centerline3D: THREE.Vector3[];
  /** Toggle the procedural road layer and re-drape the satellite ground so
   * the photo road meets the car while the 3D road is hidden. Safe to call
   * before the async satellite ground has loaded. */
  setRoadVisible: (visible: boolean) => void;
  /** Toggle the OSM surface-feature layer. Safe to call before it loads. */
  setFeaturesVisible: (visible: boolean) => void;
  /** Toggle the 3D features layer (trees/buildings/barriers). Safe to call
   * before it loads (the desired state is applied once features3d.json is in). */
  setFeatures3dVisible: (visible: boolean) => void;
  /** Toggle the ground detail-texture blend (Plan A). Safe to call before
   * the satellite ground has loaded — the desired state is applied once its
   * material's shader uniforms exist. */
  setDetailTexture: (enabled: boolean) => void;
  /** Swap the ground's satellite imagery to another variant (same bbox/mesh,
   * texture-only). Loads the new texture asynchronously via THREE.TextureLoader
   * (flipY=false, sRGB, anisotropy=16 — matching buildSatelliteGround exactly)
   * and caches it per variant so switching back is instant. Safe to call
   * before the satellite ground has loaded (applied once it's ready) and
   * safe to call again mid-load (only the most recently requested variant
   * is ever applied to the material — "last click wins"). Swaps `map` on the
   * existing material instance, so the ground-detail onBeforeCompile shader
  * (attachGroundDetailShader) keeps working unchanged. */
  setSatelliteVariant: (variant: SatVariantId) => void;
  /** Update camera-dependent high-resolution ground tiles. */
  updateGround: (camera: THREE.Camera, viewportHeightPx: number, overview?: boolean) => void;
  /** Toggle the vector track markings (Task 2): the shader white edge lines on
   * the satellite ground AND the overlays.json curb ribbons, as one layer. Safe
   * to call before the ground/curbs have loaded — the state is applied on
   * arrival. */
  setTrackLines: (enabled: boolean) => void;
  /** Show/hide the trial tiles from the imagegen_trials override manifest —
   * the in-app A/B switch between the shipped 静岡20cm SR look and the
   * experimental patches. Standard SR tiles are unaffected. Safe to call
   * before the corridor layer has loaded. */
  setTrialTilesVisible: (visible: boolean) => void;
  /** Terrain heightfield sample (scene Y) at a scene-local (x, z), from the
   * same TerrainSampler the satellite drape uses. null until the async terrain
   * load resolves or when (x, z) falls outside the heightfield bbox. Used as
   * the drive-on-AC ground fallback once the car leaves the road collision
   * mesh — off-track the road-clamped analytic profile can differ from the
   * visible terrain by metres (the car appeared to hover). */
  terrainHeightAt: (x: number, z: number) => number | null;
  /** Dispose network-backed resources that are not owned by Object3D. */
  dispose: () => void;
}

/** Ground clearance below the road surface while the procedural road shows. */
const ROAD_ON_CLEARANCE = 0.6;
/** Near-zero clearance so the photo road carries the car when hidden. */
const ROAD_OFF_CLEARANCE = 0.08;

// OSM feature interior sampling: fine near the road corridor (where the
// drape cuts a cliff below the asphalt), coarse on open terrain — keeps the
// layer at tens of thousands of vertices instead of millions.
const FEATURE_FINE_STEP = 10;
const FEATURE_COARSE_STEP = 32;

/**
 * Sampling helpers for the OSM feature layer, built around a 30 m spatial
 * hash of the centerline. Inside the mask (everything within ~60-90 m of
 * the road) heights use the exact corridor drape and triangles refine at
 * FEATURE_FINE_STEP; outside it every point is provably past the corridor
 * blend (>26 m lateral), so the height is pure terrain and triangles refine
 * coarsely — this keeps the one-time build far below a second.
 */
export function createFeatureDrape(
  track: TrackData,
  sampler: HeightSampler | null,
): { stepAt: (x: number, z: number) => number; groundHeightAt: (x: number, z: number) => number } {
  const CELL = 30;
  const REACH = 2; // cells; any point in an unmarked cell is >56 m from the road
  const keyOf = (cx: number, cz: number) => (cx + 512) * 1024 + (cz + 512);
  const near = new Set<number>();
  for (const p of track.centerline) {
    const cx = Math.round(p.x / CELL);
    const cz = Math.round(p.z / CELL);
    for (let dx = -REACH; dx <= REACH; dx += 1) {
      for (let dz = -REACH; dz <= REACH; dz += 1) {
        near.add(keyOf(cx + dx, cz + dz));
      }
    }
  }
  const nearRoad = (x: number, z: number) =>
    near.has(keyOf(Math.round(x / CELL), Math.round(z / CELL)));

  const stepAt = (x: number, z: number) =>
    nearRoad(x, z) ? FEATURE_FINE_STEP : FEATURE_COARSE_STEP;

  const groundHeightAt = (x: number, z: number) => {
    const { lat, lng } = localToLatLng(x, z, track.origin);
    if (!nearRoad(x, z)) {
      const alt = sampler?.heightAt(lat, lng);
      if (alt != null) {
        return (alt - track.origin.alt) * ELEVATION_SCALE;
      }
    }
    return computeDrapedGroundHeight(track, x, z, lat, lng, sampler);
  };

  return { stepAt, groundHeightAt };
}

/**
 * Build the full track visual group synchronously first (road + markers),
 * then asynchronously load the satellite ground and OSM feature layer and
 * splice them in.
 */
export function buildTrack(
  track: TrackData,
  textureLoader: THREE.TextureLoader,
): TrackBuildResult {
  if (track.trackId === "fuji" && track.visualProfile === "cg") return buildFujiCgTrack(track, textureLoader);
  const group = new THREE.Group();

  const points = track.centerline.map(
    (p) => new THREE.Vector3(p.x, p.y * ELEVATION_SCALE, p.z),
  );

  // -- Procedural road visuals, grouped so they toggle as one layer
  const proceduralRoad = new THREE.Group();
  proceduralRoad.name = "procedural-road";
  proceduralRoad.add(buildRoadMesh(track, points));
  proceduralRoad.add(buildStartFinishLine(track, points));
  proceduralRoad.add(buildBrakingMarkers(track, points));
  group.add(proceduralRoad);

  // -- Fallback ground first (replaced by satellite if loaded)
  const fallback = buildFallbackGround();
  group.add(fallback);

  // Layer state. Both async loads below apply the latest state on arrival,
  // so the toggles are safe to call at any time.
  let roadVisible = true;
  let featuresVisible = true;
  let features3dVisible = true;
  let detailTextureEnabled = true;
  // Vector track markings (Task 2): white edge lines (shader uniform on the
  // ground) + overlays.json curb ribbons, toggled together as one layer.
  let trackLinesEnabled = true;
  let ground: { geometry: THREE.BufferGeometry; cache: DrapeCache; scratchY: Float32Array } | null = null;
  let featuresGroup: THREE.Group | null = null;
  let features3dGroup: THREE.Group | null = null;
  let curbsGroup: THREE.Group | null = null;
  let edgeLinesGroup: THREE.Group | null = null;
  let groundDetailUniforms: GroundDetailUniforms | null = null;
  let enhancedGround: EnhancedCorridorGround | null = null;
  let disposed = false;

  // Lazy-load bookkeeping: the OSM-feature, 3D-feature and track-line assets
  // (features.json ~tens of KB, features3d.json ~1 MB, features/road_edges +
  // overlays) are only fetched when their layer is (or becomes) visible, so a
  // user-mode startup with every one of them off pays for none of them. Each
  // load is idempotent (the *Requested guards) so repeated toggles never
  // refetch. The feature drape (createFeatureDrape) needed by the OSM/3D
  // feature builders is only ready after the async satellite+terrain load;
  // ensureOsmFeatures/ensureFeatures3d defer until then and are re-checked
  // once it resolves.
  let featureDrape: { stepAt: (x: number, z: number) => number; groundHeightAt: (x: number, z: number) => number } | null =
    null;
  let osmFeaturesRequested = false;
  let features3dRequested = false;
  let trackLinesRequested = false;

  // Satellite variant state: the ground material instance (set once built),
  // a per-variant texture cache (so switching back is instant and nothing
  // already loaded is disposed for the life of the session), and the most
  // recently requested variant id ("last click wins" — see setSatelliteVariant).
  let satelliteMaterial: THREE.MeshStandardMaterial | null = null;
  const satelliteTextureCache = new Map<SatVariantId, THREE.Texture>();
  let latestSatVariant: SatVariantId = track.satVariant ?? "default";

  const baseSatelliteVariant = (variant: SatVariantId): SatVariantId =>
    variant === "shizuoka_x2" ? "shizuoka" : variant;

  // Which satellite variant carries the SR corridor layer: fuji pairs it
  // with the Shizuoka imagery variant; GSI-bootstrapped tracks (suzuka,
  // okayama, ...) build their corridor from the default base itself. Tracks
  // without a corridor manifest just log a warn on the first enable.
  const corridorVariant: SatVariantId = (track.trackId ?? "barber") === "fuji" ? "shizuoka_x2" : "default";
  const applyEnhancedVisibility = () => {
    enhancedGround?.setEnabled(latestSatVariant === corridorVariant);
  };

  const applyGroundClearance = () => {
    if (!ground) return;
    const clearance = roadVisible ? ROAD_ON_CLEARANCE : ROAD_OFF_CLEARANCE;
    const heights = reblendDrapedHeights(ground.cache, clearance, ground.scratchY);
    const posAttr = ground.geometry.attributes["position"] as THREE.BufferAttribute;
    for (let i = 0; i < posAttr.count; i++) {
      posAttr.setY(i, heights[i]);
    }
    posAttr.needsUpdate = true;
    ground.geometry.computeVertexNormals();
    ground.geometry.computeBoundingBox();
    ground.geometry.computeBoundingSphere();
  };

  const setRoadVisible = (visible: boolean) => {
    if (visible === roadVisible) return;
    roadVisible = visible;
    proceduralRoad.visible = visible;
    applyGroundClearance();
    enhancedGround?.setRoadVisible(visible);
  };

  const updateGround = (camera: THREE.Camera, viewportHeightPx: number, overview = false) => {
    enhancedGround?.update(camera, viewportHeightPx, overview);
  };

  const setFeaturesVisible = (visible: boolean) => {
    featuresVisible = visible;
    if (visible) ensureOsmFeatures();
    if (featuresGroup) featuresGroup.visible = visible;
  };

  const setFeatures3dVisible = (visible: boolean) => {
    features3dVisible = visible;
    if (visible) ensureFeatures3d();
    if (features3dGroup) features3dGroup.visible = visible;
  };

  const setDetailTexture = (enabled: boolean) => {
    detailTextureEnabled = enabled;
    if (groundDetailUniforms) {
      groundDetailUniforms.uDetailStrength.value = enabled ? 1 : 0;
    }
  };

  let trialTilesVisible = true;
  const setTrialTilesVisible = (visible: boolean) => {
    trialTilesVisible = visible;
    enhancedGround?.setOverrideTilesVisible(visible);
  };

  const setTrackLines = (enabled: boolean) => {
    trackLinesEnabled = enabled;
    if (enabled) ensureTrackLines();
    if (groundDetailUniforms) {
      // Profile-driven ribbons stay above local high-resolution imagery. Keep
      // the symmetric shader only as a fallback for tracks without a profile.
      groundDetailUniforms.uEdgeLines.value = enabled && !edgeLinesGroup ? 1 : 0;
    }
    if (edgeLinesGroup) edgeLinesGroup.visible = enabled;
    if (curbsGroup) curbsGroup.visible = enabled;
  };

  // -- Async satellite + terrain load, in parallel. Terrain is optional: a
  // failed/missing fetch resolves to null (TerrainSampler.load never
  // rejects), and the satellite ground still builds in pure corridor mode.
  // The OSM feature layer builds afterwards so it can reuse the sampler
  // (and still builds, sampler-less, if the satellite ground failed).
  const trackDir = `/data/tracks/${track.trackId ?? "barber"}/`;
  const initialSatVariant: SatVariantId = track.satVariant ?? "default";
  const satFile = satelliteFilename(initialSatVariant);
  const metaUrl = `${trackDir}satellite_meta.json`;
  const referenceStudy = track.visualProfile === "reference";
  const satUrl = `${trackDir}${referenceStudy ? "reference_study/base_4k.webp" : satFile}`;
  const terrainMetaUrl = `${trackDir}terrain_meta.json`;
  const terrainBaseUrl = trackDir;
  const featuresUrl = `${trackDir}features.json`;
  const features3dUrl = `${trackDir}features3d.json`;
  const roadEdgesUrl = `${trackDir}road_edges.json`;
  const overlaysUrl = `${trackDir}overlays.json`;

  // -- Lazy layer loaders (only fetch when the layer is visible) --------------

  /** Fetch + build the OSM surface-feature layer (features.json) once, when its
   * layer is on. Needs the feature drape from the satellite/terrain load; if
   * that is not ready yet this is a no-op and the drape-ready step re-invokes
   * it when featuresVisible. */
  const ensureOsmFeatures = (): void => {
    if (osmFeaturesRequested || disposed || !featureDrape) return;
    osmFeaturesRequested = true;
    const drape = featureDrape;
    void loadFeatures(featuresUrl)
      .then((data) => {
        if (disposed || !data) return;
        featuresGroup = buildFeaturesGroup(data, drape.groundHeightAt, drape.stepAt);
        featuresGroup.visible = featuresVisible;
        group.add(featuresGroup);
      })
      .catch((err) => console.warn("OSM feature layer failed:", err));
  };

  /** Fetch + build the 3D feature layer (features3d.json, ~1 MB) once, when its
   * layer is on. Same drape dependency as ensureOsmFeatures. */
  const ensureFeatures3d = (): void => {
    if (features3dRequested || disposed || !featureDrape) return;
    features3dRequested = true;
    const drape = featureDrape;
    void loadFeatures3d(features3dUrl)
      .then((data3d) => {
        if (disposed || !data3d) return;
        // Front hint: nearest centreline point, so a grandstand faces the
        // track. Linear scan over the (few-hundred-point) centreline is cheap
        // and only runs per landmark building.
        const frontHintAt = (x: number, z: number): { x: number; z: number } => {
          let bestX = points[0].x;
          let bestZ = points[0].z;
          let bestD = Infinity;
          for (const p of points) {
            const dx = p.x - x;
            const dz = p.z - z;
            const d = dx * dx + dz * dz;
            if (d < bestD) {
              bestD = d;
              bestX = p.x;
              bestZ = p.z;
            }
          }
          return { x: bestX, z: bestZ };
        };
        const { group: g, stats } = buildFeatures3dGroup(data3d, drape.groundHeightAt, frontHintAt, track.visualProfile === "reference");
        features3dGroup = g;
        features3dGroup.visible = features3dVisible;
        group.add(features3dGroup);
        logFeatures3dStats(stats);
      })
      .catch((err) => console.warn("3D feature layer failed:", err));
  };

  /** Fetch the vector track-marking data (road_edges.json edge lines +
   * overlays.json curb ribbons) once, when the "Track lines" layer is on.
   * Independent of the satellite/terrain drape, so it can run immediately. A
   * missing file (e.g. Barber) is a silent no-op. */
  const ensureTrackLines = (): void => {
    if (trackLinesRequested || disposed) return;
    trackLinesRequested = true;
    void loadRoadEdgeProfile(roadEdgesUrl)
      .then((roadEdges) => {
        if (disposed || !roadEdges) return;
        edgeLinesGroup = buildRoadEdgeLinesGroup(track, roadEdges);
        edgeLinesGroup.visible = trackLinesEnabled;
        group.add(edgeLinesGroup);
        // Profile-driven ribbons replace the symmetric shader fallback lines.
        if (groundDetailUniforms) groundDetailUniforms.uEdgeLines.value = 0;
      })
      .catch((err) => console.warn("Road edge lines failed:", err));

    const curbCenterline: CurbCenterlinePoint[] = track.centerline.map((p) => ({
      x: p.x,
      z: p.z,
      y: p.y * ELEVATION_SCALE,
      dist: p.dist,
    }));
    void loadOverlays(overlaysUrl)
      .then((overlays) => {
        if (disposed || !overlays || overlays.curbs.length === 0) return;
        curbsGroup = buildCurbsGroup(curbCenterline, overlays.curbs, {
          halfRoadWidth: track.width / 2,
        });
        curbsGroup.visible = trackLinesEnabled;
        group.add(curbsGroup);
      })
      .catch((err) => console.warn("Curb overlay layer failed:", err));
  };

  /** Loads (or reuses the cache for) `variant` and, if it's still the most
   * recently requested one once the load resolves, swaps it onto the live
   * ground material. A later call's texture always wins a race — this one
   * silently drops its own result onto the cache without touching `map`. */
  const loadOrApplySatelliteVariant = (variant: SatVariantId) => {
    const textureVariant = baseSatelliteVariant(variant);
    const cached = satelliteTextureCache.get(textureVariant);
    if (cached) {
      if (satelliteMaterial) {
        satelliteMaterial.map = cached;
        satelliteMaterial.needsUpdate = true;
      }
      return;
    }
    const url = `${trackDir}${satelliteFilename(textureVariant)}`;
    textureLoader.load(
      url,
      (texture) => {
        applySatelliteTextureSettings(texture);
        satelliteTextureCache.set(textureVariant, texture);
        if (variant === latestSatVariant && satelliteMaterial) {
          satelliteMaterial.map = texture;
          satelliteMaterial.needsUpdate = true;
        }
      },
      undefined,
      (err) => console.warn(`Satellite variant "${variant}" failed to load:`, err),
    );
  };

  const setSatelliteVariant = (variant: SatVariantId) => {
    latestSatVariant = variant;
    loadOrApplySatelliteVariant(variant);
    applyEnhancedVisibility();
  };

  let terrainSamplerLive: HeightSampler | null = null;
  const terrainHeightAt = (x: number, z: number): number | null => {
    if (!terrainSamplerLive) return null;
    const { lat, lng } = localToLatLng(x, z, track.origin);
    const alt = terrainSamplerLive.heightAt(lat, lng);
    return alt != null ? (alt - track.origin.alt) * ELEVATION_SCALE : null;
  };

  const satelliteReady = fetch(metaUrl)
    .then((r) => r.json())
    .then(
      (meta: SatelliteMeta) =>
        new Promise<{ meta: SatelliteMeta; texture: THREE.Texture }>((resolve, reject) => {
          textureLoader.load(satUrl, (texture) => resolve({ meta, texture }), undefined, reject);
        }),
    );

  Promise.all([
    satelliteReady,
    TerrainSampler.load(terrainMetaUrl, terrainBaseUrl),
  ])
    .then(([{ meta, texture }, sampler]) => {
      if (disposed) {
        texture.dispose();
        return sampler;
      }
      const { mesh, cache, detailUniforms } = buildSatelliteGround(track, meta, texture, sampler);
      // Remove fallback, add satellite
      group.remove(fallback);
      fallback.geometry.dispose();
      group.add(mesh);
      ground = { geometry: mesh.geometry, cache, scratchY: new Float32Array(cache.roadY.length) };
      groundDetailUniforms = detailUniforms;
      groundDetailUniforms.uDetailStrength.value = detailTextureEnabled ? 1 : 0;
      // The road_edges.json edge lines load lazily via ensureTrackLines (only
      // when the Track-lines layer is on); until/unless they arrive, the
      // symmetric shader fallback carries the edge lines when the layer is on.
      groundDetailUniforms.uEdgeLines.value = trackLinesEnabled && !edgeLinesGroup ? 1 : 0;
      if (!roadVisible) applyGroundClearance();

      // Wire up variant switching onto the material this ground just built,
      // caching the texture that was already loaded to build it.
      satelliteMaterial = mesh.material as THREE.MeshStandardMaterial;
      satelliteTextureCache.set(baseSatelliteVariant(initialSatVariant), texture);
      // A setSatelliteVariant() call could have arrived while this initial
      // load was still in flight; apply whatever is currently the latest
      // request now that the material exists (a no-op if nothing changed).
      if (latestSatVariant !== initialSatVariant) {
        loadOrApplySatelliteVariant(latestSatVariant);
      }

      enhancedGround = new EnhancedCorridorGround({
        manifestUrl: `${trackDir}${referenceStudy ? "reference_study" : "satellite_corridor_x2"}/manifest.json`,
        overrideManifestUrl: referenceStudy ? undefined : `${trackDir}imagegen_trials/manifest.json`,
        track,
        drapeInputs: (x, z, lat, lng) => computeDrapeInputs(track, x, z, lat, lng, sampler),
        // SR tiles cover exactly the corridor the chase camera looks at, so
        // they need the same close-range material response as the base ground
        // (photo-derived zone mask — tiles have no baked corridor attributes).
        detailUniforms,
      });
      group.add(enhancedGround.group);
      enhancedGround.setRoadVisible(roadVisible);
      enhancedGround.setOverrideTilesVisible(trialTilesVisible);
      applyEnhancedVisibility();
      return sampler;
    })
    .catch((err): TerrainSampler | null => {
      console.warn("Satellite ground load failed:", err);
      return null;
    })
    .then((sampler) => {
      terrainSamplerLive = sampler;
      // One drape shared by the flat OSM polygons and the 3D features, so both
      // sit on the exact post-drape ground the satellite photo uses. Store it
      // so the lazy loaders can build their layers on demand, then kick off
      // any feature layer whose toggle is already on.
      featureDrape = createFeatureDrape(track, sampler);
      if (featuresVisible) ensureOsmFeatures();
      if (features3dVisible) ensureFeatures3d();
      if (track.visualProfile === "reference") {
        void loadRoadEdgeProfile(roadEdgesUrl).then(profile => {
          if (disposed || !profile) return;
          group.add(buildFujiReferenceRoad(track, profile, satelliteMaterial?.map ?? undefined));
        }).catch(err => console.warn("Reference road failed:", err));
      }
    })
    .catch((err) => console.warn("Feature drape setup failed:", err));

  // Track lines (road_edges.json edge lines + overlays.json curbs) load lazily
  // via ensureTrackLines the first time the "Track lines" layer is switched on.
  // ReplayScene calls setTrackLines() with the store's initial value right after
  // load(), so developer mode (Track-lines on) still fetches at startup while
  // user mode (off) skips both files entirely.

  return {
    group,
    centerline3D: points,
    setRoadVisible,
    setFeaturesVisible,
    setFeatures3dVisible,
    setDetailTexture,
    setSatelliteVariant,
    updateGround,
    setTrackLines,
    setTrialTilesVisible,
    terrainHeightAt,
    dispose: () => {
      disposed = true;
      group.getObjectByName("fuji-reference-road")?.userData.dispose?.();
      enhancedGround?.dispose();
      enhancedGround = null;
    },
  };
}

/** Log the 3D-feature layer's measured draw calls / vertices so the P3b
 * draw-call budget (≤10 added) can be verified from the console. */
function logFeatures3dStats(stats: Features3DStats): void {
  console.log(
    `[Features3D] drawCalls=${stats.drawCalls} vertices=${stats.vertices} ` +
      `trees=${stats.trees} buildings=${stats.buildings} barriers=${stats.barriers}`,
  );
}
