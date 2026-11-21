"""Verifier step 4: kerb-contact events recomputed from raw signals with ONE rule for IMU and audio.

Event list: the T1 contact candidates (session, lap, kerb, T1 onsetT) are used ONLY to locate a +-1 s
window; every time below is recomputed here.
  IMU   VerticalAcc (native 50 Hz, own XRK extraction), band 4-20 Hz (4th-order Butterworth, zero phase),
        power, 60 ms centred mean, dB, linearly resampled to 10 ms; lap t = (ms - tc0)/1000 + shift
  audio stream 1 band 20-120 Hz power, 50 ms centred mean, 10 ms hop (v_audio_env.py), dB; overlay-mapped
        t_ov = PTS - lapStartPts - delta (video-timer-reads.json mapping, as v_offset.py)
  rule  (identical for both signals) base = median dB of a quiet pre-window; start6 = FIRST upward crossing
        of base + 6 dB inside the search window that stays above for >= 30 ms (linear interpolation);
        peak1 = max within 0.3 s after start6; onset50 = first upward crossing of base + (peak1 - base)/2
        at or after start6 - 0.1 s. Accepted when peak1 - base >= 8 dB.
  IMU   base window [T1 onset - 2.0, T1 onset - 0.6]; search window [T1 onset - 0.4, T1 onset + 0.6]
  audio base window [IMU start6 - 2.2, IMU start6 - 1.0] (t_ov); search window [IMU start6 - 0.8,
        IMU start6 + 0.5] (covers any offset L in [-0.5, 0.8] s; NO prior offset)
  xcorr per-event lag of the audio dB envelope against the IMU dB envelope over [IMU start6 - 0.5,
        IMU start6 + 1.0], lag range [-0.5, 0.8] s (shape-based, independent of the onset rule)
  L_audio = IMU onset - audio onset (t_ov) (or the xcorr lag): the per-event video<->logger offset implied
        by the audio (same convention as the join: true t = t_ov + L), valid only if the camera's audio is
        in sync with its video frames.
CG check: own pose re-implementation (v_cgpose.py): inner-tyre block-line gap at the own IMU onset50,
the first CG block crossing within [onset - 1.5, onset + 1.5] s and its time minus the IMU onset.
Writes v_kerb_events.json (numbers only).
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfiltfilt

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from v_offset import WORK, lap_doc, lap_start_pts  # noqa: E402
import v_cgpose as P  # noqa: E402

KT = HERE.parent
TAG = {'fuji_aim_01': 'wet', 'fuji_aim_2020_07_30': 'dry'}
T1 = json.loads((KT / 'T1-logger-events/events.json').read_text(encoding='utf-8'))
JOINL = json.loads((KT / 'join/summary.json').read_text(encoding='utf-8'))['timeBase']['perLap']
OWNL = json.loads((HERE / 'v_offset.json').read_text(encoding='utf-8'))


def db(x):
    return 10 * np.log10(np.maximum(x, 1e-15))


def onset(t, e_db, base_win, search_win):
    bm = (t >= base_win[0]) & (t <= base_win[1])
    if bm.sum() < 20:
        return None
    base = float(np.median(e_db[bm]))
    w = np.flatnonzero((t >= search_win[0]) & (t <= search_win[1]))
    if len(w) < 5:
        return None
    thr6 = base + 6.0
    st = None
    for j in w:
        if j > 0 and e_db[j] >= thr6 and e_db[j - 1] < thr6 and np.all(e_db[j:j + 3] >= thr6):
            a, b = e_db[j - 1], e_db[j]
            st = float(t[j - 1] + (thr6 - a) / (b - a) * (t[j] - t[j - 1]))
            js = j
            break
    if st is None:
        return dict(base=round(base, 2), start6=None, onset50=None, riseDb=None)
    pw = np.flatnonzero((t >= st) & (t <= st + 0.3))
    kp = int(pw[np.argmax(e_db[pw])])
    rise = float(e_db[kp] - base)
    thr = base + rise / 2
    o50 = None
    j = max(1, int(np.searchsorted(t, st - 0.1)))
    while j <= kp:
        if e_db[j] >= thr and e_db[j - 1] < thr:
            a, b = e_db[j - 1], e_db[j]
            o50 = float(t[j - 1] + (thr - a) / (b - a) * (t[j] - t[j - 1]))
            break
        j += 1
    if o50 is None and e_db[max(1, int(np.searchsorted(t, st - 0.1))) - 1] >= thr:
        o50 = st
    return dict(base=round(base, 2), start6=st, onset50=o50, tPeak1=float(t[kp]), riseDb=round(rise, 2))


def xcorr_event(ti, ei, ta, ea, lo, hi):
    g = np.arange(lo, hi, 0.01)
    a = np.interp(g, ti, ei)
    best = (-2, None)
    for L in np.arange(-0.5, 0.8001, 0.005):
        b = np.interp(g - L, ta, ea)
        if a.std() == 0 or b.std() == 0:
            continue
        r = float(np.corrcoef(a, b)[0, 1])
        if r > best[0]:
            best = (r, float(L))
    return best


cache = {}


def imu_env(race, lap):
    key = ('imu', race, lap)
    if key not in cache:
        z = np.load(WORK / f'xrk_{race}.npz', allow_pickle=False)
        maps = json.loads(str(z['maps']))
        d = lap_doc(race, lap)
        shift = float((d['meta'].get('channel_time_alignment') or {}).get('shiftSeconds', 0.0))
        t = (z['VerticalAcc_t'] - maps[str(lap)]['tc0Ms']) / 1000.0 + shift
        sos = butter(4, [4.0, 20.0], btype='band', fs=50.0, output='sos')
        y = sosfiltfilt(sos, z['VerticalAcc'])
        e = np.convolve(y * y, np.ones(3) / 3, mode='same')
        g = np.arange(-5.0, float(d['t'][-1]) + 5.0, 0.01)
        cache[key] = (g, db(np.interp(g, t, e)))
    return cache[key]


def audio_env(race, lap):
    key = ('aud', race, lap)
    if key not in cache:
        d = lap_doc(race, lap)
        sp = lap_start_pts(race, lap, d['meta'])
        a = np.load(WORK / f"audio_{TAG[race]}_L{lap}.npz")
        cache[key] = (a['s1_t'] - sp - float(d['meta']['first_sample_after_lap_start_seconds']), db(a['s1_b20_120']))
    return cache[key]


rows = []
cands = []
for x in T1['records']:
    for c in x['kerbContacts']:
        cands.append((x['session'], x['lap'], x['kerb'], c['onsetT'], c['confidence'], True))
for c in T1['otherContacts']:
    cands.append((c['session'], c['lap'], c.get('kerb') or c.get('kerbAtOnset') or '?', c['onsetT'], c['confidence'], False))
for race, lap, kerb, t1on, conf, inside in cands:
    ti, ei = imu_env(race, lap)
    im = onset(ti, ei, (t1on - 2.0, t1on - 0.6), (t1on - 0.4, t1on + 0.6))
    rec = dict(session=race, lap=lap, kerb=kerb, insidePass=inside, t1Confidence=conf, t1OnsetT=round(t1on, 3))
    if not im or im['onset50'] is None or im['riseDb'] < 8:
        rec['imu'] = im
        rows.append(rec)
        continue
    rec['imu'] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in im.items()}
    ta, ea = audio_env(race, lap)
    s6 = im['start6']
    au = onset(ta, ea, (s6 - 2.2, s6 - 1.0), (s6 - 0.8, s6 + 0.5))
    rec['audio'] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in au.items()} if au else None
    if au and au['onset50'] is not None and au['riseDb'] >= 8:
        rec['L_audio'] = round(im['onset50'] - au['onset50'], 3)
        rec['L_audio_start6'] = round(im['start6'] - au['start6'], 3)
    r, lx = xcorr_event(ti, ei, ta, ea, s6 - 0.5, s6 + 1.0)
    rec['L_xcorr'] = None if lx is None else round(lx, 3)
    rec['xcorrR'] = round(r, 3)
    jl = JOINL[race][str(lap)]['adopted']
    ol = OWNL[f"{TAG[race]}_L{lap}"]['rowFit']['yawTransient']['L']
    rec['L_join'] = jl
    rec['L_ownVideoYaw'] = ol
    if 'L_audio' in rec:
        rec['resid_joinL'] = round(rec['L_audio'] - jl, 3)      # = IMU onset - (audio onset + L_join)
        rec['resid_ownL'] = round(rec['L_audio'] - ol, 3)
    if rec.get('L_xcorr') is not None:
        rec['residXcorr_joinL'] = round(rec['L_xcorr'] - jl, 3)
    # CG gap at the own IMU onset, and first CG block crossing near it
    if kerb in P.zones() and kerb.startswith('k'):
        t0 = im['onset50']
        tg = np.arange(t0 - 1.5, t0 + 1.5, 0.01)
        gp = P.kerb_gaps(race, lap, tg, kerb)['block']['nearest']
        g0 = P.kerb_gaps(race, lap, [t0], kerb)['block']['nearest'][0]
        rec['cgGapBlockAtImuOnset'] = None if not np.isfinite(g0) else round(float(g0), 3)
        on = np.isfinite(gp) & (gp <= 0)
        idx = np.flatnonzero(on[1:] & ~on[:-1]) + 1
        if len(idx):
            k = idx[np.argmin(np.abs(tg[idx] - t0))]
            a, b = gp[k - 1], gp[k]
            tc = tg[k - 1] + (a / (a - b)) * 0.01 if np.isfinite(a) and a != b else tg[k]
            rec['cgFirstBlockCrossT'] = round(float(tc), 3)
            rec['cgCrossMinusImuOnset'] = round(float(tc - t0), 3)
        rec['cgMinGapWithin1p5s'] = round(float(np.nanmin(gp)), 3) if np.isfinite(gp).any() else None
    rows.append(rec)
    print(race[-5:], lap, kerb, conf, 'imu', rec['imu'].get('onset50'), 'rise', rec['imu'].get('riseDb'),
          'aud', (rec.get('audio') or {}).get('onset50'), (rec.get('audio') or {}).get('riseDb'),
          'L_audio', rec.get('L_audio'), 'L_s6', rec.get('L_audio_start6'), 'L_xc', rec.get('L_xcorr'), rec.get('xcorrR'),
          'gap', rec.get('cgGapBlockAtImuOnset'), 'cgX-imu', rec.get('cgCrossMinusImuOnset'))

(HERE / 'v_kerb_events.json').write_text(json.dumps(rows, indent=1), encoding='utf-8')
