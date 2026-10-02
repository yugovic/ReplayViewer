"""GPS course-rate vs gyro yaw-rate around each apex: lag and amplitude ratio (GPS shape check)."""
import json, sys
import numpy as np
from scipy.interpolate import CubicSpline
from vib import *
rows=json.load(open(f'{SP}/kerb_contact.json'))
INSIDE=[1,3,7,9,10,13,15,18,19,20,21,23,24]
out=[]
for race,n in [('fuji_aim_01',4),('fuji_aim_2020_07_30',6)]:
    z=dict(np.load(f'{SP}/xrk_{race}.npz')); maps=json.loads(str(z['maps']))
    # gyro sign/scale vs GPS course rate over whole session
    for k in range(1,n+1):
        d,t,raw,reg=lap_data(race,k)
        cx,cz=CubicSpline(t,reg[:,0]),CubicSpline(t,reg[:,1])
        vx,vz=cx(t,1),cz(t,1); psi=np.unwrap(np.arctan2(vx,vz)); wg=np.degrees(np.gradient(psi,t))
        ty=(z['YawRate_t']-maps[str(k)]['tc0'])/1000+SHIFT[race]; wy=z['YawRate']
        m=(ty>=0)&(ty<=t[-1]); ty,wy=ty[m],wy[m]
        sgn=np.sign(np.corrcoef(np.interp(ty,t,wg),wy)[0,1])
        wy=sgn*wy
        for r in rows:
            if r['race']!=race or r['lap']!=k or r['mode']!='reg' or r['kerb'] not in INSIDE: continue
            tc=r['tGap']; w=(t>tc-3)&(t<tc+3)
            if w.sum()<20: continue
            tt=t[w]; g=wg[w]
            best=(None,-2)
            for s in np.arange(-0.4,0.41,0.01):
                y=np.interp(tt+s,ty,wy); c=np.corrcoef(g,y)[0,1]
                if c>best[1]: best=(s,c)
            y=np.interp(tt+best[0],ty,wy)
            ratio=np.polyfit(y,g,1)[0]
            out.append(dict(race=race,lap=k,kerb=r['kerb'],lagS=round(best[0],2),r=round(best[1],3),ampRatio=round(ratio,3),
                            peakGyro=round(float(np.max(np.abs(y))),1),peakGps=round(float(np.max(np.abs(g))),1)))
import collections
import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from paths import ROOT, WORK, XRK, VIDEO  # noqa: E402
for race in ('fuji_aim_01','fuji_aim_2020_07_30'):
    o=[x for x in out if x['race']==race]
    lag=np.array([x['lagS'] for x in o]); amp=np.array([x['ampRatio'] for x in o]); rr=np.array([x['r'] for x in o])
    print(race,'apex windows',len(o),'lag median',np.median(lag),'IQR',np.percentile(lag,[25,75]),'amp ratio median',np.round(np.median(amp),3),'IQR',np.round(np.percentile(amp,[25,75]),3),'r median',np.round(np.median(rr),3))
    worst=sorted(o,key=lambda x:abs(x['ampRatio']-1))[-4:]
    print('  worst amp:',[(x['lap'],x['kerb'],x['ampRatio'],x['lagS']) for x in worst])
json.dump(out,open(f'{SP}/gyro_vs_gps.json','w'),indent=0)
