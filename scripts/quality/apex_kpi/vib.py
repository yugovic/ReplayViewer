"""Kerb-ride vibration (IMU) vs viewer-predicted wheel-on-kerb along the lap."""
import json, sys, math
from pathlib import Path
import numpy as np
from scipy.ndimage import uniform_filter1d
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from paths import ROOT, WORK, XRK, VIDEO  # noqa: E402
ROOT=ROOT; sys.path.insert(0,str(ROOT/'pipeline'))
from register_gps_to_track import load_limits, to_local, wheel_points, load_vehicle_geometry
SP=str(WORK)
SHIFT={'fuji_aim_01':0.014,'fuji_aim_2020_07_30':0.507}   # IMU/logger-clock -> GPS time base
L=load_limits(ROOT/'public/data/tracks/fuji',0.0); geo=load_vehicle_geometry(ROOT/'public/data/vehicles/mazda2-dj.json')
tr=json.loads((ROOT/'public/data/tracks/fuji/track.json').read_text(encoding='utf-8')); o=tr['origin']
g=json.loads((ROOT/'public/data/tracks/fuji/cg_study/geometry.json').read_text(encoding='utf-8'))
def vib(z,tc0,shift):
    out={}
    for c in ('VerticalAcc','RollRate','PitchRate'):
        t=(z[c+'_t']-tc0)/1000+shift; v=z[c]
        hp=v-uniform_filter1d(v,15)          # remove <~3 Hz (50 Hz data, 0.3 s)
        rms=np.sqrt(uniform_filter1d(hp*hp,10))   # 0.2 s window
        out[c]=(t,rms)
    return out
def lap_data(race,k):
    d=json.loads((ROOT/f'public/data/races/{race}/osaki_hmr_demio_101_lap_{k:03d}.json').read_text(encoding='utf-8'))
    reg=json.loads((ROOT/f'public/data/races/{race}/gps_registration.json').read_text(encoding='utf-8'))
    off={l['lap']:l['offsetMeters'] for l in reg['laps']}[k]
    t=np.array(d['t'],float); raw=to_local(d['lat'],d['lng'],o)
    return d,t,raw,raw+np.array(off)
if __name__=='__main__':
    race,k=sys.argv[1],int(sys.argv[2])
    z=dict(np.load(f'{SP}/xrk_{race}.npz')); maps=json.loads(str(z['maps']))
    V=vib(z,maps[str(k)]['tc0'],SHIFT[race])
    d,t,raw,reg=lap_data(race,k)
    idx,_=L.lateral(reg); st=L.dist[idx]
    W=wheel_points(reg,t,geometry=geo)
    gl=np.minimum(L.road_left.signed_gap(W[:,0],+1),L.road_left.signed_gap(W[:,2],+1))
    gr=np.minimum(L.road_right.signed_gap(W[:,1],-1),L.road_right.signed_gap(W[:,3],-1))
    fig,ax=plt.subplots(3,1,figsize=(22,10),sharex=True)
    for c,col in (('VerticalAcc','k'),('RollRate','b')):
        tt,r=V[c]; m=(tt>=0)&(tt<=t[-1]); s_=np.interp(tt[m],t,st)
        ax[0 if c=='VerticalAcc' else 1].plot(s_,r[m],col,lw=0.6); ax[0 if c=='VerticalAcc' else 1].set_ylabel(c+' HP rms')
    ax[2].plot(st,gl,'g',lw=0.8,label='left inner gap (reg)'); ax[2].plot(st,gr,'r',lw=0.8,label='right inner gap (reg)')
    ax[2].axhline(0,color='k',lw=0.5); ax[2].set_ylim(-3,4); ax[2].legend()
    for i,c in enumerate(g['curbs']):
        s0,s1=c['rows'][0][0],c['rows'][-1][0]
        for a in ax: a.axvspan(s0,s1,color='g' if c['side']=='left' else 'r',alpha=0.08)
        ax[0].text((s0+s1)/2,ax[0].get_ylim()[1]*0.9,str(i),fontsize=7)
    plt.tight_layout(); plt.savefig(f'{SP}/vib_{race}_L{k}.png',dpi=70)
    print('saved')
