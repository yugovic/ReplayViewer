"""T1 step 1: extract the native-rate logger channels of the two Fuji sessions from the AiM XRK files.

Inputs (local, never published; passed by environment variable):
  FUJI_WET_XRK   XRK of 2020-07-29 (fuji_aim_01)
  FUJI_DRY_XRK   XRK of 2020-07-30 (fuji_aim_2020_07_30)
  T1_WORK        work directory OUTSIDE the repository (npz files with raw logger samples land here)

Output: $T1_WORK/xrk_native_<race>.npz with, per channel, '<ch>_t' (logger timecode, ms, as stored) and
'<ch>' (value as stored; no resampling, no repair), plus 'lapmap' (JSON): per shipped lap the XRK timecode
tc0 of the lap JSON's first GPS sample, re-derived here from the XRK GPS Speed vs the shipped lap speed and
compared with the tc0 published in public/data/races/<race>/kerb_contacts.json (timeBase.lapMapping).

Run with a Python that has libxrk (system Python on the analysis PC):
  PYTHONIOENCODING=utf-8 python extract_xrk_native.py
"""
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np
from libxrk import aim_xrk

ROOT = Path(__file__).resolve().parents[3]
WORK = Path(os.environ['T1_WORK'])
WORK.mkdir(parents=True, exist_ok=True)
XRK = {'fuji_aim_01': os.environ.get('FUJI_WET_XRK', ''), 'fuji_aim_2020_07_30': os.environ.get('FUJI_DRY_XRK', '')}
CHANNELS = ['InlineAcc', 'LateralAcc', 'VerticalAcc', 'RollRate', 'PitchRate', 'YawRate',
            'ECU_WS_FL', 'ECU_WS_FR', 'ECU_WS_RL', 'ECU_WS_RR', 'ECU_VEH_SPD', 'SPD_2',
            'ECU_BRK_P', 'ECU_STEER_ANG', 'ECU_PPS', 'ECU_TPS', 'ECU_LONG_ACC',
            'GPS Speed', 'GPS Latitude', 'GPS Longitude', 'GPS_Yaw_Rate', 'GPS_LateralAcc', 'GPS_InlineAcc']
LAP_PREFIX = 'osaki_hmr_demio_101_lap_'


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def lap_numbers(race):
    idx = json.loads((ROOT / f'public/data/races/{race}/laps.json').read_text(encoding='utf-8'))
    return [int(s['lap']) for s in idx['selected']]


def main():
    for race, path in XRK.items():
        if not path or not Path(path).is_file():
            sys.exit(f'{race}: XRK not found (set FUJI_WET_XRK / FUJI_DRY_XRK)')
        log = aim_xrk(path)
        out = {}
        for c in CHANNELS:
            tb = log.channels[c]
            out[c + '_t'] = tb.column('timecodes').to_numpy().astype(np.float64)
            out[c] = tb.column(c).to_numpy().astype(np.float64)
        kc = json.loads((ROOT / f'public/data/races/{race}/kerb_contacts.json').read_text(encoding='utf-8'))
        pub = kc['timeBase']['lapMapping']
        gs_t, gs = out['GPS Speed_t'], out['GPS Speed']
        unit = 3.6 if np.nanmax(gs) < 100 else 1.0
        lapmap = {}
        for lap in lap_numbers(race):
            d = json.loads((ROOT / f'public/data/races/{race}/{LAP_PREFIX}{lap:03d}.json').read_text(encoding='utf-8'))
            t = np.asarray(d['t'], float)
            sp = np.asarray(d['speed'], float)
            tc_pub = float(pub[str(lap)]['tc0Ms'])
            # The shipped lap t grid is the converter's uniform 10 Hz logger-time grid; the raw GPS samples sit
            # between grid points, so lap speed = GPS speed linearly interpolated at tc0 + 1000 t.
            def rms_at(tc0):
                v = np.interp(tc0 + t * 1000.0, gs_t, gs) * unit
                return float(np.sqrt(np.mean((v - sp) ** 2)))
            err_pub = rms_at(tc_pub)
            # independent search: coarse over raw GPS samples (nearest-sample alignment), then 1 ms refinement
            coarse = []
            for j in range(0, len(gs) - len(sp)):
                if abs(gs[j] * unit - sp[0]) > 3.0:
                    continue
                coarse.append((float(np.sqrt(np.mean((gs[j:j + len(sp)] * unit - sp) ** 2))), float(gs_t[j])))
            coarse.sort()
            best = (1e9, None)
            for _, tcj in coarse[:5]:
                for tc0 in np.arange(tcj - 100.0, tcj + 100.0, 1.0):
                    e = rms_at(tc0)
                    if e < best[0]:
                        best = (e, float(tc0))
            tc_own = best[1]
            lapmap[str(lap)] = dict(tc0Ms=tc_pub, tc0OwnMs=tc_own, rmsKmhPublished=err_pub, rmsKmhOwn=best[0],
                                    agree=bool(abs(tc_own - tc_pub) < 0.5), nGps=len(sp), tEnd=float(t[-1]))
            print(race, lap, lapmap[str(lap)])
        out['lapmap'] = np.array(json.dumps(lapmap))
        out['xrk_sha256'] = np.array(sha256(path))
        np.savez_compressed(WORK / f'xrk_native_{race}.npz', **out)
        print(race, 'saved', {c: len(out[c]) for c in CHANNELS})


if __name__ == '__main__':
    main()
