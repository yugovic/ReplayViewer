import * as THREE from 'three';
import type { Barrier3D, GroundHeightFn } from './Features3DBuilder';

export interface FujiContext {
  enabled?: boolean;
  hill: [number,number][];
  woodland: [number,number][];
  source: string;
  sourceSha256: string;
}
export function insideContextPolygon(x:number,z:number,points:[number,number][]) {
  let inside=false;
  for(let i=0,j=points.length-1;i<points.length;j=i++) {
    const [ax,az]=points[i],[bx,bz]=points[j];
    if((az>z)!==(bz>z) && x<(bx-ax)*(z-az)/(bz-az)+ax)inside=!inside;
  }
  return inside;
}

/** One physical plane with UVs measured in metres, rather than two overlapping
 * translucent faces. Posts continue to come from the existing opaque batch. */
export function buildCgFenceGeometry(barriers:Barrier3D[],heightAt:GroundHeightFn) {
  const positions:number[]=[],uvs:number[]=[],indices:number[]=[];
  for(const b of barriers.filter(b=>b.type==='fence')) {
    let distance=0;
    for(let i=0;i<b.points.length-1;i++) {
      const a=b.points[i],c=b.points[i+1],length=Math.hypot(c[0]-a[0],c[1]-a[1]);
      const ya=heightAt(...a),yc=heightAt(...c);
      if(!Number.isFinite(ya+yc)||Math.abs(yc-ya)>4){distance+=length;continue;}
      const start=positions.length/3;
      positions.push(a[0],ya,a[1],c[0],yc,c[1],a[0],ya+b.height,a[1],c[0],yc+b.height,c[1]);
      uvs.push(distance,0,distance+length,0,distance,b.height,distance+length,b.height);
      indices.push(start,start+2,start+1,start+1,start+2,start+3);distance+=length;
    }
  }
  const geometry=new THREE.BufferGeometry();
  geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));
  geometry.setAttribute('uv',new THREE.Float32BufferAttribute(uvs,2));
  geometry.setIndex(indices);geometry.computeVertexNormals();geometry.computeBoundingSphere();return geometry;
}

export function buildCgFenceMaterial() {
  const material=new THREE.MeshStandardMaterial({color:0x39423c,roughness:.85,metalness:.15,
    side:THREE.DoubleSide,transparent:true,opacity:.85,depthWrite:false});
  material.onBeforeCompile=shader=>{
    shader.vertexShader=shader.vertexShader.replace('#include <common>','#include <common>\nvarying vec2 fenceMeters;')
      .replace('#include <begin_vertex>','#include <begin_vertex>\nfenceMeters=uv;');
    shader.fragmentShader=shader.fragmentShader.replace('#include <common>','#include <common>\nvarying vec2 fenceMeters;')
      .replace('#include <alphatest_fragment>',`#include <alphatest_fragment>
        // Illustrative 10cm diamond pitch / thin wires, not a measured spec.
        vec2 grid=vec2(fenceMeters.x+fenceMeters.y,fenceMeters.x-fenceMeters.y)/.10;
        vec2 aa=max(fwidth(grid),vec2(.001));
        vec2 d=abs(fract(grid+.5)-.5);
        vec2 wire=1.0-smoothstep(vec2(.025)-aa,vec2(.025)+aa,d);
        float coverage=max(wire.x,wire.y);
        // Preserve low average coverage when subpixel instead of a solid wall.
        coverage=mix(coverage,.0975,smoothstep(.25,1.0,max(aa.x,aa.y)));
        diffuseColor.a*=coverage;
        if(diffuseColor.a<.005)discard;
      `);
  };
  material.customProgramCacheKey=()=> 'fuji-wire-fence-v1';
  return material;
}
