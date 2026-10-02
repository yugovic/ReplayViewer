import type { CgRoadRow, FujiCgGeometry } from './FujiCgStudy';
import type { CurbAdjustment } from './FujiCgEditor';

/**
 * Kerb zones for the Fuji CG study (cg_study/kerb_zones.json, BACKLOG
 * GPS-REG-14). Per kerb, three image-traced lines on the geometry.json 0.5 m
 * station grid:
 *   paint -> block : flat white strip (track-limit paint) at road level
 *   block -> outer : raised red/white block band
 * Lines come from the 2019 VIRTUAL SHIZUOKA orthophoto (CC BY 4.0). They are
 * image boundaries, not surveyed kerb geometry, and not GPS ground truth.
 *
 * The file is optional. Anything that fails validation falls back to the
 * geometry.json kerb it would have replaced, so the viewer never shows a
 * double kerb or a missing kerb because of a bad zone.
 */

export type XZ = [number, number];
export type KerbSide = 'left' | 'right';

export interface KerbZone {
  id: string;
  side: KerbSide;
  /** Index in geometry.curbs this zone replaces; null = kerb missing from the CG (e.g. Dunlop-R). */
  replaces: number | null;
  station: number[];
  paint: XZ[];
  block: XZ[];
  outer: XZ[];
  /** Per station (segment start): 1 = red block, 0 = white block. */
  red: number[];
  /** zone = from the zones file, cg = copied from the replaced CG kerb, illustrative = assumed alternation. */
  redSource: 'zone' | 'cg' | 'illustrative';
}

export interface KerbZoneSet {
  status: 'absent' | 'invalid' | 'loaded';
  zones: KerbZone[];
  rejected: { id: string; reason: string }[];
  source?: string;
  license?: string;
  surveyTruth?: boolean;
  geometrySha256?: string;
  error?: string;
}

export const KERB_ZONE_VERSION = 3;
/** Sanity bounds; the 2019 ortho tracing gives strips 0.26-0.70 m and bands 0.55-1.94 m. */
export const KERB_ZONE_LIMITS = { stripMin: .1, stripMax: 1.2, bandMin: .2, bandMax: 3.5, maxEdgeDistance: 4 } as const;
/** Median CG red/white block run (5 stations = 2.5 m); only used when no observed pattern exists. */
export const ILLUSTRATIVE_BLOCK_STATIONS = 5;
const STEP = .5;

/** Road edge vertex on the given side, as the CG road mesh draws it. */
export function roadEdgeAt(row: CgRoadRow, side: KerbSide): XZ {
  if (side === 'left') return row[7] !== undefined && row[8] !== undefined ? [row[7], row[8]] : [row[1] + row[3]*row[5], row[2] + row[4]*row[5]];
  return row[9] !== undefined && row[10] !== undefined ? [row[9], row[10]] : [row[1] + row[3]*row[6], row[2] + row[4]*row[6]];
}

/** Unit horizontal normal pointing away from the road on the given side. */
export function outwardAt(row: CgRoadRow, side: KerbSide): XZ {
  const l = roadEdgeAt(row, 'left'), r = roadEdgeAt(row, 'right');
  let dx = r[0]-l[0], dz = r[1]-l[1];
  const len = Math.hypot(dx, dz);
  if (len > 1e-6) { dx /= len; dz /= len; } else { dx = row[3]; dz = row[4]; }
  return side === 'left' ? [-dx, -dz] : [dx, dz];
}

/** Signed lateral (the road row's row[5]..row[6] parameter) of an XZ point.
 * Inverse of the road mesh's edge interpolation, so surface heights sampled
 * with it agree with the road at the same XZ. */
export function lateralAt(row: CgRoadRow, x: number, z: number): number {
  const l = roadEdgeAt(row, 'left'), r = roadEdgeAt(row, 'right');
  const dx = r[0]-l[0], dz = r[1]-l[1], len2 = dx*dx + dz*dz;
  if (len2 < 1e-9) return (x-row[1])*row[3] + (z-row[2])*row[4];
  const t = ((x-l[0])*dx + (z-l[1])*dz)/len2;
  return row[5] + (row[6]-row[5])*t;
}

