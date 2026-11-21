"""Verifier step 1: independent XRK extraction + lap mapping (system python with libxrk).

Reads the two source XRKs (paths from env FUJI_WET_XRK / FUJI_DRY_XRK) and writes
$VERIFY_WORK/xrk_<race>.npz with the native channels (timecodes in logger ms) and a JSON
summary of the lap mapping next to this script (v_xrk_maps.json).

Lap mapping (own rule, not T1's): the shipped lap JSON speed is the XRK GPS Speed resampled
on a 100 ms grid (the shipped lat/lng are smoothed, so speed is used). tc0 = logger ms of lap
t = 0, found by least squares: coarse over raw-sample alignments, then refined in 1 ms steps by
linear interpolation of the XRK GPS Speed onto tc0 + 1000 * t. Checked against the XRK lap
table (lap start) and the lap meta.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
from libxrk import aim_xrk

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('REPLAYVIEWER_ROOT', HERE.parents[2]))
WORK = Path(os.environ['VERIFY_WORK'])
XRK = {'fuji_aim_01': os.environ['FUJI_WET_XRK'], 'fuji_aim_2020_07_30': os.environ['FUJI_DRY_XRK']}
NL = {'fuji_aim_01': 4, 'fuji_aim_2020_07_30': 6}
CH = ['InlineAcc', 'LateralAcc', 'VerticalAcc', 'RollRate', 'PitchRate', 'YawRate', 'ECU_STEER_ANG', 'ECU_RPM',
      'ECU_VEH_SPD', 'ECU_WS_RL', 'ECU_WS_RR', 'GPS Speed', 'GPS Latitude', 'GPS Longitude', 'GPS_Yaw_Rate', 'ECU_BRK_P']

summary = {}
for race, path in XRK.items():
    log = aim_xrk(path)
    out = {}
    for c in CH:
        tb = log.channels[c]
        out[c.replace(' ', '_') + '_t'] = tb.column('timecodes').to_numpy().astype(np.float64)
        out[c.replace(' ', '_')] = tb.column(c).to_numpy().astype(np.float64)
    laps = log.laps
    lnum = laps.column('num').to_numpy()
    lst = laps.column('start_time').to_numpy().astype(float)
    out['lap_num'], out['lap_start'] = lnum, lst
    gt, gs = out['GPS_Speed_t'], out['GPS_Speed']
    unit = 3.6 if np.nanmax(gs) < 100 else 1.0
    maps = {}
    for k in range(1, NL[race] + 1):
        d = json.loads((ROOT / f'public/data/races/{race}/osaki_hmr_demio_101_lap_{k:03d}.json').read_text(encoding='utf-8'))
        sp = np.array(d['speed'], float)
        n = len(sp)
        # least-squares sample alignment of the lap speed against the XRK GPS speed
        best = (1e9, None)
        for i0 in range(0, len(gs) - n):
            e = float(np.mean((gs[i0:i0 + n] * unit - sp) ** 2))
            if e < best[0]:
                best = (e, i0)
        e, i0 = best
        tl = np.array(d['t'], float) * 1000.0
        fine = (1e9, None)
        for off in np.arange(-150.0, 150.5, 1.0):
            q = gt[i0] + off + tl
            e2 = float(np.mean((np.interp(q, gt, gs) * unit - sp) ** 2))
            if e2 < fine[0]:
                fine = (e2, gt[i0] + off)
        e, tc0 = fine[0], float(fine[1])
        xrk_lap_start = float(lst[list(lnum).index(k)])
        meta = d['meta']
        cta = meta.get('channel_time_alignment') or {}
        maps[str(k)] = dict(tc0Ms=tc0, rmsKmh=e ** 0.5, xrkLapStartMs=xrk_lap_start,
                            tc0MinusXrkLapStartS=(tc0 - xrk_lap_start) / 1000.0,
                            metaDelta=meta.get('first_sample_after_lap_start_seconds'),
                            metaLapStartTimecodeMs=(cta.get('source') or {}).get('lapStartTimecodeMs'),
                            shiftSeconds=cta.get('shiftSeconds', 0.0), n=n,
                            lapDurationS=float(d['t'][-1]))
    out['maps'] = json.dumps(maps)
    np.savez_compressed(WORK / f'xrk_{race}.npz', **out)
    summary[race] = maps
    print(race, json.dumps(maps, indent=1))
(HERE / 'v_xrk_maps.json').write_text(json.dumps(summary, indent=1), encoding='utf-8')
