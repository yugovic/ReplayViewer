"""I2 fusion trial, step 1: extract native XRK channels for the two Fuji sessions.

Reads the local XRK logs (paths from env FUJI_WET_XRK / FUJI_DRY_XRK, never written into outputs),
writes <I2_WORK>/xrk_<race>.npz (raw logger channels; local only, NOT in the repo) and checks the
lap mapping t <-> XRK GPS timecode tc0 + 1000 t against kerb_contacts.json timeBase.lapMapping by
re-matching GPS Speed (shipped speed = linear interp of native GPS Speed on the lap grid).

Env:
  FUJI_WET_XRK, FUJI_DRY_XRK   local XRK files (7/29 wet = fuji_aim_01, 7/30 dry = fuji_aim_2020_07_30)
  I2_WORK                      local work dir (outside the repo)
Usage:  python i2_extract.py
"""
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np
from libxrk import aim_xrk

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
WORK = Path(os.environ['I2_WORK'])
WORK.mkdir(parents=True, exist_ok=True)
XRK = {'fuji_aim_01': os.environ.get('FUJI_WET_XRK', ''), 'fuji_aim_2020_07_30': os.environ.get('FUJI_DRY_XRK', '')}
CH = ['InlineAcc', 'LateralAcc', 'VerticalAcc', 'RollRate', 'PitchRate', 'YawRate',
      'ECU_WS_FL', 'ECU_WS_FR', 'ECU_WS_RL', 'ECU_WS_RR', 'ECU_VEH_SPD', 'ECU_STEER_ANG', 'ECU_LAT_ACC', 'ECU_LONG_ACC',
      'GPS Speed', 'GPS Latitude', 'GPS Longitude', 'GPS Altitude', 'GPS_Position_Accuracy', 'GPS_Velocity_Accuracy',
      'GPS_Satellites', 'GPS_pDOP', 'GPS_Fix', 'GPS_Yaw_Rate', 'GPS_LateralAcc', 'GPS_InlineAcc', 'SPD_2']


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def lap_files(race):
    idx = json.loads((ROOT / f'public/data/races/{race}/laps.json').read_text(encoding='utf-8'))
    return [int(s['lap']) for s in idx['selected']]


def main():
    report = {}
    for race, path in XRK.items():
        if not path:
            print('skip', race, '(env not set)')
            continue
        log = aim_xrk(path)
        out = {}
        for c in CH:
            tb = log.channels[c]
            out[c + '_t'] = tb.column('timecodes').to_numpy().astype(np.float64)
            out[c] = tb.column(c).to_numpy().astype(np.float64)
        contacts = json.loads((ROOT / f'public/data/races/{race}/kerb_contacts.json').read_text(encoding='utf-8'))
        lm = contacts['timeBase']['lapMapping']
        gs_t, gs = out['GPS Speed_t'], out['GPS Speed']
        unit = 3.6 if np.nanmax(gs) < 100 else 1.0
        checks = {}
        for lap in lap_files(race):
            d = json.loads((ROOT / f'public/data/races/{race}/osaki_hmr_demio_101_lap_{lap:03d}.json').read_text(encoding='utf-8'))
            t = np.asarray(d['t'], float)
            sp = np.asarray(d['speed'], float)
            tc0 = float(lm[str(lap)]['tc0Ms'])
            v = np.interp(tc0 + 1000 * t, gs_t, gs) * unit
            checks[str(lap)] = {'tc0Ms': tc0, 'speedRmsKmh': float(np.sqrt(np.mean((v - sp) ** 2)))}
        out['lapMapping'] = json.dumps(checks)
        np.savez_compressed(WORK / f'xrk_{race}.npz', **out)
        rates = {c: float(np.median(np.diff(out[c + '_t']))) for c in CH}
        report[race] = {'xrkSha256': sha(path), 'lapMappingCheck': checks, 'medianSampleIntervalMs': rates,
                        'speedUnitFactor': unit}
        print(race, json.dumps(checks))
    (WORK / 'extract_report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
