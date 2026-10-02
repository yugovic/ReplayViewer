import json, numpy as np
import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from paths import ROOT, WORK, XRK, VIDEO  # noqa: E402
SP=str(WORK)
ROOT=str(ROOT)
for race,n in [('fuji_aim_01',4),('fuji_aim_2020_07_30',6)]:
    z=dict(np.load(f'{SP}/xrk_{race}.npz'))
    gs_t=z['GPS Speed_t']; gs=z['GPS Speed']
    print(race,'gps speed range',gs.min(),gs.max(),'tc range',gs_t[0],gs_t[-1])
    maps={}
    for k in range(1,n+1):
        d=json.load(open(f'{ROOT}/public/data/races/{race}/osaki_hmr_demio_101_lap_{k:03d}.json',encoding='utf-8'))
        t=np.array(d['t']); sp=np.array(d['speed'])
        unit=3.6 if gs.max()<100 else 1.0
        # coarse: find tc0 on 100ms grid minimizing rms, then refine 1ms
        best=(1e9,None)
        for tc0 in gs_t[: len(gs_t)-len(t)]:
            v=np.interp(tc0+t*1000,gs_t,gs)*unit
            e=np.sqrt(np.mean((v-sp)**2))
            if e<best[0]: best=(e,tc0)
        e0,tc=best
        for tc0 in np.arange(tc-100,tc+100,1.0):
            v=np.interp(tc0+t*1000,gs_t,gs)*unit
            e=np.sqrt(np.mean((v-sp)**2))
            if e<best[0]: best=(e,tc0)
        maps[k]=dict(tc0=float(best[1]),rmsKmh=float(best[0]))
        print(' lap',k,'tc0',best[1],'rms km/h',round(best[0],4))
    z['maps']=json.dumps(maps)
    np.savez_compressed(f'{SP}/xrk_{race}.npz',**z)
