// Continuity check: for each primitive in a GLB, count connected components
// (by shared vertex POSITION, welded by exact coords) and boundary edges.
// A continuous closed road ribbon = 1 component; gaps/breaks = many.
const fs = require("fs");

function readGlb(path) {
  const buf = fs.readFileSync(path);
  const jsonLen = buf.readUInt32LE(12);
  const json = JSON.parse(buf.slice(20, 20 + jsonLen).toString("utf8"));
  let off = 20 + jsonLen;
  let bin = null;
  while (off < buf.length) {
    const len = buf.readUInt32LE(off), type = buf.readUInt32LE(off + 4);
    if (type === 0x004e4942) { bin = buf.slice(off + 8, off + 8 + len); break; }
    off += 8 + len;
  }
  return { json, bin };
}

function accessorData(g, bin, idx) {
  const a = g.json.accessors[idx];
  const bv = g.json.bufferViews[a.bufferView];
  const start = (bv.byteOffset ?? 0) + (a.byteOffset ?? 0);
  const compBytes = { 5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4 }[a.componentType];
  const nComp = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 }[a.type];
  const Ctor = { 5121: Uint8Array, 5123: Uint16Array, 5125: Uint32Array, 5126: Float32Array }[a.componentType];
  return new Ctor(bin.buffer, bin.byteOffset + start, a.count * nComp);
}

class DSU {
  constructor(n) { this.p = new Int32Array(n); for (let i = 0; i < n; i++) this.p[i] = i; }
  find(x) { while (this.p[x] !== x) { this.p[x] = this.p[this.p[x]]; x = this.p[x]; } return x; }
  union(a, b) { const ra = this.find(a), rb = this.find(b); if (ra !== rb) this.p[ra] = rb; }
}

const g = readGlb(process.argv[2]);
for (const mesh of g.json.meshes ?? []) {
  for (const [pi, p] of mesh.primitives.entries()) {
    const pos = accessorData(g, g.bin, p.attributes.POSITION);
    const idxRaw = accessorData(g, g.bin, p.indices);
    const nv = pos.length / 3;
    // weld duplicate positions (exporters split verts at UV/normal seams)
    const weld = new Map();
    const remap = new Int32Array(nv);
    for (let i = 0; i < nv; i++) {
      const key = pos[i * 3].toFixed(4) + "," + pos[i * 3 + 1].toFixed(4) + "," + pos[i * 3 + 2].toFixed(4);
      let w = weld.get(key);
      if (w === undefined) { w = i; weld.set(key, w); }
      remap[i] = w;
    }
    const dsu = new DSU(nv);
    const edgeUse = new Map();
    for (let t = 0; t < idxRaw.length; t += 3) {
      const a = remap[idxRaw[t]], b = remap[idxRaw[t + 1]], c = remap[idxRaw[t + 2]];
      dsu.union(a, b); dsu.union(b, c);
      for (const [u, v] of [[a, b], [b, c], [c, a]]) {
        const k = u < v ? u + "_" + v : v + "_" + u;
        edgeUse.set(k, (edgeUse.get(k) ?? 0) + 1);
      }
    }
    const roots = new Set();
    for (const w of new Set(remap)) roots.add(dsu.find(w));
    let boundary = 0;
    for (const c of edgeUse.values()) if (c === 1) boundary++;
    const matName = p.material != null ? g.json.materials[p.material]?.name : "(none)";
    console.log(`${mesh.name}[${pi}] mat=${matName}: ${nv} verts (${weld.size} welded), ` +
      `${idxRaw.length / 3} tris, components=${roots.size}, boundaryEdges=${boundary}`);
  }
}