/** Signed distance of a point beyond the road edge (+ = outside the road). */
export function offsetFromEdge(row: CgRoadRow, side: KerbSide, p: XZ): number {
  const e = roadEdgeAt(row, side), n = outwardAt(row, side);
  return (p[0]-e[0])*n[0] + (p[1]-e[1])*n[1];
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const isXZList = (v: unknown, n: number): v is XZ[] =>
  Array.isArray(v) && v.length === n && v.every(p => Array.isArray(p) && p.length === 2 && finite(p[0]) && finite(p[1]));
const median = (values: number[]) => { const s = [...values].sort((a, b) => a-b); return s.length ? (s[(s.length-1) >> 1] + s[s.length >> 1])/2 : NaN; };

/** Validate one zone against the delivered road grid. Returns a reason when unusable. */
function checkLines(id: string, side: KerbSide, station: unknown, k: Record<string, unknown>, geometry: FujiCgGeometry): string | null {
  if (!Array.isArray(station) || station.length < 2 || !station.every(finite)) return 'station must be >= 2 finite values';
  for (let j = 0; j < station.length; j++) {
    const index = Math.round(station[j]/STEP), row = geometry.road[index];
    if (!row || Math.abs(row[0]-station[j]) > 1e-6) return `station ${station[j]} is not on the geometry.json 0.5 m grid`;
    if (j && Math.abs(station[j]-station[j-1]-STEP) > 1e-6) return `stations must be contiguous 0.5 m steps (at ${station[j]})`;
  }
  for (const key of ['paint', 'block', 'outer']) if (!isXZList(k[key], station.length)) return `${key} must be ${station.length} finite [x,z] pairs`;
  const paint = k.paint as XZ[], block = k.block as XZ[], outer = k.outer as XZ[];
  const L = KERB_ZONE_LIMITS;
  for (let j = 0; j < station.length; j++) {
    const row = geometry.road[Math.round(station[j]/STEP)];
    const p = offsetFromEdge(row, side, paint[j]), b = offsetFromEdge(row, side, block[j]), o = offsetFromEdge(row, side, outer[j]);
    if (Math.abs(p) > L.maxEdgeDistance) return `${id}: paint ${p.toFixed(2)} m from the road edge at ${station[j]} m (frame mismatch?)`;
    if (b-p < L.stripMin || b-p > L.stripMax) return `${id}: white strip ${(b-p).toFixed(2)} m at ${station[j]} m`;
    if (o-b < L.bandMin || o-b > L.bandMax) return `${id}: block band ${(o-b).toFixed(2)} m at ${station[j]} m`;
  }
  return null;
}

/** Parse + validate kerb_zones.json against geometry.json. Never throws. */
export function resolveKerbZones(raw: unknown, geometry: FujiCgGeometry): KerbZoneSet {
  if (raw === null || raw === undefined) return { status: 'absent', zones: [], rejected: [] };
  if (!isObject(raw)) return { status: 'invalid', zones: [], rejected: [], error: 'kerb zones file is not an object' };
  if (raw.version !== KERB_ZONE_VERSION) return { status: 'invalid', zones: [], rejected: [], error: `unsupported kerb zones version ${String(raw.version)}` };
  if (raw.trackId !== 'fuji') return { status: 'invalid', zones: [], rejected: [], error: `kerb zones are for track ${String(raw.trackId)}` };
  if (!Array.isArray(raw.kerbs)) return { status: 'invalid', zones: [], rejected: [], error: 'kerbs must be an array' };
  const set: KerbZoneSet = { status: 'loaded', zones: [], rejected: [],
    source: typeof raw.source === 'string' ? raw.source : undefined,
    license: typeof raw.license === 'string' ? raw.license : undefined,
    // Never upgraded to survey truth by the viewer, whatever the file says.
    surveyTruth: false,
    geometrySha256: typeof raw.geometrySha256 === 'string' ? raw.geometrySha256 : undefined };
  const claimed = new Set<number>();
  const accepted: KerbZone[] = [];
  for (const k of raw.kerbs) {
    const id = isObject(k) && typeof k.id === 'string' ? k.id : '?';
    const reject = (reason: string) => { set.rejected.push({ id, reason }); };
    if (!isObject(k)) { reject('not an object'); continue; }
    const side = k.side;
    if (side !== 'left' && side !== 'right') { reject(`side ${String(side)}`); continue; }
    const problem = checkLines(id, side, k.station, k, geometry);
    if (problem) { reject(problem); continue; }
    const station = k.station as number[];
    const start = station[0], end = station[station.length-1];
    let replaces: number | null = null;
    if (k.addedOutsideCg === true) {
      const clash = geometry.curbs.findIndex(c => c.side === side && c.rows[0][0] <= end && c.rows[c.rows.length-1][0] >= start);
      if (clash >= 0) { reject(`added kerb overlaps CG kerb ${clash} on the ${side} side`); continue; }
    } else {
      const match = /^k(\d+)$/.exec(id);
      const index = match ? Number(match[1]) : -1, curb = geometry.curbs[index];
      if (!curb) { reject('no CG kerb with this index'); continue; }
      if (curb.side !== side || curb.rows.length !== station.length || curb.rows[0][0] !== start || curb.rows[curb.rows.length-1][0] !== end) {
        reject(`does not match CG kerb ${index} (${curb.side} ${curb.rows[0][0]}-${curb.rows[curb.rows.length-1][0]} m)`); continue;
      }
      if (claimed.has(index)) { reject(`CG kerb ${index} is already replaced`); continue; }
      replaces = index;
    }
    if (accepted.some(z => z.side === side && z.station[0] <= end && z.station[z.station.length-1] >= start)) { reject('overlaps another zone'); continue; }
    let red: number[], redSource: KerbZone['redSource'];
    if (Array.isArray(k.red) && k.red.length === station.length && k.red.every(v => v === 0 || v === 1)) { red = k.red as number[]; redSource = 'zone'; }
    else if (replaces !== null) { red = geometry.curbs[replaces].rows.map(r => r[7] ? 1 : 0); redSource = 'cg'; }
    else { red = station.map((_, j) => Math.floor(j/ILLUSTRATIVE_BLOCK_STATIONS) % 2); redSource = 'illustrative'; }
    if (replaces !== null) claimed.add(replaces);
    accepted.push({ id, side, replaces, station: [...station], paint: (k.paint as XZ[]).map(p => [p[0], p[1]]),
      block: (k.block as XZ[]).map(p => [p[0], p[1]]), outer: (k.outer as XZ[]).map(p => [p[0], p[1]]), red, redSource });
  }
  set.zones = accepted;
  return set;
}

/** Median strip (paint->block) and band (block->outer) widths of a zone, metres. */
export function zoneWidths(zone: KerbZone) {
  return {
    strip: median(zone.paint.map((p, j) => Math.hypot(zone.block[j][0]-p[0], zone.block[j][1]-p[1]))),
    band: median(zone.block.map((b, j) => Math.hypot(zone.outer[j][0]-b[0], zone.outer[j][1]-b[1]))),
  };
}

/** Apply the interactive CG kerb edits (FujiCgEditor) to the zones that
 * replace those kerbs: same lateral shift along the CG kerb normal, width
 * scale on the block band. Always rebuilt from the unedited zones. */
export function adjustKerbZones(zones: readonly KerbZone[], source: FujiCgGeometry, adjustments: readonly CurbAdjustment[]): KerbZone[] {
  if (!adjustments.length) return [...zones];
  const byIndex = new Map(adjustments.map(a => [a.index, a]));
  return zones.map(zone => {
    const change = zone.replaces === null ? undefined : byIndex.get(zone.replaces);
    if (!change || zone.replaces === null) return zone;
    const rows = source.curbs[zone.replaces].rows;
    const paint: XZ[] = [], block: XZ[] = [], outer: XZ[] = [];
    zone.station.forEach((_, j) => {
      const r = rows[j], w = Math.hypot(r[3]-r[1], r[4]-r[2]) || 1;
      const nx = (r[3]-r[1])/w, nz = (r[4]-r[2])/w, d = change.offsetMeters;
      const p = zone.paint[j], b = zone.block[j], o = zone.outer[j];
      const bx = b[0]+nx*d, bz = b[1]+nz*d;
      paint.push([p[0]+nx*d, p[1]+nz*d]);
      block.push([bx, bz]);
      outer.push([bx+(o[0]-b[0])*change.widthScale, bz+(o[1]-b[1])*change.widthScale]);
    });
    return { ...zone, paint, block, outer };
  });
}

/** Fetch the optional zones file; resolves null when missing or unreadable. */
export async function fetchKerbZonesJson(url: string): Promise<unknown> {
  try {
    const response = await fetch(url);
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}
