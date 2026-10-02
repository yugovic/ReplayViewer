import json, sys, math
from pathlib import Path
import numpy as np
from vib import *
rows=[]
for race,n in [('fuji_aim_01',4),('fuji_aim_2020_07_30',6)]:
    z=dict(np.load(f'{SP}/xrk_{race}.npz')); maps=json.loads(str(z['maps']))
    for k in range(1,n+1):
        V=vib(z,maps[str(k)]['tc0'],SHIFT[race])
        d,t,raw,reg=lap_data(race,k)
        for mode,xy in (('raw',raw),('reg',reg)):
            idx,_=L.lateral(xy); st=L.dist[idx]
            W=wheel_points(xy,t,geometry=geo)
            gl=np.minimum(L.road_left.signed_gap(W[:,0],+1),L.road_left.signed_gap(W[:,2],+1))
            gr=np.minimum(L.road_right.signed_gap(W[:,1],-1),L.road_right.signed_gap(W[:,3],-1))
            for i,c in enumerate(g['curbs']):
                s0,s1=c['rows'][0][0],c['rows'][-1][0]
                m=(st>=s0-3)&(st<=s1+3)
                if m.sum()<2: continue
                gap=(gl if c['side']=='left' else gr)[m]
                j=int(np.argmin(gap))
                rec=dict(race=race,lap=k,kerb=i,side=c['side'],mode=mode,gapMin=float(gap[j]),gapStation=float(st[m][j]),tGap=float(t[m][j]),
                         onKerbSeconds=float((gap<0).sum()*0.1))
                if mode=='reg':
                    tt,rr=V['RollRate']; tv,rv=V['VerticalAcc']
                    tm=(tt>=t[m][0])&(tt<=t[m][-1]); tvm=(tv>=t[m][0])&(tv<=t[m][-1])
                    rec['rollPeak']=float(rr[tm].max()) if tm.any() else None
                    rec['vertPeak']=float(rv[tvm].max()) if tvm.any() else None
                    if tm.any():
                        jj=np.argmax(rr[tm]); rec['tRollPeak']=float(tt[tm][jj]); rec['sRollPeak']=float(np.interp(tt[tm][jj],t,st))
                rows.append(rec)
json.dump(rows,open(f'{SP}/kerb_contact.json','w'),indent=0)
# baseline: roll HP rms distribution on straights
import collections
import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from paths import ROOT, WORK, XRK, VIDEO  # noqa: E402
print('roll peak distribution per kerb (reg rows):')
by=collections.defaultdict(list)
for r in rows:
    if r['mode']=='reg': by[(r['kerb'],r['race'])].append(r)
for (kb,race),rs in sorted(by.items()):
    raw=[x for x in rows if x['mode']=='raw' and x['kerb']==kb and x['race']==race]
    R=' '.join('%.2f'%x['rollPeak'] for x in rs); Vv=' '.join('%.3f'%x['vertPeak'] for x in rs)
    G=' '.join('%+.2f'%x['gapMin'] for x in rs); Gr=' '.join('%+.2f'%x['gapMin'] for x in raw)
    print('k%2d %-5s %s roll %s | vert %s | gapReg %s | gapRaw %s'%(kb,rs[0]['side'],race[-5:],R,Vv,G,Gr))
