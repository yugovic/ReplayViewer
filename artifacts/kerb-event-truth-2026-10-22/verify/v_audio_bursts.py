"""Verifier step 4d: audio-implied video<->logger offset from ALL strong IMU vibration bursts (kerbs, bumps,
outside kerbs), not only the published kerb contacts -> more events for 7/29.

IMU burst detection (own): HP > 3 Hz power of VerticalAcc + LateralAcc + InlineAcc (50 Hz, lap t with the lap
shift), 0.1 s centred mean, dB; base = running 20th percentile over 6 s; bursts = runs above base + 12 dB lasting
>= 0.1 s, merged across gaps < 0.3 s; extent [t0, t1] = run limits. For each burst the T2-like rule
(v_audio_prior_test.py) is applied to IMU and audio (stream 1, 20-120 Hz, t_ov) with priors P = 0.0 / 0.2 / 0.4;
an event counts when the audio rise >= 12 dB, the audio peak is not on the window edge and L_audio is the
same (+-0.02 s) for all three priors (prior-insensitive).
Writes v_audio_bursts.json (numbers only).
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.ndimage import percentile_filter
from scipy.signal import butter, sosfiltfilt

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from v_offset import WORK, lap_doc, lap_start_pts  # noqa: E402
from v_audio_prior_test import burst, db  # noqa: E402

TAG = {'fuji_aim_01': 'wet', 'fuji_aim_2020_07_30': 'dry'}
NL = {'fuji_aim_01': 4, 'fuji_aim_2020_07_30': 6}
OWNL = json.loads((HERE / 'v_offset.json').read_text(encoding='utf-8'))
out = {}
for race in ('fuji_aim_01', 'fuji_aim_2020_07_30'):
    z = np.load(WORK / f'xrk_{race}.npz', allow_pickle=False)
    maps = json.loads(str(z['maps']))
    rows = []
    for lap in range(1, NL[race] + 1):
        d = lap_doc(race, lap)
        meta = d['meta']
        shift = float((meta.get('channel_time_alignment') or {}).get('shiftSeconds', 0.0))
        tc0 = maps[str(lap)]['tc0Ms']
        tt = (z['VerticalAcc_t'] - tc0) / 1000.0 + shift
        sos = butter(4, 3.0, btype='high', fs=50.0, output='sos')
        pw = np.zeros_like(tt)
        for ch in ('VerticalAcc', 'LateralAcc', 'InlineAcc'):
            y = sosfiltfilt(sos, np.interp(tt, (z[f'{ch}_t'] - tc0) / 1000.0 + shift, z[ch]))
            pw += y * y
        ei = db(np.convolve(pw, np.ones(5) / 5, mode='same'))
        m = (tt > 1.0) & (tt < float(d['t'][-1]) - 1.0)
        ti, ei = tt[m], ei[m]
        base = percentile_filter(ei, 20, size=300)
        on = ei > base + 12.0
        runs = []
        i = 0
        while i < len(on):
            if on[i]:
                j = i
                while j + 1 < len(on) and (on[j + 1] or (j + 15 < len(on) and on[j + 1:j + 15].any())):
                    j += 1
                if ti[j] - ti[i] >= 0.1:
                    runs.append((ti[i], ti[j]))
                i = j + 1
            else:
                i += 1
        a = np.load(WORK / f'audio_{TAG[race]}_L{lap}.npz')
        sp = lap_start_pts(race, lap, meta)
        ta = a['s1_t'] - sp - float(meta['first_sample_after_lap_start_seconds'])
        ea = db(np.convolve(a['s1_b20_120'], np.ones(5) / 5, mode='same'))
        for t0, t1 in runs:
            w0, w1 = t0 - 2.0, t1 + 2.0
            mi = (ti >= w0) & (ti <= w1)
            ma = (ta >= w0) & (ta <= w1)
            bi = burst(ti, ei, t0 - 0.2, t1 + 0.2, float(np.percentile(ei[mi], 20)))
            if bi is None:
                continue
            Ls = {}
            ok = True
            for P in (0.0, 0.2, 0.4):
                ba = burst(ta, ea, t0 - 0.2 - P, t1 + 0.2 - P, float(np.percentile(ea[ma], 20)))
                if ba is None or ba['edge'] or ba['rise'] < 12:
                    ok = False
                    break
                Ls[P] = bi['onset50'] - ba['onset50']
            rec = dict(lap=lap, t0=round(t0, 2), t1=round(t1, 2), imuRise=round(bi['rise'], 1))
            if ok and max(Ls.values()) - min(Ls.values()) <= 0.02:
                rec['L_audio'] = round(float(np.median(list(Ls.values()))), 3)
                rec['L_ownVideoYaw'] = OWNL[f'{TAG[race]}_L{lap}']['rowFit']['yawTransient']['L']
            rows.append(rec)
    v = [r['L_audio'] for r in rows if 'L_audio' in r]
    res = [r['L_audio'] - r['L_ownVideoYaw'] for r in rows if 'L_audio' in r]
    out[race] = dict(nBursts=len(rows), nAccepted=len(v),
                     L_audio=dict(median=round(float(np.median(v)), 3), q25=round(float(np.percentile(v, 25)), 3),
                                  q75=round(float(np.percentile(v, 75)), 3)) if v else None,
                     minusOwnVideoL=dict(median=round(float(np.median(res)), 3), q25=round(float(np.percentile(res, 25)), 3),
                                         q75=round(float(np.percentile(res, 75)), 3)) if res else None,
                     perLapMedian={str(l): round(float(np.median([r['L_audio'] for r in rows if r['lap'] == l and 'L_audio' in r])), 3)
                                   for l in range(1, NL[race] + 1) if any(r['lap'] == l and 'L_audio' in r for r in rows)},
                     events=rows)
    print(race, {k: v for k, v in out[race].items() if k != 'events'})
(HERE / 'v_audio_bursts.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
