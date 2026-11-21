"""T1 verification: bias / spread of the kerb-contact onset estimator on synthetic bursts injected into real logger noise.

For each session, bursts are added to the native VerticalAcc and RollRate series at random straight-line, no-brake
times (true onset at a random sub-sample position), then the production chain of build_logger_events.py is applied
(4 Hz zero-phase high-pass, centred 3-sample RMS, onsetLevel = F * max(median + 4 sigma, 2.5 median) from the 8 s of
straight-line samples before the burst, first upward crossing that starts a run of >= 2 samples).

Burst model (an assumption, not a measured kerb signature): band-limited noise 6-20 Hz under a rectangular envelope
of 0.30 s (rise 0 ms or 40 ms linear), scaled so that the burst RMS equals R x the local baseline median RMS,
R in {4, 8, 16, 32}. Output: verify_contact_onset_bias.json (error = estimated onset - true onset, s).

  T1_WORK=<work dir> python verify_contact_onset_bias.py
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.signal import butter, filtfilt

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_logger_events as B  # noqa: E402
from t1common import butter_hp, centered_rms, crossing_time  # noqa: E402

RNG = np.random.default_rng(20261022)
RATIOS = [4, 8, 16, 32]
RISES = [0.0, 0.04]
N_PER = 150


def band_noise(n, fs=50.0):
    b, a = butter(4, [6.0 / (fs / 2), 20.0 / (fs / 2)], 'bandpass')
    x = filtfilt(b, a, RNG.standard_normal(n + 200))[100:-100]
    return x / np.sqrt(np.mean(x * x))


def estimate(tc, raw, straight, t_true_ms, F):
    e = centered_rms(butter_hp(raw, B.HP_FC, 50.0), B.RMS_N)
    t = tc / 1000.0
    tt = t_true_ms / 1000.0
    m = straight & (t >= tt - 8.0) & (t <= tt - 0.5)
    vals = e[m]
    med = float(np.median(vals))
    sig = float(1.4826 * np.median(np.abs(vals - med)))
    O = F * max(med + 4 * sig, 2.5 * med)
    s = np.flatnonzero((t >= tt - 0.6) & (t <= tt + 0.35))
    for i in s:
        if e[i] > O and e[i + 1] > O:
            k = i
            while k > 0 and e[k - 1] > O and t[k - 1] >= tt - 1.1:
                k -= 1
            if e[k - 1] > O:
                return None, med
            return crossing_time(t, e, k, O) - tt, med
    return None, med


def main():
    out = {}
    for race in B.RACES:
        S = B.Session(race)
        res = {}
        for ch, tc, raw0, F in (('VA', S.tcA, S.z['VerticalAcc'], 1.0), ('RR', S.tcB, S.z['RollRate'], 2.0)):
            st = S.straight[ch]
            e0 = S.e[ch]
            cand = np.flatnonzero(st)
            cand = cand[(cand > 600) & (cand < len(tc) - 100)]
            for R in RATIOS:
                for rise in RISES:
                    errs, miss = [], 0
                    for _ in range(N_PER):
                        i0 = int(RNG.choice(cand))
                        # need 8 s of history with straight samples and a fully straight 0.6 s window
                        if not st[i0 - 30:i0 + 15].all():
                            i0 = int(RNG.choice(cand))
                        t_true = tc[i0 - 1] + RNG.uniform(0, 1) * (tc[i0] - tc[i0 - 1])
                        hist = st & (tc >= t_true - 8000) & (tc <= t_true - 500)
                        base = float(np.median(e0[hist])) if hist.sum() >= 25 else float(np.median(e0[st]))
                        n = 30
                        idx = np.arange(i0 - 1, i0 - 1 + n)
                        rel = (tc[idx] - t_true) / 1000.0
                        env = np.where(rel < 0, 0.0, 1.0)
                        if rise > 0:
                            env = np.clip(rel / rise, 0.0, 1.0)
                        env[rel > 0.30] = 0.0
                        raw = raw0.copy()
                        raw[idx] += R * base * band_noise(n) * env
                        err, _ = estimate(tc, raw, st, t_true, F)
                        if err is None:
                            miss += 1
                        else:
                            errs.append(err)
                    a = np.asarray(errs)
                    key = f'R{R}_rise{int(rise * 1000)}ms'
                    res.setdefault(ch, {})[key] = dict(
                        n=len(a), missed=miss,
                        medianErrS=round(float(np.median(a)), 4) if len(a) else None,
                        q25=round(float(np.percentile(a, 25)), 4) if len(a) else None,
                        q75=round(float(np.percentile(a, 75)), 4) if len(a) else None,
                        p5=round(float(np.percentile(a, 5)), 4) if len(a) else None,
                        p95=round(float(np.percentile(a, 95)), 4) if len(a) else None,
                        fracWithin20ms=round(float(np.mean(np.abs(a) <= 0.02)), 3) if len(a) else None,
                        fracWithin40ms=round(float(np.mean(np.abs(a) <= 0.04)), 3) if len(a) else None)
                    print(race, ch, key, res[ch][key])
        out[race] = res
    doc = dict(kind='synthetic onset-bias check (estimator only; burst model is an assumption)',
               tool='artifacts/kerb-event-truth-2026-10-22/T1-logger-events/verify_contact_onset_bias.py',
               errorDefinition='estimated onset - true onset (s); negative = estimate early',
               burstModel='6-20 Hz band-limited noise, rectangular 0.30 s envelope (rise 0 or 40 ms), burst RMS = R x baseline median RMS',
               ratiosNote='measured peakRatio of the detected contacts: median ~6 (VA) / ~16 (RR); see summary.json',
               seed=20261022, nPerCell=N_PER, results=out)
    (HERE / 'verify_contact_onset_bias.json').write_text(json.dumps(doc, indent=1), encoding='utf-8')
    print('wrote verify_contact_onset_bias.json')


if __name__ == '__main__':
    main()
