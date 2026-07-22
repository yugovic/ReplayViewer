/** GLB assembly via @gltf-transform/core (pure Node, no DOM). +Y up, metres. */

import { Document, NodeIO } from "@gltf-transform/core";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import type { MeshData } from "./mesh/road.ts";

export interface GlbMeshSpec {
  name: string;
  data: MeshData;
  material: {
    /** Linear RGBA base color factor. */
    baseColor: [number, number, number, number];
    roughness?: number;
    metalness?: number;
    doubleSided?: boolean;
  };
}

export async function writeGlb(outPath: string, meshes: GlbMeshSpec[]): Promise<void> {
  const doc = new Document();
  const buffer = doc.createBuffer();
  const scene = doc.createScene("track");
  doc.getRoot().setDefaultScene(scene);

  for (const spec of meshes) {
    const mat = doc
      .createMaterial(spec.name)
      .setBaseColorFactor(spec.material.baseColor)
      .setRoughnessFactor(spec.material.roughness ?? 0.9)
      .setMetallicFactor(spec.material.metalness ?? 0)
      .setDoubleSided(spec.material.doubleSided ?? false);

    const prim = doc
      .createPrimitive()
      .setMaterial(mat)
      .setAttribute(
        "POSITION",
        doc.createAccessor().setType("VEC3").setArray(spec.data.positions).setBuffer(buffer),
      )
      .setAttribute(
        "NORMAL",
        doc.createAccessor().setType("VEC3").setArray(spec.data.normals).setBuffer(buffer),
      )
      .setIndices(
        doc.createAccessor().setType("SCALAR").setArray(spec.data.indices).setBuffer(buffer),
      );
    if (spec.data.uvs) {
      prim.setAttribute(
        "TEXCOORD_0",
        doc.createAccessor().setType("VEC2").setArray(spec.data.uvs).setBuffer(buffer),
      );
    }
    if (spec.data.colors) {
      prim.setAttribute(
        "COLOR_0",
        doc.createAccessor().setType("VEC3").setArray(spec.data.colors).setBuffer(buffer),
      );
    }

    const mesh = doc.createMesh(spec.name).addPrimitive(prim);
    scene.addChild(doc.createNode(spec.name).setMesh(mesh));
  }

  mkdirSync(dirname(outPath), { recursive: true });
  await new NodeIO().write(outPath, doc);
}
