/**
 * Curb ribbon geometry + material (Task 2b).
 *
 * Given the track centerline (positions with cumulative arc-length + elevation)
 * and a CurbSpan (see overlays.ts), this builds a thin ribbon mesh that hugs
 * one road edge over the span, striped red/white. It follows the same
 * conventions as TrackBuilder's road mesh:
 *   - normal = (-tangent.z, 0, tangent.x); the +normal side is the driver's
 *     RIGHT (positive signed lateral — see projectPointToCenterline).
 *   - "left"/"right" in the span are relative to INCREASING centerline distance.
 * The ribbon's inner edge sits just inside the paved edge (halfRoadWidth minus a
 * small overlap) so there's no gap, and extends outward by `span.width`.
 *
 * The geometry core (buildCurbRibbonGeometry) is pure — no THREE, no DOM — so
 * vertex counts, side orientation, span clamping and stripe arc-length are unit
 * tested (curbBuilder.test.ts). buildCurbsGroup is the THREE wrapper.
 */

import * as THREE from "three";
import type { CurbSpan } from "./overlays";

/** Minimal centerline point the ribbon core needs. */
export interface CurbCenterlinePoint {
  x: number;
  z: number;
  y: number;
  dist: number;
}

export interface CurbGeometryData {
  /** Flat xyz triplets, two rows (inner, outer) per sampled station. */
  positions: Float32Array;
  /** UVs: u = 0 inner / 1 outer, v = station arc-length in metres. */
  uvs: Float32Array;
  /** Per-vertex arc-length (m) along the span, for shader stripe phase. */
  arcLength: Float32Array;
  indices: number[];
  /** Number of stations sampled along the span (vertexCount = 2 * stations). */
  stations: number;
}

export interface CurbRibbonOptions {
  /** Half the paved road width (m); the inner edge starts here. */
  halfRoadWidth: number;
  /** How far the inner edge tucks under the paved edge (m). */
  overlap?: number;
  /** Station spacing along the span (m). */
  step?: number;
  /** Vertical offset added above the sampled centerline elevation (m). */
  yOffset?: number;
}

/** Interpolate centerline (x, z, y) and unit tangent at arc-length `dist`. */
function sampleCenterline(
  points: CurbCenterlinePoint[],
  dist: number,
): { x: number; z: number; y: number; tx: number; tz: number } {
  const n = points.length;
  // Clamp to the centerline's covered range.
  if (dist <= points[0].dist) {
    const a = points[0];
    const b = points[Math.min(1, n - 1)];
    const len = Math.hypot(b.x - a.x, b.z - a.z) || 1;
    return { x: a.x, z: a.z, y: a.y, tx: (b.x - a.x) / len, tz: (b.z - a.z) / len };
  }
  const last = points[n - 1];
  if (dist >= last.dist) {
    const a = points[Math.max(0, n - 2)];
    const len = Math.hypot(last.x - a.x, last.z - a.z) || 1;
    return { x: last.x, z: last.z, y: last.y, tx: (last.x - a.x) / len, tz: (last.z - a.z) / len };
  }
  for (let i = 0; i < n - 1; i++) {
    const a = points[i];
    const b = points[i + 1];
    if (a.dist <= dist && dist <= b.dist) {
      const span = b.dist - a.dist;
      const t = span > 0 ? (dist - a.dist) / span : 0;
      const len = Math.hypot(b.x - a.x, b.z - a.z) || 1;
      return {
        x: a.x + (b.x - a.x) * t,
        z: a.z + (b.z - a.z) * t,
        y: a.y + (b.y - a.y) * t,
        tx: (b.x - a.x) / len,
        tz: (b.z - a.z) / len,
      };
    }
  }
  return { x: last.x, z: last.z, y: last.y, tx: 1, tz: 0 };
}

/**
 * Builds the ribbon geometry for one curb span. Samples the span every ~`step`
 * metres (endpoints inclusive, so stations are clamped to [startDist, endDist]),
 * placing an inner and outer vertex row along the chosen road edge.
 */
export function buildCurbRibbonGeometry(
  points: CurbCenterlinePoint[],
  span: CurbSpan,
  options: CurbRibbonOptions,
): CurbGeometryData {
  const { halfRoadWidth } = options;
  const overlap = options.overlap ?? 0.1;
  const step = options.step ?? 1.5;
  const yOffset = options.yOffset ?? 0.03;

  const start = span.startDist;
  const end = span.endDist;
  const sideSign = span.side === "right" ? 1 : -1;
  const innerOffset = halfRoadWidth - overlap;
  const outerOffset = innerOffset + span.width;

  const stations = Math.max(2, Math.ceil((end - start) / step) + 1);
  const positions = new Float32Array(stations * 2 * 3);
  const uvs = new Float32Array(stations * 2 * 2);
  const arcLength = new Float32Array(stations * 2);
  const indices: number[] = [];

  for (let s = 0; s < stations; s++) {
    const t = stations > 1 ? s / (stations - 1) : 0;
    const dist = start + (end - start) * t;
    const c = sampleCenterline(points, dist);
    // normal = (-tz, 0, tx); +normal = driver's right.
    const nx = -c.tz;
    const nz = c.tx;
    const innerX = c.x + nx * sideSign * innerOffset;
    const innerZ = c.z + nz * sideSign * innerOffset;
    const outerX = c.x + nx * sideSign * outerOffset;
    const outerZ = c.z + nz * sideSign * outerOffset;
    const y = c.y + yOffset;

    const vi = s * 2; // inner vertex index
    const vo = vi + 1; // outer vertex index
    positions[vi * 3] = innerX;
    positions[vi * 3 + 1] = y;
    positions[vi * 3 + 2] = innerZ;
    positions[vo * 3] = outerX;
    positions[vo * 3 + 1] = y;
    positions[vo * 3 + 2] = outerZ;

    uvs[vi * 2] = 0;
    uvs[vi * 2 + 1] = dist;
    uvs[vo * 2] = 1;
    uvs[vo * 2 + 1] = dist;

    arcLength[vi] = dist;
    arcLength[vo] = dist;

    if (s < stations - 1) {
      const a = vi;
      const b = vo;
      const c2 = vi + 2;
      const d = vi + 3;
      // Winding chosen so the ribbon faces +Y (up) for a right-side curb; the
      // left side mirrors across the centerline, which flips handedness, so we
      // reverse winding there to keep the visible face upward.
      if (sideSign > 0) {
        indices.push(a, b, c2, b, d, c2);
      } else {
        indices.push(a, c2, b, b, c2, d);
      }
    }
  }

  return { positions, uvs, arcLength, indices, stations };
}

