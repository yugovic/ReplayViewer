"""JOIN step - ground-truth events (onboard video + logger) vs the 3DCG replay.

Builds event_table.json and summary.json in this folder from the four extraction tracks:
  T1-logger-events/events.json          logger truth: kerb-contact onsets (IMU), steering / yaw onsets, clip markers
  T2-video-audio-events/events_video.json  video<->logger offset per lap (video yaw, kerb audio/shake, data track)
  T3-video-kerb-passages/passages.json  video clipping-point (tip) and kerb-end events + the replay's prediction
  T4-cg-traces/cg_events.json + cgpose  what the 3DCG draws (viewer pose re-run), kerb gaps at any instant
and two read-only repository inputs: the shipped lap JSON (accx, delta) and, for the turn-in
comparison, the native XRK yaw rate that T1 extracted into its work folder ($JOIN_T1_WORK,
default <repo>/../_work_events_2026-10-22/T1-logger-events/xrk_native_<race>.npz).

Run with a python that has numpy + scipy (e.g. pipeline/.venv-sr):  python build_join.py
No video or audio is opened here. Nothing outside this folder is written.

Review loop 2026-10-22 round 1 additions (existing outputs unchanged): detection.<session>.tolerance (R1-04),
alongTrack.contactFirstBlock.conditionalOnCgContact + counts.kerbContactsPrimary*BySession (R1-10), openItems (R1-09,
reads yaw_scale_check.json when present). Order: build_join.py -> yaw_scale_check.py -> build_join.py -> verify_join.py.

Sign conventions (all discrepancies are "3DCG minus truth"):
  dt      = t_CG(event) - t_truth(event)  [s]; negative = the 3DCG shows the event EARLIER
  dS      = -dt * v                       [m]; along-track position of the 3DCG minus the true car at
            the same instant; positive = 3DCG AHEAD of the real car
  dLat    = CG distance-to-kerb - true distance-to-kerb [m]; positive = the 3DCG shows the car
            FARTHER from the kerb (more towards the track centre) than it really is.
            For a kerb contact the true distance at the truth onset is 0, so dLat = CG gap at that instant
            (positive gap = the 3DCG shows the car NOT yet on the kerb when it really is).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from collections import defaultdict
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
SLABEL = {'fuji_aim_01': '2020-07-29 wet', 'fuji_aim_2020_07_30': '2020-07-30 dry'}
SHORT = {'fuji_aim_01': '0729', 'fuji_aim_2020_07_30': '0730'}
SUSPECT_T3 = {'fuji_aim_01': set(), 'fuji_aim_2020_07_30': {'k13', 'kDunlopR'}}   # T3 feature-mismatch kerbs
CHORD_BOX_S = 1.2      # the viewer's chord heading is a +-0.6 s chord -> ~1.2 s boxcar on the yaw rate
CAMS = {'nominal': 'f967_h1.26', 'fh889': 'f711_h1.25', 'fh1066': 'f711_h1.50', 'fh1209': 'f967_h1.25',
        'fh1450': 'f967_h1.50', 'fh590_profileLike': 'f472_h1.25'}
FH = {'f967_h1.26': 1218, 'f711_h1.25': 889, 'f711_h1.50': 1066, 'f967_h1.25': 1209, 'f967_h1.50': 1450,
      'f472_h1.25': 590, 'f472_h1.00': 472, 'f472_h1.50': 708, 'f711_h1.00': 711, 'f967_h1.00': 967}


def jload(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def r(v, nd=3):
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return v
    return None if not math.isfinite(v) else round(v, nd)


def stats(vals, nd=3):
    a = np.array([v for v in vals if v is not None and math.isfinite(v)], float)
    if not len(a):
        return {'n': 0}
    ab = np.abs(a)
    return {'n': int(len(a)), 'median': r(np.median(a), nd), 'mean': r(a.mean(), nd), 'sd': r(a.std(ddof=1) if len(a) > 1 else 0.0, nd),
            'q25': r(np.percentile(a, 25), nd), 'q75': r(np.percentile(a, 75), nd),
            'medianAbs': r(np.median(ab), nd), 'p90Abs': r(np.percentile(ab, 90), nd), 'maxAbs': r(ab.max(), nd),
            'rms': r(math.sqrt(float(np.mean(a * a))), nd),
            'biasShare': r(a.mean() ** 2 / max(float(np.mean(a * a)), 1e-12), 3)}


def decompose(rows, key, group='kerb'):
    """Systematic vs random: overall bias, between-group (kerb) spread of medians, within-group spread."""
    vals = [(x[group], x[key]) for x in rows if x.get(key) is not None and math.isfinite(x[key])]
    if len(vals) < 3:
        return {'n': len(vals)}
    a = np.array([v for _, v in vals])
    g = defaultdict(list)
    for k, v in vals:
        g[k].append(v)
    med = {k: float(np.median(v)) for k, v in g.items()}
    within = [v - med[k] for k, v in vals if len(g[k]) >= 2]
    between = [m for k, m in med.items() if len(g[k]) >= 2]
    rms_all = math.sqrt(float(np.mean(a * a)))
    bias = float(np.median(a))
    return {'n': len(a), 'groups': len(g), 'overallMedian': r(bias), 'rmsAll': r(rms_all),
            'rmsAfterRemovingOverallMedian': r(math.sqrt(float(np.mean((a - bias) ** 2)))),
            'sdOfGroupMedians': r(np.std(between, ddof=1) if len(between) > 1 else None),
            'rmsWithinGroup': r(math.sqrt(float(np.mean(np.square(within)))) if within else None),
            'groupMedians': {k: r(v) for k, v in sorted(med.items(), key=lambda kv: -abs(kv[1]))}}


# ------------------------------------------------------------------ inputs
P = {
    'T1': KT / 'T1-logger-events/events.json',
    'T2': KT / 'T2-video-audio-events/events_video.json',
    'T3': KT / 'T3-video-kerb-passages/passages.json',
    'T3profile': KT / 'T3-video-kerb-passages/profile_camera.json',
    'T4': KT / 'T4-cg-traces/cg_events.json',
}
T1, T2, T3, T4 = (jload(P[k]) for k in ('T1', 'T2', 'T3', 'T4'))
T3P = jload(P['T3profile'])
LAPS = {}
for s, laps in SESS.items():
    for lap in laps:
        p = ROOT / f'public/data/races/{s}/osaki_hmr_demio_101_lap_{lap:03d}.json'
        d = jload(p)
        LAPS[(s, lap)] = dict(t=np.array(d['t'], float), accx=np.array(d['accx'], float),
                              delta=float(d['meta']['first_sample_after_lap_start_seconds']), sha=sha(p),
                              path=str(p.relative_to(ROOT)).replace('\\', '/'))
NAMES = {c['kerb']: c['name'] for c in T1['corners']}
SIDE = {c['kerb']: c['side'] for c in T1['corners']}
REC = {(x['session'], x['lap'], x['kerb']): x for x in T1['records']}


def cgk(s, lap, k):
    return T4['sessions'][s]['laps'][str(lap)]['kerbs'][k]


def delta(s, lap):
    return T4['sessions'][s]['laps'][str(lap)]['deltaSeconds']


# ------------------------------------------------------------------ IMU yaw (native XRK via T1 work folder)
IMU = {}


def imu_yaw(s, lap):
    if (s, lap) in IMU:
        return IMU[(s, lap)]
    z = np.load(T1_WORK / f'xrk_native_{s}.npz', allow_pickle=False)
    li = T1['sessions'][s]['laps'][str(lap)]
    t = (z['YawRate_t'] - li['tc0Ms']) / 1000.0 + li['shiftSeconds']
    m = (t > -12) & (t < li['tEnd'] + 12)
    tt, yy = t[m], z['YawRate'][m] - T1['sessions'][s]['yawBiasDegS']
    g = np.arange(tt[0], tt[-1], 0.01)
    yi = np.interp(g, tt, yy)
    w = int(round(CHORD_BOX_S / 0.01)) | 1
    yb = np.convolve(np.pad(yi, w // 2, mode='edge'), np.ones(w) / w, mode='valid')
    IMU[(s, lap)] = (g, yi, yb)
    return IMU[(s, lap)]


# ================================================================== 1. TIME BASE
cal = T2['dataTrackCalibration']
cal_per = cal['perLapS']             # 7/30 laps 1..6
cal_mean = float(np.mean(cal_per))
LOFF = {}
for s, laps in SESS.items():
    po = T2['sessions'][s]['perLapOffsets']
    for lap in laps:
        p = po[str(lap)]
        est = {e['method']: e for e in p['estimates']}
        yaw = est.get('transientVideoYawVsGyro', {}).get('lagS')
        if s == 'fuji_aim_2020_07_30':
            cal_lolo = float(np.mean([c for i, c in enumerate(cal_per) if i != lap - 1]))
        else:
            cal_lolo = cal_mean
        LOFF[(s, lap)] = dict(
            adopted=p['bestOffsetS'], adoptedBasis=p['bestBasis'], adoptedLabel=p['bestLabel'],
            videoYaw=yaw, dataTrack=r(p['dataTrackLoggerClockDtS'] + cal_mean),
            dataTrackLOLO=r(p['dataTrackLoggerClockDtS'] + cal_lolo),
            kerbAudio=est.get('kerbAudioOnsets', {}).get('lagS'), kerbShake=est.get('kerbShakeOnsets', {}).get('lagS'),
            lapStartPtsOverlay=p['lapStartPtsOverlay'])
for s, laps in SESS.items():
    for lap in laps:
        others = [LOFF[(s, l)]['adopted'] for l in laps if l != lap]
        LOFF[(s, lap)]['sessionConstLOLO'] = r(np.median(others))
    LOFF[(s, 'sessionMedian')] = r(np.median([LOFF[(s, l)]['adopted'] for l in laps]))

# all T1 contacts (inside passes + otherContacts)
T1C = []
for x in T1['records']:
    for c in x['kerbContacts']:
        T1C.append(dict(session=x['session'], lap=x['lap'], passKerb=x['kerb'], c=c, inside=True))
for c in T1['otherContacts']:
    T1C.append(dict(session=c['session'], lap=c['lap'], passKerb=None, c=c, inside=False))


def t2_match(s, lap, c):
    rr = c.get('rrEvent')
    if not rr:
        return None
    for e in T2['sessions'][s]['kerbEvents']:
        if e['lap'] == lap and abs(e['imuT0Published'] - rr['t0']) <= 0.011:
            return e
    return None


time_events = []
for item in T1C:
    s, lap, c = item['session'], item['lap'], item['c']
    e = t2_match(s, lap, c)
    if e is None:
        continue
    Lo = LOFF[(s, lap)]
    au, sh, iv = e['audio'], e['shake'], e['imuVibration']
    row = dict(session=s, lap=lap, kerb=e['kerb'], insidePass=item['inside'], t1Confidence=c['confidence'],
               t1OnsetT=r(c['onsetT']), t1OnsetVA=r((c.get('VA') or {}).get('onsetT')), t1OnsetRR=r((c.get('RR') or {}).get('onsetT')),
               t2ImuOnset50T=r(iv['onset50LapT']), t2ImuStartT=r(iv['startLapT']),
               audioConfidence=au['confidence'], audioOnset50OverlayT=r(au['onset50LapTOverlay']), audioStartOverlayT=r(au['startLapTOverlay']),
               shakeConfidence=sh['confidence'], shakeOnset50OverlayT=r(sh['onset50LapTOverlay']), shakeStartOverlayT=r(sh['startLapTOverlay']),
               L=dict(adopted=Lo['adopted'], dataTrackLOLO=Lo['dataTrackLOLO'], sessionConstLOLO=Lo['sessionConstLOLO']))
    res = {}
    for name, Lv in (('none', 0.0), ('adopted', Lo['adopted']), ('dataTrackLOLO', Lo['dataTrackLOLO']),
                     ('sessionConstLOLO', Lo['sessionConstLOLO'])):
        d = {}
        if au['onset50LapTOverlay'] is not None:
            d['t2ImuOnset50_minus_audioOnset50'] = r(iv['onset50LapT'] - (au['onset50LapTOverlay'] + Lv))
            d['t1Onset_minus_audioOnset50'] = r(c['onsetT'] - (au['onset50LapTOverlay'] + Lv))
        if au['startLapTOverlay'] is not None:
            d['t1Onset_minus_audioStart'] = r(c['onsetT'] - (au['startLapTOverlay'] + Lv))
        if sh['onset50LapTOverlay'] is not None:
            d['t2ImuOnset50_minus_shakeOnset50'] = r(iv['onset50LapT'] - (sh['onset50LapTOverlay'] + Lv))
            d['t1Onset_minus_shakeOnset50'] = r(c['onsetT'] - (sh['onset50LapTOverlay'] + Lv))
        if sh['startLapTOverlay'] is not None:
            d['t1Onset_minus_shakeStart'] = r(c['onsetT'] - (sh['startLapTOverlay'] + Lv))
        res[name] = d
    row['residualS'] = res
    time_events.append(row)


def tb_summary(events, conf_key, confs, metric, variant):
    return stats([x['residualS'][variant].get(metric) for x in events if x[conf_key] in confs])


timebase = {'perLap': {}, 'kerbEventAgreement': {}, 'T3perLapCommonOffset': {}}
for s, laps in SESS.items():
    timebase['perLap'][s] = {str(l): {k: v for k, v in LOFF[(s, l)].items()} for l in laps}
    timebase['perLap'][s]['sessionMedianAdopted'] = LOFF[(s, 'sessionMedian')]
    ev = [x for x in time_events if x['session'] == s]
    agg = {}
    for variant in ('none', 'adopted', 'dataTrackLOLO', 'sessionConstLOLO'):
        agg[variant] = {
            'sameRule_t2ImuOnset50_minus_audioOnset50': {
                'audioHigh': tb_summary(ev, 'audioConfidence', ('high',), 't2ImuOnset50_minus_audioOnset50', variant),
                'audioHighMedium': tb_summary(ev, 'audioConfidence', ('high', 'medium'), 't2ImuOnset50_minus_audioOnset50', variant)},
            't1RefinedOnset_minus_audioStart': {
                'audioHighMedium': tb_summary(ev, 'audioConfidence', ('high', 'medium'), 't1Onset_minus_audioStart', variant)},
            't1RefinedOnset_minus_audioOnset50': {
                'audioHighMedium': tb_summary(ev, 'audioConfidence', ('high', 'medium'), 't1Onset_minus_audioOnset50', variant)},
            'sameRule_t2ImuOnset50_minus_shakeOnset50': {
                'shakeHighMedium': tb_summary(ev, 'shakeConfidence', ('high', 'medium'), 't2ImuOnset50_minus_shakeOnset50', variant)},
        }
    timebase['kerbEventAgreement'][s] = {'nMatchedEvents': len(ev), 'byOffsetVariant': agg}

# ================================================================== 3a. CLIPPING POINT (video tip) - built early (T3 per-lap check)
TIPS = []
for p in T3['passes']:
    s, lap, k = p['race'], p['lap'], p['kerb']
    row276 = p['rows']['276']
    tip = row276.get('nominal', {}).get('tip')
    if not (tip and tip['valid'] and p['tipRowConsistent']):
        continue
    Lo = LOFF[(s, lap)]
    assert abs(Lo['lapStartPtsOverlay'] - p['lapStartPts']) < 1e-6
    dl = LAPS[(s, lap)]['delta']
    v_lc = row276['videoTip']['lapTime']                       # overlay-mapped lap clock
    truth_lc = v_lc + Lo['adopted']
    truth_t = truth_lc - dl
    la = LAPS[(s, lap)]
    msk = (la['t'] >= truth_t - 0.25) & (la['t'] <= truth_t + 0.25)
    accx = float(np.mean(la['accx'][msk])) if msk.any() else None
    x = REC[(s, lap, k)]
    ce = cgk(s, lap, k)
    v = tip['speed']
    dt_nom = -(tip['dt'] + Lo['adopted'])
    cams = {}
    for lab, ck in CAMS.items():
        sv = p['sensitivity'].get(ck)
        if sv and sv.get('tipDt') is not None:
            dtc = -(sv['tipDt'] + Lo['adopted'])
            cams[lab] = dict(camera=ck, fTimesH=FH.get(ck), dt=r(dtc), dS=r(-dtc * v, 2), dLat=r(-sv['dDistance'], 2) if sv.get('dDistance') is not None else None)
    prof = None
    for pp in T3P.get('passes', []):
        if pp.get('race') == s and pp.get('lap') == lap and pp.get('kerb') == k:
            prof = pp
    marks = x['keyTimes']
    row = dict(session=s, lap=lap, kerb=k, name=NAMES[k], side=SIDE[k], suspectFeature=k in SUSPECT_T3[s],
               videoTipLapClockOverlay=r(v_lc), L=Lo['adopted'], LBasis=Lo['adoptedBasis'], LLabel=Lo['adoptedLabel'],
               truthTipLapClock=r(truth_lc), truthTipT=r(truth_t),
               cgPredTipLapClock=r(tip['replayLapTime']), speedMps=r(v, 2), accxG=r(accx),
               dt=r(dt_nom), dS=r(-dt_nom * v, 2), dtOverlayOnly=r(-tip['dt']), dSOverlayOnly=r(tip['dt'] * v, 2),
               videoForwardDistanceM=tip['videoD'], videoDistanceM=tip['videoDistance'], cgDistanceM=tip['replayDistance'],
               dLat=r(-tip['dDistance'], 2), cameraVariants=cams,
               telemetry=dict(yawRatePeakT=marks['yawRatePeak'], latGPeakT=marks['latGPeak'], minSpeedT=marks['minSpeed'], steerMaxT=marks['steerMax'],
                              truthTipMinusYawRatePeak=r(truth_t - marks['yawRatePeak']) if marks['yawRatePeak'] is not None else None,
                              truthTipMinusMinSpeed=r(truth_t - marks['minSpeed']) if marks['minSpeed'] is not None else None),
               cgApexT=ce['apex']['t'], cgApexGapBlockFront=ce['apex']['gBlockFront'],
               label=dict(dt='derived (video pixel event + T2 offset; absolute value depends on the estimated camera)',
                          dLat='estimated (camera height / mount offset)'))
    TIPS.append(row)

# per-lap common offsets of the tip timing before / after the video offset
for s, laps in SESS.items():
    rows = [x for x in TIPS if x['session'] == s and not x['suspectFeature']]
    out = {}
    for variant, key in (('overlayOnly', 'dtOverlayOnly'), ('withAdoptedL', 'dt')):
        kmed = defaultdict(list)
        for x in rows:
            kmed[x['kerb']].append(x[key])
        kmed = {k: float(np.median(v)) for k, v in kmed.items()}
        per = {}
        for lap in laps:
            dev = [x[key] - kmed[x['kerb']] for x in rows if x['lap'] == lap and len([y for y in rows if y['kerb'] == x['kerb']]) >= 2]
            per[str(lap)] = r(np.median(dev)) if dev else None
        vals = [v for v in per.values() if v is not None]
        out[variant] = {'perLapMedianDeviationS': per, 'sdAcrossLaps': r(np.std(vals, ddof=1)) if len(vals) > 1 else None}
    timebase['T3perLapCommonOffset'][s] = out

# ================================================================== 2. KERB CONTACT
def runs(tg, gap):
    on = np.isfinite(gap) & (gap <= 0)
    out = []
    i = 0
    n = len(on)
    while i < n:
        if on[i]:
            j = i
            while j + 1 < n and on[j + 1]:
                j += 1
            def cross(a, b):
                ga, gb = gap[a], gap[b]
                if not (math.isfinite(ga) and math.isfinite(gb)) or ga == gb:
                    return tg[b]
                u = ga / (ga - gb)
                return tg[a] + min(max(u, 0.0), 1.0) * (tg[b] - tg[a])
            t0 = cross(i - 1, i) if i > 0 else tg[i]
            t1 = cross(j, j + 1) if j + 1 < n else tg[j]
            out.append((float(t0), float(t1), float(np.nanmin(gap[i:j + 1]))))
            i = j + 1
        else:
            i += 1
    return out


def cg_series(s, lap, k, tw):
    dl = delta(s, lap)
    tg = np.arange(tw[0], tw[1], 0.01)
    st = cgpose.cg_states(s, lap, tg + dl, kerbs=[k])
    kk = st['kerbs'][k]
    return tg, kk, st


CONTACTS = []
for x in T1['records']:
    s, lap, k = x['session'], x['lap'], x['kerb']
    if not x['kerbContacts']:
        continue
    ce = cgk(s, lap, k)
    tw = (ce['pass']['tStart'] - 2.0, ce['pass']['tEnd'] + 2.0)
    tg, kk, st = cg_series(s, lap, k, tw)
    for c in x['kerbContacts']:
        t0, t1, tp = c['onsetT'], c['offsetT'], c['peakT']
        row = dict(session=s, lap=lap, kerb=k, name=NAMES[k], side=SIDE[k], confidence=c['confidence'],
                   respondingChannels=c['respondingChannels'], truthOnsetT=r(t0), truthOffsetT=r(t1), truthPeakT=r(tp),
                   truthOnsetLapClock=r(t0 + delta(s, lap)), kerbAtOnset=c.get('kerbAtOnset'),
                   t1Flags=c['flags'],
                   steeringAway=any('steeringAway' in f for f in c['flags']),
                   offsetReliable=not any(f in c['flags'] for f in ('offsetAtSearchLimit', 'offsetFromDetector', 'offsetBoundedByNextContact')),
                   onsetFromDetector='onsetFromDetector' in c['flags'],
                   publishedPassLabel=(x.get('publishedPassLabel') or {}).get('cls'))
        e = t2_match(s, lap, c)
        L = LOFF[(s, lap)]['adopted']
        if e is not None:
            au, sh = e['audio'], e['shake']
            row['videoConfirmation'] = dict(
                audioConfidence=au['confidence'], shakeConfidence=sh['confidence'],
                audioOnset50TrueT=r(au['onset50LapTOverlay'] + L) if au['onset50LapTOverlay'] is not None else None,
                shakeOnset50TrueT=r(sh['onset50LapTOverlay'] + L) if sh['onset50LapTOverlay'] is not None else None,
                t1OnsetMinusAudioStartTrue=r(t0 - (au['startLapTOverlay'] + L)) if au['startLapTOverlay'] is not None else None,
                sameRuleImuOnset50MinusAudioOnset50True=r(e['imuVibration']['onset50LapT'] - (au['onset50LapTOverlay'] + L)) if au['onset50LapTOverlay'] is not None else None,
                confirmed=au['confidence'] in ('high', 'medium') or sh['confidence'] in ('high', 'medium'))
        else:
            row['videoConfirmation'] = None
        dl = delta(s, lap)
        one = cgpose.cg_states(s, lap, [t0 + dl, tp + dl, t1 + dl, t0 + dl - 0.05, t0 + dl + 0.05], kerbs=[k])
        g1 = one['kerbs'][k]
        v = float(one['speedMps'][0])
        row['cgSpeedMpsAtOnset'] = r(v, 2)
        for line in ('Block', 'Paint'):
            gN = g1[f'g{line}Nearest']
            gF, gR = g1[f'g{line}Front'], g1[f'g{line}Rear']
            closing = -(gN[4] - gN[3]) / 0.10 if (math.isfinite(gN[3]) and math.isfinite(gN[4])) else None
            ser = kk[f'g{line}Nearest']
            mtruth = (tg >= t0) & (tg <= t1)
            mwide = (tg >= t0 - 0.5) & (tg <= t1 + 0.5)
            R = runs(tg, ser)
            match = None
            ov = [(min(b, t1) - max(a, t0), (a, b, gm)) for a, b, gm in R if min(b, t1) - max(a, t0) > 0]
            if ov:
                match = max(ov)[1]
            else:
                near = [(abs(a - t0), (a, b, gm)) for a, b, gm in R if abs(a - t0) <= 1.0]
                if near:
                    match = min(near)[1]
            d = dict(gapAtOnset=r(gN[0]), gapAtOnsetFront=r(gF[0]), gapAtOnsetRear=r(gR[0]), gapAtPeak=r(gN[1]), gapAtOffset=r(gN[2]),
                     closingRateMps=r(closing), lineDefinedAtOnset=bool(math.isfinite(gN[0])),
                     cgMinGapDuringTruthContact=r(np.nanmin(ser[mtruth])) if np.isfinite(ser[mtruth]).any() else None,
                     cgMinGapNearTruthContact=r(np.nanmin(ser[mwide])) if np.isfinite(ser[mwide]).any() else None,
                     cgRuns=[[r(a), r(b), r(gm)] for a, b, gm in R])
            if match:
                a, b, gm = match
                d['cgContactStartT'] = r(a)
                d['cgContactEndT'] = r(b)
                d['dtFirst'] = r(a - t0)
                d['dSFirst'] = r(-(a - t0) * v, 2)
                d['dtLast'] = r(b - t1)
                d['dtMid'] = r((a + b) / 2 - (t0 + t1) / 2)
                d['dSMid'] = r(-((a + b) / 2 - (t0 + t1) / 2) * v, 2)
                d['cgOnSeconds'] = r(b - a)
                d['truthOnSeconds'] = r(t1 - t0)
                d['cgShowsContact'] = True
            else:
                d['cgShowsContact'] = False
            row[line.lower()] = d
        row['primary'] = bool(c['confidence'] in ('high', 'medium') and not row['steeringAway'] and row['block']['lineDefinedAtOnset'])
        CONTACTS.append(row)

# pass-level detection (truth vibration vs CG on the block / paint line)
PASSES = []
for x in T1['records']:
    s, lap, k = x['session'], x['lap'], x['kerb']
    ce = cgk(s, lap, k)
    confs = [c['confidence'] for c in x['kerbContacts']]
    truth_hm = any(c in ('high', 'medium') for c in confs)
    truth_any = bool(confs)
    gmin_b = min(ce['minOverPass']['gBlockFront'], ce['minOverPass']['gBlockRear'])
    gmin_p = min(ce['minOverPass']['gPaintFront'], ce['minOverPass']['gPaintRear'])
    cgB = ce['block']['nearestTyre'] is not None
    cgP = ce['paint']['nearestTyre'] is not None

    def cls(t, c):
        return 'TP' if (t and c) else 'FN' if t else 'FP' if c else 'TN'
    PASSES.append(dict(session=s, lap=lap, kerb=k, name=NAMES[k], side=SIDE[k],
                       truthContactHighMedium=truth_hm, truthContactAny=truth_any, truthConfidences=confs,
                       publishedPassLabel=(x.get('publishedPassLabel') or {}).get('cls'),
                       cgBlockContact=cgB, cgPaintContact=cgP, cgMinGapBlock=r(gmin_b), cgMinGapPaint=r(gmin_p),
                       classBlock=cls(truth_hm, cgB), classPaint=cls(truth_hm, cgP)))


def confusion(rows, key):
    c = defaultdict(int)
    for x in rows:
        c[x[key]] += 1
    tp, fn, fp, tn = c['TP'], c['FN'], c['FP'], c['TN']
    return dict(TP=tp, FN=fn, FP=fp, TN=tn, n=len(rows), agreement=r((tp + tn) / max(len(rows), 1)),
                recall=r(tp / (tp + fn)) if tp + fn else None, precision=r(tp / (tp + fp)) if tp + fp else None)


# ================================================================== 3b. kerb ends (video) and telemetry clipping markers
ENDS = []
for p in T3['passes']:
    s, lap, k = p['race'], p['lap'], p['kerb']
    row276 = p['rows']['276']
    for ev, vk in (('redOn', 'videoRedOn'), ('redOff', 'videoRedOff')):
        e = row276.get('nominal', {}).get(ev)
        if not (e and e['valid'] and row276.get(vk)):
            continue
        L = LOFF[(s, lap)]['adopted']
        dt = -(e['dt'] + L)
        ENDS.append(dict(session=s, lap=lap, kerb=k, name=NAMES[k], side=SIDE[k], event=ev, suspectFeature=k in SUSPECT_T3[s],
                         videoLapClockOverlay=row276[vk]['lapTime'], L=L, truthLapClock=r(row276[vk]['lapTime'] + L),
                         cgPredLapClock=e['replayLapTime'], speedMps=e['speed'], dt=r(dt), dS=r(-dt * e['speed'], 2),
                         dtOverlayOnly=r(-e['dt']), label='derived (camera-dependent absolute)'))

# ================================================================== 4. TURN-IN + telemetry clip (like-for-like rate onsets)
TURN = []
for x in T1['records']:
    s, lap, k = x['session'], x['lap'], x['kerb']
    ce = cgk(s, lap, k)
    sgn_imu = 1.0 if x['cornerDir'] == 'right' else -1.0
    t_ref = ce['apex']['t']
    g, yi, yb = imu_yaw(s, lap)
    raw = cgpose.rate_onset(g, sgn_imu * yi, t_ref)
    chd = cgpose.rate_onset(g, sgn_imu * yb, t_ref)
    cg = ce['turnIn']['drawnYaw']
    tr = cgpose.cg_trace(s, lap)

    def v_at(t):
        return float(np.interp(t, tr['t'], tr['speedMps'])) if t is not None else None

    def dd(a, b):
        return None if a is None or b is None else a - b
    row = dict(session=s, lap=lap, kerb=k, name=NAMES[k], side=SIDE[k], cornerCase=x['steer']['case'], t1Flags=x['flags'],
               truth=dict(steerOnsetT=x['steer']['onsetT'], steerRampOnsetT=x['steer']['rampOnsetT'], yawOnsetT_T1rule=x['yaw']['onsetT'],
                          brakeOnsetT=x['brake']['onset']),
               imuYawSameRule=dict(raw={kk: r(raw.get(kk)) for kk in ('t10', 't25', 't50', 'tLinear0', 'tPeak')} | {'weak': raw.get('weakCorner'), 'footStop': raw.get('footStop')},
                                   chordMatched={kk: r(chd.get(kk)) for kk in ('t10', 't25', 't50', 'tLinear0', 'tPeak')} | {'weak': chd.get('weakCorner'), 'footStop': chd.get('footStop')}),
               cgDrawnYaw={kk: cg.get(kk) for kk in ('t10', 't25', 't50', 'tLinear0', 'tPeak', 'quality')})
    q = (cg.get('quality') == 'good' and not chd.get('weakCorner') and chd.get('footStop') != 'search-limit' and chd.get('t50') is not None)
    row['qualityGood'] = bool(q)
    d50 = dd(cg.get('t50'), chd.get('t50'))
    row['dtHeading50'] = r(d50)
    row['dSHeading50'] = r(-d50 * v_at(chd.get('t50')), 2) if d50 is not None else None
    dl0 = dd(cg.get('tLinear0'), chd.get('tLinear0'))
    row['dtHeadingLinear0'] = r(dl0)
    dpk = dd(cg.get('tPeak'), chd.get('tPeak'))
    row['dtYawRatePeak'] = r(dpk)
    # what the viewer shows vs the driver's input: visible CG heading-rate onset (raw IMU definitions for the truth)
    so = x['steer']['onsetT']
    row['cgLinear0MinusSteerOnset'] = r(dd(cg.get('tLinear0'), so))
    row['cgT10MinusSteerOnset'] = r(dd(cg.get('t10'), so))
    row['imuRawLinear0MinusSteerOnset'] = r(dd(raw.get('tLinear0'), so))
    row['cgLinear0MinusImuRawLinear0'] = r(dd(cg.get('tLinear0'), raw.get('tLinear0')))
    row['dSVisibleVsImuRawLinear0'] = r(-row['cgLinear0MinusImuRawLinear0'] * v_at(raw.get('tLinear0')), 2) if row['cgLinear0MinusImuRawLinear0'] is not None else None
    # visible turn-in: CG t10 (drawn heading) vs the unsmoothed IMU yaw t10, same rule; quality-gated
    rawok = (raw.get('t10') is not None and not raw.get('weakCorner') and raw.get('footStop') != 'search-limit')
    d10 = dd(cg.get('t10'), raw.get('t10'))
    row['visibleQualityGood'] = bool(q and rawok and d10 is not None and abs(d10) < 1.5)
    row['cgT10MinusImuRawT10'] = r(d10)
    row['dSVisibleT10'] = r(-d10 * v_at(raw.get('t10')), 2) if d10 is not None else None
    row['imuRawT10MinusSteerOnset'] = r(dd(raw.get('t10'), so))
    row['cgT10MinusImuRawT50'] = r(dd(cg.get('t10'), raw.get('t10')))
    # T2 video turn-in (cross-check), matched by nearest kerb and time
    vt = None
    for e in T2['sessions'][s]['turnInPerLap'].get(str(lap), []):
        if e.get('nearestKerb') == k and e.get('videoUsable') and e.get('videoOnsetLapTOverlay') is not None \
                and x['yaw']['onsetT'] is not None and abs(e['gyroOnsetLapT'] - x['yaw']['onsetT']) < 1.5:
            vt = e
    if vt is not None:
        Lv = LOFF[(s, lap)]['adopted']
        vtrue = vt['videoOnsetLapTOverlay'] + Lv
        row['videoTurnIn'] = dict(videoOnsetTrueT=r(vtrue), t2GyroOnsetT=vt['gyroOnsetLapT'],
                                  videoMinusT2Gyro=r(vtrue - vt['gyroOnsetLapT']),
                                  videoMinusT1YawOnset=r(vtrue - x['yaw']['onsetT']),
                                  videoMinusSteerOnset=r(vtrue - so) if so is not None else None,
                                  label='derived; T2 says single-corner video onsets fire ~0.06-0.08 s early (estimator bias)')
    else:
        row['videoTurnIn'] = None
    # telemetry clipping marker, like for like: CG speed minimum (position-derived) vs non-driven rear wheels
    ms = x['clip']['minSpeedRearWheels']
    w0, w1 = x['clip']['minSpeedWindow']
    m = (tr['t'] >= w0) & (tr['t'] <= w1)
    if m.sum() > 10 and ms and ms.get('t') is not None:
        sp = tr['speedMps'][m].astype(float)
        w = 31
        sps = np.convolve(np.pad(sp, w // 2, mode='edge'), np.ones(w) / w, mode='valid')
        kmin = int(np.argmin(sps))
        if 0 < kmin < len(sps) - 1:
            a, b, c3 = sps[kmin - 1], sps[kmin], sps[kmin + 1]
            den = a - 2 * b + c3
            off = 0.5 * (a - c3) / den if den != 0 else 0.0
            tmin = float(tr['t'][m][kmin] + off * 0.01)
            row['cgMinSpeedT'] = r(tmin)
            row['dtMinSpeed'] = r(tmin - ms['t'])
            row['minSpeedAtWindowEdge'] = bool(ms.get('atWindowEdge'))
    TURN.append(row)

# whole-lap timing of the drawn heading vs the IMU gyro (chord-matched), parabolic xcorr peak
XCORR = {}
for s, laps in SESS.items():
    for lap in laps:
        tr = cgpose.cg_trace(s, lap)
        g, yi, yb = imu_yaw(s, lap)
        gg = np.arange(tr['t'][0] + 2.0, tr['t'][-1] - 2.0, 0.01)
        cgr = -np.interp(gg, tr['t'], tr['yawRateDrawnDegS'].astype(float))   # viewer yaw +left; IMU +right
        lags = np.arange(-0.3, 0.3001, 0.002)
        cc = np.array([np.corrcoef(np.interp(gg + L_, g, yb), cgr)[0, 1] for L_ in lags])
        i = int(np.argmax(cc))
        off = 0.0
        if 0 < i < len(cc) - 1:
            den = cc[i - 1] - 2 * cc[i] + cc[i + 1]
            off = 0.5 * (cc[i - 1] - cc[i + 1]) / den if den != 0 else 0.0
        XCORR[f'{s}/L{lap}'] = dict(cgHeadingLeadsImuS=r(lags[i] + off * 0.002, 4), r=r(cc[i], 4),
                                    scaleCgOverImu=r(np.std(cgr) / np.std(np.interp(gg, g, yb)), 3))

# ================================================================== decomposition fit (contacts): gap = deltaLat - c * tau
def fit_gap_model(rows, line='block'):
    X, y = [], []
    for x in rows:
        d = x[line]
        if d['gapAtOnset'] is None or d['closingRateMps'] is None:
            continue
        X.append([1.0, -d['closingRateMps']])
        y.append(d['gapAtOnset'])
    if len(y) < 4:
        return None
    X, y = np.array(X), np.array(y)
    w = np.ones(len(y))
    for _ in range(30):      # Huber IRLS
        W = np.sqrt(w)[:, None]
        beta = np.linalg.lstsq(X * W, y * W[:, 0], rcond=None)[0]
        res = y - X @ beta
        sc = 1.4826 * np.median(np.abs(res - np.median(res))) + 1e-9
        u = np.abs(res) / (1.345 * sc)
        w = np.where(u <= 1, 1.0, 1.0 / u)
    return dict(n=len(y), deltaLatM=r(beta[0]), tauAheadS=r(beta[1]), residualRobustSd=r(sc))


# ================================================================== CORRECTIONS (leave-one-lap-out)
def primary_contacts(s=None):
    return [x for x in CONTACTS if x['primary'] and (s is None or x['session'] == s)]


def lolo(rows, est_fn, apply_fn, metric_fn, sessions=SESS):
    """Generic leave-one-lap-out: est_fn(train_rows) -> param; apply_fn(row, param) -> corrected value."""
    out = {'before': [], 'after': [], 'params': {}}
    for s, laps in sessions.items():
        for lap in laps:
            test = [x for x in rows if x['session'] == s and x['lap'] == lap]
            train = [x for x in rows if x['session'] == s and x['lap'] != lap]
            if not test or not train:
                continue
            prm = est_fn(train)
            if prm is None:
                continue
            out['params'][f'{s}/L{lap}'] = prm
            for x in test:
                b = metric_fn(x)
                a = apply_fn(x, prm)
                if b is not None and a is not None:
                    out['before'].append((s, b))
                    out['after'].append((s, a))
    res = {}
    for s in list(sessions) + ['pooled']:
        b = [v for ss, v in out['before'] if s == 'pooled' or ss == s]
        a = [v for ss, v in out['after'] if s == 'pooled' or ss == s]
        res[s] = dict(before=stats(b), after=stats(a))
    res['paramsPerHeldOutLap'] = out['params']
    return res


CORR = {}

# C1 video <-> logger offset: evaluated on same-rule kerb audio onsets and on the T3 tip per-lap pattern
c1 = {}
for variant in ('none', 'sessionConstLOLO', 'dataTrackLOLO', 'adopted'):
    vals = defaultdict(list)
    for x in time_events:
        if x['audioConfidence'] in ('high', 'medium'):
            v = x['residualS'][variant].get('t2ImuOnset50_minus_audioOnset50')
            if v is not None:
                vals[x['session']].append(v)
                vals['pooled'].append(v)
    c1[variant] = {s: stats(v) for s, v in vals.items()}
CORR['C1_videoTimeOffset'] = dict(
    what='true lap t of a video frame = overlay-mapped lap t + L (per lap from the SmartyCam data track + 7/30 calibration, or one session constant)',
    metric='same-rule IMU accelerometer onset50 minus kerb-audio onset50 after the offset (s), audio confidence high+medium; LOLO = calibration / session constant estimated without the held-out lap',
    results=c1, adoptedPerLap={f'{s}/L{l}': LOFF[(s, l)]['adopted'] for s in SESS for l in SESS[s]})

# C2 along-track spatial offset of the CG reference point (from the camera-free contact timing)
def est_along(train):
    v = [x['block']['dSFirst'] for x in train if x['block'].get('dSFirst') is not None]
    return {'aM': r(-np.median(v), 2)} if len(v) >= 3 else None


def apply_contact_shift(x, prm):
    # CG moved forward by a along its heading == CG state at t + a/v; recompute dtFirst from the stored runs
    d = x['block']
    if d.get('dtFirst') is None:
        return None
    v = x['cgSpeedMpsAtOnset']
    return d['dSFirst'] + prm['aM']


CORR['C2_alongTrackOffset_fromContacts'] = dict(
    what='move the drawn car along its own heading by a (m; + = forward; = an antenna-to-vehicle-centre offset), a = -median(dS of the block first contact) of the training laps',
    onContacts=lolo(primary_contacts(), est_along, apply_contact_shift, lambda x: x['block'].get('dSFirst')),
    onVideoTips=lolo([x for x in TIPS if not x['suspectFeature']],
                     lambda tr_: est_along([c for c in primary_contacts() if (c['session'], c['lap']) in {(y['session'], y['lap']) for y in tr_}]),
                     lambda x, prm: x['dS'] + prm['aM'], lambda x: x['dS']))

# C2b along-track offset estimated from the video tips themselves (camera-dependent)
CORR['C2b_alongTrackOffset_fromVideoTips'] = dict(
    what='same correction, a = -median(dS of the video tip, nominal camera) of the training laps',
    onVideoTips=lolo([x for x in TIPS if not x['suspectFeature']], lambda tr_: {'aM': r(-np.median([y['dS'] for y in tr_]), 2)},
                     lambda x, prm: x['dS'] + prm['aM'], lambda x: x['dS']),
    onContacts=lolo(primary_contacts(),
                    lambda tr_: {'aM': r(-np.median([y['dS'] for y in TIPS if not y['suspectFeature'] and (y['session'], y['lap']) in {(c['session'], c['lap']) for c in tr_}]), 2)},
                    apply_contact_shift, lambda x: x['block'].get('dSFirst')))

# C3 lateral offsets: toward-kerb delta, driver-right e, both
def side_sign(x):
    return 1.0 if x['side'] == 'right' else -1.0


def est_lat(train, mode):
    g = [(x['block']['gapAtOnset'], side_sign(x)) for x in train if x['block']['gapAtOnset'] is not None]
    if len(g) < 3:
        return None
    a = np.array(g)
    if mode == 'towardKerb':
        return {'deltaM': r(np.median(a[:, 0]), 3), 'eRightM': 0.0}
    gr = a[a[:, 1] > 0, 0]
    gl = a[a[:, 1] < 0, 0]
    if len(gr) < 1 or len(gl) < 1:
        return None
    e = (np.median(gr) - np.median(gl)) / 2
    if mode == 'driverRight':
        return {'deltaM': 0.0, 'eRightM': r(e, 3)}
    dlt = np.median(a[:, 0] - e * a[:, 1])
    return {'deltaM': r(dlt, 3), 'eRightM': r(e, 3)}


def apply_lat(x, prm):
    gv = x['block']['gapAtOnset']
    return None if gv is None else gv - prm['deltaM'] - prm['eRightM'] * side_sign(x)


CORR['C3_lateral'] = {}
for mode in ('towardKerb', 'driverRight', 'both'):
    CORR['C3_lateral'][mode] = dict(
        onContactGapAtOnset=lolo(primary_contacts(), lambda tr_, m=mode: est_lat(tr_, m), apply_lat, lambda x: x['block']['gapAtOnset']),
        onVideoTipLateral=lolo([x for x in TIPS if not x['suspectFeature']],
                               lambda tr_, m=mode: est_lat([c for c in primary_contacts() if (c['session'], c['lap']) in {(y['session'], y['lap']) for y in tr_}], m),
                               lambda x, prm: x['dLat'] - prm['deltaM'] - prm['eRightM'] * side_sign(x), lambda x: x['dLat']))

# detection effect of the toward-kerb lateral correction (LOLO on pass classes)
def det_lolo():
    out = {}
    for s, laps in SESS.items():
        before, after = [], []
        for lap in laps:
            train = [x for x in primary_contacts(s) if x['lap'] != lap]
            prm = est_lat(train, 'towardKerb')
            if prm is None:
                continue
            for p in PASSES:
                if p['session'] == s and p['lap'] == lap:
                    before.append(p['classBlock'])
                    cg_after = p['cgMinGapBlock'] is not None and (p['cgMinGapBlock'] - prm['deltaM']) <= 0
                    t = p['truthContactHighMedium']
                    after.append('TP' if (t and cg_after) else 'FN' if t else 'FP' if cg_after else 'TN')
        out[s] = dict(before=confusion([{'c': c} for c in before], 'c'), after=confusion([{'c': c} for c in after], 'c'))
    return out


CORR['C3_lateral']['towardKerb']['detectionLOLO'] = det_lolo()

# C4 joint model gap = deltaLat - c * tau, LOLO prediction of the held-out gaps
def est_joint(train):
    f = fit_gap_model(train, 'block')
    return None if f is None else {'deltaM': f['deltaLatM'], 'tauS': f['tauAheadS']}


def apply_joint(x, prm):
    d = x['block']
    if d['gapAtOnset'] is None or d['closingRateMps'] is None:
        return None
    return d['gapAtOnset'] - prm['deltaM'] + d['closingRateMps'] * prm['tauS']


CORR['C4_jointLateralAlong_fromContacts'] = dict(
    what='robust fit over contacts of gapAtOnset = deltaLat - closingRate * tau (deltaLat: CG farther from kerb, tau: CG ahead in time)',
    fitAll={s: fit_gap_model(primary_contacts(s)) for s in SESS} | {'pooled': fit_gap_model(primary_contacts())},
    fitAllPaint={s: fit_gap_model(primary_contacts(s), 'paint') for s in SESS} | {'pooled': fit_gap_model(primary_contacts(), 'paint')},
    lolo=lolo(primary_contacts(), est_joint, apply_joint, lambda x: x['block']['gapAtOnset']),
    fitExclK1={s: fit_gap_model([x for x in primary_contacts(s) if x['kerb'] != 'k1']) for s in SESS}
    | {'pooled': fit_gap_model([x for x in primary_contacts() if x['kerb'] != 'k1'])},
    loloExclK1=lolo([x for x in primary_contacts() if x['kerb'] != 'k1'], est_joint, apply_joint, lambda x: x['block']['gapAtOnset']),
    caveat='tau is driven by the events with large closing rates (k1: ~12 m/s, i.e. the CG block line meets the path at ~20 deg near its leading end); '
           'a negative tau (CG behind) contradicts the heading timing (CG heading leads the gyro by 0.00-0.05 s) and the median first-contact timing, '
           'so it is read as kerb-line geometry, not as a pose offset')

# C6 / C7 per-kerb corrections of the kerb line (along-track / lateral), LOLO, >= 2 training contacts at that kerb
def lolo_perkerb(rows, value_fn, min_train=2):
    before, after, prm = [], [], {}
    for s, laps in SESS.items():
        for lap in laps:
            for x in [y for y in rows if y['session'] == s and y['lap'] == lap]:
                b = value_fn(x)
                if b is None:
                    continue
                tr_ = [value_fn(y) for y in rows if y['session'] == s and y['lap'] != lap and y['kerb'] == x['kerb'] and value_fn(y) is not None]
                c = float(np.median(tr_)) if len(tr_) >= min_train else 0.0
                prm[f"{s}/L{lap}/{x['kerb']}"] = r(c)
                before.append((s, b))
                after.append((s, b - c))
    out = {}
    for s in list(SESS) + ['pooled']:
        out[s] = dict(before=stats([v for ss, v in before if s == 'pooled' or ss == s]), after=stats([v for ss, v in after if s == 'pooled' or ss == s]))
    out['correctionPerHeldOutEvent'] = prm
    return out


CORR['C6_perKerbAlongTrack'] = dict(
    what='per-kerb along-track shift of the kerb line (or a local along-track error), = median dS of the block first contact at that kerb on the OTHER laps (>= 2), else 0',
    onContacts=lolo_perkerb(primary_contacts(), lambda x: x['block'].get('dSFirst')),
    onVideoTipsSpecFov=lolo_perkerb([x for x in TIPS if not x['suspectFeature']], lambda x: x['cameraVariants'].get('fh889', {}).get('dS')),
    onVideoTipsNominal=lolo_perkerb([x for x in TIPS if not x['suspectFeature']], lambda x: x['dS']))
CORR['C7_perKerbLateral'] = dict(
    what='per-kerb lateral shift of the block line, = median CG gap at the truth onset at that kerb on the OTHER laps (>= 2), else 0',
    onContactGapAtOnset=lolo_perkerb(primary_contacts(), lambda x: x['block']['gapAtOnset']),
    onVideoTipLateral=lolo_perkerb([x for x in TIPS if not x['suspectFeature']], lambda x: x['dLat']))

# C5 time shift of the whole pose (would also move the heading): tested against the heading timing
CORR['C5_poseTimeShift'] = dict(
    what='shift the drawn pose in time (t -> t + tau). Rejected as a correction if the drawn heading already matches the IMU yaw: a pose time shift moves the heading too.',
    headingTimingWholeLap=XCORR)

# ================================================================== SUMMARY
def fam_rows(fam):
    if fam == 'contactFirstBlock':
        return [dict(session=x['session'], lap=x['lap'], kerb=x['kerb'], dt=x['block'].get('dtFirst'), dS=x['block'].get('dSFirst'))
                for x in primary_contacts()]
    if fam == 'contactMidBlock':
        return [dict(session=x['session'], lap=x['lap'], kerb=x['kerb'], dt=x['block'].get('dtMid'), dS=x['block'].get('dSMid'))
                for x in primary_contacts() if x['offsetReliable']]
    if fam == 'contactFirstPaint':
        return [dict(session=x['session'], lap=x['lap'], kerb=x['kerb'], dt=x['paint'].get('dtFirst'), dS=x['paint'].get('dSFirst'))
                for x in primary_contacts()]
    if fam == 'videoTipNominal':
        return [dict(session=x['session'], lap=x['lap'], kerb=x['kerb'], dt=x['dt'], dS=x['dS']) for x in TIPS if not x['suspectFeature']]
    if fam == 'videoKerbEnds':
        return [dict(session=x['session'], lap=x['lap'], kerb=x['kerb'], dt=x['dt'], dS=x['dS'], event=x['event']) for x in ENDS if not x['suspectFeature']]
    if fam == 'turnInHeading50':
        return [dict(session=x['session'], lap=x['lap'], kerb=x['kerb'], dt=x['dtHeading50'], dS=x['dSHeading50']) for x in TURN if x['qualityGood']]
    if fam == 'contactFirstBlock_exclK1':
        return [dict(session=x['session'], lap=x['lap'], kerb=x['kerb'], dt=x['block'].get('dtFirst'), dS=x['block'].get('dSFirst'))
                for x in primary_contacts() if x['kerb'] != 'k1']
    if fam == 'videoTipSpecFovCamera':
        return [dict(session=x['session'], lap=x['lap'], kerb=x['kerb'], dt=x['cameraVariants'].get('fh889', {}).get('dt'),
                     dS=x['cameraVariants'].get('fh889', {}).get('dS')) for x in TIPS if not x['suspectFeature']]
    if fam == 'turnInVisibleT10':
        return [dict(session=x['session'], lap=x['lap'], kerb=x['kerb'], dt=x['cgT10MinusImuRawT10'], dS=x['dSVisibleT10']) for x in TURN if x['visibleQualityGood']]
    if fam == 'minSpeed':
        return [dict(session=x['session'], lap=x['lap'], kerb=x['kerb'], dt=x.get('dtMinSpeed'), dS=None) for x in TURN if x.get('dtMinSpeed') is not None and not x.get('minSpeedAtWindowEdge')]
    if fam == 'yawRatePeak':
        return [dict(session=x['session'], lap=x['lap'], kerb=x['kerb'], dt=x['dtYawRatePeak'], dS=None) for x in TURN if x['qualityGood']]
    raise KeyError(fam)


FAMS = ['contactFirstBlock', 'contactFirstBlock_exclK1', 'contactMidBlock', 'contactFirstPaint', 'videoTipNominal',
        'videoTipSpecFovCamera', 'videoKerbEnds', 'turnInHeading50', 'turnInVisibleT10', 'yawRatePeak', 'minSpeed']
FAM_DOC = {
    'contactFirstBlock': 'kerb contact, camera-free: CG first crossing of the block line by the nearest inner tyre minus the IMU vibration onset (T1, audio-confirmed where available); primary contacts (high/medium, not steering-away, line defined); only passes where the CG shows contact (counts of the excluded primary contacts: alongTrack.contactFirstBlock.conditionalOnCgContact)',
    'contactFirstBlock_exclK1': 'as contactFirstBlock without k1 (Coca-Cola), whose CG block line closes at ~12 m/s at the truth onset (leading-end geometry)',
    'contactMidBlock': 'midpoint of the CG block-contact run minus midpoint of the truth vibration (onset..offset); truth offsets trail the tyre leaving the kerb (decay), so this is biased negative; reference only',
    'contactFirstPaint': 'as contactFirstBlock with the paint line (road-side start of the white strip); the paint line lies road-side of the blocks, so negative by construction',
    'videoTipNominal': 'video clipping point (T3 tip, row 276) after the T2 offset, T3 nominal camera f967/h1.26 (f*h 1218); absolute value camera-dependent',
    'videoTipSpecFovCamera': 'same tips with the spec-FOV camera f711/h1.25 (f*h 889, SmartyCam 84 deg FOV spec, h from road width 1.26 m)',
    'videoKerbEnds': 'video red-block leading/trailing end crossing row 276 (T3) after the T2 offset, nominal camera; first/last block colour ambiguity up to ~1 block',
    'turnInHeading50': 'turn-in: CG drawn-heading-rate t50 minus IMU gyro t50, both cgpose.rate_onset, IMU boxcar-filtered 1.2 s to match the viewer chord heading; quality-gated',
    'turnInVisibleT10': 'visible turn-in: CG drawn-heading-rate t10 minus the UNSMOOTHED IMU gyro t10 (same rule); includes the lead caused by the +-0.6 s chord heading',
    'yawRatePeak': 'telemetry clipping marker: CG drawn yaw-rate peak minus IMU (chord-matched) yaw-rate peak; flat peaks make it noisy',
    'minSpeed': 'telemetry clipping marker: minimum of the CG position-derived speed minus the minimum of the non-driven rear wheels (T1)'}
along = {}
for fam in FAMS:
    rows = fam_rows(fam)
    along[fam] = {}
    for s in list(SESS) + ['pooled']:
        rr_ = [x for x in rows if s == 'pooled' or x['session'] == s]
        along[fam][s] = dict(dt=stats([x['dt'] for x in rr_]), dS=stats([x['dS'] for x in rr_], 2))
    along[fam]['systematicVsRandom_dS'] = decompose([x for x in rows if x['dS'] is not None], 'dS') if any(x['dS'] is not None for x in rows) else None
    along[fam]['systematicVsRandom_dt'] = decompose(rows, 'dt')
    perlap = defaultdict(list)
    for x in rows:
        if x['dt'] is not None:
            perlap[f"{x['session']}/L{x['lap']}"].append(x['dt'])
    along[fam]['perLapMedianDt'] = {k: r(np.median(v)) for k, v in perlap.items()}

lateral = {}
for s in list(SESS) + ['pooled']:
    pc = [x for x in primary_contacts() if s == 'pooled' or x['session'] == s]
    tp = [x for x in TIPS if not x['suspectFeature'] and (s == 'pooled' or x['session'] == s)]
    lateral[s] = dict(
        contactBlockGapAtOnset=stats([x['block']['gapAtOnset'] for x in pc], 2),
        contactPaintGapAtOnset=stats([x['paint']['gapAtOnset'] for x in pc], 2),
        contactBlockGapAtOnset_highOnly=stats([x['block']['gapAtOnset'] for x in pc if x['confidence'] == 'high'], 2),
        contactBlockGapAtOnset_rightKerbs=stats([x['block']['gapAtOnset'] for x in pc if x['side'] == 'right'], 2),
        contactBlockGapAtOnset_leftKerbs=stats([x['block']['gapAtOnset'] for x in pc if x['side'] == 'left'], 2),
        cgMinGapDuringTruthContactBlock=stats([x['block']['cgMinGapDuringTruthContact'] for x in pc], 2),
        videoTipLateralNominal=stats([x['dLat'] for x in tp], 2),
        videoTipLateral_rightKerbs=stats([x['dLat'] for x in tp if x['side'] == 'right'], 2),
        videoTipLateral_leftKerbs=stats([x['dLat'] for x in tp if x['side'] == 'left'], 2))
lateral['systematicVsRandom_contactBlockGap'] = decompose([dict(kerb=x['kerb'], g=x['block']['gapAtOnset']) for x in primary_contacts()], 'g')
lateral['straightLineCheckT3'] = T3['straightLateralCheck']

# between-lap lateral consistency at the clipping point (camera-scale robust)
bl = {}
for s in SESS:
    rows = [x for x in TIPS if x['session'] == s and not x['suspectFeature']]
    byk = defaultdict(list)
    for x in rows:
        byk[x['kerb']].append(x)
    vdev, cdev, gdev, dtdev = [], [], [], []
    for k, xs in byk.items():
        if len(xs) < 2:
            continue
        mv = np.median([x['videoDistanceM'] for x in xs])
        mc = np.median([x['cgDistanceM'] for x in xs])
        mg = np.median([x['cgApexGapBlockFront'] for x in xs])
        md = np.median([x['dt'] for x in xs])
        for x in xs:
            vdev.append(x['videoDistanceM'] - mv)
            cdev.append(x['cgDistanceM'] - mc)
            gdev.append(x['cgApexGapBlockFront'] - mg)
            dtdev.append(x['dt'] - md)
    vdev, cdev, gdev = map(np.array, (vdev, cdev, gdev))
    bl[s] = dict(n=int(len(vdev)),
                 videoVsCgTipDistanceDeviation=dict(r=r(np.corrcoef(vdev, cdev)[0, 1]) if len(vdev) > 2 else None,
                                                    slope=r(np.polyfit(cdev, vdev, 1)[0]) if len(vdev) > 2 else None,
                                                    rmsDifferenceM=r(math.sqrt(float(np.mean((vdev - cdev) ** 2)))) if len(vdev) else None,
                                                    sdVideoDevM=r(np.std(vdev)), sdCgDevM=r(np.std(cdev))),
                 videoTipDistanceVsCgApexGap=dict(r=r(np.corrcoef(vdev, gdev)[0, 1]) if len(vdev) > 2 else None,
                                                  rmsDifferenceM=r(math.sqrt(float(np.mean((vdev - gdev) ** 2)))) if len(vdev) else None),
                 tipDtDeviation=stats(dtdev))
lateral['betweenLapAtClippingPoint'] = bl

# camera sensitivity of the clipping-point along-track result + accx dependence
camsum = {}
for s in SESS:
    rows = [x for x in TIPS if x['session'] == s and not x['suspectFeature']]
    camsum[s] = {lab: dict(fTimesH=FH.get(ck), dt=stats([x['cameraVariants'].get(lab, {}).get('dt') for x in rows]),
                           dS=stats([x['cameraVariants'].get(lab, {}).get('dS') for x in rows], 2))
                 for lab, ck in CAMS.items()}
    # unmodified 2026-10-03 profile
    pv = []
    for pp in T3P.get('passes', []):
        if pp.get('race') != s or pp.get('kerb') in SUSPECT_T3[s]:
            continue
        if pp.get('inNominalValidSet') and pp.get('tipDt') is not None:
            pv.append(-(pp['tipDt'] + LOFF[(s, pp['lap'])]['adopted']))
    camsum[s]['profile20261003'] = dict(dt=stats(pv))
    a = np.array([x['accxG'] for x in rows], float)
    d = np.array([x['dt'] for x in rows], float)
    kk = [x['kerb'] for x in rows]
    kmed_a = {k: np.median(a[[i for i, y in enumerate(kk) if y == k]]) for k in set(kk)}
    kmed_d = {k: np.median(d[[i for i, y in enumerate(kk) if y == k]]) for k in set(kk)}
    aw = np.array([a[i] - kmed_a[k] for i, k in enumerate(kk)])
    dw = np.array([d[i] - kmed_d[k] for i, k in enumerate(kk)])
    camsum[s]['accxDependence'] = dict(
        pooledSlopeSPerG=r(np.polyfit(a, d, 1)[0]), pooledR=r(np.corrcoef(a, d)[0, 1]),
        withinKerbSlopeSPerG=r(np.polyfit(aw, dw, 1)[0]) if np.std(aw) > 0 else None,
        withinKerbR=r(np.corrcoef(aw, dw)[0, 1]) if np.std(aw) > 0 else None,
        interceptAtZeroAccxS=r(np.polyfit(a, d, 1)[1]), medianAccxG=r(np.median(a)),
        note='positive accx = accelerating (checked: braking samples give accx < 0). Pooled correlation mixes kerbs; the within-kerb slope is the pitch test.')
# camera scale that would reconcile the video tip with the camera-free contact timing
recon = {}
for s in SESS:
    target = along['contactFirstBlock'][s]['dt'].get('median')
    pts = []
    for lab, ck in CAMS.items():
        m = camsum[s][lab]['dt'].get('median')
        if m is not None and FH.get(ck):
            pts.append((FH[ck], m))
    pts.sort()
    fh = None
    if target is not None:
        for (f0, m0), (f1, m1) in zip(pts, pts[1:]):
            if (m0 - target) * (m1 - target) <= 0 and m1 != m0:
                fh = f0 + (target - m0) * (f1 - f0) / (m1 - m0)
                break
    recon[s] = dict(contactMedianDt=target, cameraPoints=[[f, m] for f, m in pts], fTimesHThatMatches=r(fh, 0),
                    label='derived: assumes the contact-based along-track estimate is right')
camsum['reconcilingCameraScale'] = recon

# worst corners / laps
worst = {}
for fam in ('contactFirstBlock', 'videoTipNominal', 'turnInHeading50'):
    rows = [x for x in fam_rows(fam) if x['dS'] is not None]
    byk = defaultdict(list)
    for x in rows:
        byk[(x['session'], x['kerb'])].append(x['dS'])
    worst[fam] = sorted([dict(session=SHORT[s], kerb=k, name=NAMES[k], n=len(v), medianDS=r(np.median(v), 2), maxAbsDS=r(np.max(np.abs(v)), 2))
                         for (s, k), v in byk.items()], key=lambda d: -abs(d['medianDS']))[:6]
    worst[fam + '_events'] = sorted([dict(session=SHORT[x['session']], lap=x['lap'], kerb=x['kerb'], dS=x['dS'], dt=x['dt']) for x in rows],
                                    key=lambda d: -abs(d['dS']))[:6]
worst['contactLateral_events'] = sorted([dict(session=SHORT[x['session']], lap=x['lap'], kerb=x['kerb'], gapBlockAtOnset=x['block']['gapAtOnset'],
                                              cgShowsContact=x['block']['cgShowsContact']) for x in primary_contacts()],
                                        key=lambda d: -abs(d['gapBlockAtOnset']))[:8]

perkerb = {}
for fam in ('contactFirstBlock', 'videoTipNominal', 'videoTipSpecFovCamera', 'turnInHeading50'):
    rows = [x for x in fam_rows(fam) if x['dt'] is not None]
    byk = defaultdict(list)
    for x in rows:
        byk[(x['session'], x['kerb'])].append(x)
    perkerb[fam] = {f'{SHORT[s]}/{k}': dict(name=NAMES[k], n=len(v), medianDt=r(np.median([y['dt'] for y in v])),
                                             medianDS=r(np.median([y['dS'] for y in v if y['dS'] is not None]), 2) if any(y['dS'] is not None for y in v) else None,
                                             laps=sorted(y['lap'] for y in v))
                    for (s, k), v in sorted(byk.items(), key=lambda kv: (kv[0][0], list(NAMES).index(kv[0][1])))}
perkerb['contactBlockGapAtOnset'] = {}
byk = defaultdict(list)
for x in primary_contacts():
    byk[(x['session'], x['kerb'])].append(x['block']['gapAtOnset'])
for (s, k), v in byk.items():
    perkerb['contactBlockGapAtOnset'][f'{SHORT[s]}/{k}'] = dict(name=NAMES[k], n=len(v), median=r(np.median(v), 2))

vturn = {}
for s in list(SESS) + ['pooled']:
    rows = [x['videoTurnIn'] for x in TURN if x['videoTurnIn'] and (s == 'pooled' or x['session'] == s)]
    vturn[s] = dict(videoMinusT2Gyro=stats([v['videoMinusT2Gyro'] for v in rows]), videoMinusT1YawOnset=stats([v['videoMinusT1YawOnset'] for v in rows]),
                    videoMinusSteerOnset=stats([v['videoMinusSteerOnset'] for v in rows]))
vturn['t2Summary'] = {s: T2['answers']['sessions'][s]['c_videoYaw'].get('turnInVideoOnsetMinusLapOffsetS') for s in SESS}
steer_lag = {s: dict(imuRawT10MinusSteerOnset=stats([x['imuRawT10MinusSteerOnset'] for x in TURN if x['session'] == s and x['visibleQualityGood']]),
                     cgT10MinusSteerOnset=stats([x['cgT10MinusSteerOnset'] for x in TURN if x['session'] == s and x['visibleQualityGood']]))
             for s in SESS}

endcams = {}
for s in SESS:
    Lm = LOFF[(s, 'sessionMedian')]
    endcams[s] = {ck: dict(fTimesH=FH.get(ck), redOnDtApprox=r(-(v['redOnDtMedian'] + Lm)) if v.get('redOnDtMedian') is not None else None,
                           redOffDtApprox=r(-(v['redOffDtMedian'] + Lm)) if v.get('redOffDtMedian') is not None else None)
                  for ck, v in T3['summary'][s]['cameraSensitivity'].items()}
    endcams[s]['note'] = 'session-level T3 medians shifted by the session-median offset (approximate; per-lap L not applied); dt = CG minus truth'

det = {}
for s in list(SESS) + ['pooled']:
    rows = [p for p in PASSES if s == 'pooled' or p['session'] == s]
    det[s] = dict(block=confusion(rows, 'classBlock'), paint=confusion(rows, 'classPaint'),
                  FN_blockMissDistanceM=stats([p['cgMinGapBlock'] for p in rows if p['classBlock'] == 'FN'], 2),
                  FP_blockDepthM=stats([p['cgMinGapBlock'] for p in rows if p['classBlock'] == 'FP'], 2),
                  FN_list=[f"{SHORT[p['session']]} L{p['lap']} {p['kerb']} (CG min gap {p['cgMinGapBlock']} m)" for p in rows if p['classBlock'] == 'FN'],
                  FP_list=[f"{SHORT[p['session']]} L{p['lap']} {p['kerb']} (CG depth {p['cgMinGapBlock']} m, pub {p['publishedPassLabel']})" for p in rows if p['classBlock'] == 'FP'])

# ---- tolerance view of the yes/no agreement (review loop 2026-10-22, round 1, R1-04)
# The classes above use a hard 0 m threshold on the CG block-line gap: a miss by 0.016 m counts as a full
# disagreement. With a tolerance tol, a disagreement whose CG gap over the pass (FN: closest approach,
# FP: deepest point) lies within +-tol of the block line is counted as agreement. Because that is one-sided
# (marginal agreements are not penalised), the symmetric alternative is reported too: the agreement among
# the "clear" passes only (CG gap beyond +-tol, whatever the class).
DET_TOLS = (0.10, 0.25, 0.30, 0.50)


def detection_tolerance(rows, tols=DET_TOLS):
    base = confusion(rows, 'classBlock')
    n = base['n']
    out = dict(rule='per pass, CG gap g = cgMinGapBlock (min over the pass of the nearest inner tyre to the block line; '
                    'negative = on the kerb). Hard rule: CG contact <=> g < 0 (crossing). Tolerance view: an FN/FP with |g| <= tol '
                    'counts as agreement -> agreementCountingNearLineAsAgreement = (TP + TN + disagreementsWithinTol) / n. '
                    'Symmetric view: passes with |g| <= tol are set aside whatever their class -> agreementAmongClearPasses = '
                    '(TP + TN - marginalAgreements) / clearPasses.',
               label='derived', hardThreshold=dict(agreement=base['agreement'], disagreements=base['FN'] + base['FP'], n=n))
    for tol in tols:
        near = [p for p in rows if p['cgMinGapBlock'] is not None and abs(p['cgMinGapBlock']) <= tol + 1e-9]
        cnt = defaultdict(int)
        for p in near:
            cnt[p['classBlock']] += 1
        dis_in = cnt['FN'] + cnt['FP']
        marg = cnt['TP'] + cnt['TN']
        clear = n - len(near)
        out[f'{tol:.2f}'] = dict(
            tolM=tol, n=n, TP=base['TP'], FN=base['FN'], FP=base['FP'], TN=base['TN'],
            FNwithinTol=cnt['FN'], FPwithinTol=cnt['FP'], disagreementsWithinTol=dis_in,
            disagreementsBeyondTol=base['FN'] + base['FP'] - dis_in,
            agreementCountingNearLineAsAgreement=r((base['TP'] + base['TN'] + dis_in) / n) if n else None,
            marginalAgreements=marg, nearLinePasses=len(near), clearPasses=clear,
            agreementAmongClearPasses=r((base['TP'] + base['TN'] - marg) / clear) if clear else None,
            disagreementsWithinTolList=[f"{SHORT[p['session']]} L{p['lap']} {p['kerb']} {p['classBlock']} (CG gap {p['cgMinGapBlock']} m)"
                                        for p in near if p['classBlock'] in ('FN', 'FP')])
    return out


for s in list(SESS) + ['pooled']:
    det[s]['tolerance'] = detection_tolerance([p for p in PASSES if s == 'pooled' or p['session'] == s])

# ---- along-track contact statistics are conditional on the CG showing contact (R1-10)
COND_CONTACT = {}
for s in list(SESS) + ['pooled']:
    pc = [x for x in primary_contacts() if s == 'pooled' or x['session'] == s]
    nod = [x for x in pc if x['block'].get('dSFirst') is None]
    COND_CONTACT[s] = dict(
        primaryContacts=len(pc), withAlongTrackValue=len(pc) - len(nod), withoutAlongTrackValue=len(nod),
        withoutValue_cgNeverOnBlockLine=len([x for x in nod if not x['block']['cgShowsContact']]),
        excludedCgMinGapDuringTruthContactM=stats([x['block']['cgMinGapDuringTruthContact'] for x in nod], 3),
        excluded=[f"{SHORT[x['session']]} L{x['lap']} {x['kerb']} (CG min gap during the truth contact "
                  f"{x['block']['cgMinGapDuringTruthContact']} m, at the onset {x['block']['gapAtOnset']} m)" for x in nod])
along['contactFirstBlock']['conditionalOnCgContact'] = dict(
    note='dt/dS of contactFirstBlock exist only when the CG tyre crosses the block line during the pass; primary truth contacts '
         'where the CG never reaches the line have no along-track value and are absent from these statistics (they remain in '
         'lateral.contactBlockGapAtOnset and in detection as FN). Review loop 2026-10-22 round 1, R1-10.',
    label='derived', bySession=COND_CONTACT)

# ---- open items (R1-09): amplitude scale between the drawn heading rate and the IMU gyro yaw rate
_sc = [v['scaleCgOverImu'] for v in XCORR.values()]
_rr = [v['r'] for v in XCORR.values()]
YSC_P = HERE / 'yaw_scale_check.json'          # written by yaw_scale_check.py (run after a first build)
YSC = jload(YSC_P) if YSC_P.exists() else None
OPEN_ITEMS = [dict(
    id='OI-1 yawRateAmplitudeScale', status='open - cause not identified', source='review loop 2026-10-22 round 1, R1-09', label='measured',
    finding=f'whole-lap std ratio drawn heading rate / IMU gyro yaw rate (both ~1.2 s smoothed) = {min(_sc)}-{max(_sc)} on all '
            f'{len(_sc)} laps (correlation {min(_rr)}-{max(_rr)}): the gyro amplitude is about {round(100 * (1 - 1 / float(np.median(_sc))))} % '
            'smaller than the GPS-course heading rate the viewer draws.',
    impact='timing results are amplitude-free: rate_onset 10/25/50 % points are fractions of (peak - base) and the whole-lap lag is '
           'a correlation peak. Only the absolute floors inside rate_onset (hysteresis minimum, weak-corner flag) see the amplitude; '
           'the turn-in sensitivity with the gyro rescaled is in yaw_scale_check.json.',
    whereToLook='gyro scale factor / unit in the logger configuration, or the logger mount; see yaw_scale_check.json for the '
                'cross-checks against the logger\'s own GPS yaw rate, lateral acceleration / speed and the roll-rate leak.',
    rangeScaleCgOverImu=[min(_sc), max(_sc)], medianScaleCgOverImu=r(np.median(_sc)),
    diagnostics=(YSC.get('conclusion') if YSC else 'not run yet: python yaw_scale_check.py, then rebuild'))]

inputs ={k: {'path': str(v.relative_to(ROOT)).replace('\\', '/'), 'sha256': sha(v)} for k, v in P.items()}
inputs['laps'] = {f'{s}/L{l}': {'path': LAPS[(s, l)]['path'], 'sha256': LAPS[(s, l)]['sha']} for s in SESS for l in SESS[s]}
inputs['imuYaw'] = {s: {'file': f'$JOIN_T1_WORK/xrk_native_{s}.npz (T1 work folder, raw logger samples, not published)',
                        'sha256': sha(T1_WORK / f'xrk_native_{s}.npz')} for s in SESS}

SIGN = {'dt': 't_CG(event) - t_truth(event) [s]; negative = the 3DCG shows the event earlier',
        'dS': '-dt * v [m]; along-track position of the 3DCG minus the real car at the same instant; positive = 3DCG ahead',
        'dLat': 'CG distance-to-kerb minus true distance-to-kerb [m]; positive = the 3DCG shows the car farther from the kerb. For a kerb contact = CG gap at the truth onset (true gap 0).',
        'gap': 'inner tyre outer edge to the kerb line (T4/apex_kpi); negative = on the kerb',
        'L': 'video offset: true lap t of a frame = overlay-mapped lap t + L (T2)'}
LABELS = {'measured': 'directly from logger samples, video pixels/audio (by T1-T3) or the viewer pose re-run (T4)',
          'derived': 'arithmetic combination of measured values (differences, products with speed, statistics)',
          'estimated': 'depends on an assumption stated next to it (camera scale, physical model, line semantics)'}

table = dict(kind='kerb-event-truth-join', version=1, generated='2026-10-22', tool='artifacts/kerb-event-truth-2026-10-22/join/build_join.py',
             signConventions=SIGN, labels=LABELS, inputs=inputs,
             timeBase=dict(rule='video events -> true lap time with the per-lap offset L (adopted = T2 bestOffsetS: 7/30 video yaw vs gyro, measured; 7/29 data track + 7/30 calibration, estimated). Logger and CG events need no offset: T1 IMU/ECU and T4 share the GPS lap-t base.',
                           perLap={f'{s}/L{l}': LOFF[(s, l)] for s in SESS for l in SESS[s]}),
             timeBaseKerbEvents=time_events, kerbContacts=CONTACTS, kerbPasses=PASSES, clippingPointsVideo=TIPS,
             kerbEndsVideo=ENDS, turnInAndTelemetryClip=TURN, headingTimingWholeLap=XCORR)
(HERE / 'event_table.json').write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding='utf-8')

summary = dict(kind='kerb-event-truth-join-summary', version=1, generated='2026-10-22', signConventions=SIGN, labels=LABELS,
               counts=dict(timeBaseMatchedKerbEvents=len(time_events), kerbContacts=len(CONTACTS), kerbContactsPrimary=len(primary_contacts()),
                           passes=len(PASSES), videoTips=len(TIPS), videoTipsExclSuspect=len([x for x in TIPS if not x['suspectFeature']]),
                           videoKerbEnds=len(ENDS), turnInRecords=len(TURN), turnInQualityGood=len([x for x in TURN if x['qualityGood']]),
                           kerbContactsPrimaryBySession={s: len(primary_contacts(s)) for s in SESS},
                           kerbContactsPrimaryWithAlongTrackValueBySession={s: COND_CONTACT[s]['withAlongTrackValue'] for s in SESS}),
               familyDefinitions=FAM_DOC,
               timeBase=timebase, alongTrack=along, lateral=lateral, detection=det, cameraSensitivityClippingPoint=camsum,
               cameraSensitivityKerbEnds=endcams, perKerb=perkerb, videoTurnInCrossCheck=vturn, steerToYawReference=steer_lag,
               decompositionContacts=CORR['C4_jointLateralAlong_fromContacts']['fitAll'],
               decompositionContactsPaint=CORR['C4_jointLateralAlong_fromContacts']['fitAllPaint'],
               headingTimingWholeLap=XCORR, worst=worst, corrections=CORR, openItems=OPEN_ITEMS)
(HERE / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
print('wrote', HERE / 'event_table.json', HERE / 'summary.json')
