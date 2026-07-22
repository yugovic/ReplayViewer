// Experiment 1: dump the material/image/mesh inventory of one or more GLBs.
// Usage: node compare.js <label>=<path.glb> [<label>=<path.glb> ...]
const fs = require("fs");

function readGlbJson(path) {
  const buf = fs.readFileSync(path);
  if (buf.readUInt32LE(0) !== 0x46546c67) throw new Error("not a GLB: " + path);
  const jsonLen = buf.readUInt32LE(12);
  if (buf.readUInt32LE(16) !== 0x4e4f534a) throw new Error("first chunk not JSON");
  return JSON.parse(buf.slice(20, 20 + jsonLen).toString("utf8"));
}

for (const arg of process.argv.slice(2)) {
  const eq = arg.indexOf("=");
  const label = arg.slice(0, eq), path = arg.slice(eq + 1);
  const g = readGlbJson(path);
  const mb = (fs.statSync(path).size / 1048576).toFixed(1);
  console.log(`\n=== ${label} (${mb} MB) ${path}`);
  console.log("materials:", (g.materials ?? []).map(m => m.name ?? "?").join(", ") || "(none)");
  console.log("images   :", (g.images ?? []).map(i => i.name ?? i.uri ?? "?").join(", ") || "(none)");
  console.log("textures :", (g.textures ?? []).length);
  for (const mesh of g.meshes ?? []) {
    for (const [pi, p] of (mesh.primitives ?? []).entries()) {
      const verts = p.attributes?.POSITION != null ? g.accessors[p.attributes.POSITION].count : 0;
      const tris = p.indices != null ? g.accessors[p.indices].count / 3 : 0;
      const mat = p.material != null ? (g.materials[p.material].name ?? p.material) : "(no mat)";
      console.log(`mesh "${mesh.name}"[${pi}]: ${verts} verts, ${tris} tris, material=${mat}`);
    }
  }
}
