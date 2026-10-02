"""Re-run leave-one-kerb-out fits of the opt-in candidate (fitter = pipeline/register_gps_kerb_contact.py, unchanged)
and score the held-out kerb with the independent scorer kc_scorer.py.

Protocols
  impl    : as eval_kerb_registration.py: start at the in-sample candidate session offset, spread 0.3, no grid
  neutral : as the candidate itself was fitted (register_gps_kerb_contact.main): start at the SHIPPED session offset
            plus the best points of a +-3 m / 0.5 m grid; the held-out kerb's labels never touch the start point
  full    : neutral protocol without exclusion (should reproduce gps_registration_kerb.json)
"""
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = Path(r'D:\00_Dev\ReplayViewer')
sys.path.insert(0, str(ROOT / 'pipeline'))
sys.path.insert(0, str(HERE))

RACE, TRACK = 'fuji_aim_2020_07_30', 'fuji'
VEH = ROOT / 'public/data/vehicles/mazda2-dj.json'
_S = {}


def S():
    if 'S' not in _S:
        import register_gps_kerb_contact as R
        _S['S'] = R.setup(TRACK, RACE, 'dry', VEH)
    return _S['S']


def run(task):
    t0 = time.time()
    s = S()
    P = s['problem']
    k = task['holdout']
    ex = None if k is None else (lambda lab, kk=k: lab['kerb'] == kk)
    if task['protocol'] == 'impl':
        sess = P.fit_session(np.asarray(task['x0']), ex, True, spread=0.3)
    else:
        c = np.asarray(s['ref_session'], float)
        ax = np.arange(-3.0, 3.01, 0.5)
        grid = [c + np.array([a, b]) for a in ax for b in ax]
        sess = P.fit_session(c, ex, True, grid=grid)
    laps = {n: P.fit_lap(n, sess, ex, True) for n in P.laps}
    return dict(task, session=[float(v) for v in sess], laps={str(n): [float(v) for v in d] for n, d in laps.items()},
                seconds=round(time.time() - t0, 1))


def main():
    s = S()
    P = s['problem']
    kerbs = sorted({l['kerb'] for l in P.labels if l['used']})
    cand = json.loads((ROOT / f'public/data/races/{RACE}/gps_registration_kerb.json').read_text(encoding='utf-8'))
    x_c = cand['sessionOffsetMeters']
    tasks = [dict(protocol='neutral', holdout=None)]
    tasks += [dict(protocol='neutral', holdout=k) for k in kerbs]
    tasks += [dict(protocol='impl', holdout=k, x0=x_c) for k in kerbs]
    workers = int(os.environ.get('WORKERS', '8'))
    print(len(tasks), 'fits on', workers, 'workers', flush=True)
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers) as ex:
        res = list(ex.map(run, tasks, chunksize=1))
    print('done', round(time.time() - t0), 's', flush=True)
    (HERE / 'loko_refits.json').write_text(json.dumps(dict(kerbs=kerbs, labelsUsed=[
        {k_: l[k_] for k_ in ('lap', 'kerb', 'class', 'used', 'weight', 'anomaly')} for l in P.labels], fits=res), indent=1) + '\n',
        encoding='utf-8')


if __name__ == '__main__':
    main()
