"""Round 1 / report area: acceptance check of every number added or changed in report B (R1-01, R1-02, R1-03, R1-08).

Each check recomputes the value from the analysis outputs (summary.json, event_table.json, T3 vp_height.json /
yaw_focal.json, verify/v_summary.json, report-k1-check.json), formats it the way the page writes it, and confirms
that the page text contains the sentence. No video or XRK is read.
Output: report-check-numbers.json next to this file. Exit 1 on any failure.
"""
import json
import re
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
A = ROOT / 'artifacts' / 'kerb-event-truth-2026-10-22'
J = lambda p: json.loads(Path(p).read_text(encoding='utf-8'))
sm = J(A / 'join' / 'summary.json')
et = J(A / 'join' / 'event_table.json')
vh = J(A / 'T3-video-kerb-passages' / 'vp_height.json')
yf = J(A / 'T3-video-kerb-passages' / 'yaw_focal.json')
vs = J(A / 'verify' / 'v_summary.json')
k1 = J(HERE / 'report-k1-check.json')
html = (ROOT / 'docs' / 'fuji-event-truth-evaluation-2026-10-22.html').read_text(encoding='utf-8')
text = re.sub(r'<[^>]+>', '', html.split('<script>')[0])
D = json.loads(re.search(r'^const D = (\{.*\});$', html, re.M).group(1))
S30, S29 = 'fuji_aim_2020_07_30', 'fuji_aim_01'
checks = []


def chk(fid, what, ok, value, phrase=None):
    found = phrase is None or phrase in text
    checks.append(dict(finding=fid, what=what, value=value, phrase=phrase, valueOk=bool(ok), phraseFound=found))


def r(x, n):
    return float(f'{x:.{n}f}')


def q(a, p):
    s = sorted(a); i = (len(s) - 1) * p; lo = int(i); hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


# ---------------- R1-01 k1
t = k1['thresholdOnset']
chk('R1-01', 'late laps L1-L5, range', t['lateLaps'] == [1, 2, 3, 4, 5] and t['lateRange'] == [0.085, 0.355],
    t['lateRange'], '7/30の6周中5周（L1〜L5）で3DCGの接触が0.09〜0.36秒遅く始まり、L6だけ0.12秒早く始まります')
chk('R1-01', 'L6 early', t['earlyLaps'] == [6] and abs(t['earlyValues'][0] + 0.116) < 1e-9, t['earlyValues'], '7/30の6周中5周で0.09〜0.36秒遅れ、L6だけ0.12秒早い')
chk('R1-01', 'median of six laps ~0.2 s', 0.15 <= t['medianSixLaps'] <= 0.25, t['medianSixLaps'], '6周の中央値は約0.2秒遅れ')
v50 = k1['verifierOnset50']
chk('R1-01', 'verifier medians reproduced', v50['reproducedAllMedian'] == v50['verifierAllMedian'] and v50['reproducedExclK1Median'] == v50['verifierExclK1Median'],
    [v50['reproducedAllMedian'], v50['reproducedExclK1Median']])
per50 = list(v50['k1PerLapFirstBurst'].values())
chk('R1-01', '50 % rise per-lap range', r(min(per50), 2) == -0.06 and r(max(per50), 2) == 0.10, [min(per50), max(per50)], 'k1の遅れは周ごと−0.06〜+0.10秒に縮みます')
chk('R1-01', 'other kerbs under 50 %: 3DCG crosses 0.15 s early', r(-v50['reproducedExclK1Median'], 2) == 0.15 or r(-v50['reproducedExclK1Median'], 3) == 0.145,
    v50['reproducedExclK1Median'], '他の縁石で3DCGが0.15秒早く線を越える')
rel = k1['relativeToOtherKerbs']
chk('R1-01', 'relative to other kerbs 0.2-0.3 s under both rules', 0.15 <= rel['onset50'] <= 0.3 and 0.15 <= rel['threshold'] <= 0.3, rel, '他の縁石より0.2〜0.3秒遅く')
du = k1['durations']
chk('R1-01', 'durations', r(du['cgOnSecondsRange'][0], 2) == 0.23 and r(du['cgOnSecondsRange'][1], 2) == 0.42 and r(du['truthOnSecondsFirstBurstRange'][0], 1) == 0.6
    and r(du['truthOnSecondsFirstBurstRange'][1], 1) == 2.0 and du['cgShorterEveryLap'], du, '3DCG 0.23〜0.42秒、実際0.6〜2.0秒')
chk('R1-01', 'no "every lap" claim', '全周で3DCGの接触' not in text and '7/30の全周で' not in text, None)

