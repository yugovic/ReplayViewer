import { buildFujiBarrierKit } from "./FujiBarrierKit";
import * as THREE from "three";
import { adjustCgGeometry, registerCgEditor, type CurbAdjustment } from "./FujiCgEditor";
import { replaceReviewedBarriers, type ReviewedBarriers } from "./FujiReviewedBarriers";
import { buildCgFenceGeometry, buildCgFenceMaterial, insideContextPolygon, type FujiContext } from "./FujiCgContext";
import type { TrackData } from "../../replay/types";
import { localToLatLng, latLngToLocal } from "../../replay/projection";
import { projectPointToCenterline, sampleTrackSurface } from "../../replay/interpolation";
import type { TrackBuildResult } from "./TrackBuilder";
import { TerrainSampler } from "./TerrainSampler";
import { buildFeatures3dGroup, loadFeatures3d } from "./Features3DBuilder";
import { buildProceduralAsphaltNormal, buildProceduralAsphaltRoughness } from "./detailTexture";
import { latLngToTerrainUv, type Bbox } from "./groundMath";

// Explicit coordinates prevent the renderer from reinterpreting editor
// tangents/offsets differently from the orthophoto tracing pipeline.
export type CgRoadRow = [d:number,x:number,z:number,nx:number,nz:number,left:number,right:number,lx?:number,lz?:number,rx?:number,rz?:number];
export type CgCurbRow = [d:number,ix:number,iz:number,ox:number,oz:number,inner:number,outer:number,red:number,observed:number];
export interface FujiCgGeometry {
  version: number;
  road: CgRoadRow[];
  curbs: { side: string; rows: CgCurbRow[] }[];
  green: [number,number,number][];
  curbHeightMeters: number;
  source: string;
  limitations: string[];
}

interface Batch { positions: number[]; uvs: number[]; indices: number[] }
function batch(): Batch { return { positions: [], uvs: [], indices: [] }; }
type Point = [number,number,number];

/** Consistent upward winding on BOTH sides; no DoubleSide hiding mistakes. */
function quad(b: Batch, a: Point, c: Point, d: Point, e: Point) {
  const base=b.positions.length/3;
  for (const p of [a,c,d,e]) { b.positions.push(...p); b.uvs.push(p[0]/.7,p[2]/.7); }
  const up=(c[2]-a[2])*(d[0]-a[0])-(c[0]-a[0])*(d[2]-a[2]);
  b.indices.push(...(up>=0 ? [base,base+1,base+2,base+1,base+3,base+2] : [base,base+2,base+1,base+1,base+2,base+3]));
}

function mesh(b:Batch, material:THREE.Material, name:string) {
  const g=new THREE.BufferGeometry();
  g.setAttribute("position",new THREE.Float32BufferAttribute(b.positions,3));
  g.setAttribute("uv",new THREE.Float32BufferAttribute(b.uvs,2));
  g.setIndex(b.indices); g.computeVertexNormals(); g.computeBoundingSphere();
  const m=new THREE.Mesh(g,material); m.name=name; m.receiveShadow=true;
  return m;
}

function roadPoint(track:TrackData,row:CgRoadRow,side:number,offset=0):Point {
  if(row[7]!==undefined&&row[8]!==undefined&&row[9]!==undefined&&row[10]!==undefined) {
    const t=(side-row[5])/(row[6]-row[5]);
    return [row[7]+(row[9]-row[7])*t,sampleTrackSurface(track,row[0],side)+offset,row[8]+(row[10]-row[8])*t];
  }
  return [row[1]+row[3]*side,sampleTrackSurface(track,row[0],side)+offset,row[2]+row[4]*side];
}

