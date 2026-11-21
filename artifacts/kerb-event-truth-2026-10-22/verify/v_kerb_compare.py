"""Verifier step 4c: per-event comparison for the join's primary kerb contacts.

For every join kerbContacts row (primary or not) this table puts side by side:
  T1 onset (join truth)            event_table truthOnsetT
  own IMU onset50                   v_audio_prior_test.json (T2-like burst rule on own signals, published extent)
  own audio onset50 (t_ov)          same file, prior P = 0.0 and 0.2 (prior-insensitivity shown per event)
  L_audio = IMU onset50 - audio onset50; residual vs join L and vs own video-yaw L (v_offset.json)
  own CG gap (v_cgpose.py) at the T1 onset and at the own IMU onset50; join gapAtOnset
  own first CG block crossing within +-1.5 s of the T1 onset minus the T1 onset; join dtFirst
Writes v_kerb_compare.json (numbers only).
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import v_cgpose as P  # noqa: E402

KT = HERE.parent
E = json.loads((KT / 'join/event_table.json').read_text(encoding='utf-8'))
AP = json.loads((HERE / 'v_audio_prior_test.json').read_text(encoding='utf-8'))
OWNL = json.loads((HERE / 'v_offset.json').read_text(encoding='utf-8'))
TAG = {'fuji_aim_01': 'wet', 'fuji_aim_2020_07_30': 'dry'}


def first_cross(race, lap, kerb, t_ref):
    tg = np.arange(t_ref - 1.5, t_ref + 1.5, 0.005)
    gp = P.kerb_gaps(race, lap, tg, kerb)['block']['nearest']
    on = np.isfinite(gp) & (gp <= 0)
    idx = np.flatnonzero(on[1:] & ~on[:-1]) + 1
    if not len(idx):
        return None
    k = idx[np.argmin(np.abs(tg[idx] - t_ref))]
    a, b = gp[k - 1], gp[k]
    return float(tg[k - 1] + (a / (a - b)) * 0.005) if np.isfinite(a) and a != b else float(tg[k])


rows = []
for c in E['kerbContacts']:
    race, lap, kerb = c['session'], c['lap'], c['kerb']
    t1 = c['truthOnsetT']
    m = [e for e in AP[race]['events'] if e['lap'] == lap and e['t0'] - 0.6 <= t1 <= e['t1'] + 0.3]
    ev = min(m, key=lambda e: abs(e['imuOnset50'] - t1)) if m else None
    rec = dict(session=race, lap=lap, kerb=kerb, primary=c['primary'], confidence=c['confidence'], t1Onset=t1,
               joinGapAtOnset=c['block'].get('gapAtOnset'), joinDtFirst=c['block'].get('dtFirst'),
               joinCgShowsContact=c['block']['cgShowsContact'])
    g1 = P.kerb_gaps(race, lap, [t1], kerb)['block']['nearest'][0]
    rec['ownGapAtT1Onset'] = None if not np.isfinite(g1) else round(float(g1), 3)
    fc = first_cross(race, lap, kerb, t1)
    rec['ownFirstCrossMinusT1'] = None if fc is None else round(fc - t1, 3)
    if ev:
        rec['ownImuOnset50'] = ev['imuOnset50']
        rec['ownImuMinusT1'] = round(ev['imuOnset50'] - t1, 3)
        g2 = P.kerb_gaps(race, lap, [ev['imuOnset50']], kerb)['block']['nearest'][0]
        rec['ownGapAtOwnOnset50'] = None if not np.isfinite(g2) else round(float(g2), 3)
        for Pk in ('P0.0', 'P0.2'):
            a = ev.get(Pk)
            rec[f'L_audio_{Pk}'] = None if not a else a['L']
            rec[f'audioRise_{Pk}'] = None if not a else a['rise']
            rec[f'audioEdge_{Pk}'] = None if not a else a['edge']
        jl = E['timeBase']['perLap'][f'{race}/L{lap}']['adopted']
        ol = OWNL[f"{TAG[race]}_L{lap}"]['rowFit']['yawTransient']['L']
        rec['L_join'] = jl
        rec['L_ownVideoYaw'] = ol
        if rec.get('L_audio_P0.2') is not None:
            rec['residJoinL'] = round(rec['L_audio_P0.2'] - jl, 3) if jl is not None else None
            rec['residOwnL'] = round(rec['L_audio_P0.2'] - ol, 3)
    rows.append(rec)
    print(race[-5:], lap, kerb, 'P' if c['primary'] else '-', c['confidence'][:3], 't1', t1, 'ownImu', rec.get('ownImuOnset50'),
          'Laud', rec.get('L_audio_P0.0'), rec.get('L_audio_P0.2'), 'rise', rec.get('audioRise_P0.2'),
          '| gap@t1 own', rec['ownGapAtT1Onset'], 'join', rec['joinGapAtOnset'], 'gap@own50', rec.get('ownGapAtOwnOnset50'),
          '| cross-t1 own', rec['ownFirstCrossMinusT1'], 'join', rec['joinDtFirst'])
(HERE / 'v_kerb_compare.json').write_text(json.dumps(rows, indent=1), encoding='utf-8')
