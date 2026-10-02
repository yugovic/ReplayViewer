"""Extract IMU/wheel/GPS channels from the two Fuji XRKs and map XRK timecodes to each lap's t (GPS time base)."""
import json, sys, hashlib
import numpy as np
import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from paths import ROOT, WORK, XRK, VIDEO  # noqa: E402
from libxrk import aim_xrk
SP=str(WORK)
ROOT=str(ROOT)
X=XRK
CH=['InlineAcc','LateralAcc','VerticalAcc','RollRate','PitchRate','YawRate','ECU_WS_FL','ECU_WS_FR','ECU_WS_RL','ECU_WS_RR','SPD_2','GPS Speed','GPS_Yaw_Rate','ECU_BRK_P','ECU_STEER_ANG']
for race,path in X.items():
    log=aim_xrk(path)
    out={}
    for c in CH:
        tb=log.channels[c]
        out[c+'_t']=tb.column('timecodes').to_numpy().astype(np.float64)
        out[c]=tb.column(c).to_numpy().astype(np.float64) if c in tb.column_names else tb.column(1).to_numpy().astype(np.float64)
    # map lap t -> XRK timecode using GPS Speed exact sequence match
    gs_t=out['GPS Speed_t']; gs=out['GPS Speed']
    n={'fuji_aim_01':4,'fuji_aim_2020_07_30':6}[race]
    maps={}
    for k in range(1,n+1):
        d=json.load(open(f'{ROOT}/public/data/races/{race}/osaki_hmr_demio_101_lap_{k:03d}.json',encoding='utf-8'))
        sp=np.array(d['speed'])
        best=None
        # search alignment index
        unit = 3.6 if np.nanmax(gs)<100 else 1.0
        for i0 in range(0,len(gs)-len(sp)):
            if abs(gs[i0]*unit-sp[0])>0.01: continue
            seg=gs[i0:i0+len(sp)]*unit
            e=np.max(np.abs(seg-sp))
            if e<0.01: best=(i0,e); break
        if best is None: print(race,k,'no exact match'); continue
        i0=best[0]; tc0=gs_t[i0]
        dtc=np.diff(gs_t[i0:i0+len(sp)])
        maps[k]=dict(tc0=float(tc0),maxerr=float(best[1]),dtc=float(np.median(dtc)))
        print(race,k,'tc0',tc0,'speed match err',best[1],'gps dt',np.median(dtc))
    out['maps']=json.dumps(maps)
    np.savez_compressed(f'{SP}/xrk_{race}.npz',**out)
    print(race,'saved', {c:len(out[c]) for c in CH})
