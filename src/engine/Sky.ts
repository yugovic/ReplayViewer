/**
 * Sky – procedural sky and environment light for Phase 3.
 *
 * Approach:
 *   1. PMREMGenerator + a colour-sampled cube render (via CubeCamera on a
 *      sky sphere) to produce an envMap for material reflections.
 *   2. Hemisphere light simulating sky/ground bounce.
 *   3. THREE.Fog tinted to match sky colour.
 *
 * No external HDRI download required.
 */

import * as THREE from "three";

// Sky gradient colours (top → bottom) for a sunny afternoon look
const SKY_TOP_COLOR = 0x1a6ba8;
const SKY_HORIZON_COLOR = 0xa8c9e0;
const GROUND_COLOR = 0x4a5740;

/** Build a skydome mesh (large sphere, inside faces) */
function buildSkydome(): THREE.Mesh {
  // Use FrontSide with BackSide-equivalent: flip normals via scale(-1,1,1)
  // so that faces point inward while still using FrontSide culling.
  // Radius 4500 fits well inside camera.far = 5000.
  const geo = new THREE.SphereGeometry(4500, 32, 16);

  // Vertex-coloured gradient from top to bottom
  const posAttr = geo.attributes["position"] as THREE.BufferAttribute;
  const count = posAttr.count;
  const colorData = new Float32Array(count * 3);
  const topCol = new THREE.Color(SKY_TOP_COLOR);
  const horizCol = new THREE.Color(SKY_HORIZON_COLOR);
  const groundCol = new THREE.Color(GROUND_COLOR);

  for (let i = 0; i < count; i++) {
    const y = posAttr.getY(i);
    const t = y / 4500; // −1 to 1
    let col: THREE.Color;
    if (t >= 0) {
      col = topCol.clone().lerp(horizCol, 1 - t);
    } else {
      col = horizCol.clone().lerp(groundCol, -t);
    }
    colorData[i * 3] = col.r;
    colorData[i * 3 + 1] = col.g;
    colorData[i * 3 + 2] = col.b;
  }

  geo.setAttribute("color", new THREE.BufferAttribute(colorData, 3));

  const mat = new THREE.MeshBasicMaterial({
    vertexColors: true,
    side: THREE.BackSide,  // Camera is inside the sphere → BackSide renders inner surface
    depthWrite: false,
    fog: false,
  });

  const mesh = new THREE.Mesh(geo, mat);
  mesh.renderOrder = -1;
  mesh.name = "skydome";
  // Must disable frustum culling: the sphere centre stays at camera position,
  // so when the camera moves, Three.js would otherwise cull the dome.
  mesh.frustumCulled = false;
  return mesh;
}

/** Generate an env map from the sky colours for PBR material reflections */
function buildEnvMap(renderer: THREE.WebGLRenderer): THREE.Texture {
  const pmrem = new THREE.PMREMGenerator(renderer);

  // Build a tiny scene with the skydome to sample from
  const skyScene = new THREE.Scene();
  const dome = buildSkydome();
  // Shrink to fit the cube camera
  dome.scale.setScalar(10);
  skyScene.add(dome);

  // Add sun glow so specular highlights look correct
  const sunGeo = new THREE.SphereGeometry(0.6, 8, 8);
  const sunMat = new THREE.MeshBasicMaterial({ color: 0xfffde0, fog: false, toneMapped: false });
  const sun = new THREE.Mesh(sunGeo, sunMat);
  sun.position.set(3, 6, -8).normalize().multiplyScalar(8);
  skyScene.add(sun);

  const envMap = pmrem.fromScene(skyScene).texture;
  pmrem.dispose();
  return envMap;
}

export interface SkySetup {
  skydome: THREE.Mesh;
  envMap: THREE.Texture;
  hemisphere: THREE.HemisphereLight;
}

/**
 * Build a simple sun disc mesh placed in the upper sky.
 * It is added as a child of the skydome so it follows automatically.
 */
function buildSunDisc(): THREE.Mesh {
  const geo = new THREE.SphereGeometry(80, 16, 8);
  const mat = new THREE.MeshBasicMaterial({
    color: 0xfffde0,
    fog: false,
    toneMapped: false,
    depthWrite: false,
  });
  const sun = new THREE.Mesh(geo, mat);
  // Position relative to skydome centre – upper-right direction, near dome surface
  sun.position.set(-1200, 2400, -2800).normalize().multiplyScalar(4200);
  sun.renderOrder = -1;
  sun.frustumCulled = false;
  sun.name = "sun-disc";
  return sun;
}

/**
 * Set up the procedural sky environment.
 * Call once after the renderer exists.
 *
 * IMPORTANT: After each camera update, call
 *   skySetup.skydome.position.copy(camera.position)
 * so the dome stays centred on the camera regardless of world position.
 */
export function setupSky(renderer: THREE.WebGLRenderer, scene: THREE.Scene): SkySetup {
  const skydome = buildSkydome();
  scene.add(skydome);

  // Sun disc is a child of the skydome, so it follows the dome position automatically
  const sunDisc = buildSunDisc();
  skydome.add(sunDisc);

  // Replace generic scene background with proper env map for reflections
  const envMap = buildEnvMap(renderer);
  scene.environment = envMap;

  // Coloured hemisphere light: sky → ground bounce
  const hemisphere = new THREE.HemisphereLight(SKY_HORIZON_COLOR, GROUND_COLOR, 0.55);
  hemisphere.name = "sky-hemisphere";
  scene.add(hemisphere);

  // Use null background so the skydome mesh is visible (not overridden by solid color)
  scene.background = null;

  // Update fog to match horizon colour
  scene.fog = new THREE.Fog(SKY_HORIZON_COLOR, 900, 2800);

  return { skydome, envMap, hemisphere };
}
