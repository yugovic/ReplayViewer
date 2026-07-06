/**
 * DataTrail – glowing ribbon showing the driven path coloured by speed/brake.
 *
 * Implemented as a triangle-strip ribbon mesh (width ~1.2 m) with per-vertex
 * colours, AdditiveBlending, and toneMapped:false so the Bloom pass picks it up.
 *
 * WebGL linewidth > 1 is not supported on most drivers, so THREE.Line is
 * replaced with a proper mesh ribbon.
 */

import * as THREE from "three";
import { trailColor, rgbToHex } from "./trailColor";
import type { LapData, TrackData } from "../replay/types";
import { sampleReplay } from "../replay/interpolation";

const TRAIL_SAMPLES = 600; // number of centre-line samples
const RIBBON_HALF_WIDTH = 0.27; // half-width of the ribbon in metres (total 0.54 m)
const RIBBON_Y_OFFSET = 0.025; // height above road surface (2.5 cm)

/**
 * Build positions/colors for ribbon geometry from an array of centre-line points
 * and their colour values. Returns flat arrays for a triangle-strip (pairs of
 * left/right vertices per sample).
 */
function buildRibbonArrays(
  centerPoints: Array<{ x: number; y: number; z: number; r: number; g: number; b: number }>,
): { positions: Float32Array; colors: Float32Array; indices: Uint32Array } {
  const n = centerPoints.length;
  // Two vertices per sample (left + right edge)
  const positions = new Float32Array(n * 2 * 3);
  const colors = new Float32Array(n * 2 * 3);
  const indices: number[] = [];

  for (let i = 0; i < n; i++) {
    const cur = centerPoints[i];
    const prev = centerPoints[Math.max(0, i - 1)];
    const next = centerPoints[Math.min(n - 1, i + 1)];

    // Tangent along XZ plane
    const tx = next.x - prev.x;
    const tz = next.z - prev.z;
    const len = Math.sqrt(tx * tx + tz * tz);
    const nx = len > 0 ? -tz / len : 0;
    const nz = len > 0 ? tx / len : 1;

    // Left vertex
    const li = (i * 2) * 3;
    positions[li]     = cur.x + nx * RIBBON_HALF_WIDTH;
    positions[li + 1] = cur.y;
    positions[li + 2] = cur.z + nz * RIBBON_HALF_WIDTH;
    colors[li]     = cur.r;
    colors[li + 1] = cur.g;
    colors[li + 2] = cur.b;

    // Right vertex
    const ri = (i * 2 + 1) * 3;
    positions[ri]     = cur.x - nx * RIBBON_HALF_WIDTH;
    positions[ri + 1] = cur.y;
    positions[ri + 2] = cur.z - nz * RIBBON_HALF_WIDTH;
    colors[ri]     = cur.r;
    colors[ri + 1] = cur.g;
    colors[ri + 2] = cur.b;

    // Two triangles per segment (after first vertex pair)
    if (i > 0) {
      const a = (i - 1) * 2;
      const b = i * 2;
      indices.push(a, a + 1, b, a + 1, b + 1, b);
    }
  }

  return { positions, colors, indices: new Uint32Array(indices) };
}

function buildRibbonMaterial(): THREE.MeshBasicMaterial {
  return new THREE.MeshBasicMaterial({
    vertexColors: true,
    side: THREE.DoubleSide,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    transparent: true,
    opacity: 0.5,   // subdue overall brightness; brake-red still glows via Bloom
    toneMapped: false,
  });
}

/**
 * Pre-compute the full-lap trail geometry once when the lap is loaded.
 * Returns a group containing a ribbon mesh (triangle strip).
 */
export function buildDataTrail(lap: LapData, track: TrackData): THREE.Group {
  const group = new THREE.Group();
  group.name = "data-trail";

  const duration = lap.t.length > 0 ? lap.t[lap.t.length - 1] : 0;
  if (duration <= 0) return group;

  const centerPoints: Array<{ x: number; y: number; z: number; r: number; g: number; b: number }> = [];

  for (let i = 0; i <= TRAIL_SAMPLES; i++) {
    const t = (i / TRAIL_SAMPLES) * duration;
    const sample = sampleReplay(lap, track, t);
    const rgb = trailColor({
      speedKmh: sample.telemetry.speed,
      brake: sample.telemetry.brake,
    });
    centerPoints.push({
      x: sample.x,
      y: sample.y + RIBBON_Y_OFFSET,
      z: sample.z,
      r: rgb.r,
      g: rgb.g,
      b: rgb.b,
    });
  }

  const { positions, colors, indices } = buildRibbonArrays(centerPoints);

  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  geo.setIndex(new THREE.BufferAttribute(indices, 1));

  const ribbon = new THREE.Mesh(geo, buildRibbonMaterial());
  ribbon.frustumCulled = false;
  ribbon.renderOrder = 1;
  group.add(ribbon);

  return group;
}

/**
 * Build a "progress" trail that shows only up to a given time.
 * Call this once and update the draw range each frame.
 *
 * The ribbon uses indexed geometry; the draw range is over *indices*.
 */
export class LiveDataTrail {
  readonly group: THREE.Group;
  private readonly ribbon: THREE.Mesh;
  private readonly totalSamples: number;
  private readonly times: Float32Array;
  /** number of triangle indices per segment (2 triangles × 3 indices = 6) */
  private static readonly INDICES_PER_SEG = 6;

  constructor(lap: LapData, track: TrackData) {
    this.group = new THREE.Group();
    this.group.name = "live-trail";

    const duration = lap.t.length > 0 ? lap.t[lap.t.length - 1] : 0;
    this.totalSamples = TRAIL_SAMPLES + 1;
    this.times = new Float32Array(this.totalSamples);

    const centerPoints: Array<{ x: number; y: number; z: number; r: number; g: number; b: number }> = [];

    for (let i = 0; i < this.totalSamples; i++) {
      const t = (i / TRAIL_SAMPLES) * duration;
      this.times[i] = t;
      const sample = sampleReplay(lap, track, t);
      const rgb = trailColor({
        speedKmh: sample.telemetry.speed,
        brake: sample.telemetry.brake,
      });
      centerPoints.push({
        x: sample.x,
        y: sample.y + RIBBON_Y_OFFSET,
        z: sample.z,
        r: rgb.r,
        g: rgb.g,
        b: rgb.b,
      });
    }

    const { positions, colors, indices } = buildRibbonArrays(centerPoints);

    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    geo.setIndex(new THREE.BufferAttribute(indices, 1));

    this.ribbon = new THREE.Mesh(geo, buildRibbonMaterial());
    this.ribbon.frustumCulled = false;
    this.ribbon.renderOrder = 1;
    this.group.add(this.ribbon);
  }

  /** Update draw range so only the portion up to `currentTime` is visible */
  setTime(currentTime: number): void {
    let sampleCount = 0;
    for (let i = 0; i < this.totalSamples; i++) {
      if (this.times[i] <= currentTime) sampleCount = i + 1;
      else break;
    }
    // Each additional sample beyond the first adds one segment = 6 indices
    const segments = Math.max(0, sampleCount - 1);
    const indexCount = segments * LiveDataTrail.INDICES_PER_SEG;
    this.ribbon.geometry.setDrawRange(0, Math.max(0, indexCount));
  }

  dispose(): void {
    this.ribbon.geometry.dispose();
    (this.ribbon.material as THREE.Material).dispose();
  }
}

// Re-export for use in tests
export { trailColor, rgbToHex };