# ---------------- R1-02 camera scale
cs = sm['cameraSensitivityClippingPoint'][S30]
vals = [cs[k]['dS']['median'] for k in ('fh590_profileLike', 'fh889', 'fh1066', 'nominal')]
chk('R1-02', 'four 7/30 sensitivity medians in D.cam', [c['d30']['dS'] for c in D['cam']] == vals, vals)
chk('R1-02', 'range sentence', r(vals[0], 1) == -3.3 and r(vals[-1], 1) == 4.2, vals, '7/30の中央値で−3.3〜+4.2 m')
tu = sm['alongTrack']['turnInHeading50']
co = sm['alongTrack']['contactFirstBlock']
chk('R1-02', 'camera-free basis', r(tu[S30]['dS']['median'], 1) == -0.2 and r(tu[S29]['dS']['median'], 1) == -0.6 and r(co[S30]['dS']['median'], 1) == -0.6,
    [tu[S30]['dS']['median'], tu[S29]['dS']['median'], co[S30]['dS']['median']], 'カメラを使わない切り始め（7/30 −0.2 m、7/29 −0.6 m）と縁石接触（7/30 −0.6 m）で支えています')
h = vh['results']['f711']['summary'][S30]
chk('R1-02', 'camera height for f 711 from road width', r(h['hMedian'], 2) == 1.26 and r(h['hIQR'][0], 2) == 1.14 and r(h['hIQR'][1], 2) == 1.31,
    [h['hMedian'], h['hIQR']], 'カメラの高さ1.26 m（f 711 pxのとき、四分位1.14〜1.31 m）')
right = [v['fCorrected'] for k, v in yf['summary'].items() if isinstance(v, dict) and '(right)' in k]
imp = vs['checks']['impliedFocalPx_0730']
flo, fhi = min(imp['gyroOnVideo'] + imp['videoOnGyro']), max(right + imp['videoOnGyro'])
chk('R1-02', 'focal from rotation ~540-800 px', round(flo, -1) == 540 and round(fhi, -1) == 800, [flo, fhi], '焦点距離（約540〜800 px）')
chk('R1-02', 'same range in next steps and table', '540〜790' not in text and text.count('540〜800') == 3, text.count('540〜800'))
ke = sm['cameraSensitivityKerbEnds'][S30]['f711_h1.25']
within = [cs['fh889']['dt']['median'], ke['redOnDtApprox'], ke['redOffDtApprox']]
chk('R1-02', 'clipping / kerb ends at f*h 889 within 0.03 s of the contact median', all(abs(x - co[S30]['dt']['median']) <= 0.06 and abs(x) <= 0.03 for x in within) and abs(co[S30]['dt']['median']) <= 0.03,
    dict(clipDt=within[0], kerbEnds=within[1:], contactDt=co[S30]['dt']['median']), '縁石の端の時刻が、カメラを使わない縁石接触と7/30で±0.03秒以内に揃うのは整合性の確認で、独立の証明ではない')
chk('R1-02', 'tile caveat', r(vals[0], 0) == -3 and r(vals[-1], 0) == 4, vals, 'クリッピングの偏りはカメラの尺度しだいで−3〜+4 m動く')

# ---------------- R1-03 visible turn-in
tv = sm['alongTrack']['turnInVisibleT10']
chk('R1-03', 'medians', r(tv[S30]['dt']['median'], 2) == 0.01 and r(tv[S29]['dt']['median'], 2) == -0.08, [tv[S30]['dt']['median'], tv[S29]['dt']['median']],
    '（7/30 +0.01秒、7/29 −0.08秒）')
chk('R1-03', 'p90', r(tv[S30]['dt']['p90Abs'], 2) == 0.36 and r(tv[S29]['dt']['p90Abs'], 2) == 0.76, [tv[S30]['dt']['p90Abs'], tv[S29]['dt']['p90Abs']],
    '10件に1件は0.36秒（7/29は0.76秒）以上ずれます')
TV = [x for x in et['turnInAndTelemetryClip'] if x['visibleQualityGood']]
big = {s: [x['cgT10MinusImuRawT10'] for x in TV if x['session'] == s and abs(x['cgT10MinusImuRawT10']) >= tv[s]['dt']['p90Abs'] - 1e-9] for s in (S30, S29)}
chk('R1-03', 'large deviations are all 3DCG early', all(v < 0 for s in big for v in big[s]), big, '大きくずれるときは、どれも3DCGの方が早く曲がり始めます')
st_ = sm['steerToYawReference']
chk('R1-03', 'CG t10 after steering onset (median)', r(st_[S30]['cgT10MinusSteerOnset']['median'], 2) in (0.13, 0.14) and r(st_[S29]['cgT10MinusSteerOnset']['median'], 2) == 0.24,
    [st_[S30]['cgT10MinusSteerOnset']['median'], st_[S29]['cgT10MinusSteerOnset']['median']], '中央値で0.14秒後（7/29は0.24秒後）')
