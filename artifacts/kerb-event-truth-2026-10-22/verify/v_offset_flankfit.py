"""Verifier step 3c: least-squares lag + gain fit of the video yaw proxy to the gyro on yaw FLANKS only,
with a block bootstrap over flanks for the uncertainty.

Signals (as v_offset.py): video = -(row-fit intercept)/dt (v_video_flow2.py), overlay-mapped t_ov; gyro
YawRate at lap t (7/30 shift applied); both 0.2 s centred boxcar on a 10 ms grid. Flank samples: where
|d gyro / dt| (0.3 s boxcar) > 10 deg/s^2, grouped into contiguous flanks (gaps < 0.3 s merged; flanks
< 0.2 s dropped). For each lag l in [-0.2, 0.7] s (2 ms) the video is moved to t_ov + l and the gyro is
fitted as a * video + b over the flank samples; L = argmin residual (parabolic refinement).
Bootstrap: resample flanks with replacement 200 times -> 2.5/50/97.5 % of L.
Writes v_offset_flankfit.json (numbers only).
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from v_offset import GRID, RACE, WORK, boxcar, lap_doc, lap_start_pts  # noqa: E402

LAGS = np.arange(-0.2, 0.7001, 0.002)
rng = np.random.default_rng(20261022)


def fit_lag(tg, gy, tv, vv, idx_groups):
    idx = np.concatenate(idx_groups)
    y = gy[idx]
    sse = np.empty(len(LAGS))
    for i, l in enumerate(LAGS):
        x = np.interp(tg[idx], tv + l, vv)
        A = np.stack([x, np.ones_like(x)], 1)
        coef, res, *_ = np.linalg.lstsq(A, y, rcond=None)
        sse[i] = float(np.sum((A @ coef - y) ** 2))
    j = int(np.argmin(sse))
    off = 0.0
    if 0 < j < len(sse) - 1:
        y0, y1, y2 = sse[j - 1], sse[j], sse[j + 1]
        den = y0 - 2 * y1 + y2
        off = 0.5 * (y0 - y2) / den if den > 0 else 0.0
    return float(LAGS[j] + off * 0.002), float(1 - sse[j] / np.sum((y - y.mean()) ** 2))


out = {}
for tag in sys.argv[1:]:
    sess, lapname = tag.split('_')
    race, lap = RACE[sess], int(lapname[1:])
    d = lap_doc(race, lap)
    meta = d['meta']
    delta = float(meta['first_sample_after_lap_start_seconds'])
    shift = float((meta.get('channel_time_alignment') or {}).get('shiftSeconds', 0.0))
    z = np.load(WORK / f'xrk_{race}.npz', allow_pickle=False)
    tc0 = json.loads(str(z['maps']))[str(lap)]['tc0Ms']
    sp = lap_start_pts(race, lap, meta)
    tg = np.arange(2.0, float(d['t'][-1]) - 2.0, GRID)
    gy = boxcar(np.interp(tg, (z['YawRate_t'] - tc0) / 1000.0 + shift, z['YawRate']), 0.2)
    dg = np.gradient(boxcar(gy, 0.3), GRID)
    f2 = np.load(WORK / f'flow2_{tag}.npz')
    tv_raw = f2['mid'] - sp - delta
    v = -np.nanmedian(f2['a'].astype(float)[:, 10:50], axis=1) / np.diff(f2['pts'])
    tv = np.arange(tv_raw[0], tv_raw[-1], GRID)
    vv = boxcar(np.interp(tv, tv_raw, v), 0.2)
    on = np.abs(dg) > 10.0
    groups = []
    i = 0
    n = len(on)
    while i < n:
        if on[i]:
            j = i
            while j + 1 < n and (on[j + 1] or (j + 30 < n and on[j + 1:j + 30].any())):
                j += 1
            if (j - i) * GRID >= 0.2:
                groups.append(np.arange(i, j + 1))
            i = j + 1
        else:
            i += 1
    L, r2 = fit_lag(tg, gy, tv, vv, groups)
    boots = []
    for _ in range(200):
        pick = rng.integers(0, len(groups), len(groups))
        boots.append(fit_lag(tg, gy, tv, vv, [groups[k] for k in pick])[0])
    q = np.percentile(boots, [2.5, 50, 97.5])
    out[tag] = dict(race=race, lap=lap, shift=shift, nFlanks=len(groups), flankSeconds=round(sum(len(g) for g in groups) * GRID, 1),
                    L=round(L, 4), r2=round(r2, 3), boot2p5=round(float(q[0]), 4), boot50=round(float(q[1]), 4),
                    boot97p5=round(float(q[2]), 4))
    print(tag, out[tag])
p = HERE / 'v_offset_flankfit.json'
prev = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
prev.update(out)
p.write_text(json.dumps(prev, indent=1), encoding='utf-8')
