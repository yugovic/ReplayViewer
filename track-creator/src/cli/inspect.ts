/**
 * Authoring helper: prints the track's total length and, for every control
 * point, the arc-length (dist) of the nearest centerline station — so you know
 * which dist ranges to use for curbs / widthProfile keys.
 *
 *   npm run elevation -- tracks/demo   (not required)
 *   npx tsx src/cli/inspect.ts tracks/demo
 */

import { resampleCenterline } from "../centerline.ts";
import { loadTrack } from "../io.ts";

const { def } = loadTrack(process.argv[2]);
const { points, totalLength } = resampleCenterline(def.controlPoints, def.road.step, def.closed);

console.log(`track "${def.name}": totalLength = ${totalLength.toFixed(1)} m, ${points.length} stations @ ${def.road.step} m`);
console.log("control point → nearest centerline dist:");
def.controlPoints.forEach((cp, i) => {
  let best = 0;
  let bestD2 = Infinity;
  for (const p of points) {
    const d2 = (p.x - cp.x) ** 2 + (p.z - cp.z) ** 2;
    if (d2 < bestD2) {
      bestD2 = d2;
      best = p.dist;
    }
  }
  console.log(
    `  #${String(i).padStart(2)} (${String(cp.x).padStart(6)}, ${String(cp.z).padStart(6)})  →  dist ${best.toFixed(0).padStart(5)} m`,
  );
});
