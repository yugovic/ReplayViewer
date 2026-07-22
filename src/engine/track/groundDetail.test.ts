import { describe, expect, it } from "vitest";
import * as THREE from "three";
import {
  attachGroundDetailShader,
  createGroundDetailUniforms,
  type GroundDetailUniforms,
} from "./groundDetail";

/** Minimal stand-in for the WebGLProgramParameters three passes to
 * onBeforeCompile — just the include anchors our injection targets. */
function fakeShader() {
  return {
    uniforms: {} as Record<string, { value: unknown }>,
    vertexShader: "#include <common>\n#include <begin_vertex>",
    fragmentShader: [
      "#include <common>",
      "#include <map_pars_fragment>",
      "#include <map_fragment>",
      "#include <roughnessmap_fragment>",
      "#include <normal_fragment_begin>",
    ].join("\n"),
  };
}

function compile(mat: THREE.MeshStandardMaterial) {
  const shader = fakeShader();
  (mat.onBeforeCompile as unknown as (s: ReturnType<typeof fakeShader>) => void)(shader);
  return shader;
}

describe("createGroundDetailUniforms", () => {
  it("starts enabled with procedural textures under Node", () => {
    const uniforms = createGroundDetailUniforms(6.5);
    expect(uniforms.uDetailStrength.value).toBe(1);
    expect(uniforms.uEdgeLines.value).toBe(1);
    expect(uniforms.uHalfRoadWidth.value).toBe(6.5);
    // No DOM here, so the CC0 upgrade is skipped and the procedural
    // DataTextures must be the final state.
    expect((uniforms.uAsphaltAlbedo.value as THREE.DataTexture).isDataTexture).toBe(true);
    expect((uniforms.uAsphaltNormal.value as THREE.DataTexture).isDataTexture).toBe(true);
    expect((uniforms.uAsphaltRoughness.value as THREE.DataTexture).isDataTexture).toBe(true);
    expect((uniforms.uGrassAlbedo.value as THREE.DataTexture).isDataTexture).toBe(true);
    expect((uniforms.uGrassNormal.value as THREE.DataTexture).isDataTexture).toBe(true);
    expect((uniforms.uGrassRoughness.value as THREE.DataTexture).isDataTexture).toBe(true);
  });
});

describe("attachGroundDetailShader", () => {
  const uniforms: GroundDetailUniforms = createGroundDetailUniforms(6.5);

  it("vertexCorridor mode: corridor attributes, edge lines, hex tiling", () => {
    const mat = new THREE.MeshStandardMaterial();
    attachGroundDetailShader(mat, uniforms, {
      maskMode: "vertexCorridor",
      edgeLines: true,
      cacheKey: "test-vertex",
    });
    const shader = compile(mat);
    expect(shader.vertexShader).toContain("attribute float corridorMask");
    expect(shader.vertexShader).toContain("vWorldXZ");
    expect(shader.fragmentShader).toContain("vCorridorMask");
    expect(shader.fragmentShader).toContain("gdHexRatio");
    expect(shader.fragmentShader).toContain("detailRoughness");
    expect(shader.fragmentShader).toContain("roughnessFactor = mix");
    expect(shader.fragmentShader).toContain("uGrassNormal");
    expect(shader.fragmentShader).toContain("uEdgeLines");
    expect(shader.fragmentShader).not.toContain("gdAsphaltWeight");
    // The live uniform objects must be shared, not copied — toggling
    // uDetailStrength.value from the layers panel drives this material.
    expect(shader.uniforms.uDetailStrength).toBe(uniforms.uDetailStrength);
    expect(mat.customProgramCacheKey()).toBe("test-vertex");
  });

  it("photoExg mode: photo-derived mask, alpha gate, no baked attributes", () => {
    const mat = new THREE.MeshStandardMaterial();
    attachGroundDetailShader(mat, uniforms, {
      maskMode: "photoExg",
      edgeLines: false,
      cacheKey: "test-photo",
    });
    const shader = compile(mat);
    expect(shader.vertexShader).not.toContain("corridorMask");
    expect(shader.vertexShader).toContain("vWorldXZ");
    expect(shader.fragmentShader).toContain("gdAsphaltWeight");
    expect(shader.fragmentShader).toContain("diffuseColor.a");
    expect(shader.fragmentShader).toContain("rStrength *= smoothstep");
    expect(shader.fragmentShader).toContain("nStrength *= smoothstep");
    expect(shader.fragmentShader).not.toContain("uEdgeLines");
    expect(shader.fragmentShader).not.toContain("vCorridorMask");
    expect(shader.uniforms.uDetailStrength).toBe(uniforms.uDetailStrength);
    expect(shader.uniforms.uAsphaltRoughness).toBe(uniforms.uAsphaltRoughness);
    expect(shader.uniforms.uGrassRoughness).toBe(uniforms.uGrassRoughness);
    expect(mat.customProgramCacheKey()).toBe("test-photo");
  });

  it("keeps one shared uniform object across both modes (single toggle)", () => {
    const base = new THREE.MeshStandardMaterial();
    const tile = new THREE.MeshStandardMaterial();
    attachGroundDetailShader(base, uniforms, {
      maskMode: "vertexCorridor",
      edgeLines: true,
      cacheKey: "k1",
    });
    attachGroundDetailShader(tile, uniforms, {
      maskMode: "photoExg",
      edgeLines: false,
      cacheKey: "k2",
    });
    const baseShader = compile(base);
    const tileShader = compile(tile);
    expect(baseShader.uniforms.uDetailStrength).toBe(tileShader.uniforms.uDetailStrength);
  });

  it("rejects edge lines without baked lateral distances", () => {
    const mat = new THREE.MeshStandardMaterial();
    expect(() =>
      attachGroundDetailShader(mat, uniforms, {
        maskMode: "photoExg",
        edgeLines: true,
        cacheKey: "bad",
      }),
    ).toThrow(/vertexCorridor/);
  });
});
