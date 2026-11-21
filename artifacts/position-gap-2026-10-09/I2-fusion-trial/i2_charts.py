"""I2 fusion trial, step 4: charts from out/eval_*.json and out/chartdata_*.json (numbers only, no video pixels).

  charts/i2_l2_sector.png        7/30: lateral change vs shipped and GPS residual vs the fused path, 3000-4100 m
  charts/i2_kerb_labels.png      contradiction count / AUC per variant (7/30)
  charts/i2_outage.png           GPS-outage bridging: lateral error vs outage length
  charts/i2_lateral_change.png   distribution of |lateral change vs shipped| per variant (7/30, all laps)
  charts/i2_ma5_cornercut.png    fusedBest - shipped lateral vs lateral acceleration (shipped 5-point MA corner cut)
  charts/i2_outage_probe.png     sliding GPS-outage probe along the lap (i2_anomaly_probe.py)
Usage: python i2_charts.py [--out DIR]
"""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
RACE = 'fuji_aim_2020_07_30'
COL = {'imuPhysical': '#17becf', 'shipped': '#555555', 'rawGps': '#999999', 'fusedWhite': '#1f77b4', 'fusedGM': '#d62728',
       'fusedBest': '#2ca02c', 'fusedAccy': '#9467bd', 'imuHeavy': '#ff7f0e', 'lolo': '#8c564b'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', type=Path, default=HERE / 'out')
    a = ap.parse_args()
    cdir = HERE / 'charts'
    cdir.mkdir(exist_ok=True)
    ev = json.loads((a.out / f'eval_{RACE}.json').read_text(encoding='utf-8'))
    cd = json.loads((a.out / f'chartdata_{RACE}.json').read_text(encoding='utf-8'))
    names = [n for n in ev['kerbLabels']]

    # 1) L2 sector
    fig, ax = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
    for lap, item in cd['laps'].items():
        st = np.asarray(item['st'])
        m = (st > 3000) & (st < 4100)
        o = np.argsort(st[m])
        lw, al = (1.8, 1.0) if lap == '2' else (0.8, 0.45)
        for name, axi in (('fusedBest', 0), ('imuPhysical', 1)):
            key = 'dLat_' + name
            if key in item:
                ax[axi].plot(st[m][o], np.asarray(item[key])[m][o], lw=lw, alpha=al,
                             color=COL[name] if lap == '2' else '#888888', label=f'L{lap}' if lap == '2' else None)
        r = item.get('res_fusedBest')
        if r:
            rs, rl = np.asarray(r['st']), np.asarray(r['lat'])
            mm = (rs > 3000) & (rs < 4100)
            oo = np.argsort(rs[mm])
            ax[2].plot(rs[mm][oo], rl[mm][oo], lw=lw, alpha=al, color='#d62728' if lap == '2' else '#888888',
                       label='L2' if lap == '2' else None)
    for axi, t in zip(ax, ('fusedBest (likelihood-chosen model) minus shipped: lateral m (+ = right of travel)',
                           'imuPhysical (IMU-trusting prior: GPS white 0.3 m + slow GM 1.0 m/30 s) minus shipped: lateral m',
                           'native GPS (+offset) minus fusedBest path at the fixes: lateral m')):
        axi.axvspan(3250, 3900, color='#f2c94c', alpha=0.15)
        axi.axhline(0, color='k', lw=0.5)
        axi.set_title(t, fontsize=10)
        axi.legend(loc='upper left', fontsize=8)
        axi.grid(alpha=0.3)
    ax[2].set_xlabel('CG station (m); shaded = flagged 7/30 L2 anomaly 3250-3900 m; grey = other laps')
    fig.tight_layout()
    fig.savefig(cdir / 'i2_l2_sector.png', dpi=90)
    plt.close(fig)

    # 2) kerb labels
    fig, ax = plt.subplots(1, 2, figsize=(12, 4))
    c = [ev['kerbLabels'][n]['allLabels']['contradictionCount'] for n in names]
    c2 = [ev['kerbLabels'][n]['withoutAnomalyLabels']['contradictionCount'] for n in names]
    au = [ev['kerbLabels'][n]['allLabels']['auc'] for n in names]
    au2 = [ev['kerbLabels'][n]['withoutAnomalyLabels']['auc'] for n in names]
    x = np.arange(len(names))
    ax[0].bar(x - 0.2, c, 0.4, label='all labels', color=[COL.get(n, '#333') for n in names])
    ax[0].bar(x + 0.2, c2, 0.4, label='without L2 anomaly labels', color=[COL.get(n, '#333') for n in names], alpha=0.45)
    ax[0].set_xticks(x, names, rotation=30, fontsize=8)
    ax[0].set_title('7/30 kerb-label contradictions (lower = better)', fontsize=10)
    ax[0].legend(fontsize=8)
    ax[1].bar(x - 0.2, au, 0.4, color=[COL.get(n, '#333') for n in names])
    ax[1].bar(x + 0.2, au2, 0.4, color=[COL.get(n, '#333') for n in names], alpha=0.45)
    ax[1].set_ylim(0.4, 1.0)
    ax[1].set_xticks(x, names, rotation=30, fontsize=8)
    ax[1].set_title('7/30 AUC strong < silent (higher = better); light = without anomaly labels', fontsize=10)
    fig.tight_layout()
    fig.savefig(cdir / 'i2_kerb_labels.png', dpi=90)
    plt.close(fig)

    # 3) outage
    if ev.get('outageTest'):
        fig, ax = plt.subplots(figsize=(7, 4))
        for name, res in ev['outageTest'].items():
            Ls = [float(k[:-1]) for k in res]
            p50 = [res[k]['lateralVsWithheldGpsM'][0] for k in res]
            p90 = [res[k]['lateralVsWithheldGpsM'][1] for k in res]
            ax.plot(Ls, p50, 'o-', color=COL.get(name, '#333'), label=f'{name} median')
            ax.plot(Ls, p90, 's--', color=COL.get(name, '#333'), alpha=0.6, label=f'{name} p90')
        ax.set_xlabel('GPS position outage length (s); IMU + GPS speed bridge it')
        ax.set_ylabel('|lateral| bridged path vs withheld GPS fix (m)')
        ax.set_title('7/30: how far IMU dead reckoning agrees with GPS', fontsize=10)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(cdir / 'i2_outage.png', dpi=90)
        plt.close(fig)

    # 4) lateral change distribution
    fig, ax = plt.subplots(figsize=(8, 4))
    pq = ev['pathQuality']
    ns = [n for n in pq if n != 'shipped']
    vals = np.array([pq[n]['lateralVsShippedAllLapsM'] for n in ns], float)
    x = np.arange(len(ns))
    for i, lab in enumerate(('p50', 'p95', 'p99', 'max')):
        ax.bar(x + (i - 1.5) * 0.2, vals[:, i], 0.2, label=lab)
    ax.set_xticks(x, ns, rotation=20, fontsize=8)
    ax.set_ylabel('|lateral position change vs shipped| (m)')
    ax.set_title('7/30 all laps: how much each variant moves the drawn path sideways', fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis='y')
    fig.tight_layout()
    fig.savefig(cdir / 'i2_lateral_change.png', dpi=90)
    plt.close(fig)
    # 5) MA5 corner cut: fusedBest - shipped lateral vs path lateral acceleration
    fig, ax = plt.subplots(figsize=(6, 4.5))
    xs, ys = [], []
    for lap, item in cd['laps'].items():
        a_ = np.asarray(item['alat_fusedBest'])
        d_ = np.asarray(item['dLat_fusedBest'])
        n = min(len(a_), len(d_))
        xs.append(a_[:n]); ys.append(d_[:n])
    x_, y_ = np.concatenate(xs), np.concatenate(ys)
    c = np.polyfit(x_, y_, 1)
    ax.plot(x_[::3], y_[::3], '.', ms=2, alpha=0.35, color='#2ca02c')
    xx = np.linspace(x_.min(), x_.max(), 10)
    ax.plot(xx, np.polyval(c, xx), 'k-', lw=1, label=f'fit {c[0]:.4f} s^2 x a_lat (theory 5-pt MA: -0.0100)')
    ax.set_xlabel('path lateral acceleration (m/s^2, + = right turn)')
    ax.set_ylabel('fusedBest minus shipped lateral (m, + = right)')
    ax.set_title('7/30: the whole fused-vs-shipped difference is the shipped\n'
                 '5-point moving-average corner cut', fontsize=10)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(cdir / 'i2_ma5_cornercut.png', dpi=90)
    plt.close(fig)
    # 6) sliding GPS-outage probe (IMU bridge vs withheld GPS), 4 s and 2 s windows
    pp = a.out / f'anomaly_probe_{RACE}.json'
    if pp.exists():
        pr = json.loads(pp.read_text(encoding='utf-8'))
        fig, ax = plt.subplots(2, 1, figsize=(12, 6.5), sharex=True)
        for axi, W in zip(ax, ('2s', '4s')):
            w = pr['windows'][W]
            for lap, rows in w['rows'].items():
                rows = sorted(rows, key=lambda r: r['station'])
                st = [r['station'] for r in rows]
                v = [r['maxAbsLatM'] for r in rows]
                axi.plot(st, v, '-', lw=1.6 if lap == '2' else 0.7, color='#d62728' if lap == '2' else '#999999',
                         alpha=1.0 if lap == '2' else 0.6, label='L2' if lap == '2' else None)
            axi.axhline(w['sessionP99M'], color='k', ls='--', lw=0.8, label=f"session p99 {w['sessionP99M']} m")
            axi.axvspan(3250, 3900, color='#f2c94c', alpha=0.15)
            axi.set_ylabel('max |lat| GPS - IMU bridge (m)')
            axi.set_title(f'7/30 sliding GPS-outage probe, {W} windows (grey = other laps)', fontsize=10)
            axi.legend(fontsize=8, loc='upper left')
            axi.grid(alpha=0.3)
        ax[1].set_xlabel('CG station (m)')
        fig.tight_layout()
        fig.savefig(cdir / 'i2_outage_probe.png', dpi=90)
        plt.close(fig)
    print('charts ->', cdir)


if __name__ == '__main__':
    main()
