"""Leave-one-kerb-out: session-level translation fitted on track limits (v2 course) + IMU labels of the other kerbs.
Variants: session(track only), session(track+labels LOKO), session+lap deviations (sigma 0.25 m) with labels LOKO."""
import json, sys, math
import numpy as np
from scipy.optimize import minimize
import eval_v2 as E
import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from paths import ROOT, WORK, XRK, VIDEO  # noqa: E402
from register_gps_to_track import huber
OUT={}
for race,n,cu in (('fuji_aim_2020_07_30',6,1.0),('fuji_aim_01',4,0.0)):
    z=dict(np.load(f'{E.SP}/xrk_{race}.npz')); maps=json.loads(str(z['maps']))
    laps=[E.Lap(race,k,z,maps) for k in range(1,n+1)]
    def gmin(l,kb,d):
        K=E.KERB[kb]; ps=l.passes[kb]; W=l.W50[ps['mask']]+np.asarray(d); fi,ri=(0,2) if K['sign']>0 else (1,3)
        return min(K['block'].signed_gap(W[:,fi],K['sign']).min(),K['block'].signed_gap(W[:,ri],K['sign']).min())
    def lab(l,kb,d):
        ps=l.passes[kb]; g=gmin(l,kb,d)
        return (max(0,g) if ps['cls']=='strong' else max(0,-0.4-g)) if ps['cls'] in ('strong','silent') else 0.0
    track=[ (lambda d,l=l: l.track_cost(d,cu)) for l in laps]
    def sess_cost(d,excl=None,labels=True):
        c=sum(f(d) for f in track)
        if labels: c+=50*sum(lab(l,kb,d)**2 for l in laps for kb in l.passes if kb!=excl)
        return c
    def fit_sess(excl=None,labels=True,x0=(0.5,-2.5)):
        best=None
        for a in (x0,(x0[0]+1,x0[1]),(x0[0],x0[1]+1),(x0[0]-1,x0[1]),(x0[0],x0[1]-1)):
            r=minimize(lambda d: sess_cost(d,excl,labels),a,method='Nelder-Mead',options=dict(xatol=1e-3,fatol=1e-4))
            if best is None or r.fun<best.fun: best=r
        return best.x
    def fit_lap(l,sess,excl,sig=0.25):
        f=lambda d: l.track_cost(d,cu)+50*sum(lab(l,kb,d)**2 for kb in l.passes if kb!=excl)+float(np.sum((np.asarray(d)-sess)**2))/(sig**2)*0.5
        return minimize(f,sess,method='Nelder-Mead',options=dict(xatol=1e-3,fatol=1e-4)).x
    x0=(0.5,-2.5) if race.endswith('30') else (-1.5,2.0)
    s_track=fit_sess(labels=False,x0=x0)
    res={'sessionTrackOnly':[], 'sessionLabelsLOKO':[], 'sessionPlusLapLOKO':[]}
    kerbs=sorted({kb for l in laps for kb,p in l.passes.items() if p['cls'] in ('strong','silent')})
    for kb in kerbs:
        s_lab=fit_sess(excl=kb,x0=tuple(s_track))
        for l in laps:
            if kb not in l.passes or l.passes[kb]['cls'] not in ('strong','silent'): continue
            cls=l.passes[kb]['cls']
            dl=fit_lap(l,s_lab,kb)
            for key,d in (('sessionTrackOnly',s_track),('sessionLabelsLOKO',s_lab),('sessionPlusLapLOKO',dl)):
                g=gmin(l,kb,d); bad=(g>0.15) if cls=='strong' else (g<-0.55)
                res[key].append(dict(lap=l.k,kerb=kb,cls=cls,g=round(float(g),2),contradiction=bool(bad)))
        print(race,'kerb',kb,'session(labels,LOKO)',np.round(s_lab,2),flush=True)
    summ={}
    for key,v in res.items():
        S=[x['g'] for x in v if x['cls']=='strong']; N=[x['g'] for x in v if x['cls']=='silent']
        summ[key]=dict(n=len(v),contradictions=[(x['lap'],x['kerb'],x['cls'],x['g']) for x in v if x['contradiction']],auc=E.auc(S,N))
        print(race,f'{key:20s}','held-out contradictions',len(summ[key]['contradictions']),'of',len(v),'AUC',None if summ[key]['auc'] is None else round(summ[key]['auc'],3),summ[key]['contradictions'],flush=True)
    OUT[race]=dict(sessionTrackOnly=np.round(s_track,3).tolist(),summary=summ,rows=res)
json.dump(OUT,open(f'{E.IT2}/loko_session.json','w'),indent=1)
