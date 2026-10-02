"""Fable review: rerun the measurer's scorer for configs (a) old CG lines and (b) new kerb_zones, shipped registration, 7/30 and 7/29."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'kerb-contact-consistency-recheck'))
import kc_recheck_scorer as S
ROOT = S.ROOT
out = {}
for race in ['fuji_aim_2020_07_30', 'fuji_aim_01']:
    reg = json.loads((ROOT / f'public/data/races/{race}/gps_registration.json').read_text(encoding='utf-8'))
    sess = reg['sessionOffsetMeters']
    offs = {}
    for l in reg['laps']:
        n = int(l['lap']) if 'lap' in l else int(l['lapNumber'])
        o = l.get('offsetMeters') or l.get('lapOffsetMeters') or l.get('totalOffsetMeters')
        offs[n] = o
    print(race, 'offsets', offs)
    laps = {}
    res = {}
    for kind in ['old', 'new']:
        rows = S.score(race, S.load_lines(kind), offs, 'label', laps=laps)
        m = S.metrics(rows)
        res[kind] = dict(metrics=m, rows=rows)
        print(race, kind, {k: m[k] for k in ('labels','measurable','strong','silent','contradictions','contradictionList','auc','withinKerbAuc')})
    # per-kerb change
    A = {(r['lap'], r['kerb']): r['g'] for r in res['old']['rows'] if r['g'] is not None}
    B = {(r['lap'], r['kerb']): r['g'] for r in res['new']['rows'] if r['g'] is not None}
    import numpy as np
    ch = {}
    for k in sorted({k for _, k in A}):
        d = [B[x] - A[x] for x in A if x[1] == k and x in B]
        ch[k] = round(float(np.median(d)), 3)
    print(race, 'median dg per kerb', ch)
    # tol sweep
    for kind in ['old', 'new']:
        print(race, kind, 'tol sweep', {t: S.metrics(res[kind]['rows'], strong_tol=t, silent_deep=-0.4 - t)['contradictions'] for t in (0, 0.1, 0.15, 0.25)})
    # k15/k23 silent labels near limit
    print(race, 'k15/k23/kDunlopR rows new:', [(r['lap'], r['kerb'], r['cls'], r['g']) for r in res['new']['rows'] if r['kerb'] in ('k15', 'k23', 'kDunlopR')])
    out[race] = {k: v['metrics'] for k, v in res.items()}
    out[race]['rows_new'] = res['new']['rows']; out[race]['rows_old'] = res['old']['rows']
Path(__file__).with_name('rerun_ab.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
