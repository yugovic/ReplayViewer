"""(a) shipped registration + OLD CG kerb lines, (b) shipped + NEW kerb_zones block lines,
(c-in) candidate registration (gps_registration_kerb.json) + new zones, in-sample.
Independent scorer kc_scorer.py; compares per-label g with the implementer's evaluation.json rows."""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kc_scorer as K  # noqa: E402

ROOT = K.ROOT
OUT = HERE
IMPL = ROOT / 'artifacts/improvement-eval-2026-10-02/after/registration-candidate/evaluation.json'
ANOMALY = {(2, 'k21'), (2, 'k9'), (2, 'k10'), (2, 'k24')}   # 7/30 L2 3250-3900 m (implementer's flag, chosen post hoc)

res = {'definitions': {
    'g': 'min over the pass of the inner front/rear tyre outer edge signed gap to the kerb BLOCK line (m, negative = on blocks)',
    'contradiction': 'strong & g > +0.15, or silent & g < -0.55', 'auc': 'P(g_strong < g_silent), ties 0.5',
    'oldCourse': 'geometry.json curbs inner line (cols 1:3) = old CG kerb start; no Dunlop-R kerb',
    'newCourse': 'public/data/tracks/fuji/cg_study/kerb_zones.json block line',
    'scorer': 'kc_scorer.py (independent re-implementation, brute-force nearest segment, projection end rule)'}}
zo, zn = K.zones_old_cg(), K.zones_new()
zn_paint = K.zones_new(line='paint')
all_rows = {}
for race in ('fuji_aim_2020_07_30', 'fuji_aim_01'):
    laps = {n: K.Lap(race, n) for n in K.race_laps(race)}
    labs = K.labels(race)
    shipped = K.offsets_from(ROOT / f'public/data/races/{race}/gps_registration.json')
    zero = {n: np.zeros(2) for n in laps}
    cfg = {
        'raw_newZones': (zn, zero),
        'a_shipped_oldCG': (zo, shipped),
        'b_shipped_newZones': (zn, shipped),
    }
    cand_path = ROOT / f'public/data/races/{race}/gps_registration_kerb.json'
    if cand_path.exists():
        cfg['c_candidate_newZones_INSAMPLE'] = (zn, K.offsets_from(cand_path))
    rows = {k: K.score(laps, labs, z, o) for k, (z, o) in cfg.items()}
    # sensitivity variants of the scorer on (a)/(b)
    rows['a_shipped_oldCG_nearestVertexEndRule'] = K.score(laps, labs, zo, shipped, end_rule='nearestVertex')
    rows['b_shipped_newZones_nearestVertexEndRule'] = K.score(laps, labs, zn, shipped, end_rule='nearestVertex')
    rows['a_shipped_oldCG_labelTimeWindow'] = K.score(laps, labs, zo, shipped, use_twin=True)
    rows['b_shipped_newZones_labelTimeWindow'] = K.score(laps, labs, zn, shipped, use_twin=True)
    if 'c_candidate_newZones_INSAMPLE' in cfg:
        rows['c_candidate_newZones_INSAMPLE_labelTimeWindow'] = K.score(laps, labs, zn, cfg['c_candidate_newZones_INSAMPLE'][1], use_twin=True)
    all_rows[race] = rows
    common = lambda r, ks={(x['lap'], x['kerb']) for x in rows['a_shipped_oldCG'] if x['g'] is not None}: (r['lap'], r['kerb']) in ks
    rr = {}
    for k, v in rows.items():
        rr[k] = {'all': K.metrics(v), 'commonWithOldCG': K.metrics(v, common)}
        if race == 'fuji_aim_2020_07_30':
            rr[k]['withoutAnomalyLabels'] = K.metrics(v, lambda r: (r['lap'], r['kerb']) not in ANOMALY)
    # per-kerb change b - a
    a = {(r['lap'], r['kerb']): r['g'] for r in rows['a_shipped_oldCG']}
    per = {}
    for r in rows['b_shipped_newZones']:
        g0 = a.get((r['lap'], r['kerb']))
        if g0 is not None and r['g'] is not None:
            per.setdefault(r['kerb'], []).append(r['g'] - g0)
    rr['gChange_b_minus_a_byKerb'] = {k: dict(median=round(float(np.median(v)), 3), min=round(float(min(v)), 3),
                                              max=round(float(max(v)), 3), n=len(v)) for k, v in sorted(per.items())}
    # verdict flips a -> b
    flips = []
    for r in rows['b_shipped_newZones']:
        g0 = a.get((r['lap'], r['kerb']))
        ca = None if g0 is None else K.contradiction(dict(r, g=g0))
        cb = None if r['g'] is None else K.contradiction(r)
        if ca != cb:
            flips.append(dict(lap=r['lap'], kerb=r['kerb'], cls=r['cls'], g_a=g0, g_b=r['g'], contra_a=ca, contra_b=cb))
    rr['verdictChanges_a_to_b'] = flips
    # bootstrap b - a (common set), c_in - b
    rr['bootstrap_b_minus_a_common'] = K.cluster_bootstrap(rows['b_shipped_newZones'], rows['a_shipped_oldCG'])
    if 'c_candidate_newZones_INSAMPLE' in rows:
        rr['bootstrap_cIn_minus_b_INSAMPLE'] = K.cluster_bootstrap(rows['c_candidate_newZones_INSAMPLE'], rows['b_shipped_newZones'])
    res[race] = rr

