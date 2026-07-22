/**
 * AcScene – the full textured Assetto Corsa MOD track scene (road, kerbs, grass,
 * terrain, grandstands, pit buildings, structures) extracted from the .kn5 with
 * materials + textures and baked into viewer-local coordinates.
 *
 * Data: `public/data/tracks/<id>/scene.glb` — produced offline by kn5.mjs
 * (scene mode) with DDS textures decoded to PNG (dds2png.py) and the ICP
 * transform applied. This is the *visual* MOD world; ground-contact for the car
 * still comes from the lightweight road collision mesh (AcTrackMesh), so we do
 * not raycast this 600k-triangle scene every frame.
 */

import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

export interface AcSceneHandle {
  group: THREE.Group;
  setVisible: (visible: boolean) => void;
  dispose: () => void;
}

function finalizeMaterials(root: THREE.Object3D): void {
  root.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const m of mats) {
      const std = m as THREE.MeshStandardMaterial;
      if (std.map) std.map.colorSpace = THREE.SRGBColorSpace;
      std.needsUpdate = true;
    }
    mesh.castShadow = false;
    mesh.receiveShadow = true;
  });
}

function buildHandle(scene: THREE.Group): AcSceneHandle {
  scene.name = "ac-scene";
  finalizeMaterials(scene);

  const setVisible = (visible: boolean) => {
    scene.visible = visible;
  };

  const dispose = () => {
    scene.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh) return;
      mesh.geometry.dispose();
      const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      for (const m of mats) {
        const std = m as THREE.MeshStandardMaterial;
        std.map?.dispose();
        std.dispose();
      }
    });
  };

  return { group: scene, setVisible, dispose };
}

/**
 * Loads the full textured MOD scene for a track id. Resolves to null when the
 * track ships no `scene.glb` (the common case) or the load fails.
 */
export async function loadAcScene(trackId: string): Promise<AcSceneHandle | null> {
  const url = `/data/tracks/${trackId}/scene.glb`;
  try {
    const res = await fetch(url, { method: "HEAD" });
    // Vite's SPA fallback returns 200 for missing files, so also require a
    // binary-ish content-type or a large body via the real load's error path.
    if (!res.ok) return null;
  } catch {
    return null;
  }
  return new Promise((resolve) => {
    new GLTFLoader().load(
      url,
      (gltf) => resolve(buildHandle(gltf.scene)),
      undefined,
      () => resolve(null),
    );
  });
}
