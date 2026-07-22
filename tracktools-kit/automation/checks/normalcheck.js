// Diagnose invisible road segments: per-triangle GEOMETRIC normal (winding)
// for the asphalt primitive. Downward-facing (ny<0) tris are backface-culled
// in top view -> look like gaps. Clusters are reported by position.
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

const g = readGlb(process.argv[2]);
const wantMat = process.argv[3] ?? "asphalt";
for (const mesh of g.json.meshes ?? []) {
  for (const p of mesh.primitives) {
    const matName = p.material != null ? g.json.materials[p.material]?.name : "(none)";
    if (matName !== wantMat) continue;
    const pos = acc(g, p.attributes.POSITION);
    const idx = acc(g, p.indices);
    let down = 0, up = 0, degen = 0;
    const downPts = [];
    for (let t = 0; t < idx.length; t += 3) {
      const [a, b, c] = [idx[t], idx[t + 1], idx[t + 2]];
      const ax = pos[a*3], ay = pos[a*3+1], az = pos[a*3+2];
      const ux = pos[b*3]-ax, uy = pos[b*3+1]-ay, uz = pos[b*3+2]-az;
      const vx = pos[c*3]-ax, vy = pos[c*3+1]-ay, vz = pos[c*3+2]-az;
      const ny = uz*vx - ux*vz; // y of cross(u,v)
      const len = Math.hypot(uy*vz-uz*vy, ny, ux*vy-uy*vx);
      if (len < 1e-9) { degen++; continue; }
      if (ny < 0) { down++; downPts.push([ax, az]); } else up++;
    }
    console.log(`${mesh.name} mat=${matName}: tris up=${up} down=${down} degenerate=${degen}`);
    if (downPts.length) {
      // crude clustering: sort by x, print bounding boxes of runs
      downPts.sort((p1, p2) => p1[0] - p2[0]);
      const n = downPts.length;
      console.log(`  down-facing sample positions (x,z): first=${downPts[0]}, ` +
        `mid=${downPts[(n/2)|0]}, last=${downPts[n-1]} (total ${n})`);
    }
  }
}
