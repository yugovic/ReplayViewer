import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { buildCgRoadGeometry, buildCgRoadUnderlayGeometry, type CgRoadRow, type FujiCgGeometry } from "./FujiCgStudy";
import { offsetFromEdge, outwardAt, resolveKerbZones, roadEdgeAt, type KerbZone, type XZ } from "./FujiKerbZones";
import type { TrackData } from "../../replay/types";

type Batch=ReturnType<typeof buildCgRoadGeometry>["road"];
const vertices=(b:Batch)=>Array.from({length:b.positions.length/3},(_,i)=>b.positions.slice(i*3,i*3+3));
const key=(p:number[])=>p.map(x=>x.toFixed(7)).join(",");
const cross=(a:XZ,b:XZ,c:XZ)=>(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
function triangles(b:Batch){return Array.from({length:b.indices.length/3},(_,i)=>b.indices.slice(i*3,i*3+3).map(j=>[b.positions[j*3],b.positions[j*3+2]] as XZ));}
function area(b:Batch){return triangles(b).reduce((n,[a,c,d])=>n+Math.abs(cross(a,c,d))/2,0);}
const grids=new WeakMap<XZ[][],Map<string,XZ[][]>>();
function contains(tris:XZ[][],p:XZ){
 let grid=grids.get(tris);
 if(!grid){grid=new Map();for(const tri of tris){
  const loX=Math.floor(Math.min(...tri.map(p=>p[0]))/10),hiX=Math.floor(Math.max(...tri.map(p=>p[0]))/10);
  const loZ=Math.floor(Math.min(...tri.map(p=>p[1]))/10),hiZ=Math.floor(Math.max(...tri.map(p=>p[1]))/10);
  for(let x=loX;x<=hiX;x++)for(let z=loZ;z<=hiZ;z++){const k=`${x},${z}`;const list=grid.get(k)??[];list.push(tri);grid.set(k,list);}
 }grids.set(tris,grid);}
 return (grid.get(`${Math.floor(p[0]/10)},${Math.floor(p[1]/10)}`)??[]).some(([a,b,c])=>{
  if(p[0]<Math.min(a[0],b[0],c[0])-1e-7||p[0]>Math.max(a[0],b[0],c[0])+1e-7||p[1]<Math.min(a[1],b[1],c[1])-1e-7||p[1]>Math.max(a[1],b[1],c[1])+1e-7)return false;
  const d=[cross(a,b,p),cross(b,c,p),cross(c,a,p)];return d.every(x=>x>=-1e-7)||d.every(x=>x<=1e-7);
});}
const vertexSets=new WeakMap<Batch,Set<string>>();
const near=(b:Batch,p:XZ)=>{let set=vertexSets.get(b);if(!set){set=new Set(vertices(b).map(v=>key([v[0],v[2]])));vertexSets.set(b,set);}return set.has(key(p));};
// Suppress clipping without changing the source road or exterior gap fill.
function unclippedZones(g:FujiCgGeometry,zs:KerbZone[]){return zs.map(z=>({...z,outer:z.outer.map((p,j)=>{
  const row=g.road[Math.round(z.station[j]*2)];return offsetFromEdge(row,z.side,p)<0?roadEdgeAt(row,z.side):p;
})}));}
const track={width:13,totalLength:10,origin:{lat:0,lng:0,alt:0},centerline:[{dist:0,x:0,y:0,z:0,alt:0},{dist:10,x:10,y:0,z:0,alt:0}]} as TrackData;
const fixture:FujiCgGeometry={version:1,source:"fixture",limitations:[],curbHeightMeters:.055,green:[],curbs:[],road:Array.from({length:21},(_,i)=>[i*.5,i*.5,0,0,1,-5,5] as CgRoadRow)};
const zone=(side:"left"|"right",outerValues:number[]):KerbZone=>{const sign=side==="left"?-1:1,station=outerValues.map((_,i)=>2+i*.5);return{id:side,side,replaces:null,station,paint:station.map((s,i)=>[s,sign*(outerValues[i]-1.2)]),block:station.map((s,i)=>[s,sign*(outerValues[i]-.8)]),outer:station.map((s,i)=>[s,sign*outerValues[i]]),red:station.map(()=>1),redSource:"zone"};};

describe("asphalt overhang repair",()=>{
 it.each(["left","right"] as const)("trims only the protruding %s edge and keeps adjoining coverage",side=>{
  const z=zone(side,[5.4,5,4.6,4.2]),b=buildCgRoadGeometry(track,fixture,[z]),tris=triangles(b.road),sign=side==="left"?-1:1;
  expect(near(b.road,[2,sign*5])).toBe(true);expect(near(b.road,[3,sign*4.6])).toBe(true);expect(near(b.road,[3.5,sign*4.2])).toBe(true);
  expect(contains(tris,[3.25,sign*4.39])).toBe(true);expect(contains(tris,[3.25,sign*4.5])).toBe(false);
  expect(contains(tris,[3.75,sign*4.59])).toBe(true);expect(near(b.paint,[3,sign*(4.6-1.2)])).toBe(true);
  // Ordinary marking follows the shared clipped endpoint with a .16 m inward width.
  expect(near(b.paint,[3.5,sign*4.2])).toBe(true);expect(near(b.paint,[3.5,sign*(4.2-.16)])).toBe(true);
 });
 it("does not recreate overhang where exterior paint fill crosses an inward outer edge",()=>{
  const z=zone("right",[6.5,4.5]),b=buildCgRoadGeometry(track,fixture,[z]);
  expect(contains(triangles(b.road),[2.01,5.1])).toBe(true);
  expect(contains(triangles(b.road),[2.49,4.6])).toBe(false);
 });
 it("retains exterior fill where paint starts beyond the original road",()=>{
  const z=zone("right",[6.4,6.4,6.4]),b=buildCgRoadGeometry(track,fixture,[z]);
  expect(near(b.road,[2,5.2])).toBe(true);expect(contains(triangles(b.road),[2.25,5.1])).toBe(true);
 });
});

describe("all 26 delivered kerb zones",()=>{
 const t=JSON.parse(readFileSync("public/data/tracks/fuji/track.json","utf8")) as TrackData;
 const g=JSON.parse(readFileSync("public/data/tracks/fuji/cg_study/geometry.json","utf8")) as FujiCgGeometry;
 const zs=resolveKerbZones(JSON.parse(readFileSync("public/data/tracks/fuji/cg_study/kerb_zones.json","utf8")),g).zones;
 const before=buildCgRoadGeometry(t,g,unclippedZones(g,zs)),after=buildCgRoadGeometry(t,g,zs);
 const oldTriangles=triangles(before.road),newTriangles=triangles(after.road);
 const changed=zs.flatMap(z=>z.station.flatMap((s,j)=>offsetFromEdge(g.road[Math.round(s*2)],z.side,z.outer[j])< -1e-8?[{z,s,j}]:[]));
 it("keeps exact traced outer vertices and removes the known overhangs",()=>{
  expect(zs).toHaveLength(26);expect(changed).toHaveLength(27);expect([...new Set(changed.map(x=>x.z.id))]).toEqual(["k16","k19","k21"]);
  for(const {z,j} of changed)expect(near(after.road,z.outer[j]),`${z.id}:${j}`).toBe(true);
  const failures:string[]=[];
  for(const z of zs)for(let j=0;j<z.station.length-1;j++){
   const a=z.outer[j],b=z.outer[j+1],n=outwardAt(g.road[Math.round(z.station[j]*2)],z.side);
   const outside:XZ=[(a[0]+b[0])/2+n[0]*.02,(a[1]+b[1])/2+n[1]*.02];
   if(contains(newTriangles,outside))failures.push(`${z.id}@${z.station[j]}`);
  }
  expect(failures).toEqual([]);
 });
 it("only removes asphalt area, and every new triangle remains in the original surface",()=>{
  const oldKeys=new Set(oldTriangles.map(v=>v.map(p=>key(p)).sort().join(";")));
  const added=newTriangles.filter(v=>!oldKeys.has(v.map(p=>key(p)).sort().join(";")));
  expect(added.length).toBeLessThan(80);
  for(const tri of added){
   for(const weights of [[1/3,1/3,1/3],[.8,.1,.1],[.1,.8,.1],[.1,.1,.8]]){
    const p:XZ=[tri.reduce((v,q,i)=>v+q[0]*weights[i],0),tri.reduce((v,q,i)=>v+q[1]*weights[i],0)];
    expect(contains(oldTriangles,p),JSON.stringify(p)).toBe(true);
   }
  }
  const removed=area(before.road)-area(after.road);expect(removed).toBeGreaterThan(5);expect(removed).toBeLessThan(15);
 });
 it("preserves every inner-quarter seam vertex and the traced zone paint",()=>{
  const used=new Set(after.road.indices.map(i=>key(after.road.positions.slice(i*3,i*3+3))));
  const beforeVertices=vertices(before.road);
  for(const {z,s} of changed){const row=g.road[Math.round(s*2)],fraction=z.side==="left"?.25:.75;
   const x=row[7]!+(row[9]!-row[7]!)*fraction,zz=row[8]!+(row[10]!-row[8]!)*fraction;
   const original=beforeVertices.find(v=>Math.hypot(v[0]-x,v[2]-zz)<1e-8)!;
   expect(used.has(key(original)),`${z.id}@${s} seam`).toBe(true);
  }
  for(const z of zs)for(const p of z.paint)expect(near(after.paint,p),z.id).toBe(true);
 });
 it("restores masked ground only beneath the original repaired outer strips",()=>{
  const bbox={minLat:35,maxLat:36,minLng:138,maxLng:139};
  const bed=buildCgRoadUnderlayGeometry(t,g,zs,(x,z)=>x*.01+z*.02-50,bbox);
  expect(area(bed)).toBeCloseTo(67.40296009375,6);
  for(const v of vertices(bed))expect(v[1]).toBeCloseTo(v[0]*.01+v[2]*.02-50,10);
  for(const tri of triangles(bed))for(const p of tri)expect(contains(oldTriangles,p)).toBe(true);
  expect(bed.uvs.every(Number.isFinite)).toBe(true);
  expect(buildCgRoadUnderlayGeometry(t,g,[],()=>0,bbox).positions).toEqual([]);
 });
 it("contains only finite upward-facing triangles",()=>{
  const failures:string[]=[];
  for(const name of ["road","paint","red","white"] as const){const b=after[name];
   for(let i=0;i<b.indices.length;i+=3){const [a,c,d]=b.indices.slice(i,i+3).map(j=>b.positions.slice(j*3,j*3+3));
    const up=(c[2]-a[2])*(d[0]-a[0])-(c[0]-a[0])*(d[2]-a[2]);
    if(![...a,...c,...d].every(Number.isFinite)||up< -1e-7)failures.push(`${name}:${i/3}`);
   }
  }
  expect(failures).toEqual([]);
 });
});