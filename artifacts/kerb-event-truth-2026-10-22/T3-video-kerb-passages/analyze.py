"""T3 step 4: video events per pass/row and the replay's prediction of the same events, for a grid of cameras.

Video (camera-independent; from video_tracks.py):
  tip      : frame of the kerb edge's extreme position toward the image centre at row y (= minimum lateral distance of
             the kerb paint edge at the forward distance D(y) seen by that row); sub-frame time and pixel from a
             parabola through the +-3 frames around the extreme. Valid only if the extreme is >= 5 px inside the
             road mask and >= 2 valid frames exist on both sides.
  redOn    : leading end of the red blocks crossing row y in open view: first red frame of the track, preceded by
             >= 3 tracked non-red frames (time = midpoint of the last non-red and the first red frame, +-1/60 s)
  redOff   : trailing end: last red frame, followed by >= 3 tracked non-red frames
Replay: predict.py (CG road surface heights; TERRAIN) with the same frame PTS (same tip estimator on the same sampling) and dense red start/end crossings.
Cameras: camera.Cam with the measured vanishing point (vp_calib.json, 7/30 main straight; also used for 7/29: the
dashboard edge is at the same pixels on both days), roll from camera-profile.json, f x h grid.
Output: $T3_WORK/analysis_raw.json (per pass, row, camera).
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402
import predict as P  # noqa: E402
import video_tracks as VT  # noqa: E402
from camera import Cam  # noqa: E402

HERE = Path(__file__).resolve().parent
TERRAIN = True   # replay prediction on the CG road surface (track.json altLeft/altRight), car pitch/roll from the road plane
F_GRID = [472.3, 711.0, 967.0]
H_GRID = [1.0, 1.25, 1.5]
NOMINAL = (967.0, 1.26)     # self-calibrated: h 1.26 (vp_height.py), f*h ~1250 (row-timing scan), f ~930+-170 (yaw_focal.py); = 67 deg spec FOV
EXTRA_FH = [NOMINAL] + [(f, 1.26) for f in (560.0, 640.0, 720.0, 900.0, 1000.0, 1100.0)]


def vp():
    d = json.loads((HERE / 'vp_calib.json').read_text(encoding='utf-8'))['summary']['fuji_aim_2020_07_30']
    return d['v_vp_median'], d['u_vp_median']


def cams():
    v_vp, u_vp = vp()
    roll = math.radians(C.cam_profile()['roll'] * 180 / math.pi)
    out = [Cam(f=f, h=h, v_vp=v_vp, u_vp=u_vp, roll=roll) for f in F_GRID for h in H_GRID]
    out += [Cam(f=f, h=h, v_vp=v_vp, u_vp=u_vp, roll=roll) for f, h in EXTRA_FH]
    return out


def tip_of(times, u, side, lo_b, hi_b):
    """sub-frame extreme of u(t) toward the centre; returns (t, u, k) or None."""
    u = np.array([np.nan if x is None else x for x in u], float)
    ok = np.isfinite(u)
    if ok.sum() < 5:
        return None
    s = u if side == 'left' else -u
    k = int(np.nanargmax(np.where(ok, s, -np.inf)))
    if k < 2 or k > len(u) - 3 or not (ok[k - 2] and ok[k + 2]):
        return None
    if side == 'left' and u[k] > hi_b - 5:
        return None
    if side == 'right' and u[k] < lo_b + 5:
        return None
    sel = [j for j in range(k - 3, k + 4) if 0 <= j < len(u) and ok[j]]
    tt = np.array([times[j] for j in sel]) - times[k]
    c = np.polyfit(tt, u[sel], 2)
    if c[0] == 0 or (side == 'left' and c[0] > 0) or (side == 'right' and c[0] < 0):
        return dict(t=float(times[k]), u=float(u[k]), k=k, parabola=False)
    tv = -c[1] / (2 * c[0])
    if abs(tv) > 1.5 / C.FPS:
        return dict(t=float(times[k]), u=float(u[k]), k=k, parabola=False)
    return dict(t=float(times[k] + tv), u=float(np.polyval(c, tv)), k=k, parabola=True)


def red_events(times, edge, red):
    idx = [k for k, e in enumerate(edge) if e is not None]
    if not idx:
        return None, None
    on = off = None
    for j, k in enumerate(idx):
        if red[k]:
            prev = idx[max(0, j - 3):j]
            if len(prev) == 3 and not any(red[p] for p in prev) and idx[j - 1] == k - 1:
                on = dict(t=float((times[k - 1] + times[k]) / 2), k=k, u=edge[k])
            break
    for j in range(len(idx) - 1, -1, -1):
        k = idx[j]
        if red[k]:
            nxt = idx[j + 1:j + 4]
            if len(nxt) == 3 and not any(red[p] for p in nxt) and idx[j + 1] == k + 1:
                off = dict(t=float((times[k] + times[k + 1]) / 2), k=k, u=edge[k])
            break
    return on, off


def register(times, u_v, red, u_r, side, max_shift=0.8, step=0.002):
    """time shift dt (video minus replay, s) and pixel offset du (video minus replay) aligning the video edge series
    with the replay series at one row, over the frames from the first red - 6 to the last red + 6 (robust L1)."""
    times = np.asarray(times, float)
    uv = np.array([np.nan if x is None else x for x in u_v], float)
    ur = np.array([np.nan if x is None else x for x in u_r], float)
    rk = [k for k, r in enumerate(red) if r]
    if len(rk) < 3:
        return None
    sel = np.zeros(len(times), bool)
    sel[max(rk[0] - 6, 0):rk[-1] + 7] = True
    sel &= np.isfinite(uv)
    if sel.sum() < 8:
        return None
    okr = np.isfinite(ur)
    if okr.sum() < 8:
        return None
    best = None
    prof = []
    for dt in np.arange(-max_shift, max_shift + 1e-9, step):
        q = times[sel] - dt               # replay time matching video frame time
        inside = (q >= times[okr][0]) & (q <= times[okr][-1])
        if inside.sum() < 8:
            prof.append(np.inf)
            continue
        # interpolate replay only across contiguous valid samples
        urq = np.interp(q[inside], times[okr], ur[okr])
        gapok = np.interp(q[inside], times, okr.astype(float)) > 0.99
        r = uv[sel][inside][gapok] - urq[gapok]
        if len(r) < 8:
            prof.append(np.inf)
            continue
        du = float(np.median(r))
        cost = float(np.mean(np.abs(r - du)))
        prof.append(cost)
        if best is None or cost < best[0]:
            best = (cost, float(dt), du, int(len(r)))
    if best is None:
        return None
    prof = np.array(prof)
    within = np.where(prof <= best[0] + 1.0)[0]
    width = float((within.max() - within.min()) * step) if len(within) else None
    uvs = uv[sel]
    return dict(dt=best[1], du=best[2], cost=best[0], n=best[3], dtWidth1px=width,
                uRange=float(np.nanmax(uvs) - np.nanmin(uvs)))


def main():
    course = C.Course()
    win = json.loads((HERE / 'passes_windows.json').read_text(encoding='utf-8'))
    CAMS = cams()
    rf = sys.argv[1] if len(sys.argv) > 1 else ''
    out = []
    roads, laps = {}, {}
    t0 = time.time()
    for r in win['rows']:
        if 'window' not in r or rf not in r['race']:
            continue
        race, lap, kid, side = r['race'], r['lap'], r['kerb'], r['side']
        tp = C.WORK / 'tracks' / f'{race}_L{lap}_{kid}.json'
        if not tp.exists():
            continue
        tr = json.loads(tp.read_text(encoding='utf-8'))
        if race not in roads:
            roads[race] = VT.road_mask_v2(race)
        if (race, lap) not in laps:
            laps[(race, lap)] = C.Lap(race, lap, course)
        L = laps[(race, lap)]
        p0 = r['lapStartPts']
        pts = np.array(tr['pts'])
        rec = dict(race=race, lap=lap, kerb=kid, side=side, lapStartPts=p0, delta=L.delta, rows={})
        preds = {c.label(): P.predict(race, lap, kid, c, pts, VT.ROWS, roads[race], L=L, lap_start_pts=p0, use_terrain=TERRAIN) for c in CAMS}
        for y in VT.ROWS:
            ry = tr['rows'][str(y)]
            cols = np.where(roads[race][y])[0]
            lo_b, hi_b = int(cols.min()), int(cols.max())
            vt = tip_of(pts, ry['edge'], side, lo_b, hi_b)
            von, voff = red_events(pts, ry['edge'], ry['red'])
            nvalid = int(sum(e is not None for e in ry['edge']))
            nred = int(sum(ry['red']))
            row = dict(video=dict(tip=vt, redOn=von, redOff=voff, nValid=nvalid, nRed=nred,
                                  firstValidPts=float(pts[next(k for k, e in enumerate(ry['edge']) if e is not None)]) if nvalid else None,
                                  lastValidPts=float(pts[max(k for k, e in enumerate(ry['edge']) if e is not None)]) if nvalid else None),
                       cams={})
            for c in CAMS:
                pr = preds[c.label()]
                rt = tip_of(pts, pr['rows'][y]['u'], side, lo_b, hi_b)
                ent = dict(replayTip=rt, replayRedOn=pr['events']['redStart'][y], replayRedOff=pr['events']['redEnd'][y],
                           reg=register(pts, ry['edge'], ry['red'], pr['rows'][y]['u'], side))
                if ent['reg']:
                    ent['reg']['dX'] = float(ent['reg']['du'] * c.h / ((y - c.v_vp) * math.cos(c.pitch)))
                if vt:
                    X, D = c.ground(vt['u'], y)
                    ent['videoTipX'], ent['videoTipD'] = float(X), float(D)
                if rt:
                    X, D = c.ground(rt['u'], y)
                    ent['replayTipX'], ent['replayTipD'] = float(X), float(D)
                row['cams'][c.label()] = ent
            rec['rows'][y] = row
        rec['redExtent'] = preds[CAMS[0].label()]['redExtent']
        out.append(rec)
        print(race[-5:], lap, kid, f'{time.time() - t0:.0f}s',
              {y: (round(rec['rows'][y]['video']['tip']['t'] - rec['rows'][y]['cams']['f472_h1.25']['replayTip']['t'], 3)
                   if rec['rows'][y]['video']['tip'] and rec['rows'][y]['cams']['f472_h1.25']['replayTip'] else None) for y in VT.ROWS},
              flush=True)
    meta = dict(cams=[dict(label=c.label(), f=c.f, h=c.h, v_vp=c.v_vp, u_vp=c.u_vp, rollDeg=math.degrees(c.roll),
                           pitchDeg=math.degrees(c.pitch), yawDeg=math.degrees(c.yaw),
                           D_rows={y: float(c.ground(640.0, y)[1]) for y in VT.ROWS}) for c in CAMS])
    name = 'analysis_raw.json' if not rf else f'analysis_raw_{rf}.json'
    (C.WORK / name).write_text(json.dumps(dict(meta=meta, passes=out)), encoding='utf-8')


if __name__ == '__main__':
    main()
