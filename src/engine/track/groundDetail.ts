/**
 * Shared close-range ground-detail shader for photo-draped surfaces.
 *
 * The orthophoto carries the low-frequency truth (color, markings, shadows)
 * but is a watercolor blur at chase-camera range (1.5–10 m above ground —
 * a 20 cm/px source cannot resolve asphalt grain). This module injects the
 * high-frequency response on top, MSFS-style macro×micro blending:
 *
 *  - LIGHTING: tangent-space asphalt/grass normal maps perturb the draped
 *    normal, while separate roughness fields break up close-range highlights.
 *  - ALBEDO: asphalt/grass detail albedo modulates the photo as mean-neutral
 *    local-contrast ratios (sample ÷ coarse-mip average), so the distant,
 *    faded view converges to the photo exactly — the detail layer never
 *    invents color or position, only texture (imagery-safety compliant).
 *  - ANTI-TILING: detail samples go through histogram-preserving hex tiling
 *    (Heitz & Neyret, HPG 2018) — 3 taps on a random triangle grid, blended
 *    as PER-TAP mean-neutral ratios with variance renormalization — so the
 *    0.4 m repeat never reads as a pattern and the random offsets cannot
 *    introduce a systematic tint.
 *
 * Two mask modes select where "asphalt" vs "grass" detail applies:
 *  - "vertexCorridor": per-vertex corridorMask/lateralDistance attributes
 *    baked by TrackBuilder's satellite ground drape (also carries the legacy
 *    uniform-width white edge-line band used by profile-less tracks).
 *  - "photoExg": for SR corridor tiles, which have no baked attributes. The
 *    zone comes from the photo itself: ExG (Excess Green, 2G−R−B) on a
 *    coarse mip of the tile texture separates grass from pavement, and the
   *    tile's own alpha fade gates the effect. Deriving the mask from the
   *    redistributable CC BY ortho keeps the processing provenance clean.
 *
 * Both consumers share one GroundDetailUniforms object, so the layers panel
 * toggle (uDetailStrength) drives the base ground and every SR tile at once.
 */

import * as THREE from "three";
import {
  buildProceduralAsphaltAlbedo,
  buildProceduralAsphaltNormal,
  buildProceduralAsphaltRoughness,
  buildProceduralGrassAlbedo,
  buildProceduralGrassNormal,
  buildProceduralGrassRoughness,
} from "./detailTexture";

/** Uniforms exposed by attachGroundDetailShader(), kept live so the effect
 * can be toggled without recompiling the shader.
 *  - uDetailStrength drives BOTH the asphalt/grass albedo mottling and the
 *    normal-map lighting perturbation (0 disables both).
 *  - uEdgeLines drives the white road-edge line band (vertexCorridor only).
 * Detail textures start as procedural DataTextures; matching CC0 JPG assets
 * upgrade the available albedo/asphalt-normal slots in place. */
export interface GroundDetailUniforms {
  uAsphaltAlbedo: { value: THREE.Texture };
  uAsphaltNormal: { value: THREE.Texture };
  uAsphaltRoughness: { value: THREE.Texture };
  uGrassAlbedo: { value: THREE.Texture };
  uGrassNormal: { value: THREE.Texture };
  uGrassRoughness: { value: THREE.Texture };
  /** Linear-light mean color of each albedo, precomputed on the CPU. The
   * mean-neutral ratio DIVIDES by this; using a high-LOD textureLod for it
   * proved unreliable (near-zero on some GL stacks → every ratio pegged at
   * the clamp → the whole ground uniformly over-brightened), so the shader
   * never derives the denominator from the mip chain. */
  uAsphaltMean: { value: THREE.Vector3 };
  uGrassMean: { value: THREE.Vector3 };
  uDetailStrength: { value: number };
  uEdgeLines: { value: number };
  uHalfRoadWidth: { value: number };
}

/** Where the asphalt/grass zone mask comes from (see module doc). */
export type GroundDetailMaskMode = "vertexCorridor" | "photoExg";

export interface GroundDetailShaderOptions {
  maskMode: GroundDetailMaskMode;
  /** Legacy uniform-width white line band; only valid with vertexCorridor. */
  edgeLines: boolean;
  /** three.js program cache key — one per distinct mode so equal modes share
   * a compiled program across materials (119 SR tiles → 1 program). */
  cacheKey: string;
}

