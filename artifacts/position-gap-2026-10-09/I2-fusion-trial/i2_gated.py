"""I2 fusion trial, step 3c (POST-HOC, exploratory): innovation-gated fusion.

Rule (fixed before scoring, but chosen after seeing the probe): drop the GPS positions inside every 4 s probe
window of i2_anomaly_probe.py whose max |lateral GPS - IMU bridge| exceeds the session p99 of that statistic;
the likelihood-chosen smoother (fusedBest) bridges those windows with the IMU. Scored with the same kerb-label
metrics as i2_eval.py (contradictions / AUC; all labels and without the L2 anomaly labels), plus the g of every
label whose pass overlaps a dropped window.
Writes out/gated_<race>_<window>.json.  Usage: python i2_gated.py [--race R]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import i2_fusion as F  # noqa: E402
import i2_eval as V  # noqa: E402
K, base = F.K, F.base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--race', default='fuji_aim_2020_07_30')
    ap.add_argument('--window', default='4s')
    a = ap.parse_args()
    probe = json.loads((HERE / 'out' / f'anomaly_probe_{a.race}.json').read_text(encoding='utf-8'))
    pw = probe['windows'][a.window]
    thr = pw['sessionP99M']
    W = float(a.window[:-1])
    S = F.Session(a.race)
    tune = json.loads((HERE / 'out' / f'tune_{a.race}.json').read_text(encoding='utf-8'))
    p = F.Params(**tune['best']['params'])
    cal = F.calibrate(S, S.laps, imu_dt=p.imu_dt)
    zones = K.load_zones()
    limits = K.zoned_limits(base.load_limits(K.TRACK_DIR, 0.0), zones)
    dropped, paths_g, paths_f = {}, {}, {}
    for lap in S.laps:
        win = [r for r in pw['rows'][str(lap)] if r['maxAbsLatM'] > thr]
        dropped[str(lap)] = win
        L = F.lap_inputs(S, lap, cal, p)
        mask = np.ones(len(L.gt), bool)
        for r in win:
            mask &= ~((L.gt > r['tCentre'] - W / 2) & (L.gt < r['tCentre'] + W / 2))
        paths_g[lap] = F.FusedPaths(S, lap, L, F.run(L, p, cal, gps_mask=mask), limits)
        paths_f[lap] = F.FusedPaths(S, lap, L, F.run(L, p, cal), limits)
    labels = V.labels_for(a.race, zones)
    mg, rows_g = V.label_metrics(V.kpi_rows(paths_g, zones, limits), labels)
    mf, rows_f = V.label_metrics(V.kpi_rows(paths_f, zones, limits), labels)
    touched = []
    for lab_g, lab_f in zip(rows_g, rows_f):
        if lab_g['g'] != lab_f['g']:
            touched.append({'lap': lab_g['lap'], 'kerb': lab_g['kerb'], 'cls': lab_g['cls'], 'gFusedBest': lab_f['g'], 'gGated': lab_g['g']})
    out = {'race': a.race, 'posthoc': True, 'window': a.window, 'thresholdM': thr, 'droppedWindows': dropped,
           'fusedBest': mf, 'gated': mg, 'labelsWhoseGChanged': touched,
           'note': 'exploratory; n of affected labels is tiny, not a validated method'}
    (HERE / 'out' / f'gated_{a.race}_{a.window}.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
    print('threshold', thr, 'dropped', {k: [(r['station'], r['maxAbsLatM']) for r in v] for k, v in dropped.items() if v})
    for n, m in (('fusedBest', mf), ('gated', mg)):
        print(n, m['allLabels']['contradictionCount'], m['allLabels']['auc'], '| noAnom', m['withoutAnomalyLabels']['contradictionCount'], m['withoutAnomalyLabels']['auc'])
    print('changed', touched)
    return 0


if __name__ == '__main__':
    sys.exit(main())
