import * as THREE from "three";
import type { TrackData } from "../../replay/types";
import { sampleTrackSurface } from "../../replay/interpolation";

export interface RoadEdgeProfile {
  version: 1;
  stepMeters: number;
  totalLength: number;
  left: number[];
  right: number[];
  interpretation?: "track-line-center" | "paved-edge";
  lineInsetMeters?: number;
  source?: string;
}

export interface RoadEdgeOffsets {
  left: number;
  right: number;
  lineInset: number;
}

function finitePositiveArray(value: unknown): value is number[] {
  return Array.isArray(value) && value.length >= 2 && value.every(
    (item) => typeof item === "number" && Number.isFinite(item) && item > 0 && item < 100,
  );
}

export function isRoadEdgeProfile(value: unknown): value is RoadEdgeProfile {
  if (!value || typeof value !== "object") return false;
  const profile = value as Partial<RoadEdgeProfile>;
  return (
    profile.version === 1 &&
    typeof profile.stepMeters === "number" && Number.isFinite(profile.stepMeters) && profile.stepMeters > 0 &&
    typeof profile.totalLength === "number" && Number.isFinite(profile.totalLength) && profile.totalLength > 0 &&
    finitePositiveArray(profile.left) && finitePositiveArray(profile.right) &&
    profile.left.length === profile.right.length &&
    (profile.lineInsetMeters === undefined ||
      (Number.isFinite(profile.lineInsetMeters) && profile.lineInsetMeters >= 0 && profile.lineInsetMeters < 5))
  );
}

export async function loadRoadEdgeProfile(url: string): Promise<RoadEdgeProfile | null> {
  try {
    const response = await fetch(url);
    if (!response.ok) return null;
    const value: unknown = await response.json();
    return isRoadEdgeProfile(value) ? value : null;
  } catch {
    return null;
  }
}

export function sampleRoadEdgeOffsets(profile: RoadEdgeProfile, distance: number): RoadEdgeOffsets {
  const wrapped = ((distance % profile.totalLength) + profile.totalLength) % profile.totalLength;
  const position = wrapped / profile.stepMeters;
  const i0 = Math.floor(position) % profile.left.length;
  const i1 = (i0 + 1) % profile.left.length;
  const t = position - Math.floor(position);
  const lerp = (values: number[]) => values[i0] + (values[i1] - values[i0]) * t;
  return {
    left: lerp(profile.left),
    right: lerp(profile.right),
    lineInset: profile.lineInsetMeters ?? (profile.interpretation === "track-line-center" ? 0 : 0.45),
  };
}

interface XzPoint { x: number; z: number }
interface LinePoint extends XzPoint { y: number }

function sampleCenterlineXZ(track: TrackData, distance: number): XzPoint {
  const points = track.centerline;
  if (points.length === 0) return { x: 0, z: 0 };
  const d = ((distance % track.totalLength) + track.totalLength) % track.totalLength;
  let lo = 0;
  let hi = points.length - 1;
  while (lo + 1 < hi) {
    const mid = (lo + hi) >>> 1;
    if (points[mid].dist <= d) lo = mid;
    else hi = mid;
  }
  const a = points[lo];
  const b = points[hi] ?? points[0];
  const span = b.dist - a.dist;
  const t = span > 0 ? (d - a.dist) / span : 0;
  return { x: a.x + (b.x - a.x) * t, z: a.z + (b.z - a.z) * t };
}

function buildLinePoints(
  track: TrackData,
  profile: RoadEdgeProfile,
  side: "left" | "right",
  sampleStep: number,
  surfaceOffset: number,
): LinePoint[] {
  const count = Math.max(3, Math.ceil(track.totalLength / sampleStep));
  const actualStep = track.totalLength / count;
  const result: LinePoint[] = [];
  for (let i = 0; i < count; i += 1) {
    const distance = i * actualStep;
    const center = sampleCenterlineXZ(track, distance);
    const before = sampleCenterlineXZ(track, distance - 1);
    const after = sampleCenterlineXZ(track, distance + 1);
    const length = Math.hypot(after.x - before.x, after.z - before.z) || 1;
    const tx = (after.x - before.x) / length;
    const tz = (after.z - before.z) / length;
    const offsets = sampleRoadEdgeOffsets(profile, distance);
    const rawOffset = side === "left" ? offsets.left : offsets.right;
    const offset = Math.max(0, rawOffset - offsets.lineInset);
    const direction = side === "left" ? 1 : -1;
    const signedLateral = side === "left" ? -offset : offset;
    result.push({
      x: center.x + tz * offset * direction,
      z: center.z - tx * offset * direction,
      y: sampleTrackSurface(track, distance, signedLateral) + surfaceOffset,
    });
  }
  return result;
}

function buildRibbon(points: LinePoint[], width: number): THREE.BufferGeometry {
  const positions = new Float32Array(points.length * 6);
  const indices: number[] = [];
  const half = width / 2;
  for (let i = 0; i < points.length; i += 1) {
    const previous = points[(i - 1 + points.length) % points.length];
    const next = points[(i + 1) % points.length];
    const length = Math.hypot(next.x - previous.x, next.z - previous.z) || 1;
    const nx = -(next.z - previous.z) / length;
    const nz = (next.x - previous.x) / length;
    const point = points[i];
    positions.set([point.x + nx * half, point.y, point.z + nz * half], i * 6);
    positions.set([point.x - nx * half, point.y, point.z - nz * half], i * 6 + 3);
    const nextIndex = (i + 1) % points.length;
    const a = i * 2;
    const b = a + 1;
    const c = nextIndex * 2;
    const d = c + 1;
    indices.push(a, b, c, b, d, c);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  geometry.computeBoundingSphere();
  return geometry;
}

export interface RoadEdgeLineOptions {
  width?: number;
  sampleStep?: number;
  surfaceOffset?: number;
  opacity?: number;
}

/** Builds crisp vector ribbons without asking SR to invent paint geometry. */
export function buildRoadEdgeLinesGroup(
  track: TrackData,
  profile: RoadEdgeProfile,
  options: RoadEdgeLineOptions = {},
): THREE.Group {
  const width = options.width ?? 0.2;
  const sampleStep = options.sampleStep ?? Math.min(profile.stepMeters, 2);
  const surfaceOffset = options.surfaceOffset ?? 0.055;
  const opacity = options.opacity ?? 0.88;
  const group = new THREE.Group();
  group.name = "track-edge-lines";
  for (const side of ["left", "right"] as const) {
    const geometry = buildRibbon(buildLinePoints(track, profile, side, sampleStep, surfaceOffset), width);
    const material = new THREE.MeshBasicMaterial({
      color: 0xf4f2e8,
      transparent: opacity < 1,
      opacity,
      depthWrite: false,
      side: THREE.DoubleSide,
      polygonOffset: true,
      polygonOffsetFactor: -4,
      polygonOffsetUnits: -4,
    });
    const mesh = new THREE.Mesh(geometry, material);
    mesh.name = `track-edge-line-${side}`;
    mesh.renderOrder = 8;
    group.add(mesh);
  }
  return group;
}
