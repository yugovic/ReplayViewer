import * as THREE from 'three';
import type { Barrier3D, GroundHeightFn } from './Features3DBuilder';
import { buildCgFenceMaterial } from './FujiCgContext';

type P=[number,number,number];
type Batch={p:number[];uv:number[];i:number[]};
const batch=():Batch=>({p:[],uv:[],i:[]});
function quad(b:Batch,a:P,c:P,d:P,e:P,u:number,v:number,lo:number,hi:number){const n=b.p.length/3;b.p.push(...a,...c,...d,...e);b.uv.push(u,lo,v,lo,u,hi,v,hi);b.i.push(n,n+2,n+1,n+1,n+2,n+3)}
function makeMesh(b:Batch,material:THREE.Material,name:string){const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(b.p,3));g.setAttribute('uv',new THREE.Float32BufferAttribute(b.uv,2));g.setIndex(b.i);g.computeVertexNormals();g.computeBoundingSphere();const m=new THREE.Mesh(g,material);m.name=name;m.receiveShadow=true;return m;}
export const CATCH_PROFILE = [[0,0],[0,2.6],[.12,3.0],[.65,3.4]] as const;
/** Four-metre bays, independent of the source polyline's control-point spacing. */
export function sampleBarrierBays(points:[number,number][],spacing=4){
  if(points.length<2)return [];
  const lengths=[0];for(let i=1;i<points.length;i++)lengths.push(lengths[i-1]+Math.hypot(points[i][0]-points[i-1][0],points[i][1]-points[i-1][1]));
  const length=lengths.at(-1)!;if(length<.001)return [];
  const stations=new Set<number>(lengths);for(let s=0;s<length;s+=spacing)stations.add(s);
  let j=0;return [...stations].sort((a,b)=>a-b).map(s=>{while(j<lengths.length-2&&lengths[j+1]<s)j++;const f=(s-lengths[j])/(lengths[j+1]-lengths[j]||1);return {x:THREE.MathUtils.lerp(points[j][0],points[j+1][0],f),z:THREE.MathUtils.lerp(points[j][1],points[j+1][1],f),s,post:Math.abs(s/spacing-Math.round(s/spacing))<1e-6||s===length};});
}
/** Three evidence-based families; dimensions remain declared visual estimates.
 * Curved catch mesh/posts, corrugated three-band rail, and battered concrete. */