neg = {s: (sum(1 for x in TV if x['session'] == s and x['cgT10MinusSteerOnset'] < 0), sum(1 for x in TV if x['session'] == s),
           sum(1 for x in TV if x['session'] == s and x['imuRawT10MinusSteerOnset'] < 0), min(x['cgT10MinusSteerOnset'] for x in TV if x['session'] == s)) for s in (S30, S29)}
chk('R1-03', 'CG turns before steering onset in some cases', neg[S30][:3] == (8, 40, 2) and neg[S29][:3] == (2, 26, 2) and r(-min(neg[S30][3], neg[S29][3]), 1) == 0.9,
    neg, '7/30の40件中8件（7/29は26件中2件）では、ステアを切る前に向きが変わり始めて見えます（最大0.9秒前。ジャイロでは各日2件）')

# ---------------- R1-08
det = sm['detection'][S30]
chk('R1-08a', 'FN / FP medians for 7/30', det['FN_blockMissDistanceM']['median'] == 0.24 and det['FN_blockMissDistanceM']['n'] == 10
    and det['FP_blockDepthM']['median'] == -0.29 and det['FP_blockDepthM']['n'] == 11, [det['FN_blockMissDistanceM']['median'], det['FP_blockDepthM']['median']],
    '中央値0.24 m（7/30の10件）')
chk('R1-08a', 'FP phrase', True, None, '中央値0.29 m（7/30の11件）')
p90 = [sm['alongTrack'][f][s]['dS']['p90Abs'] for f in ('videoTipSpecFovCamera', 'turnInHeading50') for s in (S30, S29)]
chk('R1-08b', 'p90 range of clipping / turn-in', r(min(p90), 1) == 3.3 and r(max(p90), 1) == 4.4, p90, '90%値3.3〜4.4 m')
pl = sm['timeBase']['perLap'][S29]
ad = [pl[k]['adopted'] for k in ('1', '2', '3', '4')]
chk('R1-08c', '7/29 adopted offsets', r(min(ad), 2) == 0.17 and r(max(ad), 2) == 0.23, ad, '7/29 採用値0.17〜0.23秒（推定。妥当な幅0.17〜0.30秒）')
chk('R1-08c', 'plausible range from the verifier verdict', '0.17-0.30' in vs['offsetL'][S29]['verdict'], vs['offsetL'][S29]['verdict'])
lat = {s: q([abs(c['dLat']) for c in D['clip'] if c['s'] == s], .5) for s in ('7/30', '7/29')}
sign = [sm['cameraSensitivityClippingPoint'][S30]['fh889'], sm['lateral'][S30]['videoTipLateralNominal']['median']]
chk('R1-08d', 'clipping lateral typical ~0.5 m, sign depends on camera', 0.45 <= lat['7/30'] <= 0.6 and 0.45 <= lat['7/29'] <= 0.6, dict(typ=lat, nominalMedian=sign[1],
    page889Median=q([c['dLat'] for c in D['clip'] if c['s'] == '7/30'], .5)), 'クリッピングでの横は典型0.5 m前後で、カメラの尺度に左右される')
bl = sm['lateral']['betweenLapAtClippingPoint']
chk('R1-08e', 'correlation with n', r(bl[S30]['videoVsCgTipDistanceDeviation']['r'], 2) == 0.89 and bl[S30]['n'] == 39 and bl[S29]['videoVsCgTipDistanceDeviation']['r'] == 0.96 and bl[S29]['n'] == 11,
    [bl[S30]['n'], bl[S29]['n']], '相関0.89（7/30、39件）・0.96（7/29、11件）')
doc = (A / 'T3-video-kerb-passages' / 'analyze.py').read_text(encoding='utf-8')
chk('R1-08f', 'clipping definition matches T3 tip docstring', "kerb edge's extreme position toward the image centre at row y" in doc, 'T3 analyze.py: tip',
    '動画の決まった高さ（276行目＝前方の一定の距離）で、内側の縁石の縁が画面の中央に最も近づいたコマの時刻')

# ---------------- page hygiene
chk('hygiene', 'no personal absolute path in page', not re.search(r'(?<![A-Za-z])[A-Za-z]:[\\/]|/Users/|★', html), None)
chk('hygiene', 'no image / media element in page', not re.search(r'<(img|video|audio|source)\b|data:image', html), None)

bad = [c for c in checks if not (c['valueOk'] and c['phraseFound'])]
out = dict(kind='report-B number check (round 1, report area)', n=len(checks), failures=bad, checks=checks)
(HERE / 'report-check-numbers.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
print(f'{len(checks)} checks, {len(bad)} failures')
for c in bad:
    print('FAIL', c)
sys.exit(1 if bad else 0)
