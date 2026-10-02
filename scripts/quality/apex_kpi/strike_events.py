"""Event-based kerb-strike labelling: peaks of roll-rate HP rms; assign each event once to the nearest kerb (any side) by wheel distance at that instant."""
import json, sys
import numpy as np
import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from paths import ROOT, WORK, XRK, VIDEO  # noqa: E402
from scipy.signal import find_peaks
from vib import *
THR=1.5
INSIDE=[1,3,7,9,10,13,15,18,19,20,21,23,24]
events=[]; passes=[]
for race,n in [('fuji_aim_01',4),('fuji_aim_2020_07_30',6)]:
    z=dict(np.load(f'{SP}/xrk_{race}.npz')); maps=json.loads(str(z['maps']))
    for k in range(1,n+1):
        V=vib(z,maps[str(k)]['tc0'],SHIFT[race]); tt,rr=V['RollRate']
        d,t,raw,reg=lap_data(race,k)
        m=(tt>=0.3)&(tt<=t[-1]-0.3); tt,rr=tt[m],rr[m]
        pk,_=find_peaks(rr,height=THR,distance=25)   # >=0.5 s apart
        for mode,xy in (('raw',raw),('reg',reg)):
            idx,_=L.lateral(xy); st=L.dist[idx]; W=wheel_points(xy,t,geometry=geo)
            gl=np.minimum(L.road_left.signed_gap(W[:,0],+1),L.road_left.signed_gap(W[:,2],+1))
            gr=np.minimum(L.road_right.signed_gap(W[:,1],-1),L.road_right.signed_gap(W[:,3],-1))
            # per inside kerb pass (min gap) for no-strike evaluation
            for i in INSIDE:
                c=g['curbs'][i]; s0,s1=c['rows'][0][0],c['rows'][-1][0]; mm=(st>=s0-3)&(st<=s1+3)
                if mm.sum()<2: continue
                gp=(gl if c['side']=='left' else gr)[mm]; j=int(np.argmin(gp))
                tw0,tw1=t[mm][0],t[mm][-1]
                strike_in=[p for p in pk if tw0-0.3<=tt[p]<=tw1+0.3]
                passes.append(dict(race=race,lap=k,kerb=i,mode=mode,gapMin=float(gp[j]),t0=float(tw0),t1=float(tw1),maxRoll=float(rr[(tt>=tw0)&(tt<=tw1)].max()) if ((tt>=tw0)&(tt<=tw1)).any() else 0.0))
            for p in pk:
                te=tt[p]; w=(t>=te-0.4)&(t<=te+0.4)
                # which kerb is closest to any wheel around the event
                best=None
                for i,c in enumerate(g['curbs']):
                    s0,s1=c['rows'][0][0],c['rows'][-1][0]
                    sw=st[w]
                    if not ((sw>=s0-3)&(sw<=s1+3)).any(): continue
                    gp=(gl if c['side']=='left' else gr)[w].min()
                    if best is None or gp<best[1]: best=(i,float(gp))
                events.append(dict(race=race,lap=k,mode=mode,t=float(te),station=float(np.interp(te,t,st)),roll=float(rr[p]),
                                   kerb=None if best is None else best[0],gap=None if best is None else best[1],
                                   side=None if best is None else g['curbs'][best[0]]['side']))
json.dump(dict(events=events,passes=passes),open(f'{SP}/strike_events.json','w'),indent=0)
for race in ('fuji_aim_01','fuji_aim_2020_07_30'):
    for mode in ('raw','reg'):
        E=[e for e in events if e['race']==race and e['mode']==mode]
        onk=[e for e in E if e['kerb'] is not None]
        off=[(e['lap'],e['kerb'],round(e['gap'],2),round(e['roll'],1),round(e['station'])) for e in onk if e['gap']>0]
        print(f'{race:20s} {mode}: strike events {len(E)} (no kerb nearby {len(E)-len(onk)}), nearest-kerb gap median {np.median([e["gap"] for e in onk]):+.2f}; shown off-kerb {len(off)}: {off}')
        noev=[(p['lap'],p['kerb'],round(p['gapMin'],2)) for p in passes if p['race']==race and p['mode']==mode and p['maxRoll']<=0.8 and p['gapMin']<-0.5]
        print(f'      no-vibration passes shown >0.5 m on kerb: {noev}')
