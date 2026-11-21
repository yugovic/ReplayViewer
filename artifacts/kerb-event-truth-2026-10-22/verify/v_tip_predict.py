"""Verifier step 5b: own replay-side prediction of the video clipping-point 'tip' and the resulting dt.

For a pass, the 3DCG car (own pose re-implementation v_cgpose.py) carries a pinhole camera: 0.55 m behind
the reference point, 0 m lateral (as T3 / camera-profile.json), height h over a FLAT road, pitched so the
forward direction projects onto the measured vanishing row v_vp = 196.8 (T3 vp_calib), u_vp = 640, no roll.
At every frame time the kerb paint polyline (road-side edge of the white strip, kerb_zones.json) is
projected and its crossing with image row 276 is found (crossing nearest the image centre on the kerb
side, ground points ahead only). The CG tip = extreme of that u toward the image centre (parabola through
+-2 samples of a 1/30 s sampling, as for the video). dt = t_CG_tip - t_truth with
t_truth = (video tip PTS - lapStartPts - delta) + L (L = join adopted, and own video-yaw L).
Inputs: video tip PTS from v_tip_edges.py (own pixels) and T3 (passages.json) for comparison.
Writes v_tip_predict.json (numbers only).
"""
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import v_cgpose as P  # noqa: E402
from v_offset import WORK, lap_doc, lap_start_pts  # noqa: E402

KT = HERE.parent
V_VP, ROW = 196.8, 276.0
JL = json.loads((KT / 'join/event_table.json').read_text(encoding='utf-8'))['timeBase']['perLap']
OWNL = json.loads((HERE / 'v_offset.json').read_text(encoding='utf-8'))
T3 = json.loads((KT / 'T3-video-kerb-passages/passages.json').read_text(encoding='utf-8'))
EVENTS = [('fuji_aim_2020_07_30', 2, 'k13', 'dry_L2_k13'), ('fuji_aim_2020_07_30', 4, 'k7', 'dry_L4_k7'),
          ('fuji_aim_2020_07_30', 3, 'k24', 'dry_L3_k24'), ('fuji_aim_01', 3, 'k24', 'wet_L3_k24'),
          ('fuji_aim_01', 3, 'k3', 'wet_L3_k3')]
CAMS = [(711.0, 1.25), (967.0, 1.26), (472.3, 1.25)]


def u_at_row(race, lap, kerb, t, f, h):
    z = P.zones()[kerb]
    T = P.tyres(race, lap, [t])
    c, hd = T['centre'][0], T['heading'][0]
    fw = np.array([math.sin(hd), math.cos(hd)])
    rt = np.array([-fw[1], fw[0]])
    cam = c + (-0.55) * fw
    rel = z['paint'] - cam
    X, D = rel @ rt, rel @ fw
    th = math.atan((360.0 - V_VP) / f)
    yc = h * math.cos(th) - D * math.sin(th)
    zc = h * math.sin(th) + D * math.cos(th)
    ok = zc > 0.5
    v = np.where(ok, 360.0 + f * yc / np.where(ok, zc, 1), np.nan)
    u = np.where(ok, 640.0 + f * X / np.where(ok, zc, 1), np.nan)
    best = None
    for i in range(len(v) - 1):
        a, b = v[i], v[i + 1]
        if not (np.isfinite(a) and np.isfinite(b)) or (a - ROW) * (b - ROW) > 0 or a == b:
            continue
        s = (ROW - a) / (b - a)
        uu = u[i] + s * (u[i + 1] - u[i])
        on_side = uu > 640 - 200 if z['side'] == 'right' else uu < 640 + 200
        if on_side and (best is None or abs(uu - 640) < abs(best - 640)):
            best = uu
    return best


out = []
for race, lap, kerb, tag in EVENTS:
    d = lap_doc(race, lap)
    meta = d['meta']
    delta = float(meta['first_sample_after_lap_start_seconds'])
    sp = lap_start_pts(race, lap, meta)
    te = json.loads((WORK / f'tipedges_{tag}.json').read_text(encoding='utf-8'))
    vtip = te['rows']['276']['tipPts']
    t3 = [p for p in T3['passes'] if p['race'] == race and p['lap'] == lap and p['kerb'] == kerb][0]
    t3tip = t3['rows']['276']['videoTip']['pts']
    t_ov = vtip - sp - delta
    Lj = JL[f'{race}/L{lap}']['adopted']
    Lo = OWNL[f"{'wet' if race == 'fuji_aim_01' else 'dry'}_L{lap}"]['rowFit']['yawTransient']['L']
    rec = dict(session=race, lap=lap, kerb=kerb, ownVideoTipPts=round(vtip, 3), t3VideoTipPts=t3tip,
               ownMinusT3=round(vtip - t3tip, 3), tTruthJoinL=round(t_ov + Lj, 3), Ljoin=Lj, LownVideoYaw=Lo, cams={})
    for f, h in CAMS:
        ts = np.arange(t_ov + Lj - 1.5, t_ov + Lj + 1.5, 1 / 30)
        us = np.array([np.nan if (x := u_at_row(race, lap, kerb, t, f, h)) is None else x for t in ts])
        s = -us if P.zones()[kerb]['side'] == 'right' else us
        if not np.isfinite(s).any():
            rec['cams'][f'f{f:.0f}_h{h}'] = None
            continue
        k = int(np.nanargmax(np.where(np.isfinite(s), s, -np.inf)))
        tc = float(ts[k])
        if 2 <= k < len(ts) - 2 and np.isfinite(s[k - 2:k + 3]).all():
            cc = np.polyfit(ts[k - 2:k + 3] - ts[k], s[k - 2:k + 3], 2)
            if cc[0] < 0:
                tc = float(ts[k] - cc[1] / (2 * cc[0]))
        v = float(np.interp(tc, P.lap(race, lap)['t'][1:], np.hypot(np.diff(P.lap(race, lap)['x']), np.diff(P.lap(race, lap)['z'])) / np.diff(P.lap(race, lap)['t'])))
        dtj = tc - (t_ov + Lj)
        dto = tc - (t_ov + Lo)
        rec['cams'][f'f{f:.0f}_h{h}'] = dict(cgTipT=round(tc, 3), atEdge=bool(k in (0, len(ts) - 1)), dt_joinL=round(dtj, 3),
                                             dS_joinL=round(-dtj * v, 2), dt_ownL=round(dto, 3), dS_ownL=round(-dto * v, 2), speed=round(v, 2))
    jrow = [x for x in json.loads((KT / 'join/event_table.json').read_text(encoding='utf-8'))['clippingPointsVideo']
            if x['session'] == race and x['lap'] == lap and x['kerb'] == kerb]
    if jrow:
        rec['join'] = dict(dtNominal=jrow[0]['dt'], dtSpecFov=(jrow[0]['cameraVariants'].get('fh889') or {}).get('dt'),
                           dtF472=(jrow[0]['cameraVariants'].get('fh590_profileLike') or {}).get('dt'))
    out.append(rec)
    print(json.dumps(rec))
(HERE / 'v_tip_predict.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