/** Directory holding the shared CC0 material textures (asphalt/grass). Global,
 * not per-track — the same aggregate is reused for every circuit. */
const DETAIL_TEXTURE_DIR = "/data/textures/";

/**
 * Upgrades a detail-texture uniform from its procedural placeholder to a CC0
 * JPG if the file loads; on 404 / decode error it silently keeps the
 * procedural fallback, so a deployment missing the JPGs never breaks the
 * ground. Albedo maps decode as sRGB, the normal map stays linear
 * (NoColorSpace) as a tangent-space normal map must.
 */
/** sRGB-encoded channel value → linear light. */
export function srgbToLinear(c: number): number {
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

/** Linear-light mean of an sRGB image, via a small canvas readback. Returns
 * null when the pixels are unreadable (no DOM, tainted canvas, zero size). */
export function imageMeanLinear(image: CanvasImageSource & { width: number; height: number }): THREE.Vector3 | null {
  if (typeof document === "undefined") return null;
  try {
    const size = 64; // plenty for a mean; keeps the readback trivial
    const canvas = document.createElement("canvas");
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) return null;
    ctx.drawImage(image, 0, 0, size, size);
    const data = ctx.getImageData(0, 0, size, size).data;
    let r = 0;
    let g = 0;
    let b = 0;
    const count = size * size;
    // Decode EACH texel to linear before averaging — the shader samples are
    // hardware-decoded per texel, and srgbToLinear(mean) underestimates
    // mean(linear) badly on high-variance textures (convexity), which would
    // push every ratio systematically above 1.
    for (let i = 0; i < data.length; i += 4) {
      r += srgbToLinear(data[i] / 255);
      g += srgbToLinear(data[i + 1] / 255);
      b += srgbToLinear(data[i + 2] / 255);
    }
    return new THREE.Vector3(r / count, g / count, b / count);
  } catch {
    return null;
  }
}

function loadDetailMaterialTextures(uniforms: GroundDetailUniforms): void {
  // TextureLoader needs a DOM; under Node/Vitest the procedural fallback is
  // the final state, which is exactly what the unit tests exercise.
  if (typeof document === "undefined") return;
  const loader = new THREE.TextureLoader();
  const upgrade = (
    url: string,
    target: { value: THREE.Texture },
    srgb: boolean,
    mean?: { value: THREE.Vector3 },
  ) => {
    loader.load(
      url,
      (tex) => {
        tex.wrapS = THREE.RepeatWrapping;
        tex.wrapT = THREE.RepeatWrapping;
        tex.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
        tex.anisotropy = 16;
        tex.minFilter = THREE.LinearMipmapLinearFilter;
        tex.magFilter = THREE.LinearFilter;
        tex.generateMipmaps = true;
        tex.needsUpdate = true;
        // The ratio denominator must match the NEW texture; keep the old
        // texture (and its mean) if the pixels cannot be read back.
        const newMean = mean ? imageMeanLinear(tex.image as HTMLImageElement) : null;
        if (mean && !newMean) return;
        if (mean && newMean) mean.value = newMean;
        const prev = target.value;
        target.value = tex;
        // Free the procedural DataTexture we just replaced.
        if (prev && (prev as THREE.DataTexture).isDataTexture) prev.dispose();
      },
      undefined,
      () => {
        /* keep procedural fallback — never throws */
      },
    );
  };
  upgrade(`${DETAIL_TEXTURE_DIR}asphalt_albedo.jpg`, uniforms.uAsphaltAlbedo, true, uniforms.uAsphaltMean);
  upgrade(`${DETAIL_TEXTURE_DIR}asphalt_normal.jpg`, uniforms.uAsphaltNormal, false);
  upgrade(`${DETAIL_TEXTURE_DIR}grass_albedo.jpg`, uniforms.uGrassAlbedo, true, uniforms.uGrassMean);
}

/** Builds the shared uniforms (procedural textures immediately, CC0 upgrade
 * in the background). Create ONE of these per track scene and hand the same
 * object to every attachGroundDetailShader() call. */
/** Linear-light mean of a procedural RGBA8 DataTexture (decode each texel,
 * then average — same order the GPU effectively presents to the shader). */
