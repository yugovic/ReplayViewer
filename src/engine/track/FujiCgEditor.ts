import type { FujiCgGeometry } from './FujiCgStudy';

export interface CurbAdjustment { index: number; offsetMeters: number; widthScale: number }
export interface CgEditor {
  spans: { index: number; side: string; start: number; end: number }[];
  apply: (change: CurbAdjustment) => void;
  reset: () => void;
  export: () => string;
}
let active: CgEditor | null = null;
export const getCgEditor = () => active;
export function registerCgEditor(editor: CgEditor) {
  active = editor;
  return () => { if (active === editor) active = null; };
}

/** Rebuild from the immutable source each time so dragging never accumulates drift. */
export function adjustCgGeometry(source: FujiCgGeometry, adjustments: CurbAdjustment[]): FujiCgGeometry {
  const result = structuredClone(source);
  for (const { index, offsetMeters, widthScale } of adjustments) {
    if (!Number.isInteger(index) || !result.curbs[index] || !Number.isFinite(offsetMeters) ||
      !Number.isFinite(widthScale) || Math.abs(offsetMeters) > .5 || widthScale < .7 || widthScale > 1.3) {
      throw new Error('調整範囲外です。位置は±0.5m、幅は70〜130%で指定してください。');
    }
    const curb = result.curbs[index], original = source.curbs[index];
    const col = curb.side === 'left' ? 7 : 9;
    for (let i = 0; i < curb.rows.length; i++) {
      const row = curb.rows[i], r = original.rows[i];
      const width = Math.hypot(r[3]-r[1], r[4]-r[2]);
      const nx = (r[3]-r[1])/width, nz = (r[4]-r[2])/width;
      row[1] = r[1]+nx*offsetMeters; row[2] = r[2]+nz*offsetMeters;
      row[3] = row[1]+nx*width*widthScale; row[4] = row[2]+nz*width*widthScale;
      const road = result.road[Math.round(row[0]/.5)];
      road[col] = row[1]; road[col+1] = row[2];
    }
    for (const [endpoint, direction] of [[0, -1], [curb.rows.length-1, 1]]) {
      const r = original.rows[endpoint], p = curb.rows[endpoint];
      for (let step = 1; step < 13; step++) {
        const at = Math.round(r[0]/.5)+direction*step;
        const road = result.road[at];
        if (!road) break;
        const occupied = source.curbs.some(c => c.side === curb.side && c !== original && road[0] >= c.rows[0][0] && road[0] <= c.rows.at(-1)![0]);
        if (occupied) break;
        const t = 1-step/12, weight = t*t*(3-2*t);
        road[col]! += (p[1]-r[1])*weight; road[col+1]! += (p[2]-r[2])*weight;
      }
    }
  }
  return result;
}
