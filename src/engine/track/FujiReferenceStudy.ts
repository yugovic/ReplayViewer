import * as THREE from "three";
import type { TrackData } from "../../replay/types";
import { sampleTrackSurface } from "../../replay/interpolation";
import { sampleRoadEdgeOffsets, type RoadEdgeProfile } from "./RoadEdgeLines";
import { buildProceduralAsphaltNormal, buildProceduralAsphaltRoughness } from "./detailTexture";
import type { Tree3D } from "./Features3DBuilder";
import { localToLatLng } from "../../replay/projection";

// Visual study, not survey data. No-tree areas were inspected against the
// VIRTUAL SHIZUOKA 2019 orthophoto (CC BY 4.0). Retained forest points remain
// the legacy approximate extraction; we do not claim individual-tree truth.
// Coordinates are existing local east-X / south-Z metres. Nothing is added.
export const FUJI_NO_TREE_ZONES: ReadonlyArray<{ id: string; polygon: ReadonlyArray<readonly [number, number]> }> = [
  { id: "hairpin-island-and-marshal-equipment", polygon: [[215,142],[241,153],[217,195],[216,270],[226,319],[251,341],[202,348],[190,303],[180,233],[189,180]] },
  { id: "hairpin-west-service-road", polygon: [[139,124],[167,124],[150,233],[158,324],[163,417],[143,436],[127,339],[122,240]] },
  { id: "paddock-roofs-and-paved-apron", polygon: [[204,-15],[350,-15],[351,48],[329,95],[300,85],[277,63],[258,10]] },
  { id: "west-parking-and-utilities", polygon: [[-14,98],[108,111],[131,158],[105,227],[13,287],[-46,309],[-70,295],[-60,246],[-19,239],[48,209],[92,175],[26,144]] },
  { id: "hairpin-east-service-building", polygon: [[418,75],[441,77],[452,109],[431,121],[414,107]] },
  { id: "hairpin-south-paved-apron", polygon: [[207,340],[296,323],[356,335],[384,363],[367,420],[224,431],[201,405]] },
];

export function pointInPolygon(x: number, z: number, polygon: ReadonlyArray<readonly [number, number]>): boolean {
  let inside = false;
  for (let i=0,j=polygon.length-1;i<polygon.length;j=i++) {
    const [ax,az]=polygon[i], [bx,bz]=polygon[j];
    if ((az>z)!==(bz>z) && x<(bx-ax)*(z-az)/(bz-az)+ax) inside=!inside;
  }
  return inside;
}

export function excludedFujiTreeZone(tree: Tree3D): string | null {
  return FUJI_NO_TREE_ZONES.find(zone => pointInPolygon(tree.x, tree.z, zone.polygon))?.id ?? null;
}

export function isFujiStudyTree(tree: Tree3D): boolean {
  return tree.x >= -80 && tree.x <= 550 && tree.z >= -15 && tree.z <= 470;
}

function sample(track: TrackData, d: number) {
  const pts=track.centerline;
  let lo=0,hi=pts.length-1;
  while(lo+1<hi){const m=(lo+hi)>>>1;if(pts[m].dist<=d)lo=m;else hi=m;}
  const a=pts[lo],b=pts[hi],t=(d-a.dist)/Math.max(.001,b.dist-a.dist);
  const length=Math.hypot(b.x-a.x,b.z-a.z)||1;
  return {x:a.x+(b.x-a.x)*t,z:a.z+(b.z-a.z)*t,tx:(b.x-a.x)/length,tz:(b.z-a.z)/length};
}

