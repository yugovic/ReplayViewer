"""Open item OI-1 (review loop 2026-10-22, round 1, R1-09): why is the drawn heading rate ~1.21x the IMU gyro yaw rate?

The join's whole-lap cross-correlation (summary.headingTimingWholeLap) found scaleCgOverImu 1.21-1.22 on all 10 laps:
the viewer's heading rate (from the GPS course, +-0.6 s chord) is ~21 % larger than the gyro yaw rate. This script
checks, per lap and from the raw logger samples only, which side the other sensors agree with, and whether the
amplitude matters for the turn-in timing result:

  A  logger GPS_Yaw_Rate (AiM's own GPS course rate)          vs IMU YawRate
  B  IMU LateralAcc * g / GPS Speed  (= course rate if no slip) vs IMU YawRate
  C  IMU LateralAcc                                            vs GPS_LateralAcc  (accelerometer scale sanity)
  D  IMU RollRate                                              vs IMU YawRate     (yaw leaking into the roll axis =
                                                                                    a tilted mount; implied tilt and
                                                                                    the yaw amplitude loss it explains)
  E  turnInHeading50 recomputed with the gyro rescaled by the lap's scaleCgOverImu (same cgpose.rate_onset rule as
     build_join.py); the unscaled recomputation must reproduce event_table.json exactly (self-check).

All series are smoothed with the same 1.2 s boxcar the join uses for the IMU (CHORD_BOX_S) and compared where the
GPS speed is above 15 m/s; slopes are least squares through the origin at the best lag (search +-1 s).
Inputs: $JOIN_T1_WORK/xrk_native_<race>.npz (raw logger samples, not published), T1 events.json, T4 cg_events.json,
event_table.json + summary.json next to this file. Output: yaw_scale_check.json next to this file. No video/audio.
Run with a python that has numpy (e.g. pipeline/.venv-sr):  python yaw_scale_check.py
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
KT = HERE.parent
ROOT = Path(os.environ.get('REPLAYVIEWER_ROOT', HERE.parents[2]))
T1_WORK = Path(os.environ.get('JOIN_T1_WORK', ROOT.parent / '_work_events_2026-10-22' / 'T1-logger-events'))
sys.path.insert(0, str(KT / 'T4-cg-traces'))
import cgpose  # noqa: E402

SESS = {'fuji_aim_01': (1, 2, 3, 4), 'fuji_aim_2020_07_30': (1, 2, 3, 4, 5, 6)}
CHORD_BOX_S = 1.2
G0 = 9.80665
VMIN = 15.0
DTG = 0.01


def jload(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))


def r(v, nd=3):
    return None if v is None or not math.isfinite(float(v)) else round(float(v), nd)


def box(y, sec=CHORD_BOX_S):
    w = int(round(sec / DTG)) | 1
    return np.convolve(np.pad(y, w // 2, mode='edge'), np.ones(w) / w, mode='valid')


T1 = jload(KT / 'T1-logger-events/events.json')
T4 = jload(KT / 'T4-cg-traces/cg_events.json')
E = jload(HERE / 'event_table.json')
S = jload(HERE / 'summary.json')
XC = S['headingTimingWholeLap']


def fit(y, x, g, m, lagmax=1.0):
    """y ~ k * x(t + lag); best lag by correlation on the mask m; k = least squares through the origin."""
    best = None
    for L in np.arange(-lagmax, lagmax + 1e-9, DTG):
        xs = np.interp(g + L, g, x)
        c = float(np.corrcoef(xs[m], y[m])[0, 1])
        if best is None or c > best[0]:
            best = (c, float(L), xs)
    c, L, xs = best
    k = float(np.sum(xs[m] * y[m]) / np.sum(xs[m] ** 2))
    return dict(slope=r(k), stdRatio=r(np.std(y[m]) / np.std(xs[m])), r=r(c, 4), lagS=r(L, 2), lagAtSearchEdge=bool(abs(abs(L) - lagmax) < 1e-6))


out = dict(kind='yaw-rate amplitude scale check (open item OI-1)', generated='2026-10-22', tool='artifacts/kerb-event-truth-2026-10-22/join/yaw_scale_check.py',
           rule=__doc__.split('\n\n')[1].strip(), label='measured (logger samples) / derived (ratios)',
           inputs=dict(xrk=[f'$JOIN_T1_WORK/xrk_native_{s}.npz' for s in SESS], t1='T1-logger-events/events.json',
                       t4='T4-cg-traces/cg_events.json', join='join/event_table.json, join/summary.json'),
           perLap={})
for s, laps in SESS.items():
    z = np.load(T1_WORK / f'xrk_native_{s}.npz', allow_pickle=False)
    bias = T1['sessions'][s]['yawBiasDegS']
    for lap in laps:
        li = T1['sessions'][s]['laps'][str(lap)]
        g = np.arange(2.0, li['tEnd'] - 2.0, DTG)

        def ch(c):
            t = (z[c + '_t'] - li['tc0Ms']) / 1000.0 + li['shiftSeconds']
            return np.interp(g, t, z[c])
        yaw = box(ch('YawRate') - bias)
        roll = box(ch('RollRate'))
        ay = box(ch('LateralAcc'))
        v = box(ch('GPS Speed'))
        gyaw = box(ch('GPS_Yaw_Rate'))
        gay = box(ch('GPS_LateralAcc'))
        m = v > VMIN
        kin = np.degrees(ay * G0 / np.maximum(v, 1.0))
        d = fit(roll, yaw, g, m)
        tilt = math.degrees(math.asin(min(abs(d['slope']), 1.0)))
        d.update(impliedTiltDeg=r(tilt, 1), yawAmplitudeLossFromTiltPct=r(100 * (1 - math.cos(math.radians(tilt))), 1))
        out['perLap'][f'{s}/L{lap}'] = dict(
            drawnHeadingOverImu_join=XC[f'{s}/L{lap}']['scaleCgOverImu'],
            A_loggerGpsYawRateOverImu=fit(gyaw, yaw, g, m),
            B_latAccOverSpeedOverImu=fit(kin, yaw, g, m),
            C_imuLatAccOverGpsLatAcc=fit(ay, gay, g, m),
            D_rollRateOnYawRate=d)

# E: turn-in 50 % timing with the gyro rescaled (same rule and inputs as build_join.py TURN)
IMU = {}


def imu_yaw(s, lap, k=1.0):
    if (s, lap) not in IMU:
        zz = np.load(T1_WORK / f'xrk_native_{s}.npz', allow_pickle=False)
        li = T1['sessions'][s]['laps'][str(lap)]
        t = (zz['YawRate_t'] - li['tc0Ms']) / 1000.0 + li['shiftSeconds']
        mm = (t > -12) & (t < li['tEnd'] + 12)
        tt, yy = t[mm], zz['YawRate'][mm] - T1['sessions'][s]['yawBiasDegS']
        gg = np.arange(tt[0], tt[-1], DTG)
        yi = np.interp(gg, tt, yy)
        IMU[(s, lap)] = (gg, box(yi))
    gg, yb = IMU[(s, lap)]
    return gg, yb * k


REC = {(x['session'], x['lap'], x['kerb']): x for x in T1['records']}
rows, self_diff, flag_changes = [], [], []
for x in E['turnInAndTelemetryClip']:
    if not x['qualityGood']:
        continue
    s, lap, kk = x['session'], x['lap'], x['kerb']
    sgn = 1.0 if REC[(s, lap, kk)]['cornerDir'] == 'right' else -1.0
    t_ref = T4['sessions'][s]['laps'][str(lap)]['kerbs'][kk]['apex']['t']
    cg50 = x['cgDrawnYaw']['t50']
    g0, y0 = imu_yaw(s, lap)
    o0 = cgpose.rate_onset(g0, sgn * y0, t_ref)
    if o0 is None or o0.get('t50') is None:
        continue
    self_diff.append(abs((cg50 - o0['t50']) - x['dtHeading50']))
    k = XC[f'{s}/L{lap}']['scaleCgOverImu']
    g1, y1 = imu_yaw(s, lap, k)
    o1 = cgpose.rate_onset(g1, sgn * y1, t_ref) or {}
    if (bool(o1.get('weakCorner')) != bool(o0.get('weakCorner'))) or (o1.get('footStop') != o0.get('footStop')):
        flag_changes.append(f"{s}/L{lap} {kk}")
    if o1.get('t50') is None:
        continue
    rows.append(dict(session=s, dt0=x['dtHeading50'], dt0raw=cg50 - o0['t50'], dt1=cg50 - o1['t50']))


def st(a, nd=3):
    a = np.array(a, float)
    return dict(n=int(len(a)), median=r(np.median(a), nd), medianAbs=r(np.median(np.abs(a)), nd), p90Abs=r(np.percentile(np.abs(a), 90), nd)) if len(a) else dict(n=0)


sens = {}
for s in list(SESS) + ['pooled']:
    rr = [q for q in rows if s == 'pooled' or q['session'] == s]
    sens[s] = dict(asBuilt=st([q['dt0'] for q in rr]), gyroRescaled=st([q['dt1'] for q in rr]),
                   perEventChange=st([q['dt1'] - q['dt0raw'] for q in rr], 5),
                   maxAbsPerEventChangeS=r(max(abs(q['dt1'] - q['dt0raw']) for q in rr), 5) if rr else None)
out['E_turnIn50_gyroRescaled'] = dict(
    rule='dtHeading50 = CG drawn-heading t50 - IMU (1.2 s boxcar) t50 with cgpose.rate_onset; IMU multiplied by the lap scaleCgOverImu',
    selfCheck_unscaledReproducesJoin_maxAbsDiffS=r(max(self_diff), 6) if self_diff else None,
    onsetFlagChanges=flag_changes, bySession=sens)


# pooled ranges
def rng(key, sub='slope'):
    v = [d[key][sub] if isinstance(d[key], dict) else d[key] for d in out['perLap'].values()]
    return [min(v), max(v)]


summ = dict(drawnHeadingOverImu=rng('drawnHeadingOverImu_join'), loggerGpsYawRateOverImu=rng('A_loggerGpsYawRateOverImu'),
            latAccOverSpeedOverImu=rng('B_latAccOverSpeedOverImu'), imuLatAccOverGpsLatAcc=rng('C_imuLatAccOverGpsLatAcc'),
            rollRateOnYawRateSlope=rng('D_rollRateOnYawRate'), rollRateOnYawRateR=rng('D_rollRateOnYawRate', 'r'),
            impliedTiltDeg=rng('D_rollRateOnYawRate', 'impliedTiltDeg'),
            yawAmplitudeLossFromTiltPct=rng('D_rollRateOnYawRate', 'yawAmplitudeLossFromTiltPct'),
            gpsYawLagAtSearchEdge=[k for k, d in out['perLap'].items() if d['A_loggerGpsYawRateOverImu']['lagAtSearchEdge']])
_refs = ('drawnHeadingOverImu', 'loggerGpsYawRateOverImu', 'latAccOverSpeedOverImu')
summ['gyroLowByPct'] = [r(100 * (1 - 1 / min(summ[k][0] for k in _refs)), 1), r(100 * (1 - 1 / max(summ[k][1] for k in _refs)), 1)]
out['summary'] = summ
pe = sens['pooled']['perEventChange']
out['conclusion'] = (
    f"Two independent sensor paths (GPS course and accelerometer / speed) agree that the gyro yaw channel reads low: the logger's own GPS yaw rate is "
    f"{summ['loggerGpsYawRateOverImu'][0]}-{summ['loggerGpsYawRateOverImu'][1]}x, lateral acceleration / GPS speed "
    f"{summ['latAccOverSpeedOverImu'][0]}-{summ['latAccOverSpeedOverImu'][1]}x and the drawn heading rate "
    f"{summ['drawnHeadingOverImu'][0]}-{summ['drawnHeadingOverImu'][1]}x the gyro, on every lap of both days, while the "
    f"accelerometer agrees with the GPS lateral acceleration ({summ['imuLatAccOverGpsLatAcc'][0]}-{summ['imuLatAccOverGpsLatAcc'][1]}x). "
    f"So the gyro amplitude is ~{summ['gyroLowByPct'][0]}-{summ['gyroLowByPct'][1]} % low and the drawn heading is consistent with GPS and "
    f"accelerometer. Yaw leaks into the roll gyro (slope {summ['rollRateOnYawRateSlope'][0]}-{summ['rollRateOnYawRateSlope'][1]}, "
    f"r {summ['rollRateOnYawRateR'][0]}-{summ['rollRateOnYawRateR'][1]}), consistent with a ~{summ['impliedTiltDeg'][0]}-{summ['impliedTiltDeg'][1]} deg "
    f"mount tilt, which explains only {summ['yawAmplitudeLossFromTiltPct'][0]}-{summ['yawAmplitudeLossFromTiltPct'][1]} % of the amplitude loss; "
    f"the rest (a gyro scale factor / configuration) is not identified. Timing is unaffected: with the gyro rescaled the turn-in "
    f"50 % difference changes by median {pe.get('median')} s (max {sens['pooled']['maxAbsPerEventChangeS']} s, n {pe.get('n')}); "
    f"onset-flag changes: {len(flag_changes)}.")
(HERE / 'yaw_scale_check.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
print(json.dumps(dict(summary=summ, E=out['E_turnIn50_gyroRescaled'], conclusion=out['conclusion']), ensure_ascii=False, indent=1))