export function dataTextureMeanLinear(texture: THREE.DataTexture): THREE.Vector3 {
  const image = texture.image as { data: Uint8Array; width: number; height: number };
  const data = image.data;
  const count = image.width * image.height;
  let r = 0;
  let g = 0;
  let b = 0;
  for (let i = 0; i < data.length; i += 4) {
    r += srgbToLinear(data[i] / 255);
    g += srgbToLinear(data[i + 1] / 255);
    b += srgbToLinear(data[i + 2] / 255);
  }
  return new THREE.Vector3(r / count, g / count, b / count);
}

export function createGroundDetailUniforms(halfRoadWidth: number): GroundDetailUniforms {
  const asphaltAlbedo = buildProceduralAsphaltAlbedo();
  const grassAlbedo = buildProceduralGrassAlbedo();
  const uniforms: GroundDetailUniforms = {
    uAsphaltAlbedo: { value: asphaltAlbedo },
    uAsphaltNormal: { value: buildProceduralAsphaltNormal() },
    uAsphaltRoughness: { value: buildProceduralAsphaltRoughness() },
    uGrassAlbedo: { value: grassAlbedo },
    uGrassNormal: { value: buildProceduralGrassNormal() },
    uGrassRoughness: { value: buildProceduralGrassRoughness() },
    uAsphaltMean: { value: dataTextureMeanLinear(asphaltAlbedo) },
    uGrassMean: { value: dataTextureMeanLinear(grassAlbedo) },
    uDetailStrength: { value: 1 },
    uEdgeLines: { value: 1 },
    uHalfRoadWidth: { value: halfRoadWidth },
  };
  loadDetailMaterialTextures(uniforms);
  return uniforms;
}

/**
 * Histogram-preserving hex tiling (Heitz & Neyret 2018, simplified): sample
 * the texture at 3 randomly-offset triangle-grid vertices, blend with
 * sharpened weights and renormalize the variance lost to linear blending.
 * textureGrad keeps mip selection continuous across the random offsets.
 * GLSL ES 3.00 only — fine, three r163+ is WebGL2-only.
 */
