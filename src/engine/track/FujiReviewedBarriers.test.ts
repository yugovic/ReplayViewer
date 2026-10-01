import { describe, expect, it } from 'vitest';
import { replaceReviewedBarriers, type ReviewedBarriers } from './FujiReviewedBarriers';
import { readFileSync } from 'node:fs';

describe('source-reviewed barrier replacement',()=>{
  const review:ReviewedBarriers={version:1,bounds:[0,0,10,10],barriers:[],source:'fixture',sourceSha256:'fixture'};
  it('clips crossing segments with no vertices inside the reviewed area',()=>{
    const result=replaceReviewedBarriers([{type:'guard_rail',height:.75,points:[[-5,5],[15,5]]}],review);
    expect(result.map(r=>r.points)).toEqual([[[-5,5],[0,5]],[[10,5],[15,5]]]);
  });
  it('preserves outside geometry and never connects across a removed interval',()=>{
    const original=[{type:'guard_rail',height:.75,points:[[-5,5],[5,5],[15,5]] as [number,number][]}];
    const snapshot=JSON.stringify(original);
    expect(replaceReviewedBarriers(original,review).map(b=>b.points)).toEqual([[[-5,5],[0,5]],[[10,5],[15,5]]]);
    expect(JSON.stringify(original)).toBe(snapshot);
  });
  it('places the reviewed entry barrier beyond the photographed grass strip',()=>{
    const delivered=JSON.parse(readFileSync('public/data/tracks/fuji/cg_study/barriers-reviewed.json','utf8')) as ReviewedBarriers;
    // Frozen source-image control points, not the old line-offset prior.
    const first=delivered.barriers[0];
    expect(first.points.some(([x,z])=>Math.hypot(x-346.0,z-232.9)<1)).toBe(true);
    expect(delivered.sourceSha256).toHaveLength(64);
  });
});
