import { describe, expect, it } from "vitest";
import { buildCgRoadGeometry, type FujiCgGeometry } from "./FujiCgStudy";
import type { TrackData } from "../../replay/types";
import { readFileSync } from "node:fs";

const track={width:13,totalLength:10,origin:{lat:0,lng:0,alt:0},centerline:[
  {dist:0,x:0,y:0,z:0,alt:0},{dist:10,x:10,y:0,z:0,alt:0},
]} as TrackData;
const data:FujiCgGeometry={version:1,source:"test fixture",limitations:[],curbHeightMeters:.055,
  road:[[0,0,0,0,1,-2,8],[10,10,0,0,1,-3,9]],green:[],curbs:[
    {side:"left",rows:[[0,0,-2,0,-2.8,-2,-2.8,1,1],[10,10,-3,10,-4.5,-3,-4.5,0,1]]},
    {side:"right",rows:[[0,0,8,0,9.3,8,9.3,0,1],[10,10,9,10,10.1,9,10.1,1,1]]},
  ]};

describe("source-traced CG road",()=>{
  it("keeps asymmetric road edges and independently traced variable-width curbs",()=>{
    const b=buildCgRoadGeometry(track,data);
    const zs=b.road.positions.filter((_,i)=>i%3===2);
    expect(Math.min(...zs)).toBe(-3);expect(Math.max(...zs)).toBe(9);
    const left=b.red.positions.filter((_,i)=>i%3===2);
    expect(Math.min(...left)).toBe(-4.5);expect(Math.max(...left)).toBe(-2);
    const right=b.white.positions.filter((_,i)=>i%3===2);
    expect(Math.max(...right)).toBe(10.1);expect(Math.min(...right)).toBe(8);
  });
  it("faces the road and both curb tops upward, without mirrored-side culling",()=>{
    const batches=buildCgRoadGeometry(track,data);
    for(const b of Object.values(batches)) for(let i=0;i<b.indices.length;i+=3) {
      const [a,c,d]=b.indices.slice(i,i+3).map(j=>b.positions.slice(j*3,j*3+3));
      const y=(c[2]-a[2])*(d[0]-a[0])-(c[0]-a[0])*(d[2]-a[2]);
      expect(y).toBeGreaterThanOrEqual(-1e-8);
      expect([...a,...c,...d].every(Number.isFinite)).toBe(true);
    }
  });
});

describe("Fuji delivered geometry regressions",()=>{
  const sourceTrack=JSON.parse(readFileSync("public/data/tracks/fuji/track.json","utf8")) as TrackData;
  const sourceData=JSON.parse(readFileSync("public/data/tracks/fuji/cg_study/geometry.json","utf8")) as FujiCgGeometry;
  it("does not mistake the gantry at 2362m for a narrower road",()=>{
    const row=sourceData.road[2362*2];
    const before=sourceData.road[2358*2],after=sourceData.road[2366*2];
    expect(Math.abs(row[6]-(before[6]+after[6])/2)).toBeLessThan(.2);
  });
  it("has no downward-facing road or marking triangles anywhere in the loop",()=>{
    const batches=buildCgRoadGeometry(sourceTrack,sourceData);
    const failures:string[]=[];
    for(const name of ["road","paint","red","white"] as const) {
      const b=batches[name];
      for(let i=0;i<b.indices.length;i+=3) {
        const [a,c,d]=b.indices.slice(i,i+3).map(j=>b.positions.slice(j*3,j*3+3));
        const y=(c[2]-a[2])*(d[0]-a[0])-(c[0]-a[0])*(d[2]-a[2]);
        if(y< -1e-7)failures.push(`${name}:${i/3}`);
      }
    }
    expect(failures).toEqual([]);
  });
  it("joins every curb inner boundary to the road without exposed terrain gaps",()=>{
    for (const curb of sourceData.curbs) for (const row of curb.rows) {
      const road=sourceData.road[Math.round(row[0]/.5)],col=curb.side==="left"?7:9;
      expect(road.slice(col,col+2)).toEqual(row.slice(1,3));
    }
  });
  it("matches the frozen source-image width readings rather than a uniform curb",()=>{
    const references=JSON.parse(readFileSync("public/data/tracks/fuji/cg_study/width-checkpoints.json","utf8"));
    for(const ref of references.checkpoints) {
      const row=sourceData.curbs.filter(c=>c.side===ref.side).flatMap(c=>c.rows).find(r=>r[0]===ref.station)!;
      expect(row).toBeDefined();
      for(const [col,index] of [[1,0],[3,1]]) {
        const offset=(row[col]-ref.originXZ[0])*ref.outwardNormalXZ[0]+(row[col+1]-ref.originXZ[1])*ref.outwardNormalXZ[1];
        expect(Math.abs(offset-ref.sourceInnerOuterMeters[index])).toBeLessThan(references.perBoundaryImageReadingToleranceMeters);
      }
      expect(Math.abs(Math.hypot(row[3]-row[1],row[4]-row[2])-ref.sourceWidthMeters)).toBeLessThan(.25);
    }
  });
});