export function buildCgRoadGeometry(track:TrackData,data:FujiCgGeometry) {
  const road=batch(), paint=batch(), red=batch(), white=batch(), green=batch();
  for(let i=0;i<data.road.length-1;i++) {
    const a=data.road[i],b=data.road[i+1];
    // Interior vertices preserve the existing camber profile across a wide
    // asymmetric ribbon instead of spanning its full width with one plane.
    for(let j=0;j<4;j++) {
      const sa=a[5]+(a[6]-a[5])*j/4,sb=a[5]+(a[6]-a[5])*(j+1)/4;
      const ta=b[5]+(b[6]-b[5])*j/4,tb=b[5]+(b[6]-b[5])*(j+1)/4;
      quad(road,roadPoint(track,a,sa),roadPoint(track,a,sb),roadPoint(track,b,ta),roadPoint(track,b,tb));
    }
    for(const col of [5,6] as const) {
      const sign=col===5?-1:1;
      // Marking lies inside the measured boundary; it does not push curbs.
      quad(paint,roadPoint(track,a,a[col],.012),roadPoint(track,a,a[col]-sign*.16,.012),
        roadPoint(track,b,b[col],.012),roadPoint(track,b,b[col]-sign*.16,.012));
    }
  }
  for(const curb of data.curbs) for(let i=0;i<curb.rows.length-1;i++) {
    const a=curb.rows[i],b=curb.rows[i+1], target=a[7]?red:white;
    const point=(r:CgCurbRow,t:number,h:number):Point => [r[1]+(r[3]-r[1])*t,
      sampleTrackSurface(track,r[0],r[5]+(r[6]-r[5])*t)+h,r[2]+(r[4]-r[2])*t];
    // This low bevel is a declared visual assumption. Only XZ is traced.
    const profile=[[0,.012],[.18,data.curbHeightMeters],[1,data.curbHeightMeters],[1,-.09]];
    for(let j=0;j<profile.length-1;j++) {
      const [t,h]=profile[j],[u,k]=profile[j+1];
      quad(target,point(a,t,h),point(a,u,k),point(b,t,h),point(b,u,k));
    }
  }
  for(const [d,l,r] of data.green) {
    const index=Math.round(d/.5),a=data.road[index],b=data.road[index+1];
    if(a&&b) quad(green,roadPoint(track,a,l,.004),roadPoint(track,a,r,.004),roadPoint(track,b,l,.004),roadPoint(track,b,r,.004));
  }
  return { road,paint,red,white,green };
}

/** Separate scene path: no satellite corridor, original image tiles or legacy
 * curb ribbons are loaded. Returning to look=original uses the old builder. */
