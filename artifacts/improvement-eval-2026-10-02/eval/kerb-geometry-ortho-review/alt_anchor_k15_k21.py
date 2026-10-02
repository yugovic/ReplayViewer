"""Review check: for k15 and k21, re-detect truth WITHOUT the AFTER lines placing the search window
(CG inner + CG road edge only), then report AFTER/BEFORE residual stats on the sections whose truth moved."""
import sys, json, csv, numpy as np
sys.path.insert(0, r'D:/00_Dev/ReplayViewer/artifacts/improvement-eval-2026-10-02/eval/kerb-geometry-ortho')
import kerb_ortho_check as K
rows = list(csv.DictReader(open(K.OUT / 'sections.csv', encoding='utf-8')))
f = lambda x: float(x) if x not in ('', None) else np.nan
truth = {(r['kerb'], float(r['station'])): (f(r['w_truth_paint']), f(r['w_truth_block']), f(r['w_after_paint']), f(r['w_after_block']), f(r['w_before'])) for r in rows}
zones = {kb['id']: kb for kb in K.ZON['kerbs']}
cg = {f'k{k}': c for k, c in enumerate(K.GEO['curbs'])}
for kid in ['k15', 'k21']:
    kb = zones[kid]; side = kb['side']; zst = np.array(kb['station'], float)
    paint = np.array(kb['paint'], float); outer = np.array(kb['outer'], float)
    rows_ = np.array(cg[kid]['rows'], float); bxy, bst = rows_[:, 1:3], rows_[:, 0]
    patch = K.LabPatch(np.r_[paint[:, 0], outer[:, 0]], np.r_[paint[:, 1], outer[:, 1]], margin=15.0)
    out = []
    for s in zst[:-1] + K.STATION_OFFSET:
        c, n, offL, offR = K.road_at(s)
        dout = -n if side == 'left' else n
        w_edge = -offL if side == 'left' else offR
        cands = [w_edge]
        wb, _, _ = K.section_hit(c, dout, bxy, bst, s)
        if np.isfinite(wb): cands.append(wb)
        w_lo, w_hi = min(cands), max(cands)
        wg = np.arange(w_lo - 4.5, w_hi + 2.5 + 1e-9, K.DW)
        lab = patch.sample(c[0] + wg * dout[0], c[1] + wg * dout[1])
        r = K.detect(lab, wg, w_lo, w_hi)
        key = (kid, round(float(s), 2))
        if key not in truth: continue
        tp, tb, ap, ab, wbef = truth[key]
        if not np.isfinite(r['paint']) or not np.isfinite(tp): continue
        out.append((s, tp, r['paint'], ap, wbef))
    o = np.array(out)
    s, tp, rp, ap, wb = o.T
    moved = np.abs(tp - rp) > 0.01
    print(kid, 'n', len(o), 'moved>1cm', int(moved.sum()), 'frac', round(moved.mean(), 3))
    print('  moved stations', np.round(s[moved][[0, -1]], 2) if moved.any() else None, 'shift range', np.round(np.abs(tp - rp)[moved].min(), 2) if moved.any() else None, np.round(np.abs(tp - rp)[moved].max(), 2) if moved.any() else None)
    for nm, tr in [('main-truth', tp), ('alt-truth', rp)]:
        res = tr - ap
        print(f'  AFTER paint residual vs {nm}: median {np.median(res):+.3f} p95|r| {np.percentile(np.abs(res),95):.3f} within0.15 {np.mean(np.abs(res)<=0.15):.3f}; on moved sections: median {np.median(res[moved]) if moved.any() else float("nan"):+.3f} within0.15 {np.mean(np.abs(res[moved])<=0.15) if moved.any() else float("nan"):.3f}')
    print('  BEFORE inner(line) vs alt-truth: median', round(float(np.median(rp - wb)), 3))
    if moved.any():
        print('  moved sections: main-truth minus AFTER line (m):', np.round((tp - ap)[moved][:12], 2), ' alt-truth minus AFTER line:', np.round((rp - ap)[moved][:12], 2))
