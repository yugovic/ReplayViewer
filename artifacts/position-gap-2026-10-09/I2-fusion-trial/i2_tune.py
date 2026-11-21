"""I2 fusion trial, step 2: choose smoother hyper-parameters by the GPS innovation likelihood only
(forward EKF negative log-likelihood per GPS position fix). Kerb labels are NOT used for tuning.

Coordinate search per session: A heading noise x side-slip model, B GPS white / Gauss-Markov error model,
C dynamics again, D IMU delay x bias walk, E GPS model again. Per-lap NLL tables are stored, so leave-one-lap-out choices are argmin over the
other laps' sum (no refit needed).

Writes <out>/tune_<race>.json.  Usage: python i2_tune.py [--race R ...] [--out DIR]
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import i2_fusion as F  # noqa: E402

_S = {}


def _session(race):
    if race not in _S:
        _S[race] = F.Session(race)
    return _S[race]


def eval_params(task):
    race, pd, imu_dt_calib = task
    S = _session(race)
    p = F.Params(**pd)
    cal = F.calibrate(S, S.laps, imu_dt=p.imu_dt)
    out = {}
    for lap in S.laps:
        L = F.lap_inputs(S, lap, cal, p)
        r = F.run(L, p, cal, smooth=False)
        out[str(lap)] = r.nll / r.n_pos
    return pd, out


def key(pd):
    return json.dumps(pd, sort_keys=True)


def search(race, grid, workers):
    tasks = [(race, pd, None) for pd in grid]
    with ProcessPoolExecutor(workers) as ex:
        res = list(ex.map(eval_params, tasks))
    return res


def best(res, laps_subset=None):
    def score(item):
        pd, per = item
        ks = per.keys() if laps_subset is None else [str(l) for l in laps_subset]
        return float(np.mean([per[k] for k in ks]))
    item = min(res, key=score)
    return item[0], score(item)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--race', action='append')
    ap.add_argument('--out', type=Path, default=HERE / 'out')
    ap.add_argument('--workers', type=int, default=6)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    for race in a.race or ['fuji_aim_2020_07_30', 'fuji_aim_01']:
        t0 = time.time()
        S = _session(race)
        base_p = asdict(F.Params(sigma_white=0.05, q_chi_deg=0.6))
        allres = []

        def dyn_grid(p0):
            g = []
            for q in (0.05, 0.1, 0.2, 0.4, 0.6):
                g.append(dict(p0, q_chi_deg=q, sigma_beta_deg=0.0))
                for sb in (0.5, 1.0, 2.0):
                    for tb in (0.5, 2.0):
                        g.append(dict(p0, q_chi_deg=q, sigma_beta_deg=sb, tau_beta=tb))
            return g

        def gps_grid(p0):
            g = []
            for w in (0.02, 0.05, 0.1, 0.2):
                g.append(dict(p0, sigma_white=w, sigma_gm=0.0))
                for gm in (0.2, 0.5, 1.0):
                    for tau in (5.0, 20.0, 60.0):
                        g.append(dict(p0, sigma_white=w, sigma_gm=gm, tau_gm=tau))
            return g

        stages = {}
        r = search(race, dyn_grid(base_p), a.workers); allres += r; pA, sA = best(r); stages['A_dynamics'] = (pA, sA)
        r = search(race, gps_grid(pA), a.workers); allres += r; pB, sB = best(r); stages['B_gpsModel'] = (pB, sB)
        r = search(race, dyn_grid(pB), a.workers); allres += r; pC, sC = best(r); stages['C_dynamics'] = (pC, sC)
        g4 = [dict(pC, imu_dt=d, q_b_deg=qb) for d in (-0.06, -0.04, -0.02, 0.0, 0.02) for qb in (0.005, 0.02)]
        r = search(race, g4, a.workers); allres += r; pD, sD = best(r); stages['D_imuDelay'] = (pD, sD)
        r = search(race, gps_grid(pD), a.workers); allres += r; pE, sE = best(r); stages['E_gpsModel'] = (pE, sE)
        uniq = {}
        for pd, per in allres:
            uniq[key(pd)] = (pd, per)
        allres = list(uniq.values())
        pbest, sbest = best(allres)
        pwhite, swhite = best([x for x in allres if x[0]['sigma_gm'] == 0.0])
        pgm, sgm = best([x for x in allres if x[0]['sigma_gm'] > 0.0])
        lolo = {}
        for lap in S.laps:
            others = [l for l in S.laps if l != lap]
            pl, sl = best(allres, others)
            lolo[str(lap)] = {'params': pl, 'meanNllOtherLaps': sl}
        cal = F.calibrate(S, S.laps, imu_dt=pbest['imu_dt'])
        cal_lolo = {str(lap): asdict(F.calibrate(S, [l for l in S.laps if l != lap], imu_dt=lolo[str(lap)]['params']['imu_dt']))
                    for lap in S.laps}
        doc = {'race': race, 'criterion': 'mean forward-EKF GPS-position innovation NLL per fix (lower = better); labels not used',
               'best': {'params': pbest, 'meanNll': sbest}, 'bestWhiteOnly': {'params': pwhite, 'meanNll': swhite},
               'bestGM': {'params': pgm, 'meanNll': sgm},
               'stages': {k: {'params': v[0], 'meanNll': v[1]} for k, v in stages.items()},
               'calibration': asdict(cal), 'lolo': lolo, 'calibrationLolo': cal_lolo,
               'table': [{'params': pd, 'perLapNll': per, 'meanNll': float(np.mean(list(per.values())))} for pd, per in allres],
               'runtimeSeconds': round(time.time() - t0, 1)}
        (a.out / f'tune_{race}.json').write_text(json.dumps(doc, indent=1), encoding='utf-8')
        print(race, 'best', pbest, round(sbest, 4), '| white', round(swhite, 4), '| gm', pgm, round(sgm, 4), 'time', doc['runtimeSeconds'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