const HEX_TILING_GLSL = /* glsl */ `
vec2 gdHexHash( vec2 p ) {
  return fract( sin( vec2( dot( p, vec2( 127.1, 311.7 ) ), dot( p, vec2( 269.5, 183.3 ) ) ) ) * 43758.5453 );
}
void gdTriangleGrid( vec2 uv, out float w1, out float w2, out float w3,
                     out vec2 v1, out vec2 v2, out vec2 v3 ) {
  const mat2 gridToSkewedGrid = mat2( 1.0, -0.57735027, 0.0, 1.15470054 );
  vec2 skewed = gridToSkewedGrid * uv;
  vec2 base = floor( skewed );
  vec3 temp = vec3( fract( skewed ), 0.0 );
  temp.z = 1.0 - temp.x - temp.y;
  if ( temp.z > 0.0 ) {
    w1 = temp.z; w2 = temp.y; w3 = temp.x;
    v1 = base; v2 = base + vec2( 0.0, 1.0 ); v3 = base + vec2( 1.0, 0.0 );
  } else {
    w1 = -temp.z; w2 = 1.0 - temp.y; w3 = 1.0 - temp.x;
    v1 = base + vec2( 1.0, 1.0 ); v2 = base + vec2( 1.0, 0.0 ); v3 = base + vec2( 0.0, 1.0 );
  }
}
void gdHexWeights( vec2 uv, float patchScale, out float w1, out float w2, out float w3,
                   out vec2 o1, out vec2 o2, out vec2 o3 ) {
  vec2 v1, v2, v3;
  gdTriangleGrid( uv * patchScale, w1, w2, w3, v1, v2, v3 );
  o1 = gdHexHash( v1 ); o2 = gdHexHash( v2 ); o3 = gdHexHash( v3 );
  w1 = pow( w1, 4.0 ); w2 = pow( w2, 4.0 ); w3 = pow( w3, 4.0 );
  float wSum = w1 + w2 + w3;
  w1 /= wSum; w2 /= wSum; w3 /= wSum;
}
/* Mean-neutral hex-tiled local-contrast ratio. Each tap divides its sample by
 * the texture's precomputed linear-light mean (a uniform — NEVER the mip
 * chain; see GroundDetailUniforms.uAsphaltMean), so every tap is hue- and
 * mean-neutral BEFORE blending and the random hex offsets cannot introduce a
 * systematic tint. Variance renormalization restores the contrast lost to
 * linear blending, and the clamp bounds worst-case single-patch excursions. */
vec3 gdHexRatio( sampler2D tex, vec2 uv, float patchScale, vec3 meanColor ) {
  float w1, w2, w3;
  vec2 o1, o2, o3;
  gdHexWeights( uv, patchScale, w1, w2, w3, o1, o2, o3 );
  vec2 duvdx = dFdx( uv );
  vec2 duvdy = dFdy( uv );
  vec3 invMean = 1.0 / max( meanColor, vec3( 1e-3 ) );
  vec3 r1 = textureGrad( tex, uv + o1, duvdx, duvdy ).rgb * invMean;
  vec3 r2 = textureGrad( tex, uv + o2, duvdx, duvdy ).rgb * invMean;
  vec3 r3 = textureGrad( tex, uv + o3, duvdx, duvdy ).rgb * invMean;
  vec3 blended = w1 * r1 + w2 * r2 + w3 * r3;
  float varianceFix = inversesqrt( w1 * w1 + w2 * w2 + w3 * w3 );
  return clamp( vec3( 1.0 ) + ( blended - vec3( 1.0 ) ) * varianceFix,
                vec3( 0.45 ), vec3( 1.9 ) );
}
/* Plain hex-tiled blend for the tangent-space normal map: no variance
 * amplification (over-steep micro-normals catch the warm sun and visibly
 * discolor the ground), just offset taps to break the repeat. */
vec3 gdHexNormal( sampler2D tex, vec2 uv, float patchScale ) {
  float w1, w2, w3;
  vec2 o1, o2, o3;
  gdHexWeights( uv, patchScale, w1, w2, w3, o1, o2, o3 );
  vec2 duvdx = dFdx( uv );
  vec2 duvdy = dFdy( uv );
  vec3 blended = w1 * textureGrad( tex, uv + o1, duvdx, duvdy ).rgb
               + w2 * textureGrad( tex, uv + o2, duvdx, duvdy ).rgb
               + w3 * textureGrad( tex, uv + o3, duvdx, duvdy ).rgb;
  return blended * 2.0 - 1.0;
}
float gdHexScalar( sampler2D tex, vec2 uv, float patchScale ) {
  float w1, w2, w3;
  vec2 o1, o2, o3;
  gdHexWeights( uv, patchScale, w1, w2, w3, o1, o2, o3 );
  vec2 duvdx = dFdx( uv );
  vec2 duvdy = dFdy( uv );
  return w1 * textureGrad( tex, uv + o1, duvdx, duvdy ).r
       + w2 * textureGrad( tex, uv + o2, duvdx, duvdy ).r
       + w3 * textureGrad( tex, uv + o3, duvdx, duvdy ).r;
}
`;

/** Coarse-mip ExG (Excess Green) pavement/grass split of the photo itself.
 * Sampled at LOD 5 (≈3 m neighborhood on a 1024px corridor tile) so the zone
 * is regional, not per-pixel noise. Returns 1 on pavement, 0 on grass. */
const EXG_MASK_GLSL = /* glsl */ `
float gdAsphaltWeight() {
  // Bias (not explicit-LOD) sampling: implicit mip selection is reliable on
  // every GL stack we target, explicit textureLod proved not to be.
  vec3 photo = texture2D( map, vMapUv, 4.0 ).rgb;
  float exg = 2.0 * photo.g - photo.r - photo.b;
  return 1.0 - smoothstep( 0.02, 0.08, exg );
}
`;

