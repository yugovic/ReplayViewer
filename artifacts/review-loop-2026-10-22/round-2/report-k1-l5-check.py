"""Round 2 (report, R2-03): recompute the k1 L5 two-burst facts quoted in B and C from join/event_table.json.
Run: python -I report-k1-l5-check.py  (reads only artifacts/kerb-event-truth-2026-10-22/join/event_table.json)."""
import json, os, statistics
here = os.path.dirname(os.path.abspath(__file__))
root = os.path.abspath(os.path.join(here, '..', '..', '..'))
et = json.load(open(os.path.join(root, 'artifacts', 'kerb-event-truth-2026-10-22', 'join', 'event_table.json'), encoding='utf-8'))
k1 = [c for c in et['kerbContacts'] if c['session'] == 'fuji_aim_2020_07_30' and c['kerb'] == 'k1']
l5 = sorted([c for c in k1 if c['lap'] == 5], key=lambda c: c['truthOnsetT'])
b1, b2 = l5
cg0, cg1 = b1['block']['cgContactStartT'], b1['block']['cgContactEndT']
first = {}
for c in sorted(k1, key=lambda c: c['truthOnsetT']):
    first.setdefault(c['lap'], c)
alt = dict((lap, c['block']['dtFirst']) for lap, c in first.items())
alt[5] = b2['block']['dtFirst']
out = {
    'kind': 'report round 2 R2-03 check: k1 7/30 L5 two bursts',
    'burst1': {'onset': b1['truthOnsetT'], 'offset': b1['truthOffsetT'], 'confidence': b1['confidence'], 'channels': b1['respondingChannels'], 'videoConfirmed': b1['videoConfirmation']['confirmed'], 'audioConfidence': b1['videoConfirmation']['audioConfidence'], 'dtFirst': b1['block']['dtFirst']},
    'burst2': {'onset': b2['truthOnsetT'], 'offset': b2['truthOffsetT'], 'confidence': b2['confidence'], 'channels': b2['respondingChannels'], 'videoConfirmed': b2['videoConfirmation']['confirmed'], 'audioConfidence': b2['videoConfirmation']['audioConfidence'], 'flags': b2['t1Flags'], 'dtFirst': b2['block']['dtFirst']},
    'secondMinusFirstOnset': round(b2['truthOnsetT'] - b1['truthOnsetT'], 3),
    'cgContact': [cg0, cg1],
    'cgContactInsideFirstBurst': b1['truthOnsetT'] <= cg0 and cg1 <= b1['truthOffsetT'],
    'cgContactEndsBeforeSecondBurst': cg1 < b2['truthOnsetT'],
    'perLapFirstBurst': {lap: c['block']['dtFirst'] for lap, c in sorted(first.items())},
    'medianSixLapsFirstBurst': round(statistics.median(c['block']['dtFirst'] for c in first.values()), 3),
    'perLapIfL5SecondBurst': dict(sorted(alt.items())),
    'lateLapsIfL5SecondBurst': sum(1 for v in alt.values() if v > 0),
    'medianSixLapsIfL5SecondBurst': round(statistics.median(alt.values()), 3),
}
json.dump(out, open(os.path.join(here, 'report-k1-l5-check.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))
