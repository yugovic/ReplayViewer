"""Leave-one-kerb-out (LOKO) recheck of the opt-in candidate registration (7/30).

Fits use the candidate's own fitter (pipeline/register_gps_kerb_contact.py, default Params) because LOKO is a
property of that procedure; SCORING of the held-out kerb uses the independent scorer kc_recheck_scorer.py.

Two LOKO procedures, every kerb that has a used label:
  implementer : x0 = full in-sample candidate session, Nelder-Mead from x0 and x0 +- 0.3 m (as
                scripts/quality/apex_kpi/eval_kerb_registration.py run_fit 'loko')
  neutral     : the WHOLE candidate procedure re-run without the held-out kerb's labels: x0 = shipped session
                offset, 0.5 m grid +-3 m around it (3 best grid points) + x0 +- 0.5 m (as the full fit in
                eval_kerb_registration.main / the published candidate), so no held-out information can enter
                through the starting point.
Also the full in-sample fit (reproduction check against gps_registration_kerb.json).
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / 'pipeline'))
sys.path.insert(0, str(HERE))

RACE = 'fuji_aim_2020_07_30'
VEHICLE = ROOT / 'public/data/vehicles/mazda2-dj.json'
_S = {}


def setup():
    if 'S' not in _S:
        import register_gps_kerb_contact as R
        _S['R'] = R
        _S['S'] = R.setup('fuji', RACE, 'dry', VEHICLE, params=R.Params())
    return _S['R'], _S['S']


def grid_around(c):
    ax = np.arange(-3.0, 3.01, 0.5)
    return [np.asarray(c, float) + np.array([a, b]) for a in ax for b in ax]


def fit(task):
    R, S = setup()
    P = S['problem']
    t0 = time.time()
    h = task.get('holdout')
    ex = None if h is None else (lambda lab, k=h: lab['kerb'] == k)
    if task['proc'] == 'full':
        s = P.fit_session(S['ref_session'], grid=grid_around(S['ref_session']))
    elif task['proc'] == 'implementer':
        s = P.fit_session(np.asarray(task['x0'], float), ex, True, spread=0.3)
    elif task['proc'] == 'neutral':
        s = P.fit_session(S['ref_session'], ex, True, grid=grid_around(S['ref_session']))
    else:
        raise ValueError(task['proc'])
    laps = {n: P.fit_lap(n, s, ex, True) for n in P.laps}
    return dict(task, session=[float(v) for v in s], laps={str(n): [float(v) for v in d] for n, d in laps.items()},
                seconds=round(time.time() - t0, 1))


def main():
    import kc_recheck_scorer as K
    R, S = setup()
    P = S['problem']
    kerbs = sorted({l['kerb'] for l in P.labels if l['used']})
    print('used-label kerbs', kerbs, 'labels used', sum(l['used'] for l in P.labels), flush=True)
    t0 = time.time()
    full = fit({'proc': 'full'})
    print('full in-sample session', np.round(full['session'], 3), {n: np.round(d, 3).tolist() for n, d in full['laps'].items()},
          f"{time.time() - t0:.0f}s", flush=True)
    tasks = [dict(proc='implementer', holdout=k, x0=full['session']) for k in kerbs] + \
            [dict(proc='neutral', holdout=k) for k in kerbs]
    with ProcessPoolExecutor(max_workers=14) as ex:
        res = list(ex.map(fit, tasks, chunksize=1))
    print(f'{len(res)} LOKO fits done {time.time() - t0:.0f}s', flush=True)

    labels = K.labels_for(RACE)
    lines = K.load_lines('new')
    laps = {}
    pub = json.loads((ROOT / f'public/data/races/{RACE}/gps_registration_kerb.json').read_text(encoding='utf-8'))
    pub_off = {int(l['lap']): l['offsetMeters'] for l in pub['laps']}
    out = {'usedLabelKerbs': kerbs,
           'fullInSampleRefit': {'session': np.round(full['session'], 3).tolist(),
                                 'laps': {n: np.round(d, 3).tolist() for n, d in full['laps'].items()},
                                 'published': {'session': pub['sessionOffsetMeters'], 'laps': {str(k): v for k, v in pub_off.items()}},
                                 'maxLapDiffVsPublishedM': round(max(float(np.hypot(*(np.asarray(full['laps'][str(n)]) - np.asarray(pub_off[n]))))
                                                                     for n in pub_off), 4)},
           'folds': {}, 'metrics': {}}
    rows_by = {}
    for proc in ('implementer', 'neutral'):
        rows = []
        for r in res:
            if r['proc'] != proc:
                continue
            off = {int(n): np.asarray(d) for n, d in r['laps'].items()}
            rr = K.score(RACE, lines, off, 'label', labels, laps, only=lambda lab, k=r['holdout']: lab['kerb'] == k)
            rows += rr
            out['folds'][f"{proc}|{r['holdout']}"] = {'session': np.round(r['session'], 3).tolist(),
                                                       'laps': {n: np.round(d, 3).tolist() for n, d in r['laps'].items()},
                                                       'seconds': r['seconds'], 'rows': rr}
        rows_by[proc] = rows
        anomaly = {(2, 'k21'), (2, 'k9'), (2, 'k10'), (2, 'k24')}
        out['metrics'][proc] = {'all': K.metrics(rows),
                                'withoutL2Anomaly': K.metrics(rows, drop=lambda r: (r['lap'], r['kerb']) in anomaly),
                                'tolSweep': {f'{t:g}': K.metrics(rows, strong_tol=t, silent_deep=-0.40 - t)['contradictions']
                                             for t in (0.0, 0.10, 0.15, 0.25)}}
        m = out['metrics'][proc]['all']
        print(proc, 'LOKO', m['labels'], m['measurable'], 'contra', m['contradictions'], m['contradictionList'], 'auc', m['auc'],
              'wk', m['withinKerbAuc'], flush=True)
    # shipped + new zones rows (same scorer) for the bootstrap comparison
    reg = json.loads((ROOT / f'public/data/races/{RACE}/gps_registration.json').read_text(encoding='utf-8'))
    shipped = K.score(RACE, lines, {int(l['lap']): np.asarray(l['offsetMeters']) for l in reg['laps']}, 'label', labels, laps)
    anomaly = {(2, 'k21'), (2, 'k9'), (2, 'k10'), (2, 'k24')}
    out['bootstrap'] = {f'{proc}LOKO_minus_shippedNew': {
        'all': K.cluster_bootstrap(rows_by[proc], shipped),
        'withoutL2Anomaly': K.cluster_bootstrap(rows_by[proc], shipped, drop=lambda r: (r['lap'], r['kerb']) in anomaly)}
        for proc in ('implementer', 'neutral')}
    out['shippedNew'] = K.metrics(shipped)
    (HERE / 'loko_recheck.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(out['bootstrap'], indent=1))
    print('-> loko_recheck.json', f'{time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
