"""Planar ribbon integrity; catches failures that finite-value checks miss."""
import numpy as np

def inverted_cells(rows):
    r=np.asarray(rows);left=r[:,7:9];right=r[:,9:11];failures=[]
    def up(a,b):return a[:,1]*b[:,0]-a[:,0]*b[:,1]
    for col in range(4):
        a=left[:-1]+(right[:-1]-left[:-1])*col/4
        b=left[:-1]+(right[:-1]-left[:-1])*(col+1)/4
        c=left[1:]+(right[1:]-left[1:])*col/4
        d=left[1:]+(right[1:]-left[1:])*(col+1)/4
        bad=np.flatnonzero((up(b-a,c-a)<=0)|(up(d-b,c-b)<=0))
        failures.extend((int(i),col) for i in bad)
    return failures

def repair_folded_samples(data):
    """Reject locally reversed boundary samples, preserving positive widths.

    Interpolated spans are recorded as unmeasured, not silently certified.
    Source observations outside the rejected spans remain unchanged.
    """
    rows=data['road'];curb_at={};curb_repairs=[]
    for curb in data['curbs']:
        points=curb['rows']
        for _ in range(3):
            q=np.asarray(points);inside=q[:,1:3];outside=q[:,3:5];bad=set()
            def cross(a,b):return a[:,1]*b[:,0]-a[:,0]*b[:,1]
            for lo,hi in [(0,.18),(.18,1)]:
                a=inside[:-1]+(outside[:-1]-inside[:-1])*lo
                b=inside[:-1]+(outside[:-1]-inside[:-1])*hi
                c=inside[1:]+(outside[1:]-inside[1:])*lo
                d=inside[1:]+(outside[1:]-inside[1:])*hi
                bad.update(np.flatnonzero(cross(b-a,c-a)*cross(d-b,c-b)<-1e-10))
            if not bad:break
            ids=np.array(sorted(bad))
            for run in np.split(ids,np.flatnonzero(np.diff(ids)>1)+1):
                start=max(0,int(run[0])-2);end=min(len(points)-1,int(run[-1])+3)
                mids=(inside+outside)/2;directions=outside-inside;widths=np.linalg.norm(directions,axis=1)
                directions/=widths[:,None];max_shift=0
                for i in range(start+1,end):
                    t=(points[i][0]-points[start][0])/(points[end][0]-points[start][0])
                    mid=mids[start]*(1-t)+mids[end]*t;normal=directions[start]*(1-t)+directions[end]*t
                    normal/=np.linalg.norm(normal)
                    max_shift=max(max_shift,float(np.linalg.norm(mid-mids[i])))
                    points[i][1:5]=np.concatenate([mid-normal*widths[i]/2,mid+normal*widths[i]/2]).round(3).tolist()
                    points[i][8]=0
                    row=rows[round(points[i][0]/.5)];sign=-1 if curb['side']=='left' else 1;col=7 if sign<0 else 9
                    row[col:col+2]=(np.array(points[i][1:3])).round(3).tolist()
                curb_repairs.append(dict(side=curb['side'],start=points[start][0],end=points[end][0],maxCentreShift=max_shift,method='interpolate rejected centre/normal; retain each observed variable width'))
    data['rejectedCurbSpans']=curb_repairs
    for curb in data['curbs']:
        side=7 if curb['side']=='left' else 9
        for r in curb['rows']:curb_at[(round(r[0]/.5),side)]=r
    repairs=[]
    for iteration in range(6):
        bad=inverted_cells(rows)
        if not bad:break
        for side in [7,9]:
            ids=np.array(sorted({i for i,col in bad if (col<2)==(side==7)}),dtype=int)
            for run in np.split(ids,np.flatnonzero(np.diff(ids)>1)+1):
                if not len(run):continue
                start=max(0,int(run[0])-2-iteration);end=min(len(rows)-1,int(run[-1])+3+iteration)
                a=np.array(rows[start][side:side+2]);b=np.array(rows[end][side:side+2]);shifts=[]
                for i in range(start+1,end):
                    point=a+(b-a)*(rows[i][0]-rows[start][0])/(rows[end][0]-rows[start][0])
                    old=np.array(rows[i][side:side+2]);delta=point-old
                    rows[i][side:side+2]=point.round(3).tolist();shifts.append(float(np.linalg.norm(delta)))
                    curb=curb_at.get((i,side))
                    if curb:
                        for col in [1,3]:curb[col:col+2]=(np.array(curb[col:col+2])+delta).round(3).tolist()
                        curb[8]=0
                repairs.append(dict(side='left' if side==7 else 'right',start=rows[start][0],end=rows[end][0],maxShift=max(shifts,default=0),reason='reversed section rejected; neighbours interpolated; not an independent accuracy measurement'))
    remaining=inverted_cells(rows)
    if remaining:raise ValueError(f'Road still contains reversed triangles: {remaining[:8]}')
    data['rejectedGeometrySpans']=repairs
    return repairs
