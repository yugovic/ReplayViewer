/**
 * LandmarkBuilder – recognisable stadium landmarks for tagged buildings that
 * would otherwise be dull extruded boxes (Features3DBuilder.buildBuildings):
 *
 *  - buildGrandstand: stepped spectator tiers (front low → back high) + a back
 *    wall + a thin cantilever roof slab on columns. Seat treads carry a muted
 *    2-3 colour stripe (vertex colour) so it reads as a grandstand, not a wedge.
 *  - buildCanopy: a wall-less "columns + roof slab" only (pit-garage 'roof').
 *  - buildControlTower: podium + slimmer shaft + wider glass control room with
 *    an overhanging roof slab and a thin antenna mast (race-control tower).
 *
 * Both bake flat-shaded geometry into plain position+color soups so the caller
 * can merge them into the SAME BufferGeometry as the box buildings, keeping the
 * whole building layer at 2 draw calls (one mesh + one EdgesGeometry outline).
 * All colours are constant (no per-instance hashing) so screenshots stay stable.
 *
 * Orientation comes from the footprint's minimum-area oriented bounding box
 * (convex hull + per-edge caliper scan — exact rotating calipers is overkill
 * for these small 4-8 vertex footprints). The long OBB axis is the stand's
 * length; tiers rise along the short axis. Which short-axis end is the "front"
 * (low, course-facing) side is chosen from an optional front hint (nearest
 * track-centreline point); without a hint either side is acceptable.
 */

export interface LandmarkGeometry {
  positions: number[];
  colors: number[];
}

export interface FootprintOrientation {
  /** Centre of the oriented bounding box, world XZ. */
  cx: number;
  cz: number;
  /** Long-axis unit vector (stand length direction), world XZ. */
  ux: number;
  uz: number;
  /** Short-axis unit vector pointing FRONT→BACK (rising side), world XZ. */
  vx: number;
  vz: number;
  /** Half extent along the long axis (u). */
  halfLen: number;
  /** Half extent along the short axis (v). */
  halfWid: number;
}

// ─── Convex hull (Andrew's monotone chain, XZ plane) ────────────────────────

function convexHull(points: Array<[number, number]>): Array<[number, number]> {
  const pts = points
    .filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]))
    .slice()
    .sort((a, b) => (a[0] === b[0] ? a[1] - b[1] : a[0] - b[0]));
  if (pts.length < 3) return pts;
  const cross = (o: number[], a: number[], b: number[]) =>
    (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower: Array<[number, number]> = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0)
      lower.pop();
    lower.push(p);
  }
  const upper: Array<[number, number]> = [];
  for (let i = pts.length - 1; i >= 0; i -= 1) {
    const p = pts[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0)
      upper.pop();
    upper.push(p);
  }
  lower.pop();
  upper.pop();
  return lower.concat(upper);
}

/**
 * Minimum-area oriented bounding box of a footprint, then oriented so +v runs
 * front→back. Scans every hull edge as a candidate box axis and keeps the
 * smallest-area rectangle (calipers-free, fine for tiny footprints).
 */
export function computeFootprintOrientation(
  footprint: Array<[number, number]>,
  front?: { x: number; z: number } | null,
): FootprintOrientation {
  const hull = convexHull(footprint);
  let best: {
    area: number;
    ux: number;
    uz: number;
    cx: number;
    cz: number;
    halfU: number;
    halfV: number;
  } | null = null;

  const n = hull.length;
  for (let i = 0; i < n; i += 1) {
    const a = hull[i];
    const b = hull[(i + 1) % n];
    let ex = b[0] - a[0];
    let ez = b[1] - a[1];
    const len = Math.hypot(ex, ez);
    if (len < 1e-6) continue;
    ex /= len;
    ez /= len;
    // Perpendicular axis.
    const px = -ez;
    const pz = ex;
    let minE = Infinity;
    let maxE = -Infinity;
    let minP = Infinity;
    let maxP = -Infinity;
    for (const h of hull) {
      const de = h[0] * ex + h[1] * ez;
      const dp = h[0] * px + h[1] * pz;
      if (de < minE) minE = de;
      if (de > maxE) maxE = de;
      if (dp < minP) minP = dp;
      if (dp > maxP) maxP = dp;
    }
    const w = maxE - minE;
    const d = maxP - minP;
    const area = w * d;
    if (best === null || area < best.area) {
      const midE = (minE + maxE) / 2;
      const midP = (minP + maxP) / 2;
      best = {
        area,
        ux: ex,
        uz: ez,
        cx: midE * ex + midP * px,
        cz: midE * ez + midP * pz,
        halfU: w / 2,
        halfV: d / 2,
      };
    }
  }

  // Degenerate fallback (collinear/empty): axis-aligned unit box at centroid.
  if (best === null) {
    let sx = 0;
    let sz = 0;
    for (const p of footprint) {
      sx += p[0];
      sz += p[1];
    }
    const c = Math.max(footprint.length, 1);
    return { cx: sx / c, cz: sz / c, ux: 1, uz: 0, vx: 0, vz: 1, halfLen: 1, halfWid: 1 };
  }

  // Long axis = u (larger extent), short axis = v = u rotated +90°.
  let ux: number;
  let uz: number;
  let halfLen: number;
  let halfWid: number;
  if (best.halfU >= best.halfV) {
    ux = best.ux;
    uz = best.uz;
    halfLen = best.halfU;
    halfWid = best.halfV;
  } else {
    // Perpendicular edge is the longer axis.
    ux = -best.uz;
    uz = best.ux;
    halfLen = best.halfV;
    halfWid = best.halfU;
  }
  let vx = -uz;
  let vz = ux;

  // Orient +v to point away from the front hint (front = low, near the track).
  if (front) {
    const toCenterX = best.cx - front.x;
    const toCenterZ = best.cz - front.z;
    if (toCenterX * vx + toCenterZ * vz < 0) {
      vx = -vx;
      vz = -vz;
    }
  }

  return { cx: best.cx, cz: best.cz, ux, uz, vx, vz, halfLen, halfWid };
}

