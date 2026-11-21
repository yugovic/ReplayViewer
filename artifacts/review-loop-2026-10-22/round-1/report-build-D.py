"""Round 1 / report area: check the embedded data block (const D) of report B against the analysis outputs and add
the camera-scale sensitivity of the clipping point (finding R1-02) as D.cam.

Reads only analysis outputs (no video, no XRK):
  artifacts/kerb-event-truth-2026-10-22/join/event_table.json, join/summary.json
Writes docs/fuji-event-truth-evaluation-2026-10-22.html in place (only the `const D = {...};` line changes) and
report-build-D.json next to this file. Run with --check to verify without writing.
"""
import json
import re
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
A = ROOT / 'artifacts' / 'kerb-event-truth-2026-10-22' / 'join'
PAGE = ROOT / 'docs' / 'fuji-event-truth-evaluation-2026-10-22.html'
CHECK_ONLY = '--check' in sys.argv

et = json.loads((A / 'event_table.json').read_text(encoding='utf-8'))
sm = json.loads((A / 'summary.json').read_text(encoding='utf-8'))
html = PAGE.read_text(encoding='utf-8')
m = re.search(r'^const D = (\{.*\});$', html, re.M)
assert m, 'const D line not found'
D = json.loads(m.group(1))
DAY = {'fuji_aim_01': '7/29', 'fuji_aim_2020_07_30': '7/30'}
report = dict(kind='report-B data block check (round 1)', mismatches=[])
mis = report['mismatches']


def close(a, b, tol=0.006):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tol


# round trip of the existing data (so a rewrite changes nothing but the added key)
base = {k: v for k, v in D.items() if k != 'cam'}
report['roundTripIdentical'] = json.dumps(base, ensure_ascii=False) == m.group(1) if 'cam' not in D else None

# clipping points (fh889 camera = the page's camera), excluding suspect features
clip_et = [c for c in et['clippingPointsVideo'] if not c.get('suspectFeature')]
key = lambda s, lap, k: (s, lap, k)
ce = {key(DAY[c['session']], c['lap'], c['kerb']): c for c in clip_et}
for c in D['clip']:
    e = ce.get(key(c['s'], c['lap'], c['kerb']))
    if e is None:
        mis.append(f"clip {c['s']} L{c['lap']} {c['kerb']} not in event_table"); continue
    v = e['cameraVariants']['fh889']
    for a, b, n in ((c['dS'], v['dS'], 'dS'), (c['dt'], v['dt'], 'dt'), (c['dLat'], v['dLat'], 'dLat'),
                    (c['dSnom'], e['cameraVariants']['nominal']['dS'], 'dSnom'), (c['dSoverlay'], e['dSOverlayOnly'], 'dSoverlay')):
        if not close(a, b):
            mis.append(f"clip {c['s']} L{c['lap']} {c['kerb']} {n}: page {a} vs table {b}")
report['clip'] = dict(page=len(D['clip']), table=len(clip_et))

# turn-in (50 % point, quality good)
te = {key(DAY[t['session']], t['lap'], t['kerb']): t for t in et['turnInAndTelemetryClip'] if t['qualityGood']}
for t in D['turn']:
    e = te.get(key(t['s'], t['lap'], t['kerb']))
    if e is None or not close(t['dS'], e['dSHeading50']) or not close(t['dt'], e['dtHeading50']):
        mis.append(f"turn {t['s']} L{t['lap']} {t['kerb']}")
report['turn'] = dict(page=len(D['turn']), table=len(te))

# kerb contacts
kc = {}
for c in et['kerbContacts']:
    kc.setdefault(key(DAY[c['session']], c['lap'], c['kerb']), []).append(c)
used = {}
for c in D['contacts']:
    k = key(c['s'], c['lap'], c['kerb'])
    i = used.get(k, 0); used[k] = i + 1
    e = sorted(kc.get(k, []), key=lambda x: x['truthOnsetT'])[i] if i < len(kc.get(k, [])) else None
    if e is None:
        mis.append(f"contact {k} missing"); continue
    b = e['block']
    if not (close(c['gap'], b.get('gapAtOnset')) and close(c['dS'], b.get('dSFirst'), 0.011) and c['prim'] == e['primary']
            and c['cg'] == b.get('cgShowsContact')):
        mis.append(f"contact {k}: page {c} vs table gap {b.get('gapAtOnset')} dS {b.get('dSFirst')}")
report['contacts'] = dict(page=len(D['contacts']), table=len(et['kerbContacts']))

# passes
pc = {}
for p in D['passes']:
    pc[(p['s'], p['c'])] = pc.get((p['s'], p['c']), 0) + 1
tc = {}
for p in et['kerbPasses']:
    tc[(DAY[p['session']], p['classBlock'])] = tc.get((DAY[p['session']], p['classBlock']), 0) + 1
if pc != tc:
    mis.append(f'passes page {pc} vs table {tc}')
report['passes'] = {f'{k[0]} {k[1]}': v for k, v in sorted(pc.items())}

# camera-scale sensitivity of the clipping point (R1-02)
CAMS = [('fh590_profileLike', 'f 472 px・高さ1.25 m', '10/03の推定'),
        ('fh889', 'f 711 px・高さ1.25 m', '仕様の画角84°・このページの値'),
        ('fh1066', 'f 711 px・高さ1.50 m', '高さを高めに見た場合'),
        ('nominal', 'f 967 px・高さ1.26 m', '動画からの当初の推定')]
cs = sm['cameraSensitivityClippingPoint']
cam = []
for k, label, note in CAMS:
    row = dict(fh=cs['fuji_aim_2020_07_30'][k]['fTimesH'], cam=label, note=note)
    for sess, tag in (('fuji_aim_2020_07_30', 'd30'), ('fuji_aim_01', 'd29')):
        blk = cs[sess][k]
        row[tag] = dict(n=blk['dS']['n'], dS=blk['dS']['median'], dt=blk['dt']['median'], typ=blk['dS']['medianAbs'])
        # recompute from event_table cameraVariants (same 55 non-suspect tips)
        vals = [c['cameraVariants'][k] for c in clip_et if c['session'] == sess]
        rdS = round(st.median(v['dS'] for v in vals), 2)
        rdt = round(st.median(v['dt'] for v in vals), 3)
        if not (close(rdS, blk['dS']['median'], 0.011) and close(rdt, blk['dt']['median'], 0.0015) and len(vals) == blk['dS']['n']):
            mis.append(f'cam {k} {sess}: recomputed dS {rdS} dt {rdt} n {len(vals)} vs summary {blk["dS"]["median"]} {blk["dt"]["median"]}')
    cam.append(row)
report['cam'] = cam
report['contactMatchingFTimesH0730'] = cs['reconcilingCameraScale']['fuji_aim_2020_07_30']['fTimesHThatMatches']

D['cam'] = cam
new_line = 'const D = ' + json.dumps(D, ensure_ascii=False) + ';'
changed = new_line != m.group(0)
report['pageChanged'] = changed and not CHECK_ONLY
if changed and not CHECK_ONLY:
    PAGE.write_text(html[:m.start()] + new_line + html[m.end():], encoding='utf-8')
report['ok'] = not mis
(HERE / 'report-build-D.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
print(json.dumps({k: v for k, v in report.items() if k != 'cam'}, ensure_ascii=False, indent=1))
print('cam rows:', [(r['fh'], r['d30']['dS'], r['d29']['dS']) for r in cam])
sys.exit(0 if not mis else 1)
