import { expect,it } from 'vitest';
import { buildCgFenceGeometry,insideContextPolygon } from './FujiCgContext';
it('uses a single fence plane with continuous metre UVs across a corner',()=>{
  const g=buildCgFenceGeometry([{type:'fence',height:3,points:[[0,0],[4,0],[4,3]]}],()=>2);
  expect(g.getAttribute('position').count).toBe(8);
  expect([...g.getAttribute('uv').array]).toEqual([0,0,4,0,0,3,4,3,4,0,7,0,4,3,7,3]);
  expect(g.index!.count).toBe(12);
});
it('restricts restored canopy to the source-reviewed woodland footprint',()=>{
  const polygon:[number,number][]=[[0,0],[10,0],[10,10],[0,10]];
  expect(insideContextPolygon(5,5,polygon)).toBe(true);
  expect(insideContextPolygon(12,5,polygon)).toBe(false);
});
