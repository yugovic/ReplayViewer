"""Leave-one-LAP-out (LOLO) recheck of the candidate (7/30), supplementary to LOKO.
Fit: candidate fitter (pipeline/register_gps_kerb_contact.py) with the held-out lap's labels removed; neutral start
(shipped session + 0.5 m grid +-3 m); held-out lap offset = track term + sigma_lap prior around the session only
(no labels), others with labels. Scoring: independent kc_recheck_scorer on the held-out lap's labels."""
from __future__ import annotations

import json
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

import run_loko as L


def fit_lolo(n0):
    R, S = L.setup()
    P = S['problem']
    t0 = time.time()
    ex = lambda lab, n=n0: lab['lap'] == n
    s = P.fit_session(S['ref_session'], ex, True, grid=L.grid_around(S['ref_session']))
    laps = {n: (P.fit_lap(n, s, ex, True) if n != n0 else P.fit_lap(n, s, None, False)) for n in P.laps}
    return dict(holdout=n0, session=[float(v) for v in s], laps={str(n): [float(v) for v in d] for n, d in laps.items()},
                seconds=round(time.time() - t0, 1))


def main():
    import kc_recheck_scorer as K
    R, S = L.setup()
    laps_nos = sorted(S['problem'].laps)
    with ProcessPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(fit_lolo, laps_nos))
    labels = K.labels_for(L.RACE)
    lines = K.load_lines('new')
    cache = {}
    rows = []
    folds = {}
    for r in res:
        off = {int(n): np.asarray(d) for n, d in r['laps'].items()}
        rr = K.score(L.RACE, lines, off, 'label', labels, cache, only=lambda lab, n0=r['holdout']: lab['lap'] == n0)
        rows += rr
        folds[str(r['holdout'])] = dict(session=np.round(r['session'], 3).tolist(), heldOutLapOffset=np.round(r['laps'][str(r['holdout'])], 3).tolist(),
                                        seconds=r['seconds'], rows=rr)
    anomaly = {(2, 'k21'), (2, 'k9'), (2, 'k10'), (2, 'k24')}
    out = dict(folds=folds, metrics={'all': K.metrics(rows),
                                     'withoutL2Anomaly': K.metrics(rows, drop=lambda r: (r['lap'], r['kerb']) in anomaly),
                                     'tolSweep': {f'{t:g}': K.metrics(rows, strong_tol=t, silent_deep=-0.40 - t)['contradictions']
                                                  for t in (0.0, 0.10, 0.15, 0.25)}})
    m = out['metrics']['all']
    print('LOLO', m['labels'], 'contra', m['contradictions'], m['contradictionList'], 'auc', m['auc'], 'wk', m['withinKerbAuc'],
          'tol', out['metrics']['tolSweep'])
    print({k: (v['session'], v['heldOutLapOffset']) for k, v in folds.items()})
    (L.HERE / 'lolo_recheck.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
