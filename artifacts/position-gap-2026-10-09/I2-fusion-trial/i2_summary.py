"""I2 fusion trial, step 5: compact summary (out/i2_summary.json) from out/eval_*.json and out/tune_*.json.
Usage: python i2_summary.py
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / 'out'
RACES = {'fuji_aim_2020_07_30': '7/30 dry', 'fuji_aim_01': '7/29 wet'}
WATCH = [(2, 'k21'), (2, 'k23'), (2, 'k24'), (5, 'k9'), (5, 'k10')]


def main():
    summary = {'kind': 'i2-gnss-imu-smoother-trial', 'surveyTruth': False,
               'scripts': ['i2_extract.py', 'i2_fusion.py', 'i2_tune.py', 'i2_eval.py', 'i2_anomaly_probe.py', 'i2_gated.py',
                           'i2_charts.py', 'i2_summary.py'],
               'races': {}}
    for race, label in RACES.items():
        ev_path = OUT / f'eval_{race}.json'
        if not ev_path.exists():
            continue
        ev = json.loads(ev_path.read_text(encoding='utf-8'))
        tune = json.loads((OUT / f'tune_{race}.json').read_text(encoding='utf-8'))
        kl = {n: {'contradictions': m['allLabels']['contradictionCount'], 'auc': m['allLabels']['auc'],
                  'n': m['allLabels']['n'], 'strong': m['allLabels']['strong'], 'silent': m['allLabels']['silent'],
                  'contradictionsWithoutAnomalyLabels': m['withoutAnomalyLabels']['contradictionCount'],
                  'aucWithoutAnomalyLabels': m['withoutAnomalyLabels']['auc'],
                  'contradictionList': m['allLabels']['contradictions']} for n, m in ev['kerbLabels'].items()}
        boot = {n: {'contradictionDiff': b['contradictionDiff'], 'contradictionDiffCI95': b['contradictionDiffCI95'],
                    'aucDiff': b['aucDiff'], 'aucDiffCI95': b['aucDiffCI95']} for n, b in ev['kerbBootstrapVsShipped'].items()}
        watch = {}
        for row in ev['labelGTable']:
            if (row['lap'], row['kerb']) in WATCH:
                watch[f"L{row['lap']} {row['kerb']} {row['cls']}"] = {k: v for k, v in row.items()
                                                                       if k not in ('lap', 'kerb', 'cls', 'anomaly')}
        pq = {n: {k: v for k, v in q.items() if k != 'perLap'} for n, q in ev['pathQuality'].items()}
        sector = None
        if ev.get('l2Sector'):
            sector = {}
            for lap, rows in ev['l2Sector']['laps'].items():
                sector[lap] = {n: rows[n] for n in rows if n in ('shipped', 'fusedBest', 'imuPhysical', 'imuHeavy', 'imuPhysicalAccy')}
        b = tune['best']['params']

        def curve(var):
            return sorted((r['params'][var], round(r['meanNll'], 4)) for r in tune['table']
                          if all(r['params'][k] == b[k] for k in b if k != var))
        summary['races'][race] = {
            'likelihoodCurves': {'imuDelayS_vs_meanNll': curve('imu_dt'), 'gpsWhiteSigmaM_vs_meanNll': curve('sigma_white'),
                                 'headingNoiseDegPerSqrtS_vs_meanNll': curve('q_chi_deg'),
                                 'note': 'other parameters at the likelihood optimum; imu_dt is added to the shipped channel shift'},
            'label': label,
            'calibration': ev['calibration'],
            'likelihoodChoice': {'params': tune['best']['params'], 'meanNllPerFix': tune['best']['meanNll'],
                                 'bestWithSlowGpsError': {'params': tune['bestGM']['params'], 'meanNllPerFix': tune['bestGM']['meanNll']},
                                 'note': ('one-step GPS predictability prefers sigma_white at the grid minimum (0.02 m) and no slow-error '
                                          'state: the 10 Hz fixes are internally smooth (receiver-filtered), so the likelihood cannot '
                                          'separate a slow GPS error from motion')},
            'variantNllPerFix': ev.get('variantNllPerFix'),
            'kerbLabels': kl, 'bootstrapVsShipped': boot, 'watchedLabelsG': watch,
            'pathQuality': pq, 'outageTest': ev.get('outageTest'), 'lolo': ev['lolo'], 'l2Sector': sector,
        }
    # shipped 5-point moving-average corner cut (7/30): fusedBest - shipped lateral vs path lateral acceleration
    import numpy as np
    cdp = OUT / 'chartdata_fuji_aim_2020_07_30.json'
    if cdp.exists():
        cd = json.loads(cdp.read_text(encoding='utf-8'))
        xs, ys = [], []
        for item in cd['laps'].values():
            a_, d_ = np.asarray(item['alat_fusedBest']), np.asarray(item['dLat_fusedBest'])
            n = min(len(a_), len(d_))
            xs.append(a_[:n]); ys.append(d_[:n])
        x_, y_ = np.concatenate(xs), np.concatenate(ys)
        c = np.polyfit(x_, y_, 1)
        r = y_ - np.polyval(c, x_)
        summary['shippedMovingAverageCornerCut'] = {
            'race': 'fuji_aim_2020_07_30', 'fitMetersPerMs2': round(float(c[0]), 5), 'interceptM': round(float(c[1]), 4),
            'corr': round(float(np.corrcoef(x_, y_)[0, 1]), 4), 'theoryMetersPerMs2': -0.0100,
            'theory': 'centred 5-point mean of 10 Hz positions on an arc: inward offset = a_lat * dt^2 * sum(k^2)/(2*5) = a_lat * 0.01 s^2',
            'absLatAccP99Ms2': round(float(np.percentile(np.abs(x_), 99)), 2),
            'cutAtP99LatAccM': round(float(abs(c[0]) * np.percentile(np.abs(x_), 99)), 3),
            'residualAfterTermRmsM': round(float(r.std()), 4), 'residualAfterTermP95AbsM': round(float(np.percentile(np.abs(r), 95)), 4),
            'meaning': ('the shipped lat/lng (5-point moving average) put the car inside the GPS arc by ~0.011 s^2 x a_lat '
                        '(~0.13 m at 1.25 g); the likelihood-tuned GNSS+IMU smoother differs from the shipped path by exactly '
                        'this term plus ~5 mm RMS'),
        }
    pp = OUT / 'anomaly_probe_fuji_aim_2020_07_30.json'
    if pp.exists():
        pr = json.loads(pp.read_text(encoding='utf-8'))
        probe = {}
        for W, w in pr['windows'].items():
            allv = np.array([r['maxAbsLatM'] for rows in w['rows'].values() for r in rows])
            near = {lap: max(r['maxAbsLatM'] for r in rows if 3570 <= r['station'] <= 3680) for lap, rows in w['rows'].items()}
            probe[W] = {'sessionMedianM': w['sessionMedianM'], 'sessionP90M': w['sessionP90M'], 'sessionP99M': w['sessionP99M'],
                        'max3570to3680ByLapM': near,
                        'L2max3570to3680SessionPercentile': round(float((allv < near['2']).mean()), 4),
                        'perLapSector': w['perLap']}
        summary['l2OutageProbe'] = probe
    summary['gatedPosthoc'] = {}
    for W in ('2s', '4s'):
        gp = OUT / f'gated_fuji_aim_2020_07_30_{W}.json'
        if gp.exists():
            gd = json.loads(gp.read_text(encoding='utf-8'))
            summary['gatedPosthoc'][W] = {'thresholdM': gd['thresholdM'],
                                          'fusedBest': {k: gd['fusedBest']['allLabels'][k] for k in ('contradictionCount', 'auc')},
                                          'gated': {k: gd['gated']['allLabels'][k] for k in ('contradictionCount', 'auc')},
                                          'labelsWhoseGChanged': gd['labelsWhoseGChanged']}
    (OUT / 'i2_summary.json').write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding='utf-8')
    print('->', OUT / 'i2_summary.json')


if __name__ == '__main__':
    main()