// ─── Local-frame box baker ──────────────────────────────────────────────────

/** Grandstand palette: two muted greys + a subdued blue for the seat stripes. */
const TIER_STRIPE: Array<[number, number, number]> = [
  [0.55, 0.56, 0.57], // grey
  [0.68, 0.69, 0.70], // light grey
  [0.247, 0.435, 0.69], // #3f6fb0-ish blue
];
const STRUCT_WALL: [number, number, number] = [0.6, 0.6, 0.58]; // back wall / columns
const STRUCT_ROOF: [number, number, number] = [0.49, 0.5, 0.52]; // thin roof slab

/**
 * Append one axis-in-local box, transformed by the OBB frame, to the soups.
 * Local (a along u, b along v, y up); world = centre + a*u + b*v, Y = baseY+y.
 */
function pushBox(
  o: FootprintOrientation,
  baseY: number,
  a0: number,
  a1: number,
  b0: number,
  b1: number,
  y0: number,
  y1: number,
  color: [number, number, number],
  out: LandmarkGeometry,
): void {
  const p = (a: number, b: number, y: number): [number, number, number] => [
    o.cx + a * o.ux + b * o.vx,
    baseY + y,
    o.cz + a * o.uz + b * o.vz,
  ];
  // 8 corners.
  const c000 = p(a0, b0, y0);
  const c100 = p(a1, b0, y0);
  const c110 = p(a1, b1, y0);
  const c010 = p(a0, b1, y0);
  const c001 = p(a0, b0, y1);
  const c101 = p(a1, b0, y1);
  const c111 = p(a1, b1, y1);
  const c011 = p(a0, b1, y1);
  const quad = (
    A: number[],
    B: number[],
    C: number[],
    D: number[],
  ): void => {
    // Two triangles A-B-C, A-C-D.
    out.positions.push(...A, ...B, ...C, ...A, ...C, ...D);
    for (let k = 0; k < 6; k += 1) out.colors.push(color[0], color[1], color[2]);
  };
  quad(c001, c101, c111, c011); // top (+y)
  quad(c010, c110, c100, c000); // bottom (-y)
  quad(c000, c100, c101, c001); // front (-v)
  quad(c110, c010, c011, c111); // back (+v)
  quad(c010, c000, c001, c011); // left (-u)
  quad(c100, c110, c111, c101); // right (+u)
}

// ─── Grandstand ─────────────────────────────────────────────────────────────

const TIER_COUNT = 6;

/**
 * Stepped grandstand: TIER_COUNT nested terraces rising front→back, a back
 * wall, a thin cantilever roof slab and support columns. The nested terraces
 * (each taller and starting further back than the one in front) form the stair
 * silhouette; the caller's EdgesGeometry outlines every riser.
 */
export function buildGrandstand(
  footprint: Array<[number, number]>,
  height: number,
  baseY: number,
  orientation: FootprintOrientation,
): LandmarkGeometry {
  const out: LandmarkGeometry = { positions: [], colors: [] };
  const o = orientation;
  const L = o.halfLen;
  const W = o.halfWid;

  const seatBase = height * 0.12; // front terrace still has a low riser
  const seatTop = height * 0.62; // highest terrace top
  const wallT = Math.min(W * 0.16, 1.2); // back-wall thickness
  const seatFront = -W; // course side (low)
  const seatBack = W - wallT; // seating stops at the back wall
  const seatSpan = seatBack - seatFront;
  const tierDepth = seatSpan / TIER_COUNT;

  // Nested terraces: terrace i spans b∈[bStart_i, seatBack], y∈[0, topY_i].
  for (let i = 0; i < TIER_COUNT; i += 1) {
    const bStart = seatFront + i * tierDepth;
    const topY = seatBase + ((i + 1) / TIER_COUNT) * (seatTop - seatBase);
    pushBox(o, baseY, -L, L, bStart, seatBack, 0, topY, TIER_STRIPE[i % TIER_STRIPE.length], out);
  }

  // Back wall (full height, behind the seating).
  pushBox(o, baseY, -L, L, W - wallT, W, 0, height * 0.92, STRUCT_WALL, out);

  // Thin cantilever roof slab over the seating.
  const roofT = 0.5;
  pushBox(o, baseY, -L, L, -W * 0.35, W, height - roofT, height, STRUCT_ROOF, out);

  // Support columns along the back edge (4-8 depending on stand length).
  const colCount = Math.max(4, Math.min(8, Math.round((2 * L) / 12) + 1));
  const colW = Math.min(0.6, L * 0.06);
  const colB = W - wallT; // just inside the back wall
  for (let i = 0; i < colCount; i += 1) {
    const t = colCount === 1 ? 0.5 : i / (colCount - 1);
    const a = -L + t * (2 * L);
    pushBox(o, baseY, a - colW, a + colW, colB - colW * 2, colB, 0, height - roofT, STRUCT_WALL, out);
  }

  return out;
}

