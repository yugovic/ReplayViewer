"""TASK B2 evaluation: rendered kerb geometry (before = geometry.json CG kerbs, after = kerb_zones.json)
rasterised over the VIRTUAL SHIZUOKA 2019 orthophoto (CC BY 4.0). Read-only use of the ortho; never Google tiles.

Inputs : $KERB_RENDER_WORK/tris_{before,after}.json from dump_kerb_render.ts (top faces, XZ metres).
Outputs: render_ortho_metrics.json, render_ortho_summary.csv, overlay_*.png (this directory).

Caveat: kerb_zones.json was traced on this same ortho, so agreement here shows that the viewer reproduces
the traced lines faithfully; it is not an independent check of the lines (see B1 validation for that).
Pixel classes (calibrated on 26 kerbs): white = L*>=75 and chroma<20; red = build_fuji_cg.py RGB rule;
asphalt = L*<70 and chroma<10. Ortho pixel 0.2 m; georeferencing error ~0.2-0.3 m not removed.
Run: KERB_RENDER_WORK=<dir> <venv python> artifacts/improvement-eval-2026-10-02/after/kerb-render/eval_kerb_render.py
"""
import csv
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / 'scripts/quality/apex_kpi'))
import ortho_common as C  # noqa: E402

WORK = Path(os.environ.get('KERB_RENDER_WORK', HERE / 'work'))
S = 4                       # supersampling: 0.05 m raster for thin strips
ZONES = json.loads((ROOT / 'public/data/tracks/fuji/cg_study/kerb_zones.json').read_text())
TRIS = {v: {k: np.asarray(a, float).reshape(-1, 3, 2) for k, a in json.loads((WORK / f'tris_{v}.json').read_text()).items()}
        for v in ('before', 'after')}
INSIDE = set(ZONES['validation'].get('insideKerbs', [])) if isinstance(ZONES['validation'].get('insideKerbs'), list) else set()
ATTR = 'Ortho: VIRTUAL SHIZUOKA 2019 (Shizuoka Pref.) CC BY 4.0'


def outward(k):
    row = np.array([C.road_row(s) for s in k['station']])
    d = row[:, 9:11] - row[:, 7:9]
    d /= np.linalg.norm(d, axis=1)[:, None]
    return d if k['side'] == 'right' else -d


class Crop:
    def __init__(self, xs, zs, pad=3.):
        img = C.rgb(); H, W = img.shape[:2]
        px, py = C.pixel([min(xs) - pad, max(xs) + pad], [max(zs) + pad, min(zs) - pad])
        self.x0 = int(max(0, np.floor(min(px)))); self.x1 = int(min(W, np.ceil(max(px))))
        self.y0 = int(max(0, np.floor(min(py)))); self.y1 = int(min(H, np.ceil(max(py))))
        self.rgb = img[self.y0:self.y1, self.x0:self.x1]
        rgb = np.repeat(np.repeat(self.rgb, S, 0), S, 1)
        lab = cv2.cvtColor(rgb.astype(np.float32) / 255., cv2.COLOR_RGB2LAB)
        r, g, b = (rgb[..., i].astype(int) for i in range(3))
        chroma = np.hypot(lab[..., 1], lab[..., 2])
        self.red = (r - g > 22) & (r - b > 18) & (r > 120) & (g < 175)
        self.white = (lab[..., 0] >= 75) & (chroma < 20) & ~self.red
        self.asphalt = (lab[..., 0] < 70) & (chroma < 10)
        self.a = lab[..., 1]
        self.shape = self.red.shape
        self.bounds = (min(xs) - pad, max(xs) + pad, min(zs) - pad, max(zs) + pad)

    def poly(self, xz):
        px, py = C.pixel(xz[..., 0], xz[..., 1])
        c = np.stack([(px - self.x0) * S - .5, (py - self.y0) * S - .5], -1)
        return np.round(c * 16).astype(np.int32)

    def mask(self, tris):
        m = np.zeros(self.shape, np.uint8)
        if len(tris):
            x0, x1, z0, z1 = self.bounds
            keep = ((tris[..., 0].max(1) >= x0) & (tris[..., 0].min(1) <= x1) &
                    (tris[..., 1].max(1) >= z0) & (tris[..., 1].min(1) <= z1))
            if keep.any():
                cv2.fillPoly(m, list(self.poly(tris[keep])), 1, lineType=cv2.LINE_8, shift=4)
        return m.astype(bool)