export function buildFujiBarrierKit(barriers:Barrier3D[],heightAt:GroundHeightFn,roadAt:(x:number,z:number)=>{x:number;z:number}){
  const group=new THREE.Group();group.name='fuji-reviewed-barrier-kit';
  const meshBatch=batch(),railBatch=batch(),wallBatch=batch();const poleMatrices:THREE.Matrix4[]=[],boltMatrices:THREE.Matrix4[]=[];
  const up=new THREE.Vector3(0,1,0),q=new THREE.Quaternion();
  function rod(a:P,b:P,r:number){const av=new THREE.Vector3(...a),bv=new THREE.Vector3(...b),delta=bv.clone().sub(av);if(delta.length()<.001)return;q.setFromUnitVectors(up,delta.clone().normalize());poleMatrices.push(new THREE.Matrix4().compose(av.add(bv).multiplyScalar(.5),q.clone(),new THREE.Vector3(r,delta.length(),r)));}
  const walls=barriers.filter(b=>b.type==='wall');
  function wallBelow(x:number,z:number){for(const b of walls)for(let i=1;i<b.points.length;i++){const a=b.points[i-1],c=b.points[i],dx=c[0]-a[0],dz=c[1]-a[1],f=THREE.MathUtils.clamp(((x-a[0])*dx+(z-a[1])*dz)/(dx*dx+dz*dz||1),0,1);if(Math.hypot(x-a[0]-f*dx,z-a[1]-f*dz)<.65)return b.height;}return .05;}
  for(const b of barriers){
    const rows=sampleBarrierBays(b.points).map(r=>{const road=roadAt(r.x,r.z),dx=road.x-r.x,dz=road.z-r.z,len=Math.hypot(dx,dz)||1;return {...r,y:heightAt(r.x,r.z),nx:dx/len,nz:dz/len};});
    if(rows.length<2)continue;
    const at=(r:typeof rows[number],out:number,h:number):P=>[r.x+r.nx*out,r.y+h,r.z+r.nz*out];
    for(let i=0;i<rows.length-1;i++){
      const a=rows[i],c=rows[i+1];if(!Number.isFinite(a.y+c.y)||Math.abs(a.y-c.y)>4)continue;
      if(b.type==='fence'){
        const baseA=wallBelow(a.x,a.z),baseC=wallBelow(c.x,c.z);
        for(let k=0;k<CATCH_PROFILE.length-1;k++){
          const [o,h]=CATCH_PROFILE[k],[v,t]=CATCH_PROFILE[k+1];
          const ha=k===0?baseA:h,hc=k===0?baseC:h;
          quad(meshBatch,at(a,o,ha),at(c,o,hc),at(a,v,t),at(c,v,t),a.s,c.s,ha,t);
        }
        for(const k of [1,2,3]){const [o,h]=CATCH_PROFILE[k];rod(at(a,o,h),at(c,o,h),.012);}
      }else if(b.type==='guard_rail'){
        const profile=[[-.13,0],[-.10,.045],[-.055,.065],[0,0],[.055,.065],[.10,.045],[.13,0]];
        for(const h of [.34,.64,.94])for(let k=0;k<profile.length-1;k++){const [y,o]=profile[k],[v,n]=profile[k+1];quad(railBatch,at(a,o,h+y),at(c,o,h+y),at(a,n,h+v),at(c,n,h+v),a.s,c.s,h+y,h+v);}
      }else{
        // Tapered base and slight bay joints show mass instead of a paper ribbon.
        const profile=[[-.2,-.06],[.22,-.06],[.10,b.height],[-.10,b.height],[-.2,-.06]];
        for(let k=0;k<profile.length-1;k++){const [o,h]=profile[k],[v,t]=profile[k+1];quad(wallBatch,at(a,o,h),at(c,o,h),at(a,v,t),at(c,v,t),a.s,c.s,h,t);}
      }
    }
    for(const r of rows.filter(r=>r.post&&Number.isFinite(r.y))){
      if(b.type==='fence'){
        for(let k=0;k<CATCH_PROFILE.length-1;k++){const [o,h]=CATCH_PROFILE[k],[v,t]=CATCH_PROFILE[k+1];rod(at(r,o,h),at(r,v,t),.048);}
      }else if(b.type==='guard_rail'){
        rod(at(r,-.12,0),at(r,-.12,1.02),.047);
        for(const h of [.34,.64,.94])boltMatrices.push(new THREE.Matrix4().makeTranslation(...at(r,.078,h)));
      }else{
        rod(at(r,.107,.10),at(r,.107,b.height-.06),.011);
      }
    }
  }
  const steel=new THREE.MeshStandardMaterial({color:0xb9c0bf,roughness:.43,metalness:.55,side:THREE.DoubleSide});
  const concrete=new THREE.MeshStandardMaterial({color:0xc5c6bf,roughness:.98,side:THREE.DoubleSide});
  // Subtle concrete variation/joints, no photographic texture or extra draw calls.
  concrete.onBeforeCompile=s=>{s.vertexShader=s.vertexShader.replace('#include <common>','#include <common>\nvarying vec2 barrierUV;').replace('#include <begin_vertex>','#include <begin_vertex>\nbarrierUV=uv;');s.fragmentShader=s.fragmentShader.replace('#include <common>','#include <common>\nvarying vec2 barrierUV;').replace('#include <color_fragment>','#include <color_fragment>\nfloat joint=1.0-smoothstep(0.006,0.014,abs(fract(barrierUV.x/4.0+.5)-.5));\nfloat grain=fract(sin(dot(floor(barrierUV*90.0),vec2(12.9898,78.233)))*43758.5453);\ndiffuseColor.rgb*=mix(.95,1.03,grain)*(1.0-joint*.15);');};concrete.customProgramCacheKey=()=> 'fuji-concrete-v1';
  group.add(makeMesh(meshBatch,buildCgFenceMaterial(),'reviewed-catch-mesh'),makeMesh(railBatch,steel,'reviewed-triple-w-beams'),makeMesh(wallBatch,concrete,'reviewed-concrete-walls'));
  if(poleMatrices.length){const poles=new THREE.InstancedMesh(new THREE.CylinderGeometry(1,1,1,7),new THREE.MeshStandardMaterial({color:0x626b69,roughness:.7,metalness:.45}),poleMatrices.length);poles.name='reviewed-posts-and-tension-wires';poleMatrices.forEach((m,i)=>poles.setMatrixAt(i,m));poles.instanceMatrix.needsUpdate=true;group.add(poles);}
  if(boltMatrices.length){const bolts=new THREE.InstancedMesh(new THREE.SphereGeometry(.028,5,4),steel,boltMatrices.length);bolts.name='reviewed-rail-bolts';boltMatrices.forEach((m,i)=>bolts.setMatrixAt(i,m));bolts.instanceMatrix.needsUpdate=true;group.add(bolts);}
  group.userData.profiles=['triple-corrugated-rail','inward-curved-catch-fence','battered-concrete-wall'];group.userData.dimensions='illustrative, not measured';return group;
}