/** Uses the already edited road boundaries, not image-generated geometry. */
export function buildFujiReferenceRoad(track: TrackData, profile: RoadEdgeProfile, albedo?: THREE.Texture): THREE.Group {
  const group=new THREE.Group(); group.name="fuji-reference-road";
  group.userData.source="road_edges.json / track-creator; visually checked against CC BY 2019 orthophoto";
  const normal=buildProceduralAsphaltNormal(), roughness=buildProceduralAsphaltRoughness();
  // Full-extent native orthophoto UVs retain patches/tyre marks on the surface.
  // Only micro-normal and roughness repeat; neither introduces geometry.
  const bbox={minLng:138.91723261,maxLng:138.93658159,minLat:35.36246909,maxLat:35.37859744};
  const north=Math.asinh(Math.tan(THREE.MathUtils.degToRad(bbox.maxLat)));
  const south=Math.asinh(Math.tan(THREE.MathUtils.degToRad(bbox.minLat)));
  const width=(bbox.maxLng-bbox.minLng)*111320*Math.cos(THREE.MathUtils.degToRad(track.origin.lat));
  const height=(bbox.maxLat-bbox.minLat)*111320;
  normal.repeat.set(width/.7,height/.7); roughness.repeat.copy(normal.repeat);
  group.userData.dispose = () => { normal.dispose(); roughness.dispose(); };
  const roadMat=new THREE.MeshStandardMaterial({color:albedo?0xffffff:0x53565a,map:albedo,roughness:.94,metalness:0,
    normalMap:normal,normalScale:new THREE.Vector2(.12,.12),roughnessMap:roughness,
    polygonOffset:true,polygonOffsetFactor:-8,polygonOffsetUnits:-8});
  const white=new THREE.MeshStandardMaterial({color:0xe7e6d9,roughness:.94,polygonOffset:true,polygonOffsetFactor:-9,polygonOffsetUnits:-9});
  const red=new THREE.MeshStandardMaterial({color:0x9a342c,roughness:.9,polygonOffset:true,polygonOffsetFactor:-9,polygonOffsetUnits:-9});

  function ribbon(start:number,end:number,lateral:(d:number)=>[number,number],mat:THREE.Material,offset=.035) {
    const n=Math.ceil((end-start)/1.5),pos:number[]=[],uv:number[]=[],idx:number[]=[];
    for(let i=0;i<=n;i++) {
      const d=start+(end-start)*i/n,p=sample(track,d),[l,r]=lateral(d);
      for(let j=0;j<3;j++) {
        const side=l+(r-l)*j/2;
        const x=p.x-p.tz*side,z=p.z+p.tx*side;
        pos.push(x,sampleTrackSurface(track,d,side)+offset,z);
        const ll=localToLatLng(x,z,track.origin);
        uv.push((ll.lng-bbox.minLng)/(bbox.maxLng-bbox.minLng),
          (north-Math.asinh(Math.tan(THREE.MathUtils.degToRad(ll.lat))))/(north-south));
      }
      if(i<n) for(let j=0;j<2;j++){const a=i*3+j,b=a+3;idx.push(a,a+1,b,a+1,b+1,b);}
    }
    const geo=new THREE.BufferGeometry();geo.setAttribute("position",new THREE.Float32BufferAttribute(pos,3));
    geo.setAttribute("uv",new THREE.Float32BufferAttribute(uv,2));geo.setIndex(idx);geo.computeVertexNormals();
    const mesh=new THREE.Mesh(geo,mat);mesh.receiveShadow=true;mesh.renderOrder=25;group.add(mesh);
  }
  // The broader range keeps entry/exit transitions beyond the 47–72s excerpt.
  ribbon(1570,2630,d=>{const e=sampleRoadEdgeOffsets(profile,d);return [-e.left,e.right];},roadMat);
  for(const side of [-1,1]) ribbon(1570,2630,d=>{
    const e=sampleRoadEdgeOffsets(profile,d),edge=side<0?-e.left:e.right;
    return [edge-.08,edge+.08];
  },white,.045);
  // Existing editor spans, unioned to remove overlapping duplicate ribbons.
  // Only spans inside the inspected hairpin zone are used.
  const curbs: Array<[number,number,number]> = [[1957.5,2064,-1],[2102.5,2187,-1],[2023.5,2133.5,1],[2200.5,2348.5,1],[2335.5,2427.5,-1],[2418,2506,1]];
  for(const [start,end,side] of curbs) for(let d=start;d<end;d+=4) {
    ribbon(d,Math.min(d+4,end),s=>{const e=sampleRoadEdgeOffsets(profile,s),v=side<0?-e.left:e.right;return side<0?[v-1.2,v]:[v,v+1.2];},Math.floor((d-start)/4)%2?white:red,.065);
  }
  // Merge each shared material's segments to keep the study at three draws.
  for(const mat of [roadMat,white,red]) {
    const meshes=group.children.filter(o=>(o as THREE.Mesh).material===mat) as THREE.Mesh[];
    if(meshes.length<2)continue;
    const positions:number[]=[],uvs:number[]=[],indices:number[]=[];
    for(const mesh of meshes){const g=mesh.geometry,base=positions.length/3;
      positions.push(...Array.from(g.attributes.position.array));uvs.push(...Array.from(g.attributes.uv.array));
      indices.push(...Array.from(g.index!.array,n=>n+base));g.dispose();group.remove(mesh);}
    const g=new THREE.BufferGeometry();g.setAttribute("position",new THREE.Float32BufferAttribute(positions,3));g.setAttribute("uv",new THREE.Float32BufferAttribute(uvs,2));g.setIndex(indices);g.computeVertexNormals();
    const m=new THREE.Mesh(g,mat);m.receiveShadow=true;m.renderOrder=26;group.add(m);
  }
  return group;
}
