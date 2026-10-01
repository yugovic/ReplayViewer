import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { adjustCgGeometry } from './FujiCgEditor';
import { buildCgRoadGeometry, type FujiCgGeometry } from './FujiCgStudy';
import type { TrackData } from '../../replay/types';
const data = JSON.parse(readFileSync('public/data/tracks/fuji/cg_study/geometry.json', 'utf8')) as FujiCgGeometry;
const track = JSON.parse(readFileSync('public/data/tracks/fuji/track.json', 'utf8')) as TrackData;
describe('interactive curb adjustments', () => {
  it('preserves the source, local width changes and exact road contact on both sides', () => {
    const original = JSON.stringify(data);
    const changes = [{ index: 3, offsetMeters: .4, widthScale: 1.2 }, { index: 17, offsetMeters: -.3, widthScale: .8 }];
    const result = adjustCgGeometry(data, changes);
    for (const change of changes) {
      const c = result.curbs[change.index], col = c.side === 'left' ? 7 : 9;
      c.rows.forEach((r, i) => {
        const old = data.curbs[change.index].rows[i];
        expect(Math.hypot(r[3]-r[1], r[4]-r[2])).toBeCloseTo(Math.hypot(old[3]-old[1], old[4]-old[2])*change.widthScale, 6);
        expect(result.road[Math.round(r[0]/.5)].slice(col, col+2)).toEqual(r.slice(1, 3));
      });
    }
    expect(JSON.stringify(data)).toEqual(original);
    expect(adjustCgGeometry(data, [])).toEqual(data);
  });
  it('does not fold rendered triangles at adjustment limits in the hairpin', () => {
    for (const offset of [-.5, .5]) for (const scale of [.7, 1.3]) {
      const adjusted = adjustCgGeometry(data, [{ index: 17, offsetMeters: offset, widthScale: scale }]);
      const batches = buildCgRoadGeometry(track, adjusted);
      let failures = 0;
      for (const key of ['road', 'paint', 'red', 'white'] as const) {
        const b = batches[key];
        for (let i = 0; i < b.indices.length; i += 3) {
          const [a,c,d] = b.indices.slice(i,i+3).map(j => b.positions.slice(j*3,j*3+3));
          const y = (c[2]-a[2])*(d[0]-a[0])-(c[0]-a[0])*(d[2]-a[2]);
          if (y < -1e-7 || ![...a,...c,...d].every(Number.isFinite)) failures++;
        }
      }
      expect(failures).toBe(0);
    }
  });
  it('rejects invalid edit values', () => {
    for (const change of [{ index: -1, offsetMeters: 0, widthScale: 1 }, { index: 0, offsetMeters: NaN, widthScale: 1 }, { index: 0, offsetMeters: 1, widthScale: 1 }]) {
      expect(() => adjustCgGeometry(data, [change])).toThrow();
    }
  });
});
