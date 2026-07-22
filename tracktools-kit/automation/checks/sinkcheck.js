// How much of the road sits BELOW the terrain? Bilinear-sample the terrain
// grid at each asphalt vertex (x,z) and compare heights.
const fs = require("fs");

function readGlb(path) {
  const buf = fs.readFileSync(path);
  const jsonLen = buf.readUInt32LE(12);
  const json = JSON.parse(buf.slice(20, 20 + jsonLen).toString("utf8"));
  let off = 20 + jsonLen, bin = null;
  while (off < buf.length) {
    const len = buf.readUInt32LE(off), type = buf.readUInt32LE(off + 4);
    if (type === 0x004e4942) { bin = buf.slice(off + 8, off + 8 + len); break; }
    off += 8 + len;
  }
  return { json, bin };
}
function acc(g, idx) {
  const a = g.json.accessors[idx];
  const bv = g.json.bufferViews[a.bufferView];
  const start = (bv.byteOffset ?? 0) + (a.byteOffset ?? 0);
  const nComp = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 }[a.type];
  const Ctor = { 5121: Uint8Array, 5123: Uint16Array, 5125: Uint32Array, 5126: Float32Array }[a.componentType];
  return new Ctor(g.bin.buffer, g.bin.byteOffset + start, a.count * nComp);
}
function prims(g, name) {
  const out = [];
  for (const mesh of g.json.meshes ?? [])
    for (const p of mesh.primitives)
      if ((p.material != null ? g.json.materials[p.material]?.name : "") === name)
        out.push({ mesh: mesh.name, pos: acc(g, p.attributes.POSITION) });
  return out;
}

const g = readGlb(process.argv[2]);
const roadMat = process.argv[3] ?? "asphalt";

// terrain grid
const ter = prims(g, "grass")[0].pos;
const nv = ter.length / 3;
const xs = new Set(), zs = new Set();
for (let i = 0; i < nv; i++) { xs.add(ter[i*3]); zs.add(ter[i*3+2]); }
const X = [...xs].sort((a, b) => a - b), Z = [...zs].sort((a, b) => a - b);
const H = new Map();
for (let i = 0; i < nv; i++) H.set(ter[i*3] + "_" + ter[i*3+2], ter[i*3+1]);
console.log(`terrain grid ${X.length}x${Z.length}`);
const find = (arr, v) => { // greatest index with arr[i] <= v
  let lo = 0, hi = arr.length - 1;
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (arr[m] <= v) lo = m; else hi = m - 1; }
  return lo;
};
function terrainY(x, z) {
  const i = Math.min(find(X, x), X.length - 2), j = Math.min(find(Z, z), Z.length - 2);
  const x0 = X[i], x1 = X[i+1], z0 = Z[j], z1 = Z[j+1];
  const q = (k, l) => H.get(X[k] + "_" + Z[l]);
  const [h00, h10, h01, h11] = [q(i,j), q(i+1,j), q(i,j+1), q(i+1,j+1)];
  if ([h00,h10,h01,h11].some(h => h === undefined)) return undefined;
  const tx = (x - x0) / (x1 - x0), tz = (z - z0) / (z1 - z0);
  return (h00*(1-tx)+h10*tx)*(1-tz) + (h01*(1-tx)+h11*tx)*tz;
}

for (const { mesh, pos } of prims(g, roadMat)) {
  const n = pos.length / 3;
  let below = 0, deep = 0, min = 1e9, max = -1e9, sum = 0, cnt = 0;
  const deepSpots = [];
  for (let i = 0; i < n; i++) {
    const ty = terrainY(pos[i*3], pos[i*3+2]);
    if (ty === undefined) continue;
    const dy = pos[i*3+1] - ty;
    cnt++; sum += dy; if (dy < min) min = dy; if (dy > max) max = dy;
    if (dy < 0) below++;
    if (dy < -0.10) { deep++; if (deepSpots.length < 8 && i % 50 === 0) deepSpots.push(`(${pos[i*3].toFixed(0)},${pos[i*3+2].toFixed(0)}) dy=${dy.toFixed(2)}`); }
  }
  console.log(`${mesh} [${roadMat}] verts=${cnt}: dy mean=${(sum/cnt).toFixed(3)} min=${min.toFixed(2)} max=${max.toFixed(2)}` +
    ` | below terrain: ${(100*below/cnt).toFixed(1)}% | deeper than 10cm: ${(100*deep/cnt).toFixed(1)}%`);
  if (deepSpots.length) console.log("  deep spots:", deepSpots.join("  "));
}
