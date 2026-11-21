"""Round 1 / report area: per-lap numbers for the k1 (Coca-Cola) sentence of report B (finding R1-01).

Reads only analysis outputs (no video, no XRK):
  join/event_table.json      kerbContacts k1 primary: block.dtFirst (3DCG first line crossing minus the
                             logger threshold onset), block.gapAtOnset, cgOnSeconds, truthOnSeconds
  verify/v_kerb_compare.json the verifier's own 50 %-rise IMU onset per contact (ownImuMinusT1) and its own
                             3DCG first crossing (ownFirstCrossMinusT1)
Re-derives the verifier's cgCrossMinusOwnOnset50 medians (all / excl_k1) from v_kerb_compare.json to show the
per-event subtraction is the same quantity, then reports k1 under both onset definitions.
Output: report-k1-check.json next to this file.
"""
import json
import statistics as st
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
A = ROOT / 'artifacts' / 'kerb-event-truth-2026-10-22'
et = json.loads((A / 'join' / 'event_table.json').read_text(encoding='utf-8'))
vc = json.loads((A / 'verify' / 'v_kerb_compare.json').read_text(encoding='utf-8'))
vs = json.loads((A / 'verify' / 'v_summary.json').read_text(encoding='utf-8'))

k1 = [c for c in et['kerbContacts'] if c['kerb'] == 'k1' and c['primary'] and c['session'] == 'fuji_aim_2020_07_30']
rows = []
for c in k1:
    b = c['block']
    rows.append(dict(lap=c['lap'], onset=c['truthOnsetT'], dtFirst=b['dtFirst'], gapAtOnset=b['gapAtOnset'],
                     cgOnSeconds=b['cgOnSeconds'], truthOnSeconds=b['truthOnSeconds']))

# verifier 50 % rise: cgCross - own50 = ownFirstCrossMinusT1 - ownImuMinusT1
def d50(x):
    if x.get('ownFirstCrossMinusT1') is None or x.get('ownImuMinusT1') is None:
        return None
    return round(x['ownFirstCrossMinusT1'] - x['ownImuMinusT1'], 3)

prim0730 = [x for x in vc if x['session'] == 'fuji_aim_2020_07_30' and x['primary']]
# the verifier counts one own 50 % onset once (0730 L5 k1 has two logger bursts that share one own onset)
_seen, _dedup = set(), []
for x in sorted(prim0730, key=lambda x: (x['lap'], x['kerb'], x['t1Onset'])):
    key = (x['lap'], x['kerb'], x.get('ownImuOnset50'))
    if key in _seen:
        continue
    _seen.add(key)
    _dedup.append(x)
all50 = [d50(x) for x in _dedup if d50(x) is not None and x.get('joinCgShowsContact')]
ex50 = [d50(x) for x in _dedup if d50(x) is not None and x.get('joinCgShowsContact') and x['kerb'] != 'k1']
k150 = [dict(lap=x['lap'], t1Onset=x['t1Onset'], cgMinusOwn50=d50(x), own50MinusT1=x['ownImuMinusT1'])
        for x in prim0730 if x['kerb'] == 'k1']
ref = vs['kerbContactOnsetDefinition_0730_primary']
exT1 = [c['block']['dtFirst'] for c in et['kerbContacts'] if c['session'] == 'fuji_aim_2020_07_30' and c['primary']
        and c['kerb'] != 'k1' and c['block'].get('dtFirst') is not None]

# one value per lap (L5 has two bursts on one 3DCG contact; use the first burst = the onset the 3DCG contact belongs to)
first_per_lap = {}
for r in sorted(rows, key=lambda r: (r['lap'], r['onset'])):
    first_per_lap.setdefault(r['lap'], r)
late = [r for r in first_per_lap.values() if r['dtFirst'] > 0]
early = [r for r in first_per_lap.values() if r['dtFirst'] <= 0]
k150_first = {}
for r in sorted(k150, key=lambda r: (r['lap'], r['t1Onset'])):
    k150_first.setdefault(r['lap'], r)

out = dict(
    kind='report-B k1 sentence check (round 1, R1-01)',
    k1PrimaryContacts0730=rows,
    thresholdOnset=dict(
        perLapFirstBurstDtFirst={r['lap']: r['dtFirst'] for r in first_per_lap.values()},
        lateLaps=[r['lap'] for r in late], lateRange=[min(r['dtFirst'] for r in late), max(r['dtFirst'] for r in late)],
        earlyLaps=[r['lap'] for r in early], earlyValues=[r['dtFirst'] for r in early],
        medianSixLaps=round(st.median(r['dtFirst'] for r in first_per_lap.values()), 3),
        medianAllSevenOnsets=round(st.median(r['dtFirst'] for r in rows), 3),
        otherKerbsMedian=round(st.median(exT1), 3), otherKerbsN=len(exT1),
    ),
    durations=dict(cgOnSecondsRange=[min(r['cgOnSeconds'] for r in rows), max(r['cgOnSeconds'] for r in rows)],
                   truthOnSecondsFirstBurstRange=[min(r['truthOnSeconds'] for r in first_per_lap.values()),
                                                  max(r['truthOnSeconds'] for r in first_per_lap.values())],
                   cgShorterEveryLap=all(r['cgOnSeconds'] < r['truthOnSeconds'] for r in first_per_lap.values())),
    verifierOnset50=dict(
        reproducedAllMedian=round(st.median(all50), 3), reproducedAllN=len(all50),
        verifierAllMedian=ref['all']['cgCrossMinusOwnOnset50']['median'], verifierAllN=ref['all']['cgCrossMinusOwnOnset50']['n'],
        reproducedExclK1Median=round(st.median(ex50), 3), reproducedExclK1N=len(ex50),
        verifierExclK1Median=ref['excl_k1']['cgCrossMinusOwnOnset50']['median'], verifierExclK1N=ref['excl_k1']['cgCrossMinusOwnOnset50']['n'],
        k1PerLapFirstBurst={r['lap']: r['cgMinusOwn50'] for r in k150_first.values()},
        k1MedianSixLaps=round(st.median(r['cgMinusOwn50'] for r in k150_first.values()), 3),
        k1Own50MinusT1Median=round(st.median(r['own50MinusT1'] for r in k150), 3),
        note='The reviewer text "立ち上がり50%の定義では約0.15秒" is not reproduced: with the verifier\'s own 50 % onset the k1 '
             '3DCG crossing is -0.06..+0.10 s from the onset (median of six laps above). 0.148 s is the median of '
             'own50MinusT1 (difference between the two onset rules at k1), not a 3DCG lag. Relative to the other kerbs '
             'k1 stays about 0.2 s later under both rules.'),
)
out['relativeToOtherKerbs'] = dict(
    threshold=round(out['thresholdOnset']['medianSixLaps'] - out['thresholdOnset']['otherKerbsMedian'], 3),
    onset50=round(out['verifierOnset50']['k1MedianSixLaps'] - out['verifierOnset50']['reproducedExclK1Median'], 3))
(HERE / 'report-k1-check.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
print(json.dumps(out, ensure_ascii=False, indent=1))
