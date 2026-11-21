"""Verifier step 3d: sensitivity of the video-yaw offset L to the image rows used (parallax check).

Same signals and xcorr as v_offset.py (row-fit intercept from v_video_flow2.py vs IMU YawRate with the 7/30
shift), for three row bands of the 75-row windscreen crop (row r <-> full-res y = 150 + 2 r):
  upper  rows  4-24 (y 158-198, mostly distant scenery / horizon)
  lower  rows 25-49 (y 200-248, road surface 10-40 m ahead: more translation parallax)
  all    rows 10-49 (the band used in v_offset.py)
If L depends on the band, translation parallax biases the video yaw timing.
Writes v_offset_rows.json (numbers only).
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from v_offset import GRID, RACE, WORK, boxcar, lap_doc, lap_start_pts, xcorr_lag  # noqa: E402

BANDS = {'upper': (4, 25), 'lower': (25, 50), 'all': (10, 50)}
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
    g = np.arange(2.0, float(d['t'][-1]) - 2.0, GRID)
    gy = np.interp(g, (z['YawRate_t'] - tc0) / 1000.0 + shift, z['YawRate'])
    dy = np.gradient(boxcar(gy, 0.3), GRID)
    f2 = np.load(WORK / f'flow2_{tag}.npz')
    res = {}
    for name, (a, b) in BANDS.items():
        v = np.nanmedian(f2['a'].astype(float)[:, a:b], axis=1) / np.diff(f2['pts'])
        v = np.where(np.isfinite(v), v, 0.0)
        vv = np.interp(g, f2['mid'] - sp - delta, v)
        L1, r1, _ = xcorr_lag(boxcar(vv, 0.2), boxcar(gy, 0.2))
        L2, r2, _ = xcorr_lag(np.gradient(boxcar(vv, 0.3), GRID), dy)
        res[name] = dict(levelL=round(L1, 4), levelR=round(r1, 3), transientL=round(L2, 4), transientR=round(r2, 3))
    out[tag] = res
    print(tag, res)
p = HERE / 'v_offset_rows.json'
prev = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
prev.update(out)
p.write_text(json.dumps(prev, indent=1), encoding='utf-8')
