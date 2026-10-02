"""(a) shipped + OLD CG kerb lines, (b) shipped + NEW kerb_zones, (c-in) candidate + NEW (in-sample),
plus raw (d = 0) references; both races; two window definitions. Independent scorer (kc_recheck_scorer)."""
import json
from pathlib import Path

import numpy as np

import kc_recheck_scorer as S

OUT = Path(__file__).resolve().parent
ANOMALY = {(2, 'k21'), (2, 'k9'), (2, 'k10'), (2, 'k24')}   # 7/30 L2 3250-3900 m kerbs (as flagged by the implementer)


def offsets(race, name):
    if name == 'raw':
        laps = json.loads((S.ROOT / f'public/data/races/{race}/laps.json').read_text(encoding='utf-8'))
        return {int(s['lap']): np.zeros(2) for s in laps['selected']}
    reg = json.loads((S.ROOT / f'public/data/races/{race}/{name}').read_text(encoding='utf-8'))
    return {int(l['lap']): np.asarray(l['offsetMeters'], float) for l in reg['laps']}


def main():
    lines = {k: S.load_lines(k) for k in ('old', 'new')}
    res = {'definitions': {
        'g': 'min over the label pass window of min(inner front, inner rear) tyre outer edge signed gap to the BLOCK line (m, negative = on blocks)',
        'contradiction': f'strong with g > +{S.STRONG_TOL} m, or silent with g < {S.SILENT_DEEP} m',
        'auc': 'P(g_strong < g_silent) over all strong x silent pairs (ties 0.5)',
        'windows': {'label': 'label t0..t1 from kerb_contacts.json', 'station': 'kerb station range +-3 m at the evaluated offset'},
        'oldLines': 'geometry.json curbs[i] inner line as k{i} (no Dunlop-R)', 'newLines': 'kerb_zones.json block line'},
        'configs': {}}
    rows_all = {}
    for race in ('fuji_aim_2020_07_30', 'fuji_aim_01'):
        labels = S.labels_for(race)
        laps = {}
        regs = ['raw', 'gps_registration.json'] + (['gps_registration_kerb.json'] if race == 'fuji_aim_2020_07_30' else [])
        for reg in regs:
            off = offsets(race, reg)
            for kind in ('old', 'new'):
                if reg == 'gps_registration_kerb.json' and kind == 'old':
                    continue
                for window in ('label', 'station'):
                    name = f'{race}|{reg}|{kind}|{window}'
                    rows = S.score(race, lines[kind], off, window, labels, laps)
                    rows_all[name] = rows
                    e = {'all': S.metrics(rows)}
                    if race == 'fuji_aim_2020_07_30':
                        e['withoutL2Anomaly'] = S.metrics(rows, drop=lambda r: (r['lap'], r['kerb']) in ANOMALY)
                        e['withoutDunlopR'] = S.metrics(rows, drop=lambda r: r['kerb'] == 'kDunlopR')
                        e['frontOnly'] = S.metrics(rows, key='gFront')
                        e['tolSweepStrongButOff'] = {f'{t:g}': S.metrics(rows, strong_tol=t, silent_deep=-0.40 - t)['contradictions']
                                                     for t in (0.0, 0.10, 0.15, 0.25)}
                    res['configs'][name] = e
                    a = e['all']
                    print(f"{name:70s} n={a['labels']} meas={a['measurable']} S={a['strong']} N={a['silent']} "
                          f"contra={a['contradictions']} auc={a['auc']} wk={a['withinKerbAuc']} {a['contradictionList']} unmeas={a['unmeasurableList']}")
    # per-label change old -> new (shipped, label window)
    for race in ('fuji_aim_2020_07_30', 'fuji_aim_01'):
        a = {(r['lap'], r['kerb']): r for r in rows_all[f'{race}|gps_registration.json|old|label']}
        b = {(r['lap'], r['kerb']): r for r in rows_all[f'{race}|gps_registration.json|new|label']}
        per = {}
        for k, rb in b.items():
            ra = a[k]
            if ra['g'] is not None and rb['g'] is not None:
                per.setdefault(k[1], []).append(rb['g'] - ra['g'])
        res[f'{race}|gChangeNewMinusOld_shipped_labelWindow'] = {
            k: dict(n=len(v), median=round(float(np.median(v)), 3), min=round(float(min(v)), 3), max=round(float(max(v)), 3))
            for k, v in sorted(per.items())}
    # bootstrap (b) - (a), 7/30, labels measurable in both
    r30 = 'fuji_aim_2020_07_30'
    res['bootstrap_newMinusOld_shipped_7_30'] = {
        w: S.cluster_bootstrap(rows_all[f'{r30}|gps_registration.json|new|{w}'], rows_all[f'{r30}|gps_registration.json|old|{w}'])
        for w in ('label', 'station')}
    res['bootstrap_candidateInSampleMinusShippedNew_7_30_NOT_HELD_OUT'] = S.cluster_bootstrap(
        rows_all[f'{r30}|gps_registration_kerb.json|new|label'], rows_all[f'{r30}|gps_registration.json|new|label'])
    (OUT / 'course_and_insample.json').write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    (OUT / 'course_and_insample_rows.json').write_text(json.dumps(rows_all, ensure_ascii=False, indent=0), encoding='utf-8')
    print(json.dumps({k: v for k, v in res.items() if k.startswith('bootstrap')}, indent=1))


if __name__ == '__main__':
    main()
