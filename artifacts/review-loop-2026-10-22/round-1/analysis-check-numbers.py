"""Round 1, area "analysis": acceptance checks for R1-04 / R1-09 / R1-10.
Every number written into report B by this area is recomputed from join/summary.json and join/yaw_scale_check.json
and must appear verbatim in B. Writes analysis-check-numbers.json next to this file.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
J = ROOT / 'artifacts/kerb-event-truth-2026-10-22/join'
S = json.loads((J / 'summary.json').read_text(encoding='utf-8'))
Y = json.loads((J / 'yaw_scale_check.json').read_text(encoding='utf-8'))
B = (ROOT / 'docs/fuji-event-truth-evaluation-2026-10-22.html').read_text(encoding='utf-8')
html_text = re.sub(r'<[^>]+>', '', B)

d30, d29 = S['detection']['fuji_aim_2020_07_30'], S['detection']['fuji_aim_01']
t25, t30, t29 = d30['tolerance']['0.25'], d30['tolerance']['0.30'], d29['tolerance']['0.25']
blk = d30['block']
pct = lambda v: round(100 * v)
checks = {}
# R1-04: (TP+TN+within)/84 recomputed by hand and compared with the block and with the text
by_hand = (blk['TP'] + blk['TN'] + t25['disagreementsWithinTol']) / blk['n']
checks['R1-04 byHand==block'] = abs(by_hand - t25['agreementCountingNearLineAsAgreement']) < 0.0006
checks['R1-04 n==84'] = blk['n'] == 84
exp = {
    'R1-04 disagreements': f"7/30の不一致{blk['FN'] + blk['FP']}件のうち{t25['disagreementsWithinTol']}件",
    'R1-04 within0.30': f"0.30 m以内なら{t30['disagreementsWithinTol']}件",
    'R1-04 tol agreement': f"一致は{pct(t25['agreementCountingNearLineAsAgreement'])}%（7/29は{pct(t29['agreementCountingNearLineAsAgreement'])}%）",
    'R1-04 clear passes': f"通過（{t25['clearPasses']}件）だけで数えると一致は{pct(t25['agreementAmongClearPasses'])}%",
    'R1-04 conclusion bullet': f"食い違い{blk['FN'] + blk['FP']}件のうち{t25['disagreementsWithinTol']}件は縁石の線から0.25 m以内の僅差で、この許容で数えると{pct(t25['agreementCountingNearLineAsAgreement'])}%",
}
c = S['alongTrack']['contactFirstBlock']['conditionalOnCgContact']['bySession']['fuji_aim_2020_07_30']
g = c['excludedCgMinGapDuringTruthContactM']
lo = min(float(x.split('truth contact ')[1].split(' m')[0]) for x in c['excluded'])
exp['R1-10 n with value'] = f"線を越えた{c['withAlongTrackValue']}件だけで求めています"
exp['R1-10 n primary / excluded'] = f"主な接触{c['primaryContacts']}件のうち残りの{c['withoutAlongTrackValue']}件"
exp['R1-10 gap range'] = f"0.02〜{g['maxAbs']:.1f} m、中央値{g['median']:.2f} m"
checks['R1-10 lo rounds to 0.02'] = f'{lo:.2f}' == '0.02'
checks['R1-10 lateral n'] = S['lateral']['fuji_aim_2020_07_30']['contactBlockGapAtOnset']['n'] == c['primaryContacts']
exp['R1-10 lateral n in text'] = f"横のずれ（{c['primaryContacts']}件）"
lowpct = Y['summary']['gyroLowByPct']
exp['R1-09 pct'] = f"約{round(lowpct[0])}〜{round(lowpct[1])}%小さく"
checks['R1-09 timing unchanged'] = Y['E_turnIn50_gyroRescaled']['bySession']['pooled']['maxAbsPerEventChangeS'] < 0.001
checks['R1-09 openItems in summary'] = any(o['id'].startswith('OI-1') for o in S['openItems'])
for k, v in exp.items():
    checks[f'text: {k}'] = v in html_text
out = dict(expectedStrings=exp, checks=checks, allOk=all(checks.values()))
(HERE / 'analysis-check-numbers.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
print(json.dumps(out, ensure_ascii=False, indent=1))