// ─── THREE wrapper ───────────────────────────────────────────────────────────

/** Uniforms kept live so the curb stripe look is stable and (optionally) tunable. */
interface CurbUniforms {
  uStripeLength: { value: number };
}

/**
 * Red/white striped MeshStandardMaterial. Stripes run along the ribbon's
 * arc-length (baked as the `curbArc` attribute) with a data-driven period; the
 * boundaries are anti-aliased with fwidth() so they don't shimmer at distance,
 * and the white stripes get a touch less roughness so they catch light.
 */
function buildCurbMaterial(stripeLength: number): THREE.MeshStandardMaterial {
  const mat = new THREE.MeshStandardMaterial({
    color: 0xc01515,
    roughness: 0.6,
    metalness: 0.0,
    // DoubleSide so a mirrored (left-side) ribbon can never be backface-culled;
    // three flips the shading normal per gl_FrontFacing so lighting stays right.
    side: THREE.DoubleSide,
    // Sit on top of the satellite ground (polygonOffsetFactor 4) without
    // z-fighting: pull curbs toward the camera.
    polygonOffset: true,
    polygonOffsetFactor: -2,
    polygonOffsetUnits: -2,
  });
  const uniforms: CurbUniforms = { uStripeLength: { value: stripeLength } };

  mat.onBeforeCompile = (shader) => {
    shader.uniforms.uStripeLength = uniforms.uStripeLength;

    shader.vertexShader = shader.vertexShader
      .replace(
        "#include <common>",
        `#include <common>
attribute float curbArc;
varying float vCurbArc;`,
      )
      .replace(
        "#include <begin_vertex>",
        `#include <begin_vertex>
vCurbArc = curbArc;`,
      );

    shader.fragmentShader = shader.fragmentShader
      .replace(
        "#include <common>",
        `#include <common>
uniform float uStripeLength;
varying float vCurbArc;`,
      )
      .replace(
        "#include <map_fragment>",
        `#include <map_fragment>
{
  float phase = vCurbArc / max( uStripeLength, 0.001 );
  float f = fract( phase );
  float aa = clamp( fwidth( phase ) * 0.55, 0.0005, 0.055 );
  // redness = 1 in the second half of each stripe period, 0 in the first
  // half; both boundaries (0.5 and the 1.0/0.0 wrap) anti-aliased.
  float redness = smoothstep( 0.5 - aa, 0.5 + aa, f ) - smoothstep( 1.0 - aa, 1.0 + aa, f );
  vec3 whiteCol = vec3( 0.86 );
  vec3 redCol = vec3( 0.62, 0.035, 0.035 );
  diffuseColor.rgb = mix( whiteCol, redCol, redness );
  vCurbRedness = redness;
}`,
      )
      .replace(
        "#include <roughnessmap_fragment>",
        `#include <roughnessmap_fragment>
roughnessFactor *= mix( 0.85, 1.05, vCurbRedness );`,
      );

    // vCurbRedness must be declared for both stripe color and roughness tweak.
    shader.fragmentShader = shader.fragmentShader.replace(
      "varying float vCurbArc;",
      `varying float vCurbArc;
float vCurbRedness;`,
    );
  };

  mat.customProgramCacheKey = () => "curb-stripe-v1";
  return mat;
}

/**
 * Builds a THREE.Group of striped curb ribbons for every span in `spans`.
 * Returns an empty group when there are no spans (harmless to add to the scene).
 */
export function buildCurbsGroup(
  centerline: CurbCenterlinePoint[],
  spans: CurbSpan[],
  ribbonOptions: CurbRibbonOptions,
): THREE.Group {
  const group = new THREE.Group();
  group.name = "curbs";
  if (centerline.length < 2) return group;

  for (const span of spans) {
    const geoData = buildCurbRibbonGeometry(centerline, span, ribbonOptions);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(geoData.positions, 3));
    geometry.setAttribute("uv", new THREE.Float32BufferAttribute(geoData.uvs, 2));
    geometry.setAttribute("curbArc", new THREE.Float32BufferAttribute(geoData.arcLength, 1));
    geometry.setIndex(geoData.indices);
    geometry.computeVertexNormals();

    const mesh = new THREE.Mesh(geometry, buildCurbMaterial(span.stripeLength));
    mesh.receiveShadow = true;
    mesh.castShadow = false;
    mesh.renderOrder = 1;
    group.add(mesh);
  }
  return group;
}
