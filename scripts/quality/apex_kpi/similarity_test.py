"""Does a session-level similarity transform (rotation/scale about the track centre) reconcile labels better than translation only?"""
import json, sys, math
import numpy as np
from scipy.optimize import minimize
import eval_v2 as E
import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from paths import ROOT, WORK, XRK, VIDEO  # noqa: E402
from register_gps_to_track import huber
race,n='fuji_aim_2020_07_30',6
z=dict(np.load(f'{E.SP}/xrk_{race}.npz')); maps=json.loads(str(z['maps']))
laps=[E.Lap(race,k,z,maps) for k in range(1,n+1)]
C=np.mean(np.concatenate([l.raw for l in laps]),axis=0)
def tf(P,p):
    tx,tz,th,sc=p; c,s=math.cos(th),math.sin(th)
    Q=P-C; R=np.stack([c*Q[...,0]-s*Q[...,1], s*Q[...,0]+c*Q[...,1]],-1)
    return C+(1+sc)*R+np.array([tx,tz])
def label_terms(l,p):
    out=[]
    for kb,ps in l.passes.items():
        if ps['cls'] not in ('strong','silent'): continue
        K=E.KERB[kb]; W=tf(l.W50[ps['mask']],p)
        fi,ri=(0,2) if K['sign']>0 else (1,3)
        gm=min(K['block'].signed_gap(W[:,fi],K['sign']).min(),K['block'].signed_gap(W[:,ri],K['sign']).min())
        v=max(0,gm-0.0) if ps['cls']=='strong' else max(0,-0.4-gm)
        out.append((kb,l.k,ps['cls'],gm,v))
    return out
def cost(p,use_labels=True,free_rs=True):
    if not free_rs: p=[p[0],p[1],0,0]
    c=0
    for l in laps:
        m=E.LV2.margins(tf(l.W10[l.keep],p),1.0); c+=float(huber(np.maximum(0,-m)).sum())
        if use_labels: c+=50*sum(v*v for *_,v in label_terms(l,p))
    return c
for name,free in (('translation',False),('translation+rotation+scale',True)):
    best=None
    for th0 in ([0] if not free else [-0.001,0,0.001]):
        x0=[0.5,-3.0,th0,0.0] if free else [0.5,-3.0]
        r=minimize(lambda p: cost(p,True,free) if free else cost(list(p)+[0,0],True,False),x0,method='Nelder-Mead',options=dict(xatol=1e-4,fatol=1e-4,maxiter=800))
        if best is None or r.fun<best.fun: best=r
    p=list(best.x)+([] if free else [0,0])
    T=[t for l in laps for t in label_terms(l,p)]
    contra=[(k,kb,cls,round(gm,2)) for kb,k,cls,gm,v in T if v>0.15]
    print(f'{name:28s} params tx,tz={p[0]:+.2f},{p[1]:+.2f} rot={math.degrees(p[2]):+.4f}deg scale={p[3]*1e3:+.2f}e-3 cost={best.fun:.2f} contradictions {len(contra)}: {contra}')