export function buildFujiCgTrack(track:TrackData,loader:THREE.TextureLoader):TrackBuildResult {
  const group=new THREE.Group(); group.name="fuji-cg-study";
  group.userData.status="loading";
  let disposed=false,featuresVisible=true,features:THREE.Group|undefined;
  let unregisterEditor: (() => void) | undefined;
  let heightAt:((x:number,z:number)=>number)|undefined;
  const textures:THREE.Texture[]=[];
  const dir="/data/tracks/fuji/";
  const fetchJson=async (url:string) => { const r=await fetch(url); if(!r.ok)throw new Error(`${url}: ${r.status}`); return r.json(); };
  const atlas=loader.load(`${dir}cg_study/landcover-reviewed.png`);
  atlas.flipY=false; atlas.colorSpace=THREE.SRGBColorSpace; atlas.anisotropy=8; textures.push(atlas);
  const cutout=loader.load(`${dir}cg_study/road-cutout.png`);
  cutout.flipY=false;cutout.minFilter=THREE.LinearFilter;cutout.generateMipmaps=false;textures.push(cutout);
  const ready=Promise.all([
    fetchJson(`${dir}cg_study/geometry.json`) as Promise<FujiCgGeometry>,
    fetchJson(`${dir}cg_study/landcover-meta.json`) as Promise<{bbox:Bbox}>,
    TerrainSampler.load(`${dir}terrain_meta.json`,dir),
    loadFeatures3d(`${dir}features3d.json`),
    fetchJson(`${dir}cg_study/barriers-onboard-reviewed.json`) as Promise<ReviewedBarriers>,
    fetchJson(`${dir}cg_study/context-reviewed.json`) as Promise<FujiContext>,
  ]).then(([data,meta,sampler,featureData,barrierReview,context]) => {
    if(disposed)return;
    if(data.version!==1 || !data.road.length)throw new Error("Invalid Fuji CG geometry");
    const normal=buildProceduralAsphaltNormal(),roughness=buildProceduralAsphaltRoughness();
    normal.repeat.set(1,1); roughness.repeat.set(1,1); textures.push(normal,roughness);
    const asphalt=new THREE.MeshStandardMaterial({color:0x777a7b,roughness:.94,metalness:0,
      normalMap:normal,normalScale:new THREE.Vector2(.12,.12),roughnessMap:roughness});
    // Subtle world-space material variation. It is not a claimed tyre-mark map.
    asphalt.onBeforeCompile=shader => {
      shader.vertexShader=shader.vertexShader.replace("#include <common>","#include <common>\nvarying vec2 cgWorldXZ;")
        .replace("#include <begin_vertex>","#include <begin_vertex>\ncgWorldXZ=position.xz;");
      shader.fragmentShader=shader.fragmentShader.replace("#include <common>","#include <common>\nvarying vec2 cgWorldXZ;")
        .replace("#include <color_fragment>","#include <color_fragment>\nfloat cgMottle=sin(cgWorldXZ.x*.37+sin(cgWorldXZ.y*.11))*sin(cgWorldXZ.y*.29); diffuseColor.rgb*=1.0+.045*cgMottle;");
    };
    const material=(color:number)=>new THREE.MeshStandardMaterial({color,roughness:.88,metalness:0});
    const b=buildCgRoadGeometry(track,data);
    group.add(mesh(b.road,asphalt,"cg-road"),mesh(b.paint,material(0xe0dfd4),"cg-edge-paint"),
      mesh(b.red,material(0xb73c37),"cg-curbs-red"),mesh(b.white,material(0xe7e5d8),"cg-curbs-white"),
      mesh(b.green,material(0x438365),"cg-green-runoff"));
    const edits = new Map<number, CurbAdjustment>();
    const rebuild = () => {
      const adjusted = adjustCgGeometry(data, [...edits.values()]);
      const updated = buildCgRoadGeometry(track, adjusted);
      for (const [key, name] of [["road", "cg-road"], ["paint", "cg-edge-paint"], ["red", "cg-curbs-red"], ["white", "cg-curbs-white"], ["green", "cg-green-runoff"]] as const) {
        const existing = group.getObjectByName(name) as THREE.Mesh;
        const replacement = mesh(updated[key], existing.material as THREE.Material, name);
        existing.geometry.dispose(); existing.geometry = replacement.geometry;
      }
      group.userData.adjustments = [...edits.values()];
    };
    unregisterEditor = registerCgEditor({
      spans: data.curbs.map((c, index) => ({ index, side: c.side, start: c.rows[0][0], end: c.rows[c.rows.length-1][0] })),
      apply: change => {
        // Validate before changing the saved edit state.
        adjustCgGeometry(data, [change]);
        edits.set(change.index, change); rebuild();
      },
      reset: () => { edits.clear(); rebuild(); },
      export: () => JSON.stringify({ version: 1, source: data.source, base: '/data/tracks/fuji/cg_study/geometry.json', surveyTruth: false, note: 'Unverified interactive adjustments. Reconcile with the licensed orthophoto before adoption.', adjustments: [...edits.values()] }, null, 2),
    });
    heightAt=(x,z) => {
      const p=projectPointToCenterline(track,x,z),ll=localToLatLng(x,z,track.origin);
      const y=sampleTrackSurface(track,p.arcLength,p.signedLateralDistance);
      const terrain=(sampler?.heightAt(ll.lat,ll.lng)??(y+track.origin.alt))-track.origin.alt;
      const row=data.road[Math.min(data.road.length-1,Math.floor(p.arcLength/.5))];
      const edge=p.signedLateralDistance<0?-row[5]:row[6];
      const t=THREE.MathUtils.clamp((p.lateralDistance-edge-2)/28,0,1);
      const clearance=p.lateralDistance < edge ? .3 : .1;
      return (y-clearance)*(1-t*t*(3-2*t))+terrain*t*t*(3-2*t);
    };
    const nw=latLngToLocal(meta.bbox.maxLat,meta.bbox.minLng,track.origin);
    const se=latLngToLocal(meta.bbox.minLat,meta.bbox.maxLng,track.origin);
    const nx=Math.ceil((se.x-nw.x)/5),nz=Math.ceil((se.z-nw.z)/5);
    const ground=new THREE.PlaneGeometry(se.x-nw.x,se.z-nw.z,nx,nz); ground.rotateX(-Math.PI/2);
    const pos=ground.getAttribute("position"),uv=ground.getAttribute("uv");
    for(let i=0;i<pos.count;i++) {
      const x=pos.getX(i)+(nw.x+se.x)/2,z=pos.getZ(i)+(nw.z+se.z)/2;
      const ll=localToLatLng(x,z,track.origin),t=latLngToTerrainUv(ll.lat,ll.lng,meta.bbox);
      pos.setXYZ(i,x,heightAt(x,z),z); uv.setXY(i,t.u,t.v);
    }
    ground.computeVertexNormals();
    const terrainMesh=new THREE.Mesh(ground,new THREE.MeshStandardMaterial({map:atlas,alphaMap:cutout,alphaTest:.5,roughness:1}));
    terrainMesh.name="cg-terrain"; terrainMesh.receiveShadow=true; group.add(terrainMesh);
    if(featureData) {
      // Restore existing approximate canopy only in source-reviewed woodland.
      // These remain aggregate LiDAR/grid representations, not surveyed stems.
      const trees=featureData.trees.filter(t=>context.enabled!==false && insideContextPolygon(t.x,t.z,context.hill)
        ? insideContextPolygon(t.x,t.z,context.woodland)
        : projectPointToCenterline(track,t.x,t.z).lateralDistance>65);
      const barriers=replaceReviewedBarriers(featureData.barriers,barrierReview);
      const legacyBarriers=barriers.filter(b=>!barrierReview.barriers.includes(b));
      features=buildFeatures3dGroup({...featureData,trees,barriers:legacyBarriers},heightAt,(x,z)=>{
        let nearest=track.centerline[0],best=Infinity;
        for(const p of track.centerline){const d=(p.x-x)**2+(p.z-z)**2;if(d<best){best=d;nearest=p;}}
        return nearest;
      },true).group;
      const fence=features.getObjectByName('features3d-barriers-fence') as THREE.Mesh | undefined;
      if(fence && context.enabled!==false) {
        fence.geometry.dispose();
        (fence.material as THREE.Material).dispose();
        fence.geometry=buildCgFenceGeometry(legacyBarriers,heightAt);
        fence.material=buildCgFenceMaterial();
        fence.userData.aperture='illustrative diamond mesh, single surface';
      }
      group.userData.context={source:context.source,canopy:'existing approximate trees restricted to source-reviewed woodland',height:'native terrain unchanged'};
      features.add(buildFujiBarrierKit(barrierReview.barriers,heightAt,(x,z)=>{
        const station=projectPointToCenterline(track,x,z).arcLength;
        const row=data.road[Math.min(data.road.length-1,Math.round(station/.5))];
        return {x:row[1],z:row[2]};
      }));
      features.visible=featuresVisible;group.add(features);
    }
    group.userData.status="ready";
    group.userData.source=data.source;
    group.userData.limitations=data.limitations;
    group.userData.curbSpans=data.curbs.length;
    group.userData.terrainLoaded=!!sampler;
    group.userData.nearTrees="source-reviewed woodland only; individual positions unverified";
    group.userData.barrierReview={bounds:barrierReview.bounds,source:barrierReview.source,lines:barrierReview.barriers.length};
  }).catch(error=>{if(!disposed){group.userData.status="error";group.userData.error=String(error);console.error("Fuji CG study failed",error);}});
  void ready;
  return {group,centerline3D:track.centerline.map(p=>new THREE.Vector3(p.x,p.y,p.z)),
    // Legacy photo/road switches cannot accidentally overlay another version.
    setRoadVisible:()=>{},setFeaturesVisible:()=>{},setDetailTexture:()=>{},setSatelliteVariant:()=>{},
    setTrackLines:()=>{},setTrialTilesVisible:()=>{},updateGround:()=>{},
    setFeatures3dVisible:visible=>{featuresVisible=visible;if(features)features.visible=visible;},
    terrainHeightAt:(x,z)=>heightAt?.(x,z)??null,
    dispose:()=>{disposed=true;unregisterEditor?.();textures.forEach(t=>t.dispose());},
  };
}
