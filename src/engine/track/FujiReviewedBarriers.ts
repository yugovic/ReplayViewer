import type { Barrier3D } from './Features3DBuilder';

export interface ReviewedBarriers {
  version: number;
  bounds: [number, number, number, number];
  barriers: Barrier3D[];
  source: string;
  sourceSha256: string;
}
type Point = [number, number];

/** Split crossing segments too: filtering vertices alone reconnects a phantom rail. */
export function replaceReviewedBarriers(original: Barrier3D[], review: ReviewedBarriers): Barrier3D[] {
  const [x0,z0,x1,z1] = review.bounds;
  const result: Barrier3D[] = [];
  for (const barrier of original) {
    let run: Point[] = [];
    const flush = () => { if(run.length > 1) result.push({...barrier, points:run}); run=[]; };
    const append = (a:Point,b:Point) => {
      if(Math.hypot(b[0]-a[0],b[1]-a[1]) < 1e-6) return;
      const last=run.at(-1);
      if(last && Math.hypot(last[0]-a[0],last[1]-a[1]) > 1e-6) flush();
      if(!run.length)run.push(a);
      run.push(b);
    };
    for(let i=0;i<barrier.points.length-1;i++) {
      const a=barrier.points[i],b=barrier.points[i+1],dx=b[0]-a[0],dz=b[1]-a[1];
      let lo=0,hi=1,intersects=true;
      for(const [p,q] of [[-dx,a[0]-x0],[dx,x1-a[0]],[-dz,a[1]-z0],[dz,z1-a[1]]]) {
        if(Math.abs(p)<1e-12) {if(q<0)intersects=false;}
        else if(p<0)lo=Math.max(lo,q/p);
        else hi=Math.min(hi,q/p);
      }
      const at=(t:number):Point=>[a[0]+dx*t,a[1]+dz*t];
      if(!intersects || lo>=hi) append(a,b);
      else {if(lo>0)append(a,at(lo));flush();if(hi<1)append(at(hi),b);}
    }
    flush();
  }
  return [...result, ...review.barriers];
}
