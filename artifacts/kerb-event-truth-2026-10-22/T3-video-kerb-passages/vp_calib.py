"""T3 camera self-calibration A: vanishing point and camera height from the main straight.

On the main straight (CG stations 380-680 m, road width from cg_study/geometry.json) the two white road-edge lines are
fitted per frame (u = a + b v, inner edge of the first white run scanning outward from the image centre per row).
Their intersection is the vanishing point (VP: horizon row v_vp, column u_vp). For a flat road and a pinhole camera
with pitch p, a ground line at lateral X (parallel to the heading) satisfies (u - u_vp) = X cos(p) / h * (v - v_vp),
independent of the focal length, so h = W cos(p) / (b_right - b_left) with W the road width at that station.
Frames are decoded in memory only; diagnostic images go to $T3_WORK/vp (outside the repository).
Output: vp_calib.json next to this file (numbers only).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

S_RANGE = (380.0, 680.0)
ROWS = np.arange(212, 297)


def white_mask(fr):
    f = fr.astype(np.int16)
    mx, mn = f.max(-1), f.min(-1)
    return (mn > 165) & (mx - mn < 50)


def gray_mask(fr):
    f = fr.astype(np.int16)
    mx, mn = f.max(-1), f.min(-1)
    return (mx - mn < 30) & (mn > 60) & (mx < 170)


def edge_points(fr, road, centre_u):
    """inner edges of the first white run left/right of centre_u per row (needs >= 6 grey road px before it)."""
    wm = white_mask(fr) & road
    gm = gray_mask(fr)
    left, right = [], []
    for v in ROWS:
        rw, rg, rr = wm[v], gm[v], road[v]
        # left: scan from centre leftwards
        g = 0
        for u in range(int(centre_u), 205, -1):
            if not rr[u]:
                g = 0
                continue
            if rg[u]:
                g += 1
            elif rw[u] and rw[u - 1] and g >= 6:
                left.append((v, u))
                break
            elif not rw[u]:
                g = 0 if g < 6 else g
        g = 0
        for u in range(int(centre_u), 1088):
            if not rr[u]:
                g = 0
                continue
            if rg[u]:
                g += 1
            elif rw[u] and rw[u + 1] and g >= 6:
                right.append((v, u))
                break
            elif not rw[u]:
                g = 0 if g < 6 else g
    return np.array(left, float).reshape(-1, 2), np.array(right, float).reshape(-1, 2)


def robust_line(P, it=300, tol=1.5, rng=np.random.default_rng(1)):
    """RANSAC + refit of u = a + b v. P rows (v, u)."""
    if len(P) < 12:
        return None
    best = None
    for _ in range(it):
        i, j = rng.choice(len(P), 2, replace=False)
        if abs(P[i, 0] - P[j, 0]) < 8:
            continue
        b = (P[j, 1] - P[i, 1]) / (P[j, 0] - P[i, 0])
        a = P[i, 1] - b * P[i, 0]
        inl = np.abs(P[:, 1] - (a + b * P[:, 0])) < tol
        if best is None or inl.sum() > best.sum():
            best = inl
    if best is None or best.sum() < 12:
        return None
    A = np.stack([np.ones(best.sum()), P[best, 0]], 1)
    sol, *_ = np.linalg.lstsq(A, P[best, 1], rcond=None)
    res = P[best, 1] - A @ sol
    vr = P[best, 0]
    return dict(a=float(sol[0]), b=float(sol[1]), n=int(best.sum()), rms=float(np.sqrt(np.mean(res ** 2))),
                vmin=float(vr.min()), vmax=float(vr.max()))


def main():
    course = C.Course()
    g = json.loads((C.ROOT / 'public/data/tracks/fuji/cg_study/geometry.json').read_text(encoding='utf-8'))
    road_rows = np.array([r[:11] for r in g['road']], float)
    nrm = road_rows[:, 3:5] / np.linalg.norm(road_rows[:, 3:5], axis=1)[:, None]
    width = np.einsum('ij,ij->i', road_rows[:, 9:11] - road_rows[:, 7:9], nrm)
    out_rows = []
    diag = (C.WORK / 'vp') if C.WORK else None
    if diag:
        diag.mkdir(parents=True, exist_ok=True)
    for race in C.RACES:
        roadmask = np.load(C.WORK / f'roadmask_{race}.npy')
        idx = json.loads((C.ROOT / f'public/data/races/{race}/laps.json').read_text(encoding='utf-8'))
        for sel in idx['selected']:
            lap = int(sel['lap'])
            L = C.Lap(race, lap, course)
            p0 = C.lap_start_pts(race, lap, L.meta)
            ta, tb = L.t_at_station(S_RANGE[0]), L.t_at_station(S_RANGE[1])
            n = int((tb - ta) * C.FPS)
            fr, pts = C.decode_frames(race, p0 + ta + L.delta, n)
            for k in range(0, len(fr), 3):
                t = pts[k] - p0 - L.delta
                st = L.at(t)
                le, ri = edge_points(fr[k], roadmask, 660)
                fl, fr_ = robust_line(le), robust_line(ri)
                if not fl or not fr_ or fr_['b'] - fl['b'] < 0.5:
                    continue
                v_vp = (fr_['a'] - fl['a']) / (fl['b'] - fr_['b'])
                u_vp = fl['a'] + fl['b'] * v_vp
                i = int(round(st['s'] / 0.5))
                out_rows.append(dict(race=race, lap=lap, t=round(t, 3), pts=round(float(pts[k]), 4), station=round(st['s'], 1),
                                     speed=round(st['v'], 2), roadWidth=round(float(width[i]), 3),
                                     left=fl, right=fr_, v_vp=round(float(v_vp), 2), u_vp=round(float(u_vp), 2)))
                if diag and k % 30 == 0:
                    from PIL import Image, ImageDraw
                    im = Image.fromarray(fr[k][150:320]); dr = ImageDraw.Draw(im)
                    for (vv, uu) in le: dr.point((uu, vv - 150), fill=(0, 255, 0))
                    for (vv, uu) in ri: dr.point((uu, vv - 150), fill=(0, 255, 255))
                    for f_ in (fl, fr_):
                        dr.line([(f_['a'] + f_['b'] * v_vp, v_vp - 150), (f_['a'] + f_['b'] * 300, 150)], fill=(255, 0, 0))
                    im.save(diag / f'{race}_L{lap}_{k:04d}.jpg', quality=85)
            print(race, lap, len(out_rows), flush=True)
    # summaries
    summ = {}
    for race in C.RACES:
        R = [r for r in out_rows if r['race'] == race and r['left']['rms'] < 1.2 and r['right']['rms'] < 1.2
             and r['left']['n'] > 25 and r['right']['n'] > 15]
        if not R:
            continue
        v = np.array([r['v_vp'] for r in R]); u = np.array([r['u_vp'] for r in R])
        db = np.array([r['right']['b'] - r['left']['b'] for r in R]); W = np.array([r['roadWidth'] for r in R])
        sL = np.array([r['left']['b'] for r in R])
        summ[race] = dict(frames=len(R), v_vp_median=float(np.median(v)), v_vp_iqr=[float(np.percentile(v, 25)), float(np.percentile(v, 75))],
                          u_vp_median=float(np.median(u)), u_vp_iqr=[float(np.percentile(u, 25)), float(np.percentile(u, 75))],
                          W_over_db_median=float(np.median(W / db)), W_over_db_iqr=[float(np.percentile(W / db, 25)), float(np.percentile(W / db, 75))],
                          left_slope_median=float(np.median(sL)))
    doc = dict(kind='t3-vp-calibration', definition=__doc__, stationRange=S_RANGE, summary=summ, frames=out_rows)
    (Path(__file__).resolve().parent / 'vp_calib.json').write_text(json.dumps(doc, indent=1), encoding='utf-8')
    print(json.dumps(summ, indent=1))


if __name__ == '__main__':
    main()