def corridor(crop, k, inner=1.0, outer=1.0):
    n = outward(k); P = np.array(k['paint']) - n * inner; O = np.array(k['outer']) + n * outer
    m = np.zeros(crop.shape, np.uint8)
    for i in range(len(P) - 1):
        cv2.fillPoly(m, [crop.poly(np.array([P[i], P[i + 1], O[i + 1], O[i]]))], 1, shift=4)
    return m.astype(bool)


def frac(a, b):
    return round(float((a & b).sum()) / float(b.sum()), 4) if b.sum() else None


PX = (C.metres_per_pixel() / S) ** 2
rows, out = [], {}
for k in ZONES['kerbs']:
    P, O = np.array(k['paint']), np.array(k['outer'])
    crop = Crop(np.r_[P[:, 0], O[:, 0]], np.r_[P[:, 1], O[:, 1]])
    cor = corridor(crop, k)
    red_c = crop.red & cor
    # Half-level red (same idea as the B1 checker): a* above the midpoint of asphalt and red-core a*.
    a_mid = (np.median(crop.a[crop.asphalt & cor]) + np.median(crop.a[red_c])) / 2
    red50_c = (crop.a > a_mid) & cor
    res = {}
    for v in ('before', 'after'):
        t = TRIS[v]
        kerb = (crop.mask(t['red']) | crop.mask(t['white'])) & cor
        strip = crop.mask(t['paint']) & cor
        res[v] = dict(
            kerbArea_m2=round(kerb.sum() * PX, 2),
            kerbOnKerbPaint=frac(crop.red | crop.white, kerb),
            kerbOnAsphalt=frac(crop.asphalt, kerb),
            whiteArea_m2=round(strip.sum() * PX, 2),
            whiteOnWhitePaint=frac(crop.white, strip),
            whiteOnAsphalt=frac(crop.asphalt, strip),
            orthoRedCovered=frac(kerb, red_c),
            orthoRedUncovered_m2=round((red_c & ~kerb).sum() * PX, 2),
            orthoRed50Covered=frac(kerb, red50_c),
            orthoRed50Uncovered_m2=round((red50_c & ~kerb).sum() * PX, 2),
        )
    out[k['id']] = dict(side=k['side'], inside=k['id'] in INSIDE or bool(k.get('check', {}).get('inside')),
                        addedOutsideCg=k['addedOutsideCg'], **res)
    rows.append([k['id'], out[k['id']]['inside'], k['addedOutsideCg']] +
                [res[v][m] for m in res['before'] for v in ('before', 'after')])
    print(k['id'], {v: (res[v]['kerbOnKerbPaint'], res[v]['whiteOnWhitePaint'], res[v]['orthoRedCovered'], res[v]['orthoRed50Covered']) for v in res}, flush=True)


def rnd(x, n=4):
    return None if x is None else round(x, n)


def agg(ids):
    a = {}
    for v in ('before', 'after'):
        r = [out[i][v] for i in ids]
        w = lambda key, area: (sum((x[key] or 0) * x[area] for x in r) / sum(x[area] for x in r)) if sum(x[area] for x in r) else None
        a[v] = dict(kerbArea_m2=round(sum(x['kerbArea_m2'] for x in r), 1),
                    kerbOnKerbPaint=rnd(w('kerbOnKerbPaint', 'kerbArea_m2')),
                    kerbOnAsphalt=rnd(w('kerbOnAsphalt', 'kerbArea_m2')),
                    whiteArea_m2=round(sum(x['whiteArea_m2'] for x in r), 1),
                    whiteOnWhitePaint=rnd(w('whiteOnWhitePaint', 'whiteArea_m2')),
                    whiteOnAsphalt=rnd(w('whiteOnAsphalt', 'whiteArea_m2')),
                    orthoRedUncovered_m2=round(sum(x['orthoRedUncovered_m2'] for x in r), 1),
                    medianOrthoRedCovered=round(float(np.median([x['orthoRedCovered'] for x in r])), 4),
                    orthoRed50Uncovered_m2=round(sum(x['orthoRed50Uncovered_m2'] for x in r), 1),
                    medianOrthoRed50Covered=round(float(np.median([x['orthoRed50Covered'] for x in r])), 4))
    return a


