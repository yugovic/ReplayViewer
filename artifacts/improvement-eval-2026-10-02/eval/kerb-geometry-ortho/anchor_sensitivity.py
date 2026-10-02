"""Leak check: re-detect the ortho edges with search windows anchored WITHOUT the AFTER lines
(k-kerbs: CG kerb inner + CG road edge only; Dunlop-R: CG road edge only) and compare to results.json truth."""
import json, numpy as np
import kerb_ortho_check as K

res = json.loads((K.OUT / 'results.json').read_text(encoding='utf-8'))
import csv
rows = list(csv.DictReader(open(K.OUT / 'sections.csv', encoding='utf-8')))
f = lambda x: float(x) if x not in ('', None) else np.nan
truth = {(r['kerb'], float(r['station'])): (f(r['w_truth_paint']), f(r['w_truth_block']), f(r['w_after_paint']), f(r['w_after_block'])) for r in rows}
zones = {kb['id']: kb for kb in K.ZON['kerbs']}
cg = {f'k{k}': c for k, c in enumerate(K.GEO['curbs'])}
out = {}
for kid in K.INSIDE:
    kb = zones[kid]; side = kb['side']; zst = np.array(kb['station'], float)
    paint = np.array(kb['paint'], float); outer = np.array(kb['outer'], float)
    if kid in cg:
        rows_ = np.array(cg[kid]['rows'], float); bxy, bst = rows_[:, 1:3], rows_[:, 0]
    else:
        bxy = bst = None
    patch = K.LabPatch(np.r_[paint[:, 0], outer[:, 0]], np.r_[paint[:, 1], outer[:, 1]], margin=15.0)
    dp, db, rp, rb = [], [], [], []
    for s in zst[:-1] + K.STATION_OFFSET:
        c, n, offL, offR = K.road_at(s)
        dout = -n if side == 'left' else n
        w_edge = -offL if side == 'left' else offR
        cands = [w_edge]
        if bxy is not None:
            wb, _, _ = K.section_hit(c, dout, bxy, bst, s)
            if np.isfinite(wb): cands.append(wb)
        w_lo, w_hi = min(cands), max(cands)
        wg = np.arange(w_lo - 4.5, w_hi + 2.5 + 1e-9, K.DW)
        lab = patch.sample(c[0] + wg * dout[0], c[1] + wg * dout[1])
        r = K.detect(lab, wg, w_lo, w_hi)
        tp, tb, ap, ab = truth[(kid, round(float(s), 2))]
        dp.append(r['paint'] - tp); db.append(r['block'] - tb)
        rp.append(r['paint'] - ap); rb.append(r['block'] - ab)
    dp, db = np.array(dp), np.array(db)
    both_p = np.isfinite(dp); both_b = np.isfinite(db)
    out[kid] = dict(paint_same_within_1cm=round(float(np.mean(np.abs(dp[both_p]) < 0.01)), 3),
                    paint_n=int(both_p.sum()), paint_absmax_shift=round(float(np.nanmax(np.abs(dp))), 3) if both_p.any() else None,
                    block_same_within_1cm=round(float(np.mean(np.abs(db[both_b]) < 0.01)), 3) if both_b.any() else None,
                    block_n=int(both_b.sum()),
                    after_paint_median_with_alt_anchor=round(float(np.nanmedian(rp)), 3),
                    after_block_median_with_alt_anchor=round(float(np.nanmedian(rb)), 3))
    print(kid, out[kid], flush=True)
(K.OUT / 'anchor_sensitivity.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
