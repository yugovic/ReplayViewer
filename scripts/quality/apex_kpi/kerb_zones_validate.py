"""Validate kerb zones against the 2019 ortho with the INDEPENDENT Lab half-level checker.

The checker is the cross-section method of kerbcheck_vs_ortho.py (v3 report), copied verbatim below
(`paint_index`, `detect_edge`): asphalt reference = median Lab 1.0-3.5 m road-side, paint index
q = max(dL+, 1.2 da+), paint edge = half-level crossing of q searched from the road side, block edge = first
a*-rise to half of the red plateau within 1.6 m outward of the paint edge (only cross-sections through a RED
inner block can measure it), outer = end of the paint run. It shares no code or output with the v3 detector
(kerb_zones_v3.py: colour-distance gradient + along-track redness envelope + smoothing).

Sections: one per zone station (0.5 m), through the v3 paint point along the v3 inward normal. Every candidate
line is intersected with the SAME sections, so CG / v2 / v3 are compared on identical truth samples.
Residual = candidate - checker along the section, + = candidate lies road-side of the ortho edge.
Block truth exclusion: a red edge more than 0.8 m outward of the paint edge is the second (outer) row behind a
white inner block (kerbs 15/23) or a neighbouring block, not the block start; such sections are excluded and
counted.

Usage: python kerb_zones_validate.py [--zones PATH] [--out-dir DIR] [--write-back] [--no-images]
Writes validation.json, validation_table.csv and overlay PNGs (ortho crops, CC BY 4.0 VIRTUAL SHIZUOKA 2019).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import gaussian_filter1d

import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from paths import ROOT, WORK  # noqa: E402
import ortho_common as C  # noqa: E402

U = np.arange(-3.0, 4.0001, .04)   # + toward road centre from the section origin (checker grid)
INSIDE_IDS = [f'k{k}' for k in C.INSIDE] + ['kDunlopR']
TOL = 0.15
BLOCK_MAX_STRIP = 0.8
END_CHECK = {'k13': 6.0, 'k19': 6.0, 'k21': 6.0}


# ---------------- independent checker (verbatim from kerbcheck_vs_ortho.py) ----------------
def paint_index(lab, ref):
    dL = lab[..., 0] - ref[0]
    da = lab[..., 1] - ref[1]
    return np.maximum(np.maximum(dL, 0), 1.2*np.maximum(da, 0))

def detect_edge(prof, ref_mask_u=(1.0, 3.5), search_from=3.0, search_to=-2.8):
    """Return dict with edge u (half-level crossing), plateau info, flags."""
    res = dict(edge=np.nan, flag='', plateau=np.nan, kind='', red_edge=np.nan,
               paint_width=np.nan, refL=np.nan)
    m = (U >= ref_mask_u[0]) & (U <= ref_mask_u[1])
    ref = np.median(prof[m], axis=0)
    res['refL'] = float(ref[0]); res['refa'] = float(ref[1])
    q = gaussian_filter1d(paint_index(prof, ref), 1.0)
    qa = gaussian_filter1d(np.maximum(prof[:, 1]-ref[1], 0), 1.0)
    base = float(np.median(q[m])); noise = float(np.median(np.abs(q[m]-base))*1.4826 + 1.0)
    thr = max(base + 12, base + 5*noise)
    idx = np.flatnonzero((U <= search_from) & (U >= search_to))[::-1]  # road side -> outward
    hit = None
    for k, i in enumerate(idx):
        win = idx[k:k+4]   # sustained >= 0.12 m
        if len(win) == 4 and np.all(q[win] > thr):
            hit = i; break
    if hit is None:
        res['flag'] = 'no-paint'
        return res, q
    pl_idx = np.flatnonzero((U <= U[hit]) & (U >= U[hit]-0.35))
    P = float(np.percentile(q[pl_idx], 75))
    res['plateau'] = P - base
    level = base + 0.5*(P - base)
    # search back toward the road (increasing u) for the crossing of `level`
    i = hit
    if q[i] > level:
        while i+1 < len(U) and q[i] > level:   # walk toward road until below
            i += 1
        lo, hi = i, i-1                         # lo below level, hi above
    else:
        while i-1 >= 0 and q[i] <= level:       # walk outward until above
            i -= 1
        lo, hi = i+1, i
    q0, q1 = q[lo], q[hi]; u0, u1 = U[lo], U[hi]
    e = u0 + (level - q0)/(q1 - q0)*(u1 - u0) if q1 != q0 else U[hit]
    res['edge'] = float(e)
    # kind of first paint: white (lightness) or red (a*)
    seg = np.flatnonzero((U <= e-0.04) & (U >= e-0.16))
    dLs = float(np.median(prof[seg, 0]-ref[0])); das = float(np.median(prof[seg, 1]-ref[1]))
    res['kind'] = 'red' if das > 12 and das*1.2 > dLs*0.6 else ('white' if dLs > 10 else 'weak')
    res['dL'] = dLs; res['da'] = das
    # paint run width (until q drops under level for >=0.2 m)
    j = np.flatnonzero(U <= e)[::-1]
    w_end = np.nan
    for k, i2 in enumerate(j):
        win = j[k:k+5]
        if len(win) == 5 and np.all(q[win] < level):
            w_end = U[i2]; break
    res['paint_width'] = float(e - w_end) if np.isfinite(w_end) else np.nan
    # red onset (first a*-rise to half of red plateau) within 1.6 m outward of the edge
    rm = np.flatnonzero((U <= e+0.1) & (U >= e-1.6))[::-1]
    if len(rm) and qa[rm].max() > 15:
        Pa = float(np.percentile(qa[rm], 90)); lv = 0.5*Pa
        k2 = next((k for k in range(len(rm)) if qa[rm[k]] > lv), None)
        if k2 is not None and k2 > 0:
            i0, i1 = rm[k2-1], rm[k2]
            res['red_edge'] = float(U[i0] + (lv-qa[i0])/(qa[i1]-qa[i0])*(U[i1]-U[i0]))
        elif k2 == 0:
            res['red_edge'] = float(U[rm[0]])
    if res['plateau'] < 18: res['flag'] += 'weak-paint;'
    if res['refL'] < 30: res['flag'] += 'dark-ref(shadow?);'
    return res, q
# --------------------------------------------------------------------------------------------


def section_hits(origin, d, poly, max_dist=6.0):
    """Signed distance t along each section (origin + t d) to its nearest crossing of `poly` (nan if none)."""
    a = poly[:-1]; b = poly[1:]; e = b - a
    out = np.full(len(origin), np.nan)
    for i, (o, di) in enumerate(zip(origin, d)):
        # solve o + t di = a + s e
        den = di[0] * e[:, 1] - di[1] * e[:, 0]
        ok = np.abs(den) > 1e-12
        w = a - o
        t = np.where(ok, (w[:, 0] * e[:, 1] - w[:, 1] * e[:, 0]) / np.where(ok, den, 1), np.nan)
        s = np.where(ok, (w[:, 0] * di[1] - w[:, 1] * di[0]) / np.where(ok, den, 1), np.nan)
        m = ok & (s >= -1e-9) & (s <= 1 + 1e-9) & (np.abs(t) <= max_dist)
        if m.any():
            out[i] = t[m][np.argmin(np.abs(t[m]))]
    return out


def stats(v):
    v = np.asarray(v, float); v = v[np.isfinite(v)]
    if len(v) == 0:
        return dict(n=0)
    return dict(n=int(len(v)), median=round(float(np.median(v)), 3), q1=round(float(np.percentile(v, 25)), 3),
                q3=round(float(np.percentile(v, 75)), 3), maxabs=round(float(v[np.argmax(np.abs(v))]), 3),
                p95abs=round(float(np.percentile(np.abs(v), 95)), 3), nOverTol=int(np.sum(np.abs(v) > TOL)))


def accept(st):
    return bool(st.get('n', 0) >= 5 and abs(st['median']) <= TOL and st['q1'] >= -TOL and st['q3'] <= TOL)


def roughness(t, s, sigma_m=2.5):
    """RMS of the line offset about its own 2.5 m Gaussian trend (zig-zag measure), metres."""
    ok = np.isfinite(t)
    if ok.sum() < 10:
        return None
    g = np.arange(s[ok][0], s[ok][-1] + 1e-9, 0.25)
    v = np.interp(g, s[ok], t[ok])
    return round(float(np.sqrt(np.mean((v - gaussian_filter1d(v, sigma_m / 0.25, mode='nearest')) ** 2))), 3)


def load_v2():
    p = WORK / 'course_v2_kerbs.json'
    if not p.exists():
        return {}
    out = {}
    for kb in json.loads(p.read_text())['kerbs']:
        paint = np.array(kb['paint'], float); outer = np.array(kb['outer'], float); block = np.array(kb['block'], float)
        if kb['id'] == 'kDunlopR':                       # as shipped to eval_v2 / export_inputs (assumption)
            d = outer - paint; d /= np.maximum(np.linalg.norm(d, axis=1), 1e-6)[:, None]
            block = paint + 0.42 * d
        out[kb['id']] = dict(paint=paint, block=block, outer=outer)
    return out


def draw(fname, out_dir, cx, cz, half, scale, zones, sec, title, extra=()):
    img = C.rgb()
    px, py = C.pixel([cx - half, cx + half], [cz + half, cz - half])
    x0, x1 = int(math.floor(min(px))), int(math.ceil(max(px))); y0, y1 = int(math.floor(min(py))), int(math.ceil(max(py)))
    im = Image.fromarray(img[y0:y1, x0:x1]).resize(((x1 - x0) * scale, (y1 - y0) * scale), Image.BICUBIC)
    d = ImageDraw.Draw(im)

    def P(xy):
        a, b = C.pixel(xy[:, 0], xy[:, 1])
        return list(zip(((a - x0) * scale).tolist(), ((b - y0) * scale).tolist()))
    near = lambda xy: np.any((np.abs(xy[:, 0] - cx) < half * 1.4) & (np.abs(xy[:, 1] - cz) < half * 1.4))
    for c in C.KERBS:
        r = np.array(c['rows'], float)
        if near(r[:, 1:3]):
            d.line(P(r[:, 1:3]), fill=(40, 90, 255), width=2)      # CG inner (before)
    for xy, col in extra:
        if near(xy):
            d.line(P(xy), fill=col, width=1)
    for kb in zones:
        for key, col in (('outer', (255, 230, 0)), ('block', (0, 255, 255)), ('paint', (255, 0, 255))):
            xy = np.array(kb[key], float)
            if near(xy):
                d.line(P(xy), fill=col, width=2)
    rad = max(1.5, scale * 0.25)
    for s in (sec[::2] if scale >= 6 else []):
        for key, col in (('chk_paint_xy', (255, 60, 60)), ('chk_block_xy', (0, 210, 0))):
            if s.get(key) is not None:
                q = P(np.array([s[key]]))[0]
                d.ellipse([q[0] - rad, q[1] - rad, q[0] + rad, q[1] + rad], outline=col, width=1)
    d.rectangle([0, 0, im.width, 34], fill=(0, 0, 0))
    d.text((4, 2), title, fill=(255, 255, 255))
    d.text((4, 18), 'magenta=v3 paint  cyan=v3 block  yellow=v3 outer  blue=CG inner (before)  '
                    'o red/green = checker paint/block edge', fill=(220, 220, 220))
    d.rectangle([0, im.height - 16, im.width, im.height], fill=(0, 0, 0))
    d.text((4, im.height - 14), 'Ortho: VIRTUAL SHIZUOKA 2019 (CC BY 4.0, Shizuoka Prefecture)', fill=(220, 220, 220))
    im.save(out_dir / fname)
    return fname


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--zones', type=Path, default=ROOT / 'public/data/tracks/fuji/cg_study/kerb_zones.json')
    ap.add_argument('--out-dir', type=Path, default=ROOT / 'artifacts/improvement-eval-2026-10-02/after/kerb-zones')
    ap.add_argument('--no-images', action='store_true')
    ap.add_argument('--write-back', action='store_true',
                    help='embed the compact per-kerb check and the validation summary into the zones file')
    ap.add_argument('--block-max-strip', type=float, default=BLOCK_MAX_STRIP,
                    help='max paint-to-red distance accepted as a block-start truth sample (sensitivity runs)')
    args = ap.parse_args()
    globals()['BLOCK_MAX_STRIP'] = args.block_max_strip
    args.out_dir.mkdir(parents=True, exist_ok=True)
    zones = json.loads(args.zones.read_text(encoding='utf-8'))
    v2 = load_v2()
    cg = {f'k{k}': np.array(c['rows'], float) for k, c in enumerate(C.KERBS)}
    report, rows_csv, sections_all = {}, [], {}
    for kb in zones['kerbs']:
        kid, side = kb['id'], kb['side']
        st = np.array(kb['station'], float)
        paint = np.array(kb['paint'], float); block = np.array(kb['block'], float); outer = np.array(kb['outer'], float)
        n_in = C.inward_normals(paint, st, side, sigma_pts=4)
        patch = C.patch_for(paint, margin=10.)
        X = paint[:, :1] + U[None, :] * n_in[:, :1]; Z = paint[:, 1:] + U[None, :] * n_in[:, 1:]
        lab = patch.sample(X.ravel(), Z.ravel()).reshape(len(st), len(U), 3)
        cp = np.full(len(st), np.nan); cb = np.full(len(st), np.nan); co = np.full(len(st), np.nan)
        red_sec = np.zeros(len(st), bool)
        excl_far = 0; flagged = 0
        for i in range(len(st)):
            res, _ = detect_edge(lab[i])
            if not np.isfinite(res['edge']) or 'weak' in res['flag'] or 'dark' in res['flag']:
                flagged += 1
                continue
            cp[i] = res['edge']
            if np.isfinite(res['red_edge']):
                red_sec[i] = True
                if res['edge'] - res['red_edge'] <= BLOCK_MAX_STRIP:
                    cb[i] = res['red_edge']
                else:
                    excl_far += 1
            if np.isfinite(res['paint_width']):
                co[i] = res['edge'] - res['paint_width']
        lines = {'v3': dict(paint=paint, block=block, outer=outer)}
        if kid in cg:
            r = cg[kid]
            lines['cg'] = dict(paint=r[:, 1:3], block=r[:, 1:3], outer=r[:, 3:5])     # CG inner used as both
            # CG road edge on the kerb side (the wet boundary of the shipped registration), for context
            rs = C.ROAD[max(0, int(r[0, 0] / C.STEP) - 20):int(r[-1, 0] / C.STEP) + 21]
            e = rs[:, 7:9] if side == 'left' else rs[:, 9:11]
            lines['cgroad'] = dict(paint=e, block=e, outer=e)
        elif kid == 'kDunlopR':                       # no kerb in the CG: the CG right road edge was the boundary
            rs = C.ROAD[int(2950 / C.STEP):int(3015 / C.STEP) + 1]
            lines['cg'] = dict(paint=rs[:, 9:11], block=rs[:, 9:11], outer=rs[:, 9:11])
        if kid in v2:
            lines['v2'] = v2[kid]
        t = {name: {key: section_hits(paint, n_in, L[key]) for key in ('paint', 'block', 'outer')}
             for name, L in lines.items()}
        obs = kb.get('observed', {})
        okb = np.ones(len(st), bool)
        for g0, g1 in obs.get('blockGaps', []):
            okb &= ~((st >= g0) & (st <= g1))
        R = dict(side=side, n=len(st), addedOutsideCg=kb.get('addedOutsideCg', False),
                 checkerCoverage=dict(paint=int(np.isfinite(cp).sum()), block=int(np.isfinite(cb).sum()),
                                      outer=int(np.isfinite(co).sum()), paintFlagged=flagged,
                                      blockExcludedFarRed=excl_far))
        for name in t:
            R[name] = {}
            for key, truth in (('paint', cp), ('block', cb), ('outer', co)):
                res_ = t[name][key] - truth
                R[name][key] = stats(res_)
            R[name]['roughness'] = dict(paint=roughness(t[name]['paint'], st), block=roughness(t[name]['block'], st))
        R['v3']['blockObservedOnly'] = stats((t['v3']['block'] - cb)[okb])
        for name in t:                                   # outer on red-block sections only (red -> outside edge)
            R[name]['outerRedSections'] = stats((t[name]['outer'] - co)[red_sec])
        if kid in END_CHECK:
            L = END_CHECK[kid]
            for tag, m in (('start', st <= st[0] + L), ('end', st >= st[-1] - L)):
                R[f'ends_{tag}'] = {name: {key: stats((t[name][key] - truth)[m]) for key, truth in
                                           (('paint', cp), ('block', cb))} for name in t}
        R['acceptance'] = dict(paint=accept(R['v3']['paint']), block=accept(R['v3']['block']),
                               inside=kid in INSIDE_IDS)
        report[kid] = R
        # sections for drawing (checker points in XZ)
        secs = []
        for i in range(len(st)):
            secs.append(dict(station=float(st[i]),
                             chk_paint_xy=(paint[i] + cp[i] * n_in[i]).tolist() if np.isfinite(cp[i]) else None,
                             chk_block_xy=(paint[i] + cb[i] * n_in[i]).tolist() if np.isfinite(cb[i]) else None))
        sections_all[kid] = dict(stations=st, secs=secs, paint=paint)
        for name in t:
            for key in ('paint', 'block', 'outer'):
                s_ = R[name][key]
                rows_csv.append([kid, side, kid in INSIDE_IDS, name, key, s_.get('n', 0), s_.get('median'), s_.get('q1'),
                                 s_.get('q3'), s_.get('maxabs'), s_.get('p95abs'),
                                 accept(s_) if name == 'v3' and key in ('paint', 'block') else ''])
        v3p, v3b = R['v3']['paint'], R['v3']['block']
        cgp = R.get('cg', {}).get('paint', {}); cgb = R.get('cg', {}).get('block', {})
        print(f"{kid:9s} {side:5s} v3 paint med {v3p.get('median')} IQR [{v3p.get('q1')},{v3p.get('q3')}] max {v3p.get('maxabs')} n {v3p.get('n')} | "
              f"block med {v3b.get('median')} IQR [{v3b.get('q1')},{v3b.get('q3')}] max {v3b.get('maxabs')} n {v3b.get('n')} | "
              f"CG vs paint {cgp.get('median')} vs block {cgb.get('median')} | accept {R['acceptance']}", flush=True)
    inside_fail = [k for k in INSIDE_IDS if k in report and not (report[k]['acceptance']['paint'] and report[k]['acceptance']['block'])]

    def agg(name, key, ids):
        sel = [report[k][name][key] for k in ids if k in report and name in report[k] and report[k][name][key].get('n')]
        if not sel:
            return None
        meds = [s['median'] for s in sel]
        return dict(kerbs=len(sel), medianRange=[min(meds), max(meds)], maxAbsMedian=max(abs(m) for m in meds),
                    minQ1=min(s['q1'] for s in sel), maxQ3=max(s['q3'] for s in sel),
                    maxAbs=max((s['maxabs'] for s in sel), key=abs))
    cg_ids = [k for k in INSIDE_IDS if k != 'kDunlopR']
    aggregate = {f'{name}_{key}': agg(name, key, INSIDE_IDS if name == 'v3' else cg_ids)
                 for name in ('cg', 'cgroad', 'v2', 'v3') for key in ('paint', 'block', 'outer')}
    aggregate['cg_paint_excl15_23'] = agg('cg', 'paint', [k for k in cg_ids if k not in ('k15', 'k23')])
    aggregate['cg_block_excl15_23'] = agg('cg', 'block', [k for k in cg_ids if k not in ('k15', 'k23')])
    wide_hdr = ['kerb', 'side', 'inside', 'n_sections', 'chk_paint_n', 'chk_block_n', 'chk_block_excluded_far_red',
                'cg_vs_paint_med', 'cg_vs_paint_q1', 'cg_vs_paint_q3', 'cg_vs_paint_max',
                'cg_vs_block_med', 'cg_vs_block_q1', 'cg_vs_block_q3', 'cg_vs_block_max',
                'v2_paint_med', 'v2_block_med', 'v2_block_q1', 'v2_block_q3',
                'v3_paint_med', 'v3_paint_q1', 'v3_paint_q3', 'v3_paint_max',
                'v3_block_med', 'v3_block_q1', 'v3_block_q3', 'v3_block_max',
                'v3_outer_med', 'v3_outer_redsec_med', 'cg_outer_med', 'cg_outer_redsec_med',
                'rough_cg_inner', 'rough_v3_paint', 'rough_v3_block', 'accept_paint', 'accept_block']
    wide = []
    for kid, R in report.items():
        def g(name, key, f):
            return R.get(name, {}).get(key, {}).get(f, '')
        wide.append([kid, R['side'], kid in INSIDE_IDS, R['n'], R['checkerCoverage']['paint'], R['checkerCoverage']['block'],
                     R['checkerCoverage']['blockExcludedFarRed'],
                     g('cg', 'paint', 'median'), g('cg', 'paint', 'q1'), g('cg', 'paint', 'q3'), g('cg', 'paint', 'maxabs'),
                     g('cg', 'block', 'median'), g('cg', 'block', 'q1'), g('cg', 'block', 'q3'), g('cg', 'block', 'maxabs'),
                     g('v2', 'paint', 'median'), g('v2', 'block', 'median'), g('v2', 'block', 'q1'), g('v2', 'block', 'q3'),
                     g('v3', 'paint', 'median'), g('v3', 'paint', 'q1'), g('v3', 'paint', 'q3'), g('v3', 'paint', 'maxabs'),
                     g('v3', 'block', 'median'), g('v3', 'block', 'q1'), g('v3', 'block', 'q3'), g('v3', 'block', 'maxabs'),
                     g('v3', 'outer', 'median'), g('v3', 'outerRedSections', 'median'), g('cg', 'outer', 'median'),
                     g('cg', 'outerRedSections', 'median'),
                     R.get('cg', {}).get('roughness', {}).get('block', ''), R['v3']['roughness']['paint'],
                     R['v3']['roughness']['block'], R['acceptance']['paint'], R['acceptance']['block']])
    with open(args.out_dir / 'validation_summary.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(wide_hdr); w.writerows(wide)
    summary = dict(
        method=__doc__.split('\n\n')[0], tolerance=TOL, blockMaxStrip=BLOCK_MAX_STRIP,
        residualSign='candidate - checker along the section; + = candidate road-side of the ortho edge',
        zonesFile=str(args.zones.relative_to(ROOT)).replace('\\', '/') if args.zones.is_relative_to(ROOT) else args.zones.name,
        insideKerbs=INSIDE_IDS, insideFailures=inside_fail,
        insidePass=len(INSIDE_IDS) - len(inside_fail), insideTotal=len(INSIDE_IDS), aggregate=aggregate,
        notes=['cg for kDunlopR = CG right road edge (no kerb in the CG); cg block/paint both use the CG kerb inner line',
               'v2 = course_v2_kerbs.json candidate; its paint came from the same half-level method as this checker '
               '(not independent); its kDunlopR block = paint + 0.42 m (assumption, as in eval_v2/export_inputs)',
               'outer truth = end of the checker paint run (half of the white-strip level): on grass it lies outward '
               'of the red->grass midpoint, so outerRedSections and the overlays are the fairer outer check'])
    images = []
    if not args.no_images:
        Z = zones['kerbs']

        def at(kid, frac=None, station=None):
            S = sections_all[kid]; st = S['stations']
            i = int(np.argmin(np.abs(st - station))) if station is not None else int(round(frac * (len(st) - 1)))
            return S['paint'][i]
        allsecs = [s for v in sections_all.values() for s in v['secs']]
        views = [('kDunlopR', 0.5, 22, 4, 'Dunlop-R (added; detected block start)'),
                 ('kDunlopR', 0.25, 7, 10, 'Dunlop-R zoom (first half)'),
                 ('kDunlopR', 0.75, 7, 10, 'Dunlop-R zoom (second half)'),
                 ('k15', 0.5, 8, 8, 'kerb 15 mid (staggered double row)'),
                 ('k23', 0.5, 8, 8, 'kerb 23 mid (staggered double row)'),
                 ('k13', 0.97, 8, 8, 'kerb 13 end (white continues past red)'),
                 ('k19', 0.03, 8, 8, 'kerb 19 start'), ('k19', 0.97, 8, 8, 'kerb 19 end'),
                 ('k21', 0.03, 8, 8, 'kerb 21 start'), ('k21', 0.97, 8, 8, 'kerb 21 end'),
                 ('k1', 0.5, 8, 8, 'kerb 1 Coca-Cola mid'), ('k7', 0.5, 8, 8, 'kerb 7 Dunlop-L mid')]
        for kid, frac, half, scale, title in views:
            if kid not in sections_all:
                continue
            c = at(kid, frac)
            extra = []
            if kid == 'kDunlopR' and 'kDunlopR' in v2:
                extra = [(v2['kDunlopR']['block'], (255, 140, 0)),
                         (C.ROAD[int(2950 / C.STEP):int(3015 / C.STEP) + 1, 9:11], (40, 90, 255))]
            fn = f"overlay_{kid}_{int(frac * 100):02d}.png"
            stn = sections_all[kid]['stations'][int(round(frac * (len(sections_all[kid]['stations']) - 1)))]
            ttl = f'{title}  s={stn:.1f} m' + ('  (orange = v2 assumed block, blue = CG road edge)' if extra else '')
            images.append(draw(fn, args.out_dir, c[0], c[1], half, scale, Z, allsecs, ttl, extra))
    doc = dict(summary=summary, kerbs=report, images=images)
    (args.out_dir / 'validation.json').write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding='utf-8')
    with open(args.out_dir / 'validation_table.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['kerb', 'side', 'inside', 'line_set', 'line', 'n', 'median_m', 'q1_m', 'q3_m', 'maxabs_m', 'p95abs_m',
                    'accept_v3'])
        w.writerows(rows_csv)
    if args.write_back:
        def rel(p: Path) -> str:
            try:
                return str(p.resolve().relative_to(ROOT)).replace('\\', '/')
            except ValueError:
                return p.name
        q = lambda s: [s.get('median'), s.get('q1'), s.get('q3'), s.get('n', 0)]
        zones['validation'] = dict(
            checker='scripts/quality/apex_kpi/kerb_zones_validate.py (independent Lab half-level cross-section checker, '
                    'same 2019 ortho)',
            residual='line minus checker edge along the section normal (m); + = line lies road-side of the ortho edge; '
                     'check arrays are [median, q1, q3, n]',
            acceptance=f'|median| <= {TOL} m and IQR within +-{TOL} m for paint and block at the inside kerbs',
            blockTruthRule=f'block truth only on cross-sections through a red block whose red starts <= {BLOCK_MAX_STRIP} m '
                           'outward of the paint edge (kerbs 15/23: farther red is the outer row behind a white block)',
            insideKerbs=INSIDE_IDS, insidePass=f"{summary['insidePass']}/{summary['insideTotal']}",
            report=rel(args.out_dir / 'validation.json'))
        for kb in zones['kerbs']:
            R = report[kb['id']]
            chk = dict(paint=q(R['v3']['paint']), block=q(R['v3']['block']),
                       accepted=bool(R['acceptance']['paint'] and R['acceptance']['block']), inside=kb['id'] in INSIDE_IDS)
            if 'cg' in R:
                chk['before'] = dict(line='CG kerb inner' if not kb.get('addedOutsideCg') else 'CG road edge (no kerb)',
                                     vsPaint=R['cg']['paint'].get('median'), vsBlock=R['cg']['block'].get('median'))
            kb['check'] = chk
        args.zones.write_text(json.dumps(zones, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n',
                              encoding='utf-8')
        print('validation summary written back to', args.zones)
    print('inside pass', summary['insidePass'], '/', summary['insideTotal'], 'failures', inside_fail)
    print('wrote', args.out_dir)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
