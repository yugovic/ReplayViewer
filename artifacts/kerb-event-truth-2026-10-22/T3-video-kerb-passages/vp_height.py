"""T3 camera self-calibration B: camera height with the road cross-slope, and the camera's lateral position on the
main straight from the video (vp_calib.json frames) vs the replay.

Road cross-slope g (m/m, + = right side higher) from track.json centerline altLeft/altRight over its nominal width.
With slopes b of the two white road-edge lines about the vanishing point (u - u_vp = b (v - v_vp)), a ground line at
lateral X (right +) on a road plane tilted by g satisfies b = X cos(p) / (h - g X), so
    X_edge = b h / (cos p + b g),   W = X_R - X_L   ->   h = W / (b_R/(cos p + b_R g) - b_L/(cos p + b_L g)).
W = road width (geometry.json road edges) at the station. cos p from the vanishing-point row and the focal length
(f = 711 nominal; f = 472 / 967 change h by < 2 %).
The video lateral distance camera -> left edge (= -X_L) is then compared with the replay camera position (registered GPS
reference point, camera 0.55 m behind, 0 m lateral) relative to the CG left road edge.
Output: vp_height.json next to this file.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

HERE = Path(__file__).resolve().parent


def main():
    vp = json.loads((HERE / 'vp_calib.json').read_text(encoding='utf-8'))
    tr = json.loads((C.ROOT / 'public/data/tracks/fuji/track.json').read_text(encoding='utf-8'))
    cl = tr['centerline']
    cd = np.array([p['dist'] for p in cl])
    gsl = np.array([(p['altRight'] - p['altLeft']) / tr['width'] for p in cl])
    g = json.loads((C.ROOT / 'public/data/tracks/fuji/cg_study/geometry.json').read_text(encoding='utf-8'))
    road = np.array([r[:11] for r in g['road']], float)
    nrm = road[:, 3:5] / np.linalg.norm(road[:, 3:5], axis=1)[:, None]
    course = C.Course()
    laps = {}
    out = {}
    for f in (472.3, 711.0, 967.0):
        rows = []
        for r in vp['frames']:
            if not (r['left']['rms'] < 1.2 and r['right']['rms'] < 1.2 and r['left']['n'] > 25 and r['right']['n'] > 15):
                continue
            # pitch from the per-race median VP row (per-frame VP is noisy)
            vvp = vp['summary']['fuji_aim_2020_07_30']['v_vp_median']
            c = math.cos(math.atan((360.0 - vvp) / f))
            s = r['station']
            gg = float(np.interp(s, cd, gsl))
            bL, bR, W = r['left']['b'], r['right']['b'], r['roadWidth']
            h_flat = W * c / (bR - bL)
            h = W / (bR / (c + bR * gg) - bL / (c + bL * gg))
            XL = bL * h / (c + bL * gg)
            key = (r['race'], r['lap'])
            if key not in laps:
                laps[key] = C.Lap(r['race'], r['lap'], course)
            L = laps[key]
            st = L.at(r['t'])
            camxy = st['xy'] + st['fwd'] * -0.55
            i = int(round(s / 0.5))
            d_left_replay = float((camxy - road[i, 7:9]) @ nrm[i])
            rows.append(dict(race=r['race'], lap=r['lap'], t=r['t'], station=s, crossSlope=round(gg, 4),
                             hFlat=round(h_flat, 3), h=round(h, 3), videoDistLeftEdge=round(-XL, 3),
                             replayDistLeftEdge=round(d_left_replay, 3)))
        summ = {}
        for race in C.RACES:
            R = [x for x in rows if x['race'] == race]
            if not R:
                continue
            hh = np.array([x['h'] for x in R]); hf = np.array([x['hFlat'] for x in R])
            summ[race] = dict(frames=len(R), hMedian=float(np.median(hh)), hIQR=[float(np.percentile(hh, 25)), float(np.percentile(hh, 75))],
                              hFlatMedian=float(np.median(hf)), crossSlopeMedian=float(np.median([x['crossSlope'] for x in R])))
            per_lap = {}
            for lap in sorted({x['lap'] for x in R}):
                Q = [x for x in R if x['lap'] == lap]
                dv = np.array([x['videoDistLeftEdge'] for x in Q]); dr = np.array([x['replayDistLeftEdge'] for x in Q])
                per_lap[lap] = dict(frames=len(Q), videoMinusReplayMedian=float(np.median(dv - dr)),
                                    videoMedian=float(np.median(dv)), replayMedian=float(np.median(dr)))
            summ[race]['lateralOnStraightPerLap'] = per_lap
        out[f'f{f:.0f}'] = dict(summary=summ, frames=rows if f == 711.0 else None)
    doc = dict(kind='t3-camera-height-and-straight-lateral', definition=__doc__, results=out)
    (HERE / 'vp_height.json').write_text(json.dumps(doc, indent=1), encoding='utf-8')
    for k, v in out.items():
        print(k, json.dumps(v['summary'], indent=1))


if __name__ == '__main__':
    main()
