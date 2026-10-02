import json, sys
import numpy as np
import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from paths import ROOT, WORK, XRK, VIDEO  # noqa: E402
from scipy.ndimage import uniform_filter1d
from scipy.interpolate import CubicSpline
from vib import *
ev=json.load(open(f'{SP}/strike_events.json'))['events']
print('== wheel-speed ripple side test (7/30 strong events, reg) ==')
race='fuji_aim_2020_07_30'; z=dict(np.load(f'{SP}/xrk_{race}.npz')); maps=json.loads(str(z['maps']))
for e in [x for x in ev if x['race']==race and x['mode']=='reg' and x['kerb'] is not None]:
    tc0=maps[str(e['lap'])]['tc0']; rip={}
    for w in ('FL','FR','RL','RR'):
        t=(z[f'ECU_WS_{w}_t']-tc0)/1000+SHIFT[race]; v=z[f'ECU_WS_{w}']
        hp=v-uniform_filter1d(v,5); m=(t>e['t']-0.4)&(t<e['t']+0.4)
        rip[w]=float(np.sqrt(np.mean(hp[m]**2)))
    left=rip['FL']+rip['RL']; right=rip['FR']+rip['RR']
    print(f"L{e['lap']} s={e['station']:6.0f} kerb {e['kerb']:2d} {e['side']:5s} roll {e['roll']:.1f} | ripple L {left:.2f} R {right:.2f} -> {'left' if left>right else 'right'}")
print('== GPS QC: gain-corrected gyro vs GPS course rate per apex window ==')
gw=json.load(open(f'{SP}/gyro_vs_gps.json'))
bad=[x for x in gw if x['r']<0.95 or abs(x['lagS'])>0.15 or not (1.05<x['ampRatio']<1.35)]
print('windows',len(gw),'flagged',len(bad),[(x['race'][-5:],x['lap'],x['kerb'],x['r'],x['lagS'],x['ampRatio']) for x in bad])
print('== 2960-3010 m right edge check ==')
for race2,n in [('fuji_aim_01',4),('fuji_aim_2020_07_30',6)]:
    for k in range(1,n+1):
        d,t,raw,reg=lap_data(race2,k); idx,_=L.lateral(reg); st=L.dist[idx]; W=wheel_points(reg,t,geometry=geo)
        gr=np.minimum(L.road_right.signed_gap(W[:,1],-1),L.road_right.signed_gap(W[:,3],-1))
        m=(st>2960)&(st<3010)
        if m.any(): print(f'  {race2[-5:]} L{k}: min right gap {gr[m].min():+.2f} at s={st[m][np.argmin(gr[m])]:.0f}')
