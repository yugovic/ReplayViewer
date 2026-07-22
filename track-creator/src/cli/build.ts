/**
 * Step 2 of the pipeline: build the track GLB from track.json + the
 * pre-fetched elevation.json.
 *
 *   npm run build -- tracks/demo            → out/demo.glb
 *   npm run build -- tracks/demo out/x.glb  → custom output path
 */

import { statSync } from "node:fs";
import { join, resolve } from "node:path";
import { loadTrack, loadElevation } from "../io.ts";
import { buildStations } from "../mesh/stations.ts";
import { buildRoadMesh } from "../mesh/road.ts";
import { buildCurbsMesh } from "../mesh/curbs.ts";
import { writeGlb } from "../glb.ts";
import type { GlbMeshSpec } from "../glb.ts";

async function main() {
  const { dir, def } = loadTrack(process.argv[2]);
  const elev = loadElevation(dir);

  const track = buildStations(def, elev);
  console.log(
    `[build] ${def.name}: ${track.stations.length} stations, ${track.totalLength.toFixed(1)} m ${def.closed ? "loop" : "open"}`,
  );

  const meshes: GlbMeshSpec[] = [
    {
      name: "road",
      data: buildRoadMesh(track),
      material: { baseColor: [0.16, 0.16, 0.17, 1], roughness: 0.95 },
    },
  ];

  const curbs = buildCurbsMesh(track, def.curbs ?? []);
  if (curbs) {
    meshes.push({
      name: "curbs",
      // Vertex colors carry the stripes; keep the base color white.
      data: curbs,
      material: { baseColor: [1, 1, 1, 1], roughness: 0.7, doubleSided: true },
    });
    console.log(`[build] curbs: ${def.curbs!.length} spans, ${curbs.indices.length / 3} tris`);
  }

  const outPath = process.argv[3]
    ? resolve(process.argv[3])
    : resolve(join("out", `${def.name}.glb`));
  await writeGlb(outPath, meshes);

  const roadTris = meshes[0].data.indices.length / 3;
  const kb = (statSync(outPath).size / 1024).toFixed(0);
  console.log(`[build] wrote ${outPath} — road ${roadTris} tris, ${kb} KB`);
}

main().catch((e) => {
  console.error(e instanceof Error ? e.message : e);
  process.exit(1);
});
