"""T3 camera self-calibration C: focal length from image rotation vs GPS course rate (independent of h).

In a steady corner the far scenery above the horizon (window x 900-1072, y 128-190, right of the mirror: sky line, trees, stands) shifts
horizontally between frames by about f * dpsi / cos(pitch) (horizon points; rotation about the car vertical axis).
Per frame pair the horizontal shift is measured by phase correlation (OpenCV) of gradient images; the heading change
dpsi comes from the registered GPS course (replay spline) over the same frame interval. f = median(shift * cos p / dpsi)
over frames with |course rate| > 0.25 rad/s. Forward translation adds outward flow (parallax) in this window, which is
right of the focus of expansion: it biases f LOW in right-hand corners and HIGH in left-hand corners, so both are used.
Frames are decoded in memory only. Output: yaw_focal.json next to this file.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

HERE = Path(__file__).resolve().parent
WIN = (slice(128, 190), slice(900, 1072))
# (race, lap, station range, label)
SEGMENTS = []
for lap in (1, 2, 3, 4, 5, 6):
    SEGMENTS.append(('fuji_aim_2020_07_30', lap, (860.0, 940.0), '1st corner (right)'))
    SEGMENTS.append(('fuji_aim_2020_07_30', lap, (3255.0, 3345.0), '3250m (right)'))
    SEGMENTS.append(('fuji_aim_2020_07_30', lap, (3820.0, 3895.0), 'Final corner (right)'))
    SEGMENTS.append(('fuji_aim_2020_07_30', lap, (1400.0, 1490.0), 'Coca-Cola (left)'))
    SEGMENTS.append(('fuji_aim_2020_07_30', lap, (2105.0, 2185.0), 'Hairpin (left)'))


def grad(img):
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY).astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    return np.hypot(gx, gy)


def main():
    course = C.Course()
    v_vp = json.loads((HERE / 'vp_calib.json').read_text(encoding='utf-8'))['summary']['fuji_aim_2020_07_30']['v_vp_median']
    han = cv2.createHanningWindow((WIN[1].stop - WIN[1].start, WIN[0].stop - WIN[0].start), cv2.CV_32F)
    rows = []
    for race, lap, (sa, sb), label in SEGMENTS:
        L = C.Lap(race, lap, course)
        p0 = C.lap_start_pts(race, lap, L.meta)
        ta, tb = L.t_at_station(sa), L.t_at_station(sb)
        n = int((tb - ta) * C.FPS)
        fr, pts = C.decode_frames(race, p0 + ta + L.delta, n)
        G = [grad(f[WIN]) for f in fr]
        hd = np.unwrap(np.arctan2(L.fwd[:, 0], -L.fwd[:, 1]))   # course angle, clockwise from north (x east, z south)
        for k in range(len(fr) - 1):
            (dx, dy), resp = cv2.phaseCorrelate(G[k], G[k + 1], han)
            t0 = pts[k] - p0 - L.delta; t1 = pts[k + 1] - p0 - L.delta
            dpsi = float(np.interp(t1, L.t, hd) - np.interp(t0, L.t, hd))
            rows.append(dict(seg=label, lap=lap, pts=float(pts[k]), dx=float(dx), dy=float(dy), resp=float(resp), dpsi=dpsi,
                             rate=dpsi * C.FPS))
        print(label, lap, len(fr), flush=True)
    out = {}
    for label in sorted({r['seg'] for r in rows}):
        R = [r for r in rows if r['seg'] == label and abs(r['rate']) > 0.25 and r["resp"] > 0.05]
        if not R:
            continue
        # image shift is negative (leftward) for a right turn (dpsi > 0 clockwise): f_eff = -dx / dpsi
        fe = np.array([-r['dx'] / r['dpsi'] for r in R])
        # cos(pitch) correction needs f; iterate once from f_eff
        f0 = float(np.median(fe))
        cp = math.cos(math.atan((360.0 - v_vp) / f0))
        out[label] = dict(frames=len(R), fEffMedian=f0, fEffIQR=[float(np.percentile(fe, 25)), float(np.percentile(fe, 75))],
                          fCorrected=f0 * cp, cosPitch=cp,
                          perLap={lap: float(np.median([-r['dx'] / r['dpsi'] for r in R if r['lap'] == lap]))
                                  for lap in sorted({r['lap'] for r in R})})
    L_ = [v['fCorrected'] for k, v in out.items() if 'left' in k]
    R_ = [v['fCorrected'] for k, v in out.items() if 'right' in k]
    if L_ and R_:
        out['combined'] = dict(leftMean=float(np.mean(L_)), rightMean=float(np.mean(R_)),
                               fEstimate=float((np.mean(L_) + np.mean(R_)) / 2),
                               note='parallax biases left-hand corners high and right-hand corners low in this window; '
                                    'the mean of the two is the estimate, half their difference a rough uncertainty',
                               halfDifference=float(abs(np.mean(L_) - np.mean(R_)) / 2))
    doc = dict(kind='t3-focal-from-rotation', definition=__doc__, window=dict(rows=[128, 190], cols=[900, 1072]), summary=out)
    (HERE / 'yaw_focal.json').write_text(json.dumps(doc, indent=1), encoding='utf-8')
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
