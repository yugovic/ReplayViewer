"""Verifier step 4b: is the T2/join audio confirmation of L circular (does it follow the search prior)?

Re-implements the T2 kerb-burst rule with own signals (own XRK extraction, own audio envelope):
  IMU   HP > 3 Hz power of VerticalAcc + LateralAcc + InlineAcc (50 Hz), 0.1 s centred mean, dB, lap t
  audio stream 1 20-120 Hz power (v_audio_env.py 50 ms boxcar, then a further 50 ms boxcar ~ 0.1 s), dB, t_ov
  burst peak = max on [t0 - 0.2, t1 + 0.2] (published kerb_contacts.json extent; audio window moved earlier
        by the prior P); onset50 = crossing of base + (peak - base)/2 walking back from the peak; base =
        20th percentile over [t0 - 2, t1 + 2]
For priors P = 0.0 ... 0.4 s the per-event L_audio = IMU onset50 - audio onset50 (t_ov) is recomputed.
If the median L_audio tracks P, the audio check is circular; if it stays put, it is a measurement.
Also reports how many events have their audio peak on the search-window edge for each P.
Writes v_audio_prior_test.json (numbers only).
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfiltfilt

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from v_offset import ROOT, WORK, lap_doc, lap_start_pts  # noqa: E402

TAG = {'fuji_aim_01': 'wet', 'fuji_aim_2020_07_30': 'dry'}
PRIORS = [0.0, 0.1, 0.2, 0.3, 0.4]


def db(x):
    return 10 * np.log10(np.maximum(x, 1e-15))


def burst(t, e, lo, hi, base):
    m = np.flatnonzero((t >= lo) & (t <= hi))
    if len(m) < 3:
        return None
    ip = int(m[np.argmax(e[m])])
    rise = float(e[ip] - base)
    if rise <= 0:
        return None
    thr = base + 0.5 * rise
    i = ip
    while i > 0 and e[i - 1] >= thr:
        i -= 1
    if i == 0:
        return None
    o = float(t[i - 1] + (thr - e[i - 1]) / (e[i] - e[i - 1]) * (t[i] - t[i - 1]))
    return dict(onset50=o, tPeak=float(t[ip]), rise=rise, edge=bool(ip in (m[0], m[-1])))


def main():
    out = {}
    for race in ('fuji_aim_01', 'fuji_aim_2020_07_30'):
        kc = json.loads((ROOT / f'public/data/races/{race}/kerb_contacts.json').read_text(encoding='utf-8'))
        z = np.load(WORK / f'xrk_{race}.npz', allow_pickle=False)
        maps = json.loads(str(z['maps']))
        rows = []
        for lap_s, evs in kc['events'].items():
            lap = int(lap_s)
            d = lap_doc(race, lap)
            meta = d['meta']
            shift = float((meta.get('channel_time_alignment') or {}).get('shiftSeconds', 0.0))
            tc0 = maps[lap_s]['tc0Ms']
            sos = butter(4, 3.0, btype='high', fs=50.0, output='sos')
            tt = (z['VerticalAcc_t'] - tc0) / 1000.0 + shift
            pw = np.zeros_like(tt)
            for ch in ('VerticalAcc', 'LateralAcc', 'InlineAcc'):
                y = sosfiltfilt(sos, np.interp(tt, (z[f'{ch}_t'] - tc0) / 1000.0 + shift, z[ch]))
                pw += y * y
            pw = np.convolve(pw, np.ones(5) / 5, mode='same')
            ti, ei = tt, db(pw)
            a = np.load(WORK / f'audio_{TAG[race]}_L{lap}.npz')
            sp = lap_start_pts(race, lap, meta)
            ta = a['s1_t'] - sp - float(meta['first_sample_after_lap_start_seconds'])
            ea = db(np.convolve(a['s1_b20_120'], np.ones(5) / 5, mode='same'))
            for e in evs:
                w0, w1 = e['t0'] - 2.0, e['t1'] + 2.0
                mi = (ti >= w0) & (ti <= w1)
                ma = (ta >= w0) & (ta <= w1)
                bi = burst(ti, ei, e['t0'] - 0.2, e['t1'] + 0.2, float(np.percentile(ei[mi], 20)))
                if bi is None:
                    continue
                rec = dict(lap=lap, kerb=e['kerb'], bin=e['bin'], t0=e['t0'], t1=e['t1'], imuOnset50=round(bi['onset50'], 3),
                           imuRise=round(bi['rise'], 1))
                for P in PRIORS:
                    ba = burst(ta, ea, e['t0'] - 0.2 - P, e['t1'] + 0.2 - P, float(np.percentile(ea[ma], 20)))
                    if ba is None:
                        rec[f'P{P}'] = None
                        continue
                    rec[f'P{P}'] = dict(L=round(bi['onset50'] - ba['onset50'], 3), rise=round(ba['rise'], 1), edge=ba['edge'])
                rows.append(rec)
        summ = {}
        for P in PRIORS:
            for sel in ('all', 'strong'):
                v = [r[f'P{P}']['L'] for r in rows if r.get(f'P{P}') and not r[f'P{P}']['edge'] and
                     (sel == 'all' or (r[f'P{P}']['rise'] >= 12 and r['imuRise'] >= 12))]
                ne = sum(1 for r in rows if r.get(f'P{P}') and r[f'P{P}']['edge'])
                summ[f'P{P}_{sel}'] = dict(n=len(v), median=round(float(np.median(v)), 3) if v else None,
                                           q25=round(float(np.percentile(v, 25)), 3) if v else None,
                                           q75=round(float(np.percentile(v, 75)), 3) if v else None, nPeakOnEdge=ne)
        out[race] = dict(summary=summ, events=rows)
        print(race, json.dumps(summ))
    (HERE / 'v_audio_prior_test.json').write_text(json.dumps(out, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
