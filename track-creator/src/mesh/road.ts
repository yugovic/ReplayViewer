/**
 * Road ribbon mesh: 3 vertex columns per station (left edge, centerline,
 * right edge) so camber shows in the shading. Follows the repo convention:
 * normal = (-tz, 0, tx), +normal = driver's right; triangles wound to face +Y.
 * For closed tracks the first station is duplicated as a final row so the
 * loop closes without relying on index wrap-around.
 */

import { computeNormals } from "./normals.ts";
import type { StationTrack, Station } from "./stations.ts";

export interface MeshData {
  positions: Float32Array;
  normals: Float32Array;
  uvs?: Float32Array;
  /** RGB per vertex (linear 0..1). */
  colors?: Float32Array;
  indices: Uint32Array;
}

const COLS = 3;

function writeRow(
  positions: Float32Array,
  uvs: Float32Array,
  row: number,
  s: Station,
  vDist: number,
) {
  const nx = -s.tz;
  const nz = s.tx;
  const xs = [
    { x: s.x - nx * s.offsetLeft, z: s.z - nz * s.offsetLeft, y: s.yLeft, u: 0 },
    { x: s.x, z: s.z, y: s.yCenter, u: 0.5 },
    { x: s.x + nx * s.offsetRight, z: s.z + nz * s.offsetRight, y: s.yRight, u: 1 },
  ];
  for (let c = 0; c < COLS; c++) {
    const v = row * COLS + c;
    positions[v * 3] = xs[c].x;
    positions[v * 3 + 1] = xs[c].y;
    positions[v * 3 + 2] = xs[c].z;
    uvs[v * 2] = xs[c].u;
    uvs[v * 2 + 1] = vDist;
  }
}

export function buildRoadMesh(track: StationTrack): MeshData {
  const { stations, closed, totalLength } = track;
  const rows = stations.length + (closed ? 1 : 0);
  const positions = new Float32Array(rows * COLS * 3);
  const uvs = new Float32Array(rows * COLS * 2);
  const indices: number[] = [];

  for (let r = 0; r < stations.length; r++) {
    writeRow(positions, uvs, r, stations[r], stations[r].dist);
  }
  if (closed) {
    writeRow(positions, uvs, stations.length, stations[0], totalLength);
  }

  for (let r = 0; r < rows - 1; r++) {
    for (let c = 0; c < COLS - 1; c++) {
      const a = r * COLS + c; // toward -normal (left)
      const b = a + 1; // toward +normal (right)
      const c2 = a + COLS;
      const d = b + COLS;
      // Same winding as curbBuilder's right-side ribbon: faces +Y.
      indices.push(a, b, c2, b, d, c2);
    }
  }

  const idx = new Uint32Array(indices);
  return { positions, normals: computeNormals(positions, idx), uvs, indices: idx };
}