ids = list(out)
summary = dict(all=agg(ids), inside=agg([i for i in ids if out[i]['inside']]),
               cgKerbsOnly=agg([i for i in ids if not out[i]['addedOutsideCg']]),
               k15_k23=agg(['k15', 'k23']), kDunlopR=agg(['kDunlopR']))
(HERE / 'render_ortho_metrics.json').write_text(json.dumps(dict(
    method=__doc__.strip().splitlines()[0], caveat=__doc__.split('Caveat: ')[1].split('\n\n')[0].replace('\n', ' '),
    classes='white: L*>=75 & chroma<20 (not red); red: r-g>22 & r-b>18 & r>120 & g<175; asphalt: L*<70 & chroma<10',
    corridor='per kerb: from paint - 1.0 m (road side) to outer + 1.0 m, over the zone station range',
    raster=f'{S}x supersampled ortho ({C.metres_per_pixel() / S:.3f} m), top faces of the rendered batches',
    metrics=dict(kerbOnKerbPaint='share of rendered block-band area on red or white ortho paint',
                 kerbOnAsphalt='share of rendered block-band area on asphalt-coloured ortho pixels',
                 whiteOnWhitePaint='share of rendered white strip/marking area (paint batch) on white ortho paint',
                 orthoRedCovered='share of red ortho pixels in the corridor covered by the rendered block band',
                 orthoRedUncovered_m2='red ortho area in the corridor not covered by the rendered block band',
                 orthoRed50Covered='as orthoRedCovered with a half-level red class (a* above the asphalt/red-core midpoint, per kerb); the lenient RGB rule is the one the CG kerbs were traced with, so it favours them at the outer edge'),
    attribution=ATTR, summary=summary, perKerb=out), indent=1) + '\n')
metric_names = list(out[ids[0]]['before'])
with open(HERE / 'render_ortho_summary.csv', 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['kerb', 'inside', 'addedOutsideCg'] + [f'{m}_{v}' for m in metric_names for v in ('before', 'after')])
    w.writerows(rows)
print(json.dumps(summary, indent=1))


# ---- overlays: before | after over the ortho crop ----
def overlay(kid, f, half=11.):
    k = next(z for z in ZONES['kerbs'] if z['id'] == kid)
    j = int(round(f * (len(k['station']) - 1)))
    cx, cz = np.mean([k['paint'][j], k['outer'][j]], axis=0)
    crop = Crop([cx - half, cx + half], [cz - half, cz + half], pad=0)
    base = cv2.resize(crop.rgb, None, fx=S, fy=S, interpolation=cv2.INTER_LINEAR)[..., ::-1].copy()
    panels = []
    for v in ('before', 'after'):
        t = TRIS[v]; im = base.copy()
        layers = [(crop.mask(t['red']) | crop.mask(t['white']), (255, 0, 255)),
                  (crop.mask(t['paint']), (255, 255, 0)), (crop.mask(t['roadFill']), (0, 220, 255))]
        for m, col in layers:
            cs, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
            cv2.drawContours(im, cs, -1, col, 2)
        label = 'BEFORE: geometry.json CG kerb' if v == 'before' else 'AFTER: kerb_zones.json'
        cv2.rectangle(im, (0, 0), (im.shape[1], 30), (0, 0, 0), -1)
        cv2.putText(im, f'{label}  {kid} @ {k["station"][j]:.1f} m', (8, 21), cv2.FONT_HERSHEY_SIMPLEX, .6, (255, 255, 255), 1, cv2.LINE_AA)
        panels.append(im)
    gap = np.full((panels[0].shape[0], 8, 3), 255, np.uint8)
    im = np.hstack([panels[0], gap, panels[1]])
    foot = np.zeros((44, im.shape[1], 3), np.uint8)
    cv2.putText(foot, 'magenta = kerb block band (red+white)   cyan = white paint (strip / edge marking)   orange = asphalt fill',
                (8, 17), cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(foot, ATTR + f'; crop {2 * half:.0f} m, pixel 0.2 m. Lines are image traces, not survey.', (8, 37),
                cv2.FONT_HERSHEY_SIMPLEX, .5, (200, 200, 200), 1, cv2.LINE_AA)
    cv2.imwrite(str(HERE / f'overlay_{kid}_{int(f * 100):02d}.png'), np.vstack([im, foot]))


for kid, f in [('k15', .5), ('k23', .5), ('kDunlopR', .5), ('k21', .97), ('k16', .97), ('k1', .5)]:
    overlay(kid, f)
print('overlays written')
