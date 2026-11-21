"""T3: the same video-vs-replay tip / red-end comparison with the UNMODIFIED 2026-10-03 camera profile
(artifacts/video-cg-alignment-2026-10-03/camera-profile.json: f 472.3 px, h 1.25 m, pitch 18.21 deg, yaw -0.69 deg,
roll 1.84 deg), for the task's "absolute estimate with the camera profile". Uses the video tracks of video_tracks.py
and the estimators of analyze.py. Output: profile_camera.json next to this file (numbers only).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze as A  # noqa: E402
import common as C  # noqa: E402
import predict as P  # noqa: E402
import video_tracks as VT  # noqa: E402
from camera import profile_cam  # noqa: E402

HERE = Path(__file__).resolve().parent


def main():
    cam = profile_cam()
    course = C.Course()
    win = json.loads((HERE / 'passes_windows.json').read_text(encoding='utf-8'))
    passages = json.loads((HERE / 'passages.json').read_text(encoding='utf-8'))
    valid_ids = {(p['race'], p['lap'], p['kerb']) for p in passages['passes']
                 if p['tipRowConsistent'] and p['rows']['276']['nominal'].get('tip', {}).get('valid')
                 and p['kerb'] not in passages['summary'][p['race']]['suspectKerbs']['kerbs']}
    roads, laps, rows = {}, {}, []
    for r in win['rows']:
        if 'window' not in r:
            continue
        race, lap, kid, side = r['race'], r['lap'], r['kerb'], r['side']
        tp = C.WORK / 'tracks' / f'{race}_L{lap}_{kid}.json'
        if not tp.exists():
            continue
        tr = json.loads(tp.read_text(encoding='utf-8'))
        roads.setdefault(race, VT.road_mask_v2(race))
        if (race, lap) not in laps:
            laps[(race, lap)] = C.Lap(race, lap, course)
        L = laps[(race, lap)]
        pts = np.array(tr['pts'])
        pr = P.predict(race, lap, kid, cam, pts, [276], roads[race], L=L, lap_start_pts=r['lapStartPts'], use_terrain=True)
        ry = tr['rows']['276']
        cols = np.where(roads[race][276])[0]
        vt = A.tip_of(pts, ry['edge'], side, int(cols.min()), int(cols.max()))
        rt = A.tip_of(pts, pr['rows'][276]['u'], side, int(cols.min()), int(cols.max()))
        sign = 1.0 if side == 'right' else -1.0
        e = dict(race=race, lap=lap, kerb=kid, inNominalValidSet=(race, lap, kid) in valid_ids)
        if vt and rt:
            v = L.at(rt['t'] - r['lapStartPts'] - L.delta)['v']
            Xv, Dv = cam.ground(vt['u'], 276); Xr, Dr = cam.ground(rt['u'], 276)
            e.update(tipDt=round(vt['t'] - rt['t'], 3), tipAlongM=round((vt['t'] - rt['t']) * v, 2),
                     videoDistance=round(float(sign * Xv), 2), replayDistance=round(float(sign * Xr), 2),
                     dDistance=round(float(sign * (Xv - Xr)), 2), videoD=round(float(Dv), 2))
        von, voff = A.red_events(pts, ry['edge'], ry['red'])
        for name, vv, rr in (('redOn', von, pr['events']['redStart'][276]), ('redOff', voff, pr['events']['redEnd'][276])):
            if vv and rr and rr['inside']:
                e[name + 'Dt'] = round(vv['t'] - rr['pts'], 3)
        rows.append(e)
    summ = {}
    for race in C.RACES:
        R = [x for x in rows if x['race'] == race and x['inNominalValidSet'] and 'tipDt' in x and abs(x['tipDt']) < 0.8]
        if not R:
            continue
        a = lambda k: [x[k] for x in R]
        summ[race] = dict(n=len(R), tipDtMedian=round(float(np.median(a('tipDt'))), 3),
                          tipDtIQR=[round(float(np.percentile(a('tipDt'), q)), 3) for q in (25, 75)],
                          tipAlongMMedian=round(float(np.median(a('tipAlongM'))), 2),
                          dDistanceMedian=round(float(np.median(a('dDistance'))), 2),
                          dDistanceIQR=[round(float(np.percentile(a('dDistance'), q)), 2) for q in (25, 75)],
                          videoDistanceMedian=round(float(np.median(a('videoDistance'))), 2),
                          D276Median=round(float(np.median(a('videoD'))), 2),
                          redOnDtMedian=round(float(np.median([x['redOnDt'] for x in rows if x['race'] == race and 'redOnDt' in x and abs(x['redOnDt']) < 0.8])), 3),
                          redOffDtMedian=round(float(np.median([x['redOffDt'] for x in rows if x['race'] == race and 'redOffDt' in x and abs(x['redOffDt']) < 0.8])), 3))
    doc = dict(kind='t3-profile-camera-comparison', definition=__doc__,
               camera=dict(f=cam.f, h=cam.h, pitchDeg=round(math.degrees(cam.pitch), 3), yawDeg=round(math.degrees(cam.yaw), 3),
                           rollDeg=round(math.degrees(cam.roll), 3), horizonAtCentre=[round(cam.u_vp, 1), round(cam.v_vp, 1)],
                           D276FlatAtCentre=round(float(cam.ground(640.0, 276)[1]), 2)),
               note='same passes as the nominal valid tip set (row-consistent, non-suspect kerbs); the profile horizon is 8 px lower '
                    'than the measured vanishing point and its f is not supported by the rotation / row-timing checks',
               summary=summ, passes=rows)
    (HERE / 'profile_camera.json').write_text(json.dumps(doc, indent=1), encoding='utf-8')
    print(json.dumps(summ, indent=1), doc['camera'])


if __name__ == '__main__':
    main()
