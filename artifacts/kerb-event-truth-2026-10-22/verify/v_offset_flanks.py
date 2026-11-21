"""Verifier step 3b: per-corner flank timing of video yaw vs IMU gyro (and vs GPS course rate).

For every corner (|gyro yaw rate| peak > 12 deg/s, peaks >= 2 s apart) the rising and falling flank
crossings at 50 % of the corner's own peak are measured on the gyro (lap t, 7/30 shift applied) and
on the video yaw proxy (v_video_flow2.py row-fit intercept, overlay-mapped t_ov), both smoothed with the
same 0.3 s centred boxcar. The video peak is searched in [gyro peak - 1.0 s, gyro peak + 0.6 s] (no prior
offset). L_corner = t50_gyro - t50_video (> 0: logger later than the overlay-placed video).
Writes v_offset_flanks.json (numbers only).
usage: python v_offset_flanks.py wet_L3 dry_L2 ...
"""
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from v_offset import (GRID, RACE, WORK, ORIGIN, boxcar, lap_doc, lap_start_pts, to_local)  # noqa: E402


def crossings(t, y, i_peak, frac, direction):
    """50 % crossing on the flank before (direction=-1) or after (+1) the peak, base = 0."""
    thr = frac * y[i_peak]
    k = i_peak
    while 0 < k < len(y) - 1 and y[k] >= thr:
        k += direction
    if not (0 < k < len(y) - 1):
        return None
    a, b = (k, k - direction) if direction < 0 else (k - 1, k)
    ya, yb = y[a], y[b]
    if ya == yb:
        return float(t[a])
    u = (thr - ya) / (yb - ya)
    return float(t[a] + u * (t[b] - t[a]))


res = {}
for tag in sys.argv[1:]:
    sess, lapname = tag.split('_')
    race, lap = RACE[sess], int(lapname[1:])
    d = lap_doc(race, lap)
    meta = d['meta']
    delta = float(meta['first_sample_after_lap_start_seconds'])
    shift = float((meta.get('channel_time_alignment') or {}).get('shiftSeconds', 0.0))
    t_lap = np.array(d['t'], float)
    z = np.load(WORK / f'xrk_{race}.npz', allow_pickle=False)
    tc0 = json.loads(str(z['maps']))[str(lap)]['tc0Ms']
    sp = lap_start_pts(race, lap, meta)
    g = np.arange(1.0, t_lap[-1] - 1.0, GRID)
    gy = boxcar(np.interp(g, (z['YawRate_t'] - tc0) / 1000.0 + shift, z['YawRate']), 0.3)
    xy = to_local(d['lat'], d['lng'], ORIGIN)
    hdg = np.unwrap(np.arctan2(np.gradient(xy[:, 0], t_lap), np.gradient(xy[:, 1], t_lap)))
    co = boxcar(np.interp(g, t_lap, np.degrees(np.gradient(hdg, t_lap))), 0.3)
    f2 = np.load(WORK / f'flow2_{tag}.npz')
    vr = np.nanmedian(f2['a'].astype(float)[:, 10:50], axis=1) / np.diff(f2['pts'])
    vv = boxcar(np.interp(g, f2['mid'] - sp - delta, -vr), 0.3)     # sign: + = same sense as the gyro
    # corners from the gyro
    ag = np.abs(gy)
    peaks = []
    order = np.argsort(-ag)
    for i in order:
        if ag[i] < 12.0:
            break
        if all(abs(g[i] - g[j]) > 2.0 for j in peaks):
            peaks.append(i)
    peaks.sort()
    rows = []
    for i in peaks:
        s = np.sign(gy[i])
        # local maximum of s*gy around i (already the global max of its 2 s neighbourhood)
        w = (g >= g[i] - 1.0) & (g <= g[i] + 0.6)
        jj = np.flatnonzero(w)
        jv = jj[np.argmax(s * vv[jj])]
        if s * vv[jv] <= 0:
            continue
        rec = dict(tPeakGyro=round(float(g[i]), 3), peakDegS=round(float(gy[i]), 1))
        for name, sig in (('video', s * vv), ('gyro', s * gy), ('course', s * co)):
            ip = jv if name == 'video' else i
            if name == 'course':
                wc = np.flatnonzero((g >= g[i] - 0.6) & (g <= g[i] + 0.6))
                ip = wc[np.argmax(sig[wc])]
            rec[name] = dict(rise50=crossings(g, sig, ip, 0.5, -1), fall50=crossings(g, sig, ip, 0.5, +1),
                             rise25=crossings(g, sig, ip, 0.25, -1))
        for fl in ('rise50', 'fall50', 'rise25'):
            a, b, c = rec['gyro'][fl], rec['video'][fl], rec['course'][fl]
            rec[f'L_{fl}'] = None if a is None or b is None else round(a - b, 4)
            rec[f'Lcourse_{fl}'] = None if c is None or b is None else round(c - b, 4)
        rows.append(rec)

    def st(key):
        v = np.array([r[key] for r in rows if r.get(key) is not None and abs(r[key]) < 1.0])
        if not len(v):
            return None
        return dict(n=int(len(v)), median=round(float(np.median(v)), 4), q25=round(float(np.percentile(v, 25)), 4),
                    q75=round(float(np.percentile(v, 75)), 4), mean=round(float(v.mean()), 4),
                    seMedian=round(float(1.253 * v.std(ddof=1) / np.sqrt(len(v))), 4) if len(v) > 1 else None)
    res[tag] = dict(race=race, lap=lap, shift=shift, lapStartPtsOverlay=round(sp, 4), nCorners=len(rows),
                    L_rise50=st('L_rise50'), L_fall50=st('L_fall50'), L_rise25=st('L_rise25'),
                    Lcourse_rise50=st('Lcourse_rise50'), Lcourse_fall50=st('Lcourse_fall50'), corners=rows)
    print(tag, 'corners', len(rows), 'L_rise50', res[tag]['L_rise50'], 'L_fall50', res[tag]['L_fall50'],
          'L_rise25', res[tag]['L_rise25'], 'course rise50', res[tag]['Lcourse_rise50'])
p = HERE / 'v_offset_flanks.json'
prev = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
prev.update(res)
p.write_text(json.dumps(prev, indent=1), encoding='utf-8')