# compare per-label g with the implementer's rows
impl = json.loads(IMPL.read_text(encoding='utf-8'))
cmp = {}
mine = all_rows['fuji_aim_2020_07_30']
pairs = {'shipped(newZones)': ('b_shipped_newZones', impl['heldOutRows']['shipped']),
         'raw(newZones)': ('raw_newZones', impl['heldOutRows']['raw'])}
for name, (mk, irows) in pairs.items():
    m = {(r['lap'], r['kerb']): r['g'] for r in mine[mk]}
    diffs = [abs(m[(r['lap'], r['kerb'])] - r['g']) for r in irows if r['g'] is not None and m.get((r['lap'], r['kerb'])) is not None]
    cmp[name] = dict(n=len(diffs), maxAbsDiff=round(float(max(diffs)), 4), medianAbsDiff=round(float(np.median(diffs)), 4))
res['agreementWithImplementerRows'] = cmp
res['implementerCourseFix'] = {k: {x: v['allLabels'][x] for x in ('n', 'contradictionCount', 'auc', 'withinKerbAuc', 'unmeasurable')}
                               for k, v in impl['courseFixShippedRegistration'].items() if isinstance(v, dict) and 'allLabels' in v}
res['implementerCandidateInSample'] = {x: impl['heldOut']['candidateInSample (not held out)']['allLabels'][x]
                                       for x in ('n', 'contradictionCount', 'auc', 'withinKerbAuc', 'contradictions')}
(OUT / 'course_and_candidate_eval.json').write_text(json.dumps(res, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
(OUT / 'per_label_rows.json').write_text(json.dumps(all_rows, ensure_ascii=False, indent=0) + '\n', encoding='utf-8')

# console summary
for race in ('fuji_aim_2020_07_30', 'fuji_aim_01'):
    print('==', race)
    for k, v in res[race].items():
        if isinstance(v, dict) and 'all' in v:
            a = v['all']
            c = v['commonWithOldCG']
            print(f"{k:48s} meas {a['measurable']}/{a['labels']} S{a['strong']}/N{a['silent']} contra {a['contradictionCount']} AUC {a['auc']} wkAUC {a['withinKerbAuc']} "
                  f"| common: meas {c['measurable']} contra {c['contradictionCount']} AUC {c['auc']}  {a['contradictions']}")
    print('flips', res[race]['verdictChanges_a_to_b'])
    print('boot b-a', res[race]['bootstrap_b_minus_a_common'])
    if 'bootstrap_cIn_minus_b_INSAMPLE' in res[race]:
        print('boot cIn-b', res[race]['bootstrap_cIn_minus_b_INSAMPLE'])
    print('gchange', res[race]['gChange_b_minus_a_byKerb'])
print(res['agreementWithImplementerRows'])
