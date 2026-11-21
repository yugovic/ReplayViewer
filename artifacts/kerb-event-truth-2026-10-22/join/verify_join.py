"""Independent checks of event_table.json / summary.json (writes verify_join.json).
Re-derives numbers from the raw track outputs WITHOUT the join's helper functions:
  1. CG gap at the truth onset from T4's stored 100 Hz gap arrays (traces/*.npz gap_<kerb>) vs the join (cgpose recomputation)
  2. CG first block contact of the join vs T4 cg_events block.nearestTyre.tFirst (when the matched run is the first run)
  3. video-tip dt by hand from passages.json + events_video.json
  4. same-rule IMU-minus-audio residual vs T2's own kerbEventSummary
  5. no absolute personal paths in the files of this folder
  6. (review round 1, R1-04) yes/no classes from the truth flag + sign of the CG gap, and the tolerance view, re-derived
  7. (R1-10) primary contacts with / without an along-track value vs summary.alongTrack.contactFirstBlock
  8. (R1-09) openItems OI-1 scale range vs headingTimingWholeLap; yaw_scale_check.py self-check (unscaled = join)
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
KT = HERE.parent
E = json.loads((HERE / 'event_table.json').read_text(encoding='utf-8'))
S = json.loads((HERE / 'summary.json').read_text(encoding='utf-8'))
T2 = json.loads((KT / 'T2-video-audio-events/events_video.json').read_text(encoding='utf-8'))
T3 = json.loads((KT / 'T3-video-kerb-passages/passages.json').read_text(encoding='utf-8'))
T4 = json.loads((KT / 'T4-cg-traces/cg_events.json').read_text(encoding='utf-8'))
out = {}

# 1 ------------------------------------------------------------------------------------------
d1 = []
for x in E['kerbContacts']:
    if x['block']['gapAtOnset'] is None:
        continue
    z = np.load(KT / f"T4-cg-traces/traces/{x['session']}_L{x['lap']}.npz")
    t = z['t']
    g = z[f"gap_{x['kerb']}"]          # paintFront, paintRear, blockFront, blockRear
    i0 = int(z[f"gapStart_{x['kerb']}"])
    tt = t[i0:i0 + len(g)]
    gb = np.fmin(g[:, 2], g[:, 3])
    v = float(np.interp(x['truthOnsetT'], tt, gb))
    d1.append(abs(v - x['block']['gapAtOnset']))
out['1_gapAtOnset_vs_T4_storedArrays'] = dict(n=len(d1), maxAbsDiffM=round(max(d1), 4), medianAbsDiffM=round(float(np.median(d1)), 4),
                                              note='linear interpolation of the stored 100 Hz gaps vs cgpose recomputation at the exact instant')

# 2 ------------------------------------------------------------------------------------------
d2 = []
for x in E['kerbContacts']:
    b = x['block']
    if b.get('cgContactStartT') is None or not b['cgRuns']:
        continue
    ce = T4['sessions'][x['session']]['laps'][str(x['lap'])]['kerbs'][x['kerb']]['block']['nearestTyre']
    if ce is None:
        continue
    # compare only when the matched run is the first run inside the T4 pass window
    p = T4['sessions'][x['session']]['laps'][str(x['lap'])]['kerbs'][x['kerb']]['pass']
    inpass = [rr for rr in b['cgRuns'] if p['tStart'] - 0.01 <= rr[0] <= p['tEnd']]
    if inpass and abs(inpass[0][0] - b['cgContactStartT']) < 1e-6:
        d2.append(abs(ce['tFirst'] - b['cgContactStartT']))
out['2_cgFirstContact_vs_T4_cg_events'] = dict(n=len(d2), maxAbsDiffS=round(max(d2), 4) if d2 else None)

# 3 ------------------------------------------------------------------------------------------
L = {}
for s, sd in T2['sessions'].items():
    for lap, p in sd['perLapOffsets'].items():
        L[(s, int(lap))] = p['bestOffsetS']
d3 = []
for x in E['clippingPointsVideo']:
    p = [q for q in T3['passes'] if q['race'] == x['session'] and q['lap'] == x['lap'] and q['kerb'] == x['kerb']][0]
    r276 = p['rows']['276']
    truth = r276['videoTip']['lapTime'] + L[(x['session'], x['lap'])]
    dt = r276['nominal']['tip']['replayLapTime'] - truth
    d3.append(abs(dt - x['dt']))
out['3_videoTipDt_byHand'] = dict(n=len(d3), maxAbsDiffS=round(max(d3), 4))

# 4 ------------------------------------------------------------------------------------------
ours = S['timeBase']['kerbEventAgreement']['fuji_aim_2020_07_30']['byOffsetVariant']['adopted']['sameRule_t2ImuOnset50_minus_audioOnset50']['audioHighMedium']
t2 = T2['sessions']['fuji_aim_2020_07_30']['kerbEventSummary']['imuOnsetMinusCorrectedAudioOnsetS']
out['4_sameRuleResidual_vs_T2'] = dict(join=dict(n=ours['n'], median=ours['median']), t2=dict(n=t2['n'], median=t2['median']),
                                       agree=bool(ours['n'] == t2['n'] and abs(ours['median'] - t2['median']) <= 0.001))

# 5 ------------------------------------------------------------------------------------------
user = re.escape(Path.home().name)          # resolved at run time, never stored in a file
pat = re.compile(r'([A-Za-z]:[\\/]+(Users|00_Dev|★)|/[a-z]/00_Dev|' + user + ')')
hits = []
for f in HERE.iterdir():
    if f.suffix in ('.json', '.py', '.svg') and f.name != 'verify_join.json':
        txt = f.read_text(encoding='utf-8')
        for m in pat.finditer(txt):
            hits.append(f'{f.name}: <match at {m.start()}>')
out['5_noPersonalAbsolutePaths'] = dict(hits=hits, ok=not hits, scope='all .json / .py / .svg in this folder (drive-letter user/dev paths, the local media folder, the login name)')

# 6 (review loop round 1, R1-04) -------------------------------------------------------------
# yes/no classes and the tolerance view re-derived from the pass rows alone (truth flag + sign of the CG gap)
SESS6 = sorted({p['session'] for p in E['kerbPasses']})
mism6, sign_incons = [], 0
for s in SESS6 + ['pooled']:
    rows = [p for p in E['kerbPasses'] if s == 'pooled' or p['session'] == s]
    cls = []
    for p in rows:
        cg = p['cgMinGapBlock'] < 0
        if cg != p['cgBlockContact']:
            sign_incons += 1
        t = p['truthContactHighMedium']
        cls.append(('TP' if cg else 'FN') if t else ('FP' if cg else 'TN'))
    n = len(rows)
    agree = sum(c in ('TP', 'TN') for c in cls)
    for tol in (0.10, 0.25, 0.30, 0.50):
        within = sum(1 for p, c in zip(rows, cls) if c in ('FN', 'FP') and abs(p['cgMinGapBlock']) <= tol + 1e-9)
        marg = sum(1 for p, c in zip(rows, cls) if c in ('TP', 'TN') and abs(p['cgMinGapBlock']) <= tol + 1e-9)
        clear = sum(1 for p in rows if abs(p['cgMinGapBlock']) > tol + 1e-9)
        mine = (round((agree + within) / n, 3), round((agree - marg) / clear, 3) if clear else None)
        blk = S['detection'][s]['tolerance'][f'{tol:.2f}']
        theirs = (blk['agreementCountingNearLineAsAgreement'], blk['agreementAmongClearPasses'])
        if mine != theirs:
            mism6.append(f'{s} tol {tol}: mine {mine} vs summary {theirs}')
t30 = S['detection']['fuji_aim_2020_07_30']['tolerance']['0.25']
out['6_detectionTolerance_rederived'] = dict(mismatches=mism6, cgContactFlagVsGapSignInconsistent=sign_incons,
                                            r0730_tol025=dict(withinTol=t30['disagreementsWithinTol'], n=t30['n'],
                                                              agreement=t30['agreementCountingNearLineAsAgreement'],
                                                              agreementAmongClearPasses=t30['agreementAmongClearPasses']),
                                            ok=not mism6 and sign_incons == 0)

# 7 (R1-10) ------------------------------------------------------------------------------------
c7 = {}
for s in SESS6:
    pc = [x for x in E['kerbContacts'] if x['primary'] and x['session'] == s]
    with_v = sum(1 for x in pc if x['block'].get('dSFirst') is not None)
    blk = S['alongTrack']['contactFirstBlock']['conditionalOnCgContact']['bySession'][s]
    c7[s] = dict(primary=len(pc), withValue=with_v, summary=(blk['primaryContacts'], blk['withAlongTrackValue']),
                 familyN=S['alongTrack']['contactFirstBlock'][s]['dS']['n'])
out['7_contactAlongTrackConditioning'] = dict(bySession=c7, ok=all(v['summary'] == (v['primary'], v['withValue']) and v['familyN'] == v['withValue'] for v in c7.values()))

# 8 (R1-09) ------------------------------------------------------------------------------------
sc = [v['scaleCgOverImu'] for v in S['headingTimingWholeLap'].values()]
oi = [o for o in S.get('openItems', []) if o['id'].startswith('OI-1')]
ysc_p = HERE / 'yaw_scale_check.json'
ysc = json.loads(ysc_p.read_text(encoding='utf-8')) if ysc_p.exists() else {}
selfc = ysc.get('E_turnIn50_gyroRescaled', {}).get('selfCheck_unscaledReproducesJoin_maxAbsDiffS')
out['8_openItemYawScale'] = dict(present=bool(oi), range=oi[0]['rangeScaleCgOverImu'] if oi else None, recomputed=[min(sc), max(sc)],
                                 yawScaleCheckSelfCheckMaxAbsDiffS=selfc,
                                 ok=bool(oi) and oi[0]['rangeScaleCgOverImu'] == [min(sc), max(sc)] and selfc is not None and selfc < 0.001)

out['allOk'] = bool(out['1_gapAtOnset_vs_T4_storedArrays']['maxAbsDiffM'] < 0.02 and (out['2_cgFirstContact_vs_T4_cg_events']['maxAbsDiffS'] or 0) < 0.012
                    and out['3_videoTipDt_byHand']['maxAbsDiffS'] < 0.0015 and out['4_sameRuleResidual_vs_T2']['agree'] and out['5_noPersonalAbsolutePaths']['ok']
                    and out['6_detectionTolerance_rederived']['ok'] and out['7_contactAlongTrackConditioning']['ok'] and out['8_openItemYawScale']['ok'])
(HERE / 'verify_join.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
print(json.dumps(out, ensure_ascii=False, indent=1))
