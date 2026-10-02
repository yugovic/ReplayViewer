"""Independent ortho check of Fuji kerb geometry: BEFORE (CG curbs in geometry.json) vs AFTER (kerb_zones.json).

Evaluation only. Reads repo files, writes only into this folder.

Why this is a separate sample from the implementer's validator (kerb_zones_validate.py):
  * stations: zone stations + 0.25 m (midway between the 0.5 m stations used to build and to validate the zones);
  * section direction: normal of the CG road centreline (geometry.json 'road'), interpolated at the station,
    so it comes from neither candidate line (the implementer cut sections along the v3 paint-line normal,
    through the v3 paint points);
  * sampling: 0.02 m bicubic (order 3) on Lab (the implementer used 0.04 m bilinear);
  * edge estimator: least-squares erf step fit (centre of a blurred step), not a half-level crossing
    (implementer's checker) and not a gradient peak (v3 builder);
      - paint: projection of Lab onto the asphalt->first-paint colour direction, detection by positive-only
        standardised L/a deviation from the asphalt median;
      - block: a* (redness) step outward of the paint edge, accepted only when the red starts within
        BLOCK_MAX m of the paint edge (otherwise the inner block is white and the red is the outer row).
Candidate lines only place the search windows (asphalt reference 1.2-4.0 m road-side of the most road-side
candidate, search from 1.0 m road-side of it to 1.5 m outward of the most outward one); the edge itself comes
from the image.

Residual = truth_w - line_w along the section (w grows outward, away from the road centre),
so + = the line lies road-side of the ortho edge (same sign as the implementer's report).

Source imagery: VIRTUAL SHIZUOKA 2019 LP orthophoto 20 cm (CC BY 4.0, Shizuoka Prefecture). The same 2019
ortho was used to build the AFTER zones, so this is a consistency check on the same image, not survey truth.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import gaussian_filter1d, map_coordinates
from scipy.optimize import least_squares
from scipy.special import erf

Image.MAX_IMAGE_PIXELS = None
ROOT = Path(r'D:/00_Dev/ReplayViewer')
OUT = Path(__file__).resolve().parent
ORTHO = ROOT / 'pipeline/cache/shizuoka_ortho/native_mosaic.png'
META = json.loads((ORTHO.parent / 'native_mosaic_meta.json').read_text())
TRACK = json.loads((ROOT / 'public/data/tracks/fuji/track.json').read_text())
GEO_P = ROOT / 'public/data/tracks/fuji/cg_study/geometry.json'
ZON_P = ROOT / 'public/data/tracks/fuji/cg_study/kerb_zones.json'
GEO = json.loads(GEO_P.read_text())
ZON = json.loads(ZON_P.read_text(encoding='utf-8'))
ROAD = np.array(GEO['road'], float)        # station, cx, cz, nx, nz, offL, offR, lx, lz, rx, rz
INSIDE = ['k1', 'k3', 'k7', 'k9', 'k10', 'k13', 'k15', 'k18', 'k19', 'k20', 'k21', 'k23', 'k24', 'kDunlopR']
DW = 0.02                 # section sample step (m)
TOL = 0.15                # criterion |median| <= TOL
BLOCK_MAX = 0.75          # red onset must be within this distance outward of the paint edge (inner red block)
BLOCK_SENS = (0.6, 0.75, 1.0)
STATION_OFFSET = 0.25
ATTRIB = 'Ortho: VIRTUAL SHIZUOKA 2019 LP orthophoto 20 cm, Shizuoka Prefecture, CC BY 4.0 (lines/markers added)'

# ---------------------------------------------------------------- georeference (own copy of the formula)
_B = META['bbox']; _O = TRACK['origin']
_WI, _HI = META['imageWidth'], META['imageHeight']
_N = np.arcsinh(np.tan(np.radians(_B['maxLat']))); _S = np.arcsinh(np.tan(np.radians(_B['minLat'])))


def to_px(x, z):
    """Track local XZ (x east, z south, m) -> raster coordinates measured from the outer pixel edge."""
    x = np.asarray(x, float); z = np.asarray(z, float)
    lat = _O['lat'] - z / 111320.0
    lng = _O['lng'] + x / (111320.0 * math.cos(math.radians(_O['lat'])))
    return ((lng - _B['minLng']) / (_B['maxLng'] - _B['minLng']) * _WI,
            (_N - np.arcsinh(np.tan(np.radians(lat)))) / (_N - _S) * _HI)


_RGB = None


def ortho():
    global _RGB
    if _RGB is None:
        t = time.time()
        _RGB = np.array(Image.open(ORTHO).convert('RGB'))
        print(f'ortho loaded {_RGB.shape} in {time.time() - t:.1f}s', flush=True)
    return _RGB


class LabPatch:
    def __init__(self, xs, zs, margin=15.0, rgb=None, rgb_origin=(0, 0)):
        img = ortho() if rgb is None else rgb
        ox, oy = rgb_origin                                   # global pixel of img[0, 0] (synthetic test)
        px, py = to_px([min(xs) - margin, max(xs) + margin], [max(zs) + margin, min(zs) - margin])
        self.x0 = int(max(ox, math.floor(min(px)) - 4)); self.x1 = int(min(ox + img.shape[1], math.ceil(max(px)) + 4))
        self.y0 = int(max(oy, math.floor(min(py)) - 4)); self.y1 = int(min(oy + img.shape[0], math.ceil(max(py)) + 4))
        crop = img[self.y0 - oy:self.y1 - oy, self.x0 - ox:self.x1 - ox]
        lab = cv2.cvtColor(crop.astype(np.float32) / 255.0, cv2.COLOR_RGB2LAB)
        self.lab = [lab[..., c].astype(np.float64) for c in range(3)]

    def sample(self, x, z):
        px, py = to_px(x, z)
        cx = px - 0.5 - self.x0; cy = py - 0.5 - self.y0      # pixel centre i at edge coordinate i+0.5
        return np.stack([map_coordinates(ch, [cy, cx], order=3, mode='nearest') for ch in self.lab], axis=-1)


# ---------------------------------------------------------------- road frame
def road_at(s):
    i = int(np.searchsorted(ROAD[:, 0], s)) - 1
    i = max(0, min(i, len(ROAD) - 2))
    a, b = ROAD[i], ROAD[i + 1]
    t = (s - a[0]) / (b[0] - a[0])
    r = a * (1 - t) + b * t
    n = r[3:5] / np.linalg.norm(r[3:5])
    return r[1:3], n, r[5], r[6]


def section_hit(c, d, poly, stations, s, wmax=12.0):
    """w where the ray c + w d crosses polyline `poly`; among several crossings take the one whose
    interpolated polyline station is closest to s (independent of the truth edge)."""
    a = poly[:-1]; e = poly[1:] - a
    den = d[0] * e[:, 1] - d[1] * e[:, 0]
    ok = np.abs(den) > 1e-12
    wv = a - c
    w = np.where(ok, (wv[:, 0] * e[:, 1] - wv[:, 1] * e[:, 0]) / np.where(ok, den, 1), np.nan)
    t = np.where(ok, (wv[:, 0] * d[1] - wv[:, 1] * d[0]) / np.where(ok, den, 1), np.nan)
    m = ok & (t >= -1e-9) & (t <= 1 + 1e-9) & (np.abs(w) <= 40)
    if not m.any():
        return np.nan, np.nan, 0
    idx = np.flatnonzero(m)
    st = stations[idx] + t[idx] * (stations[idx + 1] - stations[idx])
    k = int(np.argmin(np.abs(st - s)))
    if abs(st[k] - s) > 3.0:
        return np.nan, np.nan, len(idx)
    j = idx[k]
    tang = e[j] / max(np.linalg.norm(e[j]), 1e-9)
    cosang = abs(d[0] * (-tang[1]) + d[1] * tang[0])            # |section . line normal|
    return float(w[j]), float(cosang), len(idx)


# ---------------------------------------------------------------- estimator
def erf_step(p, w):
    e, sig, lo, hi = p
    return lo + (hi - lo) * 0.5 * (1.0 + erf((w - e) / (math.sqrt(2.0) * sig)))


def fit_step(w, y, e0, lo0, hi0, sig0=0.08):
    p0 = np.array([e0, sig0, lo0, hi0], float)
    span = max(abs(hi0 - lo0), 1e-3)
    lb = [w[0], 0.01, lo0 - 2 * span, lo0 - 0.5 * span]
    ub = [w[-1], 0.45, hi0 + 0.5 * span, hi0 + 2 * span]
    p0 = np.clip(p0, np.array(lb) + 1e-6, np.array(ub) - 1e-6)
    r = least_squares(lambda p: erf_step(p, w) - y, p0, bounds=(lb, ub), loss='soft_l1', f_scale=0.1 * span)
    return r.x, bool(r.success)


def first_run(mask, start, length):
    """First index >= start (ascending) where mask is true for `length` consecutive samples."""
    n = len(mask)
    for i in range(start, n - length + 1):
        if mask[i:i + length].all():
            return i
    return None


def detect(lab, w, w_lo, w_hi):
    """Paint edge and block edge (section coordinate w, outward +). Returns dict."""
    res = dict(paint=np.nan, block=np.nan, block_any=np.nan, flag='', contrast=np.nan, sig_p=np.nan,
               sig_b=np.nan, red_plateau=np.nan, run=np.nan)
    m0 = (w >= w_lo - 4.0) & (w <= w_lo - 1.2)
    A0 = np.median(lab[m0], axis=0)
    s0 = 1.4826 * np.median(np.abs(lab[m0] - A0), axis=0)
    if A0[0] < 30:
        res['flag'] = 'dark-asphalt-ref'; return res
    zL = (lab[:, 0] - A0[0]) / max(s0[0], 1.5)
    za = (lab[:, 1] - A0[1]) / max(s0[1], 1.5)
    z = gaussian_filter1d(np.hypot(np.maximum(zL, 0), np.maximum(za, 0)), 1.0)
    c = gaussian_filter1d(np.maximum(lab[:, 0] - A0[0], lab[:, 1] - A0[1]), 1.0)
    start = int(np.searchsorted(w, w_lo - 1.0))
    stop = int(np.searchsorted(w, w_hi + 1.5))
    sus = (z > 6.0) & (c > 10.0)
    sus[stop:] = False
    hit = first_run(sus, start, 8)                        # >= 0.16 m sustained
    if hit is None:
        res['flag'] = 'no-paint'; return res
    wh = w[hit]
    A = np.median(lab[(w >= wh - 1.5) & (w <= wh - 0.3)], axis=0)
    P = np.median(lab[(w >= wh + 0.06) & (w <= wh + 0.26)], axis=0)
    dvec = P - A; contrast = float(np.linalg.norm(dvec)); res['contrast'] = contrast
    if contrast < 15.0:
        res['flag'] = 'weak-paint'; return res
    y = (lab - A) @ dvec / contrast ** 2                  # asphalt 0 -> first paint 1
    fw = (w >= wh - 0.5) & (w <= wh + 0.26)
    p, ok = fit_step(w[fw], y[fw], wh, 0.0, 1.0)
    e, sig, lo, hi = p
    if not ok or hi - lo < 0.5 or sig > 0.35 or not (w[fw][0] + 0.05 < e < w[fw][-1] - 0.02):
        res['flag'] = 'paint-fit-rejected'; return res
    # paint run (strip + blocks) must be wider than a road marking
    j0 = int(np.searchsorted(w, e))
    low = z < 3.0
    jend = first_run(low, j0, 5)
    run = (w[jend] - e) if jend is not None else (w[-1] - e)
    res['run'] = float(run)
    if run < 0.25:
        res['flag'] = 'narrow-paint(marking?)'; return res
    res['paint'] = float(e); res['sig_p'] = float(sig)
    # ---- block: a* step outward of the paint edge
    r = gaussian_filter1d(lab[:, 1] - A[1], 1.0)
    R = (w >= e - 0.05) & (w <= e + 1.3)
    Pr = float(np.percentile(r[R], 90)); res['red_plateau'] = Pr
    if Pr < 15.0:
        res['flag'] = 'no-red-near-paint'; return res
    jr = first_run(r > 0.5 * Pr, int(np.searchsorted(w, e - 0.05)), 5)
    if jr is None or w[jr] > e + 1.3:
        res['flag'] = 'no-red-near-paint'; return res
    g = w[jr]
    if g - e < 0.08:
        res['flag'] = 'red-at-paint-line'; return res
    bw = (w >= max(e + 0.04, g - 0.3)) & (w <= g + 0.3)
    pb, okb = fit_step(w[bw], r[bw], g, float(np.median(r[(w >= e + 0.04) & (w <= max(e + 0.06, g - 0.06))])), Pr, 0.05)
    eb, sigb = pb[0], pb[1]
    if not okb or sigb > 0.35 or not (w[bw][0] + 0.02 < eb < w[bw][-1] - 0.02):
        res['flag'] = 'block-fit-rejected'; return res
    res['block_any'] = float(eb); res['sig_b'] = float(sigb)
    if eb - e <= BLOCK_MAX:
        res['block'] = float(eb)
    else:
        res['flag'] = 'red-is-outer-row'
    return res


# ---------------------------------------------------------------- statistics
def stats(v):
    v = np.asarray(v, float); v = v[np.isfinite(v)]
    if len(v) == 0:
        return dict(n=0)
    return dict(n=int(len(v)), median=round(float(np.median(v)), 3), q1=round(float(np.percentile(v, 25)), 3),
                q3=round(float(np.percentile(v, 75)), 3), maxabs=round(float(v[np.argmax(np.abs(v))]), 3),
                p95abs=round(float(np.percentile(np.abs(v), 95)), 3),
                within_tol=round(float(np.mean(np.abs(v) <= TOL)), 3))


def meets(st):
    return bool(st.get('n', 0) >= 5 and abs(st['median']) <= TOL)


def meets_iqr(st):
    return bool(meets(st) and st['q1'] >= -TOL and st['q3'] <= TOL)


# ---------------------------------------------------------------- synthetic self-test of the estimator
def self_test(rng):
    """Render straight paint edges into a 0.2 m raster (area-averaged, Gaussian PSF 0.6 px) in the true
    georeference, then run the same section sampler + detector. Reports bias of paint/block estimates."""
    errs_p, errs_b = [], []
    H = W = 400
    for trial in range(40):
        ang = rng.uniform(0, 2 * np.pi)
        d = np.array([math.cos(ang), math.sin(ang)])
        base = ROAD[int(rng.integers(500, 8500)), 1:3]
        cx, cz = float(base[0]) + rng.uniform(-5, 5), float(base[1]) + rng.uniform(-5, 5)
        px0, py0 = to_px(cx, cz)
        X0, Y0 = int(px0) - W // 2, int(py0) - H // 2
        ss = 4                                              # supersampling for area averaging
        gy, gx = np.mgrid[0:H * ss, 0:W * ss]
        gpx = X0 + (gx + 0.5) / ss; gpy = Y0 + (gy + 0.5) / ss
        # invert to_px locally (linear is fine over 80 m)
        mx = (to_px(cx + 1, cz)[0] - px0); mz = (to_px(cx, cz + 1)[1] - py0)
        wx = cx + (gpx - px0) / mx; wz = cz + (gpy - py0) / mz
        e_true = 6.0 + rng.uniform(-0.1, 0.1); strip = rng.uniform(0.33, 0.46)
        wcoord = (wx - cx) * d[0] + (wz - cz) * d[1]
        img = np.empty((H * ss, W * ss, 3), np.float32); img[:] = (95, 95, 98)
        img[wcoord > e_true] = (205, 205, 200)
        img[wcoord > e_true + strip] = (200, 40, 45)
        img[wcoord > e_true + strip + 0.9] = (60, 120, 50)
        img = img.reshape(H, ss, W, ss, 3).mean(axis=(1, 3))
        img = cv2.GaussianBlur(img, (0, 0), 0.6)
        img = np.clip(img + rng.normal(0, 3, img.shape), 0, 255).astype(np.uint8)
        p = LabPatch([cx - 20, cx + 20], [cz - 20, cz + 20], margin=5, rgb=img, rgb_origin=(X0, Y0))
        wg = np.arange(0.5, 10.0 + 1e-9, DW)
        lab = p.sample(cx + wg * d[0], cz + wg * d[1])
        r = detect(lab, wg, e_true + rng.uniform(-0.6, 0.6), e_true + 0.6)
        errs_p.append(r['paint'] - e_true); errs_b.append(r['block'] - (e_true + strip))
    ep, eb = np.array(errs_p), np.array(errs_b)
    return dict(trials=len(ep), paint_bias_median=round(float(np.nanmedian(ep)), 4),
                paint_absmax=round(float(np.nanmax(np.abs(ep))), 4), paint_detected=int(np.isfinite(ep).sum()),
                block_bias_median=round(float(np.nanmedian(eb)), 4), block_absmax=round(float(np.nanmax(np.abs(eb))), 4),
                block_detected=int(np.isfinite(eb).sum()))


# ---------------------------------------------------------------- main loop
def main():
    t0 = time.time()
    st_info = dict(geometry_sha256=hashlib.sha256(GEO_P.read_bytes()).hexdigest(),
                   kerb_zones_sha256=hashlib.sha256(ZON_P.read_bytes()).hexdigest())
    rng = np.random.default_rng(20261002)
    selftest = self_test(rng)
    print('self-test', selftest, flush=True)
    zones = {kb['id']: kb for kb in ZON['kerbs']}
    cg = {f'k{k}': c for k, c in enumerate(GEO['curbs'])}
    report, sec_rows, per_sections = {}, [], {}
    for kid in INSIDE:
        kb = zones[kid]; side = kb['side']
        zst = np.array(kb['station'], float)
        paint = np.array(kb['paint'], float); block = np.array(kb['block'], float); outer = np.array(kb['outer'], float)
        if kid in cg:
            rows = np.array(cg[kid]['rows'], float)
            before = dict(line='CG kerb inner (geometry.json curbs)', xy=rows[:, 1:3], st=rows[:, 0])
            before_outer = dict(xy=rows[:, 3:5], st=rows[:, 0])
        else:
            i0 = int(np.searchsorted(ROAD[:, 0], 2945.0)); i1 = int(np.searchsorted(ROAD[:, 0], 3020.0))
            before = dict(line='no kerb in CG; stand-in = CG right road edge', xy=ROAD[i0:i1, 9:11], st=ROAD[i0:i1, 0])
            before_outer = None
        sts = zst[:-1] + STATION_OFFSET
        allxy = np.vstack([paint, outer, before['xy'][(before['st'] >= zst[0] - 2) & (before['st'] <= zst[-1] + 2)]])
        patch = LabPatch(allxy[:, 0], allxy[:, 1], margin=15.0)
        secs = []
        for s in sts:
            c, n, offL, offR = road_at(s)
            dout = -n if side == 'left' else n
            w_edge = -offL if side == 'left' else offR
            w_ap, cos_ap, _ = section_hit(c, dout, paint, zst, s)
            w_ab, cos_ab, _ = section_hit(c, dout, block, zst, s)
            w_bf, cos_bf, nh = section_hit(c, dout, before['xy'], before['st'], s)
            cands = [v for v in (w_ap, w_ab, w_bf, w_edge) if np.isfinite(v)]
            w_lo, w_hi = min(cands), max(cands)
            wg = np.arange(w_lo - 4.5, w_hi + 2.5 + 1e-9, DW)
            lab = patch.sample(c[0] + wg * dout[0], c[1] + wg * dout[1])
            r = detect(lab, wg, w_lo, w_hi)
            rec = dict(kerb=kid, station=round(float(s), 2), w_truth_paint=r['paint'], w_truth_block=r['block'],
                       w_truth_block_any=r['block_any'], flag=r['flag'], contrast=r['contrast'],
                       red_plateau=r['red_plateau'], sig_p=r['sig_p'], sig_b=r['sig_b'], run=r['run'],
                       w_after_paint=w_ap, w_after_block=w_ab, w_before=w_bf, w_cg_road_edge=w_edge,
                       cos_after_paint=cos_ap, cos_before=cos_bf, before_hits=nh)
            rec['res_after_paint'] = r['paint'] - w_ap
            rec['res_after_block'] = r['block'] - w_ab
            rec['res_before_paint'] = r['paint'] - w_bf
            rec['res_before_block'] = r['block'] - w_bf
            rec['strip_measured'] = r['block_any'] - r['paint']
            P = c + dout * r['paint'] if np.isfinite(r['paint']) else None
            Bp = c + dout * r['block'] if np.isfinite(r['block']) else None
            rec['xy_truth_paint'] = P.tolist() if P is not None else None
            rec['xy_truth_block'] = Bp.tolist() if Bp is not None else None
            secs.append(rec)
        per_sections[kid] = secs
        get = lambda key: np.array([x[key] for x in secs], float)
        flags = {}
        for x in secs:
            if x['flag']:
                flags[x['flag']] = flags.get(x['flag'], 0) + 1
        R = dict(side=side, sections=len(secs), station_range=[float(sts[0]), float(sts[-1])],
                 before_line=before['line'], flags=flags,
                 truth_paint_n=int(np.isfinite(get('w_truth_paint')).sum()),
                 truth_block_n=int(np.isfinite(get('w_truth_block')).sum()),
                 before=dict(paint=stats(get('res_before_paint')), block=stats(get('res_before_block'))),
                 after=dict(paint=stats(get('res_after_paint')), block=stats(get('res_after_block'))),
                 strip_measured=stats(get('strip_measured')),
                 min_cos_after_paint=round(float(np.nanmin(get('cos_after_paint'))), 4),
                 min_cos_before=round(float(np.nanmin(get('cos_before'))), 4) if np.isfinite(get('cos_before')).any() else None)
        # block-rule sensitivity (red onset within X m of the paint edge)
        sens = {}
        ba = get('w_truth_block_any'); pt = get('w_truth_paint')
        for X in BLOCK_SENS:
            tb = np.where(ba - pt <= X, ba, np.nan)
            sens[str(X)] = dict(after=stats(tb - get('w_after_block')), before=stats(tb - get('w_before')))
        R['block_rule_sensitivity'] = sens
        for ph in ('before', 'after'):
            R[ph]['meets_median'] = dict(paint=meets(R[ph]['paint']), block=meets(R[ph]['block']))
            R[ph]['meets_median_and_iqr'] = dict(paint=meets_iqr(R[ph]['paint']), block=meets_iqr(R[ph]['block']))
        if kid == 'kDunlopR':
            R['before']['note'] = ('No kerb exists in the CG here (missing). Counted as NOT meeting the criterion; the '
                                   'numbers are for the CG right road edge as a stand-in only.')
            R['before']['meets_median'] = dict(paint=False, block=False)
            R['before']['meets_median_and_iqr'] = dict(paint=False, block=False)
            R['before']['standin_meets_median'] = dict(paint=meets(R['before']['paint']), block=meets(R['before']['block']))
        report[kid] = R
        a, b = R['after'], R['before']
        print(f"{kid:9s} n={len(secs):3d} truth p/b {R['truth_paint_n']}/{R['truth_block_n']} | "
              f"BEFORE paint {b['paint'].get('median')} [{b['paint'].get('q1')},{b['paint'].get('q3')}] max {b['paint'].get('maxabs')}"
              f" block {b['block'].get('median')} [{b['block'].get('q1')},{b['block'].get('q3')}] max {b['block'].get('maxabs')} | "
              f"AFTER paint {a['paint'].get('median')} [{a['paint'].get('q1')},{a['paint'].get('q3')}] max {a['paint'].get('maxabs')}"
              f" block {a['block'].get('median')} [{a['block'].get('q1')},{a['block'].get('q3')}] max {a['block'].get('maxabs')}"
              f" | strip {R['strip_measured'].get('median')} flags {flags}", flush=True)
        for x in secs:
            sec_rows.append(x)
    counts = {}
    for ph in ('before', 'after'):
        for key in ('paint', 'block'):
            counts[f'{ph}_{key}_median'] = sum(report[k][ph]['meets_median'][key] for k in INSIDE)
            counts[f'{ph}_{key}_median_and_iqr'] = sum(report[k][ph]['meets_median_and_iqr'][key] for k in INSIDE)
    counts['before_both_median'] = sum(report[k]['before']['meets_median']['paint'] and report[k]['before']['meets_median']['block'] for k in INSIDE)
    counts['after_both_median'] = sum(report[k]['after']['meets_median']['paint'] and report[k]['after']['meets_median']['block'] for k in INSIDE)
    counts['total'] = len(INSIDE)
    sens_counts = {}
    for X in BLOCK_SENS:
        sens_counts[str(X)] = dict(
            after=sum(meets(report[k]['block_rule_sensitivity'][str(X)]['after']) for k in INSIDE),
            before=sum(meets(report[k]['block_rule_sensitivity'][str(X)]['before']) for k in INSIDE if k != 'kDunlopR'))
    # pooled residuals
    pooled = {}
    for ph, key in (('before', 'paint'), ('before', 'block'), ('after', 'paint'), ('after', 'block')):
        v = np.array([x[f'res_{ph}_{key}'] for x in sec_rows if x['kerb'] != 'kDunlopR' or ph == 'after'], float)
        pooled[f'{ph}_{key}_excl_DunlopR_before'] = stats(v)
    print('counts', counts, 'block-rule sensitivity', sens_counts, flush=True)

    # ---------------------------------------------------------------- overlays
    images = []
    for kid, mode in (('k15', 'maxdiff'), ('k23', 'maxdiff'), ('kDunlopR', 'frac0.4')):
        secs = per_sections[kid]
        if mode == 'maxdiff':
            dif = np.array([abs(x['w_before'] - x['w_after_block']) for x in secs], float)
            i = int(np.nanargmax(dif))
        else:
            i = int(round(0.4 * (len(secs) - 1)))
        s = secs[i]['station']
        c, n, _, _ = road_at(s)
        dout = -n if zones[kid]['side'] == 'left' else n
        wc = np.nanmean([secs[i]['w_after_paint'], secs[i]['w_before']])
        centre = c + dout * wc
        fn = f'overlay_{kid}_s{int(round(s * 100)):06d}.png'
        images.append(draw_overlay(fn, centre, 9.0, 10, kid, s, report, zones, cg, per_sections))
    out = dict(
        task='course kerb geometry accuracy (BEFORE = geometry.json curbs + missing Dunlop-R; AFTER = kerb_zones.json)',
        method=__doc__, settings=dict(DW=DW, TOL=TOL, BLOCK_MAX=BLOCK_MAX, BLOCK_SENS=BLOCK_SENS,
                                      STATION_OFFSET=STATION_OFFSET),
        inputs=dict(geometry=str(GEO_P.relative_to(ROOT)), kerb_zones=str(ZON_P.relative_to(ROOT)),
                    ortho=str(ORTHO.relative_to(ROOT)), **st_info),
        residual_sign='truth - line along the section (outward +); + = line lies road-side of the ortho edge',
        not_survey_truth=('Same VIRTUAL SHIZUOKA 2019 ortho was the source of the AFTER zones (and of the CG). '
                          'Ortho georeferencing error (~0.2-0.3 m) cancels in this comparison and is not measured. '
                          '2019 imagery vs 2020 sessions not verified.'),
        estimator_self_test=selftest, counts=counts, block_rule_sensitivity_counts=sens_counts, pooled=pooled,
        kerbs=report, images=images, attribution=ATTRIB, runtime_s=round(time.time() - t0, 1))
    (OUT / 'results.json').write_text(json.dumps(out, indent=1, ensure_ascii=False, default=float), encoding='utf-8')
    # per-kerb table
    with open(OUT / 'per_kerb_table.csv', 'w', newline='', encoding='utf-8') as f:
        wr = csv.writer(f)
        wr.writerow(['kerb', 'side', 'sections', 'truth_paint_n', 'truth_block_n', 'phase', 'line',
                     'median_m', 'q1_m', 'q3_m', 'maxabs_m', 'p95abs_m', 'frac_within_0.15', 'meets_|median|<=0.15',
                     'meets_median_and_IQR', 'before_line'])
        for kid in INSIDE:
            R = report[kid]
            for ph in ('before', 'after'):
                for key in ('paint', 'block'):
                    st = R[ph][key]
                    wr.writerow([kid, R['side'], R['sections'], R['truth_paint_n'], R['truth_block_n'], ph, key,
                                 st.get('median'), st.get('q1'), st.get('q3'), st.get('maxabs'), st.get('p95abs'),
                                 st.get('within_tol'), R[ph]['meets_median'][key], R[ph]['meets_median_and_iqr'][key],
                                 R['before_line'] if ph == 'before' else 'kerb_zones.json ' + key])
    keys = ['kerb', 'station', 'flag', 'w_truth_paint', 'w_truth_block', 'w_truth_block_any', 'w_after_paint',
            'w_after_block', 'w_before', 'w_cg_road_edge', 'res_after_paint', 'res_after_block', 'res_before_paint',
            'res_before_block', 'strip_measured', 'contrast', 'red_plateau', 'sig_p', 'sig_b', 'run',
            'cos_after_paint', 'cos_before', 'before_hits']
    with open(OUT / 'sections.csv', 'w', newline='', encoding='utf-8') as f:
        wr = csv.writer(f); wr.writerow(keys)
        for x in sec_rows:
            wr.writerow([(round(x[k], 4) if isinstance(x[k], float) and np.isfinite(x[k]) else ('' if isinstance(x[k], float) else x[k])) for k in keys])
    print('done in', round(time.time() - t0, 1), 's', flush=True)


def draw_overlay(fname, centre, half, scale, kid, s, report, zones, cg, per_sections):
    img = ortho()
    cx, cz = centre
    px, py = to_px([cx - half, cx + half], [cz + half, cz - half])
    x0, x1 = int(math.floor(min(px))), int(math.ceil(max(px))); y0, y1 = int(math.floor(min(py))), int(math.ceil(max(py)))
    im = Image.fromarray(img[y0:y1, x0:x1]).resize(((x1 - x0) * scale, (y1 - y0) * scale), Image.NEAREST)
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype('arial.ttf', 15); small = ImageFont.truetype('arial.ttf', 13)
    except OSError:
        font = small = ImageFont.load_default()

    def P(xy):
        xy = np.asarray(xy, float)
        a, b = to_px(xy[:, 0], xy[:, 1])
        return list(zip(((a - x0) * scale).tolist(), ((b - y0) * scale).tolist()))

    def near(xy):
        xy = np.asarray(xy, float)
        return np.any((np.abs(xy[:, 0] - cx) < half * 1.5) & (np.abs(xy[:, 1] - cz) < half * 1.5))
    # BEFORE: CG kerb inner (and outer, thinner), CG road edge
    for k, c in cg.items():
        r = np.array(c['rows'], float)
        if near(r[:, 1:3]):
            d.line(P(r[:, 3:5]), fill=(70, 120, 255), width=1)
            d.line(P(r[:, 1:3]), fill=(40, 90, 255), width=3)
    i0 = int(np.searchsorted(ROAD[:, 0], s - 30)); i1 = int(np.searchsorted(ROAD[:, 0], s + 30))
    side = zones[kid]['side']
    edge = ROAD[i0:i1, 9:11] if side == 'right' else ROAD[i0:i1, 7:9]
    if kid == 'kDunlopR':
        d.line(P(edge), fill=(40, 90, 255), width=3)          # the only BEFORE boundary at Dunlop-R
    # AFTER: zones
    for kb in zones.values():
        for key, col, wd in (('outer', (255, 230, 0), 2), ('block', (0, 255, 255), 2), ('paint', (255, 0, 255), 2)):
            xy = np.array(kb[key], float)
            if near(xy):
                d.line(P(xy), fill=col, width=wd)
    # this check's ortho edges
    rad = 4
    for x in per_sections[kid]:
        for key, col in (('xy_truth_paint', (255, 40, 40)), ('xy_truth_block', (0, 220, 0))):
            if x[key] is not None:
                q = P([x[key]])[0]
                d.ellipse([q[0] - rad, q[1] - rad, q[0] + rad, q[1] + rad], outline=col, width=2)
    # scale bars: 0.4 m and 1 m (pixels per metre from the georeference)
    ppm = (to_px(cx + 1, cz)[0] - to_px(cx, cz)[0]) * scale
    yb = im.height - 46
    d.rectangle([8, yb - 22, 8 + ppm * 1.0 + 140, yb + 12], fill=(0, 0, 0))
    d.rectangle([14, yb, 14 + ppm * 0.4, yb + 6], fill=(255, 255, 255))
    d.text((18 + ppm * 0.4, yb - 4), '0.4 m', fill=(255, 255, 255), font=small)
    d.rectangle([14, yb - 16, 14 + ppm * 1.0, yb - 10], fill=(255, 255, 255))
    d.text((18 + ppm * 1.0, yb - 20), '1 m', fill=(255, 255, 255), font=small)
    R = report[kid]
    bp, bb, ap, ab = R['before']['paint'], R['before']['block'], R['after']['paint'], R['after']['block']
    bl = 'CG kerb inner' if kid != 'kDunlopR' else 'CG right road edge (no kerb in the CG at Dunlop-R)'
    words = (f'{kid}  station {s:.2f} m  |  crop {2 * half:.0f} m, {scale}x nearest (1 screen px = {0.2 / scale:.2f} m, '
             f'1 ortho px = 0.2 m)  |  per-kerb median residual, m (+ = line road-side of ortho edge): '
             f'BEFORE paint {bp.get("median")}, block {bb.get("median")}; AFTER paint {ap.get("median")}, '
             f'block {ab.get("median")}  ||  BEFORE: thick blue = {bl}' + ('' if kid == 'kDunlopR' else
             ', thin blue = CG kerb outer') + '  ||  AFTER (kerb_zones.json): magenta = paint, cyan = block, '
             'yellow = outer  ||  this check (stations +0.25 m, road-normal sections, erf fit): red o = ortho paint '
             'edge, green o = ortho block edge (red-block start)').split(' ')
    lines, cur = [], ''
    for wd in words:
        if wd == '||':
            lines.append(cur.strip()); cur = ''; continue
        t = (cur + ' ' + wd).strip()
        if d.textlength(t, font=small) > im.width - 12:
            lines.append(cur.strip()); cur = wd
        else:
            cur = t
    if cur.strip():
        lines.append(cur.strip())
    hh = 6 + 17 * len(lines)
    d.rectangle([0, 0, im.width, hh], fill=(0, 0, 0))
    for li, t in enumerate(lines):
        d.text((6, 3 + 17 * li), t, fill=(255, 255, 255) if li == 0 else (220, 220, 220), font=small)
    d.rectangle([0, im.height - 20, im.width, im.height], fill=(0, 0, 0))
    d.text((6, im.height - 18), ATTRIB, fill=(220, 220, 220), font=small)
    im.save(OUT / fname)
    return fname


if __name__ == '__main__':
    main()
