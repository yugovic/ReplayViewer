/**
 * AcTrackMesh – the REAL Assetto Corsa MOD road geometry (extracted from the
 * .kn5 and baked into viewer-local coordinates), loaded as an actual 3D surface
 * the car can drive on.
 *
 * Data: `public/data/tracks/<id>/road_ac_local.glb` — road + kerb meshes merged,
 * already transformed into this viewer's frame by the offline kn5 extractor
 * (kn5.mjs + the ICP transform from build_overlay.py). The ICP alignment
 * includes a reflection, so triangle winding is flipped; we render/raycast with
 * DoubleSide so both lighting and the downward height ray behave.
 */

import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

export interface AcTrackMeshHandle {
  group: THREE.Group;
  setVisible: (visible: boolean) => void;
  /** True MOD surface height (viewer-local Y) under (x,z), or null if the point
   * is not over the extracted road geometry. */
  heightAt: (x: number, z: number) => number | null;
  dispose: () => void;
}

const AC_COLOR = 0xc07820;

// Hot-path scratch for heightAt() — reused every frame, no allocations.
const HEIGHT_RAY = new THREE.Raycaster();
const RAY_ORIGIN = new THREE.Vector3();
const RAY_DOWN = new THREE.Vector3(0, -1, 0);
const RAY_HITS: THREE.Intersection[] = [];

function buildHandle(gltf: { scene: THREE.Group }): AcTrackMeshHandle {
  const group = gltf.scene;
  group.name = "ac-track-mesh";

  const material = new THREE.MeshStandardMaterial({
    color: AC_COLOR,
    roughness: 0.9,
    metalness: 0.0,
    side: THREE.DoubleSide, // reflection-flipped winding + downward raycast
    flatShading: true,
  });

  const meshes: THREE.Mesh[] = [];
  group.traverse((o) => {
    if ((o as THREE.Mesh).isMesh) {
      const m = o as THREE.Mesh;
      m.material = material;
      m.renderOrder = 9; // under the thin overlay lines (renderOrder 11)
      meshes.push(m);
    }
  });
  group.updateMatrixWorld(true);

  // Ray origin height: above the mesh's top. Compute from a bounding box.
  const bbox = new THREE.Box3().setFromObject(group);
  const rayTop = bbox.max.y + 10;

  const heightAt = (x: number, z: number): number | null => {
    RAY_ORIGIN.set(x, rayTop, z);
    HEIGHT_RAY.set(RAY_ORIGIN, RAY_DOWN);
    RAY_HITS.length = 0;
    HEIGHT_RAY.intersectObjects(meshes, false, RAY_HITS);
    return RAY_HITS.length > 0 ? RAY_HITS[0].point.y : null;
  };

  const setVisible = (visible: boolean) => {
    group.visible = visible;
  };

  // Verification aid: expose the mesh XZ bounds so callers can probe on-mesh.
  (group.userData as { bbox?: number[] }).bbox = [
    bbox.min.x, bbox.max.x, bbox.min.z, bbox.max.z, bbox.min.y, bbox.max.y,
  ];

  const dispose = () => {
    meshes.forEach((m) => m.geometry.dispose());
    material.dispose();
  };

  return { group, setVisible, heightAt, dispose };
}

/**
 * Loads the real MOD road mesh for a track id. Resolves to null when the track
 * ships no `road_ac_local.glb` (the common case) or the load fails — the caller
 * treats "no mesh" as normal, not an error.
 */
export async function loadAcTrackMesh(trackId: string): Promise<AcTrackMeshHandle | null> {
  const url = `/data/tracks/${trackId}/road_ac_local.glb`;
  try {
    const head = await fetch(url, { method: "HEAD" });
    if (!head.ok) return null;
  } catch {
    return null;
  }
  return new Promise((resolve) => {
    new GLTFLoader().load(
      url,
      (gltf) => resolve(buildHandle(gltf as unknown as { scene: THREE.Group })),
      undefined,
      () => resolve(null),
    );
  });
}
