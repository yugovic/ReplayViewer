/**
 * AcOverlay – a verification-reference layer that draws an Assetto Corsa track
 * MOD's road edges over the reconstructed track, so the two geometries can be
 * eyeballed in-scene, and (optionally) lets the car drive on the AC surface.
 *
 * The data (`public/data/tracks/<id>/ac_overlay.json`) is produced offline: the
 * MOD's `side_l.csv` / `side_r.csv` edges are rigid-aligned (ICP) to this
 * viewer's centerline and baked into viewer-local coordinates, so the loader
 * here is deliberately dumb — it just draws points it is handed. Only tracks
 * that ship the file get an overlay; everything else silently has none.
 *
 * The baked points carry a small uniform vertical LIFT so the visual ribbon
 * floats just above the reconstruction road (avoids z-fighting). For driving we
 * need the TRUE surface, so we build a second, invisible "drivable" mesh with
 * the lift removed and raycast against it in heightAt().
 */

import * as THREE from "three";

interface AcOverlayData {
  trackId: string;
  left: [number, number, number][];
  right: [number, number, number][];
  center: [number, number, number][];
  /** Uniform vertical lift (m) baked into the points by the offline generator.
   * Optional for forward-compat; falls back to BAKED_LIFT_M. */
  lift?: number;
}

export interface AcOverlayHandle {
  group: THREE.Group;
  setVisible: (visible: boolean) => void;
  /** True AC surface height (viewer-local Y) under (x,z), or null if the point
   * is off the AC ribbon. Works whether or not the visual overlay is shown. */
  heightAt: (x: number, z: number) => number | null;
  dispose: () => void;
}

/** AC series amber, matched to the verification report's palette. */
const AC_COLOR = 0xc07820;

/** Vertical lift (m) the offline generator (build_overlay.py, `LIFT`) bakes into
 * every point. Used to recover the true surface when the JSON omits `lift`. */
const BAKED_LIFT_M = 0.35;

// Hot-path scratch for heightAt() raycasting — reused every frame, no allocs.
const HEIGHT_RAY = new THREE.Raycaster();
const RAY_ORIGIN = new THREE.Vector3();
const RAY_DOWN = new THREE.Vector3(0, -1, 0);
const RAY_HITS: THREE.Intersection[] = [];

function toPoints(rows: [number, number, number][]): THREE.Vector3[] {
  return rows.map(([x, y, z]) => new THREE.Vector3(x, y, z));
}

function edgeLine(points: THREE.Vector3[], material: THREE.Material): THREE.Line {
  const geom = new THREE.BufferGeometry().setFromPoints(points);
  return new THREE.Line(geom, material);
}

/** Interleaved left/right ribbon positions + triangle-strip indices. `dy` is
 * added to every Y (0 for the visual ribbon, -lift for the drivable surface). */
function ribbonGeometry(
  left: THREE.Vector3[],
  right: THREE.Vector3[],
  dy: number,
): THREE.BufferGeometry {
  const n = Math.min(left.length, right.length);
  const positions = new Float32Array(n * 2 * 3);
  for (let i = 0; i < n; i++) {
    positions.set([left[i].x, left[i].y + dy, left[i].z], i * 6);
    positions.set([right[i].x, right[i].y + dy, right[i].z], i * 6 + 3);
  }
  const indices: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    const a = i * 2, b = i * 2 + 1, c = (i + 1) * 2, d = (i + 1) * 2 + 1;
    indices.push(a, b, c, b, d, c);
  }
  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geom.setIndex(indices);
  return geom;
}

/**
 * Builds the overlay group: a translucent ribbon between the two edges plus a
 * bright line on each edge. Rendered after the ground with depthWrite off so it
 * reads as an overlay rather than fighting the road for the depth buffer.
 */
export function buildAcOverlay(data: AcOverlayData): AcOverlayHandle {
  const group = new THREE.Group();
  group.name = "ac-overlay";
  group.renderOrder = 10;

  const left = toPoints(data.left);
  const right = toPoints(data.right);
  const lift = data.lift ?? BAKED_LIFT_M;

  // Visual ribbon (translucent fill showing the AC footprint), as baked.
  const visGeom = ribbonGeometry(left, right, 0);
  const ribbonMat = new THREE.MeshBasicMaterial({
    color: AC_COLOR,
    transparent: true,
    opacity: 0.22,
    side: THREE.DoubleSide,
    depthWrite: false,
  });
  const ribbon = new THREE.Mesh(visGeom, ribbonMat);
  ribbon.renderOrder = 10;
  group.add(ribbon);

  // Edge lines — brighter, so the AC track limits read clearly.
  const lineMat = new THREE.LineBasicMaterial({
    color: AC_COLOR,
    transparent: true,
    opacity: 0.95,
    depthTest: true,
    depthWrite: false,
  });
  const leftLine = edgeLine(left, lineMat);
  const rightLine = edgeLine(right, lineMat);
  leftLine.renderOrder = 11;
  rightLine.renderOrder = 11;
  group.add(leftLine, rightLine);

  // Invisible drivable surface at TRUE AC elevation (lift removed). Kept out of
  // the scene graph so it never renders, but is still raycastable by heightAt().
  const surfGeom = ribbonGeometry(left, right, -lift);
  surfGeom.computeBoundingBox();
  // DoubleSide so the downward height ray hits regardless of triangle winding.
  const surfMat = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide });
  const surfMesh = new THREE.Mesh(surfGeom, surfMat);
  surfMesh.visible = false;
  surfMesh.updateMatrixWorld();
  const rayTop = (surfGeom.boundingBox?.max.y ?? 0) + 5;

  const heightAt = (x: number, z: number): number | null => {
    RAY_ORIGIN.set(x, rayTop, z);
    HEIGHT_RAY.set(RAY_ORIGIN, RAY_DOWN);
    RAY_HITS.length = 0;
    HEIGHT_RAY.intersectObject(surfMesh, false, RAY_HITS);
    return RAY_HITS.length > 0 ? RAY_HITS[0].point.y : null;
  };

  const setVisible = (visible: boolean) => {
    group.visible = visible;
  };

  const dispose = () => {
    visGeom.dispose();
    ribbonMat.dispose();
    leftLine.geometry.dispose();
    rightLine.geometry.dispose();
    lineMat.dispose();
    surfGeom.dispose();
    surfMat.dispose();
  };

  return { group, setVisible, heightAt, dispose };
}

/**
 * Fetches and builds the overlay for a track id. Resolves to null when the
 * track ships no `ac_overlay.json` (the common case) or the fetch fails — the
 * caller treats "no overlay" as normal, not an error.
 */
export async function loadAcOverlay(trackId: string): Promise<AcOverlayHandle | null> {
  try {
    const res = await fetch(`/data/tracks/${trackId}/ac_overlay.json`);
    if (!res.ok) return null;
    const data = (await res.json()) as AcOverlayData;
    if (!data.left?.length || !data.right?.length) return null;
    return buildAcOverlay(data);
  } catch {
    return null;
  }
}
