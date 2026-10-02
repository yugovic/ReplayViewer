"""Score the re-run LOKO folds (loko_refits.json) with the independent scorer; compare with the implementer's rows."""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kc_scorer as K  # noqa: E402

RACE = 'fuji_aim_2020_07_30'
ROOT = K.ROOT
ANOMALY = {(2, 'k21'), (2, 'k9'), (2, 'k10'), (2, 'k24')}
fits = json.loads((HERE / 'loko_refits.json').read_text(encoding='utf-8'))
impl = json.loads((ROOT / 'artifacts/improvement-eval-2026-10-02/after/registration-candidate/evaluation.json').read_text(encoding='utf-8'))
laps = {n: K.Lap(RACE, n) for n in K.race_laps(RACE)}
labs = K.labels(RACE)
zn = K.zones_new()
shipped = K.offsets_from(ROOT / f'public/data/races/{RACE}/gps_registration.json')
cand = K.offsets_from(ROOT / f'public/data/races/{RACE}/gps_registration_kerb.json')
rows_b = K.score(laps, labs, zn, shipped)
rows_cin = K.score(laps, labs, zn, cand)
raw = K.score(laps, labs, zn, {n: np.zeros(2) for n in laps})

out = {}
full = next(f for f in fits['fits'] if f['holdout'] is None)
out['fullRefitReproducesCandidateFile'] = bool(all(
    np.allclose(np.round(full['laps'][str(n)], 3), cand[n], atol=1e-6) for n in laps))
for proto in ('neutral', 'impl'):
    rows, moved = [], {}
    for f in fits['fits']:
        if f['protocol'] != proto or f['holdout'] is None:
            continue
        k = f['holdout']
        off = {int(n): np.asarray(d) for n, d in f['laps'].items()}
        sub = [lb for lb in labs if lb['kerb'] == k]
        rows += K.score(laps, sub, zn, off)
        shift = max(float(np.hypot(*(off[n] - cand[n]))) for n in laps)
        moved[k] = round(shift, 3)
    out[proto] = {'all': K.metrics(rows), 'withoutAnomaly': K.metrics(rows, lambda r: (r['lap'], r['kerb']) not in ANOMALY),
                  'maxLapShiftVsInSampleM': moved,
                  'foldsIdenticalToInSample': sorted(k for k, v in moved.items() if v < 0.002)}
    out[proto + '_rows'] = rows
    if proto == 'neutral':
        loko = rows
# compare with the implementer's LOKO rows
im = {(r['lap'], r['kerb']): r['g'] for r in impl['heldOutRows']['candidateLOKO']}
mm = {(r['lap'], r['kerb']): r['g'] for r in out['impl_rows']}
mn = {(r['lap'], r['kerb']): r['g'] for r in loko}
d_impl = [abs(mm[k] - im[k]) for k in im if im[k] is not None and mm.get(k) is not None]
d_neut = [abs(mn[k] - im[k]) for k in im if im[k] is not None and mn.get(k) is not None]
out['agreementWithImplementerLOKORows'] = {'implProtocol': dict(n=len(d_impl), maxAbsDiff=round(max(d_impl), 4)),
                                           'neutralProtocol': dict(n=len(d_neut), maxAbsDiff=round(max(d_neut), 4))}
out['implementerLOKO'] = {x: impl['heldOut']['candidateLOKO']['allLabels'][x] for x in ('n', 'contradictionCount', 'auc', 'withinKerbAuc', 'contradictions')}
out['implementerLOKOSessionOffsets'] = impl['lokoSessionOffsets']
# reference metrics on the same 59 labels
out['reference'] = {'raw_newZones': K.metrics(raw), 'b_shipped_newZones': K.metrics(rows_b),
                    'c_candidate_INSAMPLE': K.metrics(rows_cin)}
out['referenceWithoutAnomaly'] = {k: K.metrics(v, lambda r: (r['lap'], r['kerb']) not in ANOMALY)
                                  for k, v in (('raw_newZones', raw), ('b_shipped_newZones', rows_b), ('c_candidate_INSAMPLE', rows_cin))}
# bootstrap LOKO - shipped (b), by kerb
out['bootstrap_LOKO_minus_b'] = {'all': K.cluster_bootstrap(loko, rows_b),
                                 'withoutAnomaly': K.cluster_bootstrap(loko, rows_b, keep=lambda r: (r['lap'], r['kerb']) not in ANOMALY)}
# only the folds whose fit actually changed when the kerb was held out (k7, k9, k10): the genuinely held-out part
moved_k = [k for k, v in out['neutral']['maxLapShiftVsInSampleM'].items() if v >= 0.002]
sel = lambda r: r['kerb'] in moved_k
out['movedFoldsOnly'] = {'kerbs': moved_k, 'LOKO': K.metrics(loko, sel), 'b_shipped': K.metrics(rows_b, sel),
                         'c_inSample': K.metrics(rows_cin, sel)}
# per-label g for the held-out kerbs that moved
out['movedFoldRows'] = [dict(lap=r['lap'], kerb=r['kerb'], cls=r['cls'], g_LOKO=r['g'],
                             g_inSample=next(x['g'] for x in rows_cin if (x['lap'], x['kerb']) == (r['lap'], r['kerb'])),
                             g_shipped=next(x['g'] for x in rows_b if (x['lap'], x['kerb']) == (r['lap'], r['kerb'])))
                        for r in loko if r['kerb'] in moved_k]
# hinge activity at the in-sample fit (which labels bind the candidate)
act = []
for r in rows_cin:
    if r['g'] is None:
        continue
    if r['cls'] == 'strong' and -0.05 <= r['g']:
        act.append(f"L{r['lap']} {r['kerb']} strong g={r['g']:+.3f}")
    if r['cls'] == 'silent' and r['g'] <= -0.35:
        act.append(f"L{r['lap']} {r['kerb']} silent g={r['g']:+.3f}")
out['labelsNearHingeAtInSampleFit'] = act
(HERE / 'loko_eval.json').write_text(json.dumps(out, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
for k in ('fullRefitReproducesCandidateFile', 'agreementWithImplementerLOKORows', 'implementerLOKO'):
    print(k, out[k])
for proto in ('neutral', 'impl'):
    a, w = out[proto]['all'], out[proto]['withoutAnomaly']
    print(proto, 'all', a['measurable'], a['contradictionCount'], a['auc'], a['withinKerbAuc'], a['contradictions'], '| noAnom', w['measurable'], w['contradictionCount'], w['auc'])
    print('  identical folds', out[proto]['foldsIdenticalToInSample'])
for k, v in out['reference'].items():
    w = out['referenceWithoutAnomaly'][k]
    print(k, v['contradictionCount'], v['auc'], v['withinKerbAuc'], '| noAnom', w['contradictionCount'], w['auc'])
print('boot', json.dumps(out['bootstrap_LOKO_minus_b']))
print('moved', json.dumps({k: (v if isinstance(v, list) else {x: v[x] for x in ('measurable', 'contradictionCount', 'auc', 'contradictions')}) for k, v in out['movedFoldsOnly'].items()}, ensure_ascii=False))
for r in out['movedFoldRows']:
    print(r)
print('near hinge', act)
