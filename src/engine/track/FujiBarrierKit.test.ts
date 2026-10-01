import {it,expect} from 'vitest';
import {buildFujiBarrierKit,sampleBarrierBays} from './FujiBarrierKit';
import * as THREE from 'three';
it('spaces physical posts independently of source control-point sampling',()=>{
 const rows=sampleBarrierBays([[0,0],[3,0],[10,0]]);expect(rows.filter(r=>r.post).map(r=>r.s)).toEqual([0,4,8,10]);expect(rows.some(r=>r.s===3)).toBe(true);
});
it('bends catch mesh toward the course and keeps three separated corrugated bands',()=>{
 const g=buildFujiBarrierKit([{type:'fence',height:3.4,points:[[0,0],[8,0]]},{type:'guard_rail',height:1,points:[[0,0],[8,0]]}],()=>0,(x)=>({x,z:10}));
 const mesh=g.getObjectByName('reviewed-catch-mesh') as THREE.Mesh;const p=mesh.geometry.getAttribute('position');let top=0;for(let i=0;i<p.count;i++){expect(Number.isFinite(p.getX(i)+p.getY(i)+p.getZ(i))).toBe(true);if(p.getY(i)>3.39){expect(p.getZ(i)).toBeCloseTo(.65);top++;}}expect(top).toBeGreaterThan(0);
 const rail=g.getObjectByName('reviewed-triple-w-beams') as THREE.Mesh;const rp=rail.geometry.getAttribute('position');const ys=Array.from({length:rp.count},(_,i)=>rp.getY(i));expect(Math.min(...ys)).toBeCloseTo(.21);expect(Math.max(...ys)).toBeCloseTo(1.07);expect(ys.some(y=>y>.47&&y<.50)).toBe(false);
});