// ─── Control tower ──────────────────────────────────────────────────────────

/** Glass band of the control room: subdued blue, clearly "windows" next to the
 * grey structural palette. */
const TOWER_GLASS: [number, number, number] = [0.32, 0.47, 0.62];

/**
 * Race-control tower: a full-footprint podium, a slimmer shaft, then a wider
 * all-round-glass control room under an overhanging roof slab, topped by a
 * thin antenna mast. The control room sits slightly toward the front (-v,
 * course-facing) edge so the tower reads as overlooking the track.
 */
export function buildControlTower(
  footprint: Array<[number, number]>,
  height: number,
  baseY: number,
  orientation: FootprintOrientation,
): LandmarkGeometry {
  const out: LandmarkGeometry = { positions: [], colors: [] };
  const o = orientation;
  const L = o.halfLen;
  const W = o.halfWid;

  // nDSM height for a slender tower averages in lower surroundings (fuji's
  // control centre measures 15.3 m yet clearly tops the 17 m pit roofline in
  // photos). Give the landmark a minimum visual height so it reads as the
  // tower it is — visualization choice, not surveyed truth.
  const h = Math.max(height, 20);
  const podiumTop = h * 0.34;
  const shaftTop = h * 0.66;
  const glassTop = h * 0.9;

  // Podium: the whole footprint, two-storey block.
  pushBox(o, baseY, -L, L, -W, W, 0, podiumTop, STRUCT_WALL, out);

  // Shaft: slimmer, biased toward the front (course) edge.
  const shaftL = L * 0.55;
  const shaftFront = -W;
  const shaftBack = W * 0.45;
  pushBox(o, baseY, -shaftL, shaftL, shaftFront, shaftBack, podiumTop, shaftTop, STRUCT_WALL, out);

  // Control room: wider than the shaft with an all-round glass band.
  const roomL = Math.min(L * 0.8, shaftL + Math.min(2.5, L * 0.25));
  const roomFront = -W * 1.08; // slight cantilever over the course side
  const roomBack = W * 0.6;
  pushBox(o, baseY, -roomL, roomL, roomFront, roomBack, shaftTop, glassTop, TOWER_GLASS, out);

  // Overhanging roof slab.
  const roofT = Math.min(0.6, h * 0.05);
  pushBox(
    o,
    baseY,
    -roomL * 1.12,
    roomL * 1.12,
    roomFront - W * 0.08,
    roomBack + W * 0.08,
    glassTop,
    glassTop + roofT,
    STRUCT_ROOF,
    out,
  );

  // Antenna mast: thin, on one roof corner, up to ~1.25× the tower height.
  const mastR = 0.2;
  const mastA = roomL * 0.7;
  const mastB = (roomFront + roomBack) / 2;
  pushBox(o, baseY, mastA - mastR, mastA + mastR, mastB - mastR, mastB + mastR, glassTop + roofT, h * 1.25, STRUCT_WALL, out);

  return out;
}

// ─── Canopy (columns + roof slab, no walls) ─────────────────────────────────

/**
 * Wall-less canopy for pit-garage 'roof' footprints: a thin roof slab on corner
 * + mid columns. No tiers or walls, so its vertical (side) surface area is a
 * small fraction of a solid box of the same footprint.
 */
export function buildCanopy(
  footprint: Array<[number, number]>,
  height: number,
  baseY: number,
): LandmarkGeometry {
  const out: LandmarkGeometry = { positions: [], colors: [] };
  const o = computeFootprintOrientation(footprint);
  const L = o.halfLen;
  const W = o.halfWid;
  const roofT = Math.min(0.5, height * 0.3);

  // Roof slab covering the full footprint.
  pushBox(o, baseY, -L, L, -W, W, height - roofT, height, STRUCT_ROOF, out);

  // Columns: four corners plus a mid-span pair if long.
  const colW = Math.min(0.4, Math.min(L, W) * 0.18);
  const inset = colW * 1.5;
  const aPositions = 2 * L > 14 ? [-L + inset, 0, L - inset] : [-L + inset, L - inset];
  const bPositions = [-W + inset, W - inset];
  for (const a of aPositions) {
    for (const b of bPositions) {
      pushBox(o, baseY, a - colW, a + colW, b - colW, b + colW, 0, height - roofT, STRUCT_WALL, out);
    }
  }

  return out;
}
