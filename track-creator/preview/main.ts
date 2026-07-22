/**
 * Minimal GLB viewer for built tracks.
 *   npm run view          → http://localhost:5174/  (loads /demo.glb)
 *   …/?glb=mytrack        → loads /mytrack.glb from out/
 */

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const name = new URLSearchParams(location.search).get("glb") ?? "demo";
const hud = document.getElementById("hud")!;

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
document.body.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x10141c);
scene.fog = new THREE.Fog(0x10141c, 800, 2500);

const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.1, 5000);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

scene.add(new THREE.HemisphereLight(0xbfd4ff, 0x30281e, 1.1));
const sun = new THREE.DirectionalLight(0xfff2dd, 2.2);
sun.position.set(300, 400, 200);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
scene.add(sun);

const grid = new THREE.GridHelper(2000, 100, 0x2a3240, 0x1a212c);
scene.add(grid);

new GLTFLoader().load(
  `/${name}.glb`,
  (gltf) => {
    const root = gltf.scene;
    root.traverse((o) => {
      if (o instanceof THREE.Mesh) {
        o.castShadow = true;
        o.receiveShadow = true;
      }
    });
    scene.add(root);

    const box = new THREE.Box3().setFromObject(root);
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    const radius = Math.max(size.x, size.z) * 0.72;
    controls.target.copy(center);
    if (new URLSearchParams(location.search).has("top")) {
      camera.position.set(center.x, center.y + radius * 1.9, center.z + 0.01);
    } else {
      camera.position.set(center.x + radius * 0.6, center.y + radius * 0.8, center.z + radius);
    }
    camera.lookAt(center);
    grid.position.y = box.min.y - 0.5;

    // Configure the shadow camera around the track.
    const d = radius * 1.2;
    Object.assign(sun.shadow.camera, { left: -d, right: d, top: d, bottom: -d });
    sun.shadow.camera.updateProjectionMatrix();
    sun.target.position.copy(center);
    scene.add(sun.target);

    let tris = 0;
    root.traverse((o) => {
      if (o instanceof THREE.Mesh) {
        const idx = o.geometry.getIndex();
        tris += (idx ? idx.count : o.geometry.getAttribute("position").count) / 3;
      }
    });
    hud.textContent = `${name}.glb  |  ${size.x.toFixed(0)}×${size.z.toFixed(0)} m  Δy ${size.y.toFixed(1)} m  |  ${tris.toFixed(0)} tris`;
    (window as unknown as { __trackLoaded: boolean }).__trackLoaded = true;
    // Scriptable camera for screenshot automation.
    (window as unknown as { __setCam: unknown }).__setCam = (
      px: number, py: number, pz: number, tx: number, ty: number, tz: number,
    ) => {
      camera.position.set(px, py, pz);
      controls.target.set(tx, ty, tz);
      controls.update();
    };
  },
  undefined,
  (err) => {
    hud.textContent = `failed to load /${name}.glb — run: npm run build -- tracks/${name}\n${err}`;
  },
);

addEventListener("resize", () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

renderer.setAnimationLoop(() => {
  controls.update();
  renderer.render(scene, camera);
});
