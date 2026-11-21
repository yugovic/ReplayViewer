"""I2 fusion trial, step 3b: does the IMU 'see' the 7/30 L2 3250-3900 m GPS anomaly?

Sliding GPS-outage probe: GPS positions are withheld in windows of W s (centres every 1.5 s along each lap,
windows of one smoother run >= 15 s apart); the likelihood-chosen smoother (fusedBest) bridges each window
with the IMU + GPS speed, anchored by GPS at both ends. Probe statistic per window = max |lateral| (track
normal) of withheld GPS fix minus bridged path. A short (< W) lateral GPS excursion that the IMU does not
follow shows up as a large probe value; a slow error (>> W) does not.

Background: same statistic at the same CG station on the other laps, and over the whole lap.
Writes out/anomaly_probe_<race>.json.  Usage: python i2_anomaly_probe.py [--race R] [--windows 2 4]
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import i2_fusion as F  # noqa: E402
K, base = F.K, F.base

SECTOR = (3250.0, 3900.0)
STEP_S = 1.5
GROUP_SPACING_S = 15.0


def lap_probe(task):
    race, lap, W = task
    S = F.Session(race)
    tune = json.loads((HERE / 'out' / f'tune_{race}.json').read_text(encoding='utf-8'))
    p = F.Params(**tune['best']['params'])
    cal = F.calibrate(S, S.laps, imu_dt=p.imu_dt)
    zones = K.load_zones()
    limits = K.zoned_limits(base.load_limits(K.TRACK_DIR, 0.0), zones)
    T = S.lap_T(lap)
    L = F.lap_inputs(S, lap, cal, p)
    full = F.run(L, p, cal)
    centres = np.arange(W, T - W, STEP_S)
    ngroups = int(np.ceil(GROUP_SPACING_S / STEP_S))
    rows = []
    for g in range(ngroups):
        cs = centres[g::ngroups]
        mask = np.ones(len(L.gt), bool)
        for c in cs:
            mask &= ~((L.gt > c - W / 2) & (L.gt < c + W / 2))
        r = F.run(L, p, cal, gps_mask=mask)
        for c in cs:
            m = (L.gt > c - W / 2) & (L.gt < c + W / 2)
            if m.sum() < 3:
                continue
            pred = F.gps_point(r.xs[L.gk[m]], L.gdt[m])
            idx, _ = limits.lateral(pred)
            lat = np.einsum('ij,ij->i', L.gxy[m] - pred, limits.normal[idx])
            st = float(np.median(limits.dist[idx]))
            rows.append({'tCentre': round(float(c), 2), 'station': round(st, 1), 'maxAbsLatM': round(float(np.abs(lat).max()), 3),
                         'rmsLatM': round(float(np.sqrt(np.mean(lat ** 2))), 3)})
    rows.sort(key=lambda x: x['tCentre'])
    return lap, W, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--race', default='fuji_aim_2020_07_30')
    ap.add_argument('--windows', type=float, nargs='+', default=[2.0, 4.0])
    ap.add_argument('--workers', type=int, default=8)
    a = ap.parse_args()
    S = F.Session(a.race)
    tasks = [(a.race, lap, W) for W in a.windows for lap in S.laps]
    with ProcessPoolExecutor(a.workers) as ex:
        res = list(ex.map(lap_probe, tasks))
    out = {'race': a.race, 'definition': __doc__.split('Background')[0].strip(), 'sector': SECTOR, 'windows': {}}
    for W in a.windows:
        per = {str(lap): rows for lap, w, rows in res if w == W}
        allv = np.array([r['maxAbsLatM'] for rows in per.values() for r in rows])
        summ = {}
        for lap, rows in per.items():
            st = np.array([r['station'] for r in rows])
            v = np.array([r['maxAbsLatM'] for r in rows])
            sec = (st >= SECTOR[0]) & (st <= SECTOR[1])
            # same-station comparison: for each sector window, percentile among other laps' windows within +-40 m
            pcts = []
            for s_, v_ in zip(st[sec], v[sec]):
                others = [r['maxAbsLatM'] for l2, rr in per.items() if l2 != lap for r in rr if abs(r['station'] - s_) <= 40]
                if others:
                    pcts.append(float((np.asarray(others) < v_).mean()))
            j = int(np.argmax(np.where(sec, v, -1))) if sec.any() else None
            summ[lap] = {'sectorMedianM': round(float(np.median(v[sec])), 3) if sec.any() else None,
                         'sectorMaxM': round(float(v[sec].max()), 3) if sec.any() else None,
                         'sectorMaxStation': float(st[j]) if j is not None else None,
                         'restMedianM': round(float(np.median(v[~sec])), 3), 'restP90M': round(float(np.percentile(v[~sec], 90)), 3),
                         'restMaxM': round(float(v[~sec].max()), 3),
                         'sectorMeanPercentileVsOtherLapsSameStation': round(float(np.mean(pcts)), 3) if pcts else None}
        out['windows'][f'{W:g}s'] = {'sessionMedianM': round(float(np.median(allv)), 3), 'sessionP90M': round(float(np.percentile(allv, 90)), 3),
                                     'sessionP99M': round(float(np.percentile(allv, 99)), 3), 'perLap': summ, 'rows': per}
        print(f'W={W:g}s session median {np.median(allv):.3f} p90 {np.percentile(allv, 90):.3f} p99 {np.percentile(allv, 99):.3f}')
        for lap, s_ in summ.items():
            print('  L' + lap, {k: v for k, v in s_.items()})
    (HERE / 'out' / f'anomaly_probe_{a.race}.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