/**
 * Injects the ground material response into a MeshStandardMaterial via
 * onBeforeCompile. See the module doc for what it does; per-mode differences:
 *
 *  - vertexCorridor: zone mask = baked corridorMask attribute; optionally
 *    draws the legacy ~20 cm white edge-line band just inside each paved
 *    edge (lateralDistance ∈ [uHalfRoadWidth−0.55, −0.35], fwidth() AA,
 *    gated by uEdgeLines) for tracks without a measured edge profile.
 *  - photoExg: zone mask = gdAsphaltWeight() from the tile photo; the effect
 *    additionally fades with the tile's own alpha (corridor fade ring), so
 *    detail never pops where the SR layer dissolves into the base ground.
 *
 * Both fade out over 60→160 m (normals included, to kill distance shimmer)
 * and are gated by uDetailStrength (0 = fully off).
 *
 * vViewPosition (distance fade) and viewMatrix (world→view for the normal
 * perturbation) are standard shader globals — no extra plumbing needed.
 */
export function attachGroundDetailShader(
  mat: THREE.MeshStandardMaterial,
  uniforms: GroundDetailUniforms,
  options: GroundDetailShaderOptions,
): void {
  const vertexMode = options.maskMode === "vertexCorridor";
  if (options.edgeLines && !vertexMode) {
    throw new Error("edgeLines requires the vertexCorridor mask mode");
  }

  mat.onBeforeCompile = (shader) => {
    shader.uniforms.uAsphaltAlbedo = uniforms.uAsphaltAlbedo;
    shader.uniforms.uAsphaltNormal = uniforms.uAsphaltNormal;
    shader.uniforms.uAsphaltRoughness = uniforms.uAsphaltRoughness;
    shader.uniforms.uGrassAlbedo = uniforms.uGrassAlbedo;
    shader.uniforms.uGrassNormal = uniforms.uGrassNormal;
    shader.uniforms.uGrassRoughness = uniforms.uGrassRoughness;
    shader.uniforms.uAsphaltMean = uniforms.uAsphaltMean;
    shader.uniforms.uGrassMean = uniforms.uGrassMean;
    shader.uniforms.uDetailStrength = uniforms.uDetailStrength;
    if (vertexMode) {
      shader.uniforms.uEdgeLines = uniforms.uEdgeLines;
      shader.uniforms.uHalfRoadWidth = uniforms.uHalfRoadWidth;
    }

    shader.vertexShader = shader.vertexShader
      .replace(
        "#include <common>",
        `#include <common>
${vertexMode ? "attribute float corridorMask;\nattribute float lateralDistance;\nvarying float vCorridorMask;\nvarying float vLateralDistance;" : ""}
varying vec2 vWorldXZ;`,
      )
      .replace(
        "#include <begin_vertex>",
        `#include <begin_vertex>
${vertexMode ? "vCorridorMask = corridorMask;\nvLateralDistance = lateralDistance;" : ""}
vWorldXZ = ( modelMatrix * vec4( transformed, 1.0 ) ).xz;`,
      );

    // In three's fragment main(), <map_fragment> (diffuseColor) runs before
    // <normal_fragment_begin>, so both injected blocks may read diffuseColor
    // and the photo-derived masks.
    const maskExpr = vertexMode ? "vCorridorMask" : "gdAsphaltWeight()";
    const alphaGate = vertexMode ? "" : "\n    aStrength *= smoothstep( 0.15, 0.6, diffuseColor.a );";
    const normalAlphaGate = vertexMode ? "" : "\n  nStrength *= smoothstep( 0.15, 0.6, diffuseColor.a );";
    const roughnessAlphaGate = vertexMode ? "" : "\n  rStrength *= smoothstep( 0.15, 0.6, diffuseColor.a );";

    shader.fragmentShader = shader.fragmentShader
      .replace(
        "#include <common>",
        `#include <common>
uniform sampler2D uAsphaltAlbedo;
uniform sampler2D uAsphaltNormal;
uniform sampler2D uAsphaltRoughness;
uniform sampler2D uGrassAlbedo;
uniform sampler2D uGrassNormal;
uniform sampler2D uGrassRoughness;
uniform vec3 uAsphaltMean;
uniform vec3 uGrassMean;
uniform float uDetailStrength;
${vertexMode ? "uniform float uEdgeLines;\nuniform float uHalfRoadWidth;\nvarying float vCorridorMask;\nvarying float vLateralDistance;" : ""}
varying vec2 vWorldXZ;
${HEX_TILING_GLSL}`,
      )
      // The photo-ExG helper needs the map sampler/varying declarations that
      // <map_pars_fragment> provides, so it is appended after that include.
      .replace(
        "#include <map_pars_fragment>",
        `#include <map_pars_fragment>
${vertexMode ? "" : EXG_MASK_GLSL}`,
      )
      // Procedural material response only: changes highlight breakup, never
      // the ortho's position or low-frequency color information.
      .replace(
        "#include <roughnessmap_fragment>",
        `#include <roughnessmap_fragment>
{
  float camDist = length( vViewPosition );
  float distanceFade = 1.0 - smoothstep( 60.0, 160.0, camDist );
  float rStrength = uDetailStrength * distanceFade;${roughnessAlphaGate}
  if ( rStrength > 0.0001 ) {
    float roughA = gdHexScalar( uAsphaltRoughness, vWorldXZ / 0.32, 0.35 );
    float roughG = gdHexScalar( uGrassRoughness, vWorldXZ / 0.9, 0.35 );
    float detailRoughness = mix( roughG, roughA, ${maskExpr} );
    roughnessFactor = mix(
      roughnessFactor,
      clamp( detailRoughness, 0.55, 1.0 ),
      rStrength * 0.7
    );
  }
}`,
      )
      // Normal perturbation: 'normal' here is the view-space geometric normal.
      .replace(
        "#include <normal_fragment_begin>",
        `#include <normal_fragment_begin>
{
  float camDist = length( vViewPosition );
  float distanceFade = 1.0 - smoothstep( 60.0, 160.0, camDist );
  float nStrength = uDetailStrength * distanceFade;${normalAlphaGate}
  if ( nStrength > 0.0001 ) {
    vec3 normalA = gdHexNormal( uAsphaltNormal, vWorldXZ / 0.4, 0.35 );
    vec3 normalG = gdHexNormal( uGrassNormal, vWorldXZ / 1.0, 0.35 );
    vec3 nTex = clamp( mix( normalG, normalA, ${maskExpr} ), -1.0, 1.0 );
    // Tangent-space xy tilt -> world (tangent +X, bitangent +Z), added to the
    // draped normal so terrain slope shading is preserved, then to view space.
    vec3 perturbWorld = vec3( nTex.x, 0.0, nTex.y ) * nStrength * 0.9;
    vec3 perturbView = ( viewMatrix * vec4( perturbWorld, 0.0 ) ).xyz;
    normal = normalize( normal + perturbView );
  }
}`,
      )
      .replace(
        "#include <map_fragment>",
        `#include <map_fragment>
{
  float camDist = length( vViewPosition );
  float distanceFade = 1.0 - smoothstep( 60.0, 160.0, camDist );
  float aStrength = uDetailStrength * distanceFade;${alphaGate}
  if ( aStrength > 0.0001 ) {
    // Hex-tiled mean-neutral local contrast (see gdHexRatio): modulates the
    // photo's brightness texture without inventing color or position.
    vec3 ratioA = gdHexRatio( uAsphaltAlbedo, vWorldXZ / 0.4, 0.35, uAsphaltMean );
    vec3 ratioG = gdHexRatio( uGrassAlbedo, vWorldXZ / 1.2, 0.35, uGrassMean );
    vec3 ratio = mix( ratioG, ratioA, ${maskExpr} );
    ratio = vec3( 1.0 ) + ( ratio - vec3( 1.0 ) ) * 1.4; // legibility boost
    diffuseColor.rgb *= mix( vec3( 1.0 ), clamp( ratio, vec3( 0.4 ), vec3( 2.0 ) ), aStrength );
  }${
    options.edgeLines
      ? `
  // White edge lines: ~20 cm band just inside each paved edge (fallback for
  // tracks without a measured edge profile; profile ribbons override this).
  {
    float d = vLateralDistance;
    float inner = uHalfRoadWidth - 0.55;
    float outer = uHalfRoadWidth - 0.35;
    float aa = fwidth( d ) + 1e-4;
    float band = smoothstep( inner - aa, inner + aa, d ) - smoothstep( outer - aa, outer + aa, d );
    diffuseColor.rgb = mix( diffuseColor.rgb, vec3( 0.87 ), band * 0.85 * uEdgeLines );
  }`
      : ""
  }
}`,
      );
  };

  // Static per-mode shader edits -> constant cache key, so all materials in
  // the same mode share one compiled program.
  mat.customProgramCacheKey = () => options.cacheKey;
}
