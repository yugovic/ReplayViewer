"""Independent check of CG kerb inner edges vs. native ortho (read-only on repo).

Method (differs from pipeline's fixed RGB red threshold on 0.1 m strips):
 - Lab colour space; asphalt reference = robust median in the road 1.0-3.5 m
   inside the CG edge for each cross-section.
 - paint index q(u) = max(dL+, k*da+) (only brighter or redder than asphalt
   counts: tyre marks / shadows do not trigger).
 - Edge = half-level crossing between local asphalt level and paint plateau,
   searched from the road side outward, linear sub-sample interpolation
   on a 0.04 m bilinear profile.  Cross-check: k-means (k=5) colour classes
   on the local patch -> soft "paint vs asphalt" membership 0.5 crossing.
"""
import json, math, sys
from pathlib import Path
import numpy as np
import cv2
import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from paths import ROOT, WORK, XRK, VIDEO  # noqa: E402
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import map_coordinates, gaussian_filter1d

Image.MAX_IMAGE_PIXELS = None
ROOT = ROOT
OUT = WORK
OUT.mkdir(parents=True, exist_ok=True)
source = ROOT / 'pipeline/cache/shizuoka_ortho/native_mosaic.png'
meta = json.loads(source.with_name('native_mosaic_meta.json').read_text())
track = json.loads((ROOT / 'public/data/tracks/fuji/track.json').read_text())
geo = json.loads((ROOT / 'public/data/tracks/fuji/cg_study/geometry.json').read_text())
print('loading ortho...', flush=True)
rgb = np.array(Image.open(source).convert('RGB'))
H, W = rgb.shape[:2]
b = meta['bbox']; o = track['origin']
north = np.arcsinh(np.tan(np.radians(b['maxLat'])))
south = np.arcsinh(np.tan(np.radians(b['minLat'])))

def pixel(x, z):
    x = np.asarray(x, float); z = np.asarray(z, float)
    lat = o['lat'] - z / 111320
    lng = o['lng'] + x / (111320 * math.cos(math.radians(o['lat'])))
    return ((lng-b['minLng'])/(b['maxLng']-b['minLng'])*W,
            (north-np.arcsinh(np.tan(np.radians(lat))))/(north-south)*H)

road = np.array(geo['road'], float)
STEP = geo['stationStep']
def road_row(s):
    return road[int(round(s/STEP))]

class Patch:
    """Local crop converted to Lab, bilinear sampling in world XZ."""
    def __init__(self, cx, cz, half=70.):
        px, py = pixel([cx-half, cx+half], [cz+half, cz-half])
        self.x0 = int(max(0, math.floor(min(px))-4)); self.x1 = int(min(W, math.ceil(max(px))+4))
        self.y0 = int(max(0, math.floor(min(py))-4)); self.y1 = int(min(H, math.ceil(max(py))+4))
        self.rgb = rgb[self.y0:self.y1, self.x0:self.x1]
        self.lab = cv2.cvtColor(self.rgb.astype(np.float32)/255., cv2.COLOR_RGB2LAB)
    def sample(self, x, z, arr=None):
        arr = self.lab if arr is None else arr
        px, py = pixel(x, z)
        px = px - .5 - self.x0; py = py - .5 - self.y0
        return np.stack([map_coordinates(arr[:, :, c], [py, px], order=1, mode='nearest')
                         for c in range(arr.shape[2])], axis=-1)

U = np.arange(-3.0, 4.0001, .04)   # + toward road centre from the CG edge point

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

def kmeans_edge(prof, centres, asphalt_id, paint_ids):
    if not paint_ids: return np.nan
    d = np.linalg.norm(prof[:, None, :]-centres[None], axis=2)
    da = d[:, asphalt_id]; dp = d[:, paint_ids].min(axis=1)
    mem = gaussian_filter1d(da/(da+dp+1e-6), 1.0)
    idx = np.flatnonzero((U <= 3.0) & (U >= -2.8))[::-1]
    for k, i in enumerate(idx):
        win = idx[k:k+4]
        if len(win) == 4 and np.all(mem[win] > .5):
            if k == 0: return float(U[i])
            j = idx[k-1]
            return float(U[j] + (.5-mem[j])/(mem[i]-mem[j])*(U[i]-U[j]))
    return np.nan

INSIDE = [1, 3, 7, 9, 10, 13, 15, 18, 19, 20, 21, 23, 24]
PRIORITY = [13, 1, 3, 7, 10, 15, 21, 24, 23]
NAMES = {1:'Coca-Cola', 3:'hairpin', 7:'Dunlop', 13:'T1'}
kerbs = geo['curbs']
NSEC = 15
results = {}

def to_px(patch, x, z, x0, y0, s):
    px, py = pixel(x, z)
    return list(zip(((np.asarray(px)-x0)*s).tolist(), ((np.asarray(py)-y0)*s).tolist()))

def draw_overlay(fname, cx, cz, half, scale, kerb_idx, secs, title):
    px, py = pixel([cx-half, cx+half], [cz+half, cz-half])
    x0 = int(math.floor(min(px))); x1 = int(math.ceil(max(px)))
    y0 = int(math.floor(min(py))); y1 = int(math.ceil(max(py)))
    crop = Image.fromarray(rgb[y0:y1, x0:x1]).resize(((x1-x0)*scale, (y1-y0)*scale), Image.BICUBIC)
    d = ImageDraw.Draw(crop)
    # road edges near the crop
    near = (np.abs(road[:, 1]-cx) < half*1.5) & (np.abs(road[:, 2]-cz) < half*1.5)
    ids = np.flatnonzero(near)
    if len(ids):
        for grp in np.split(ids, np.flatnonzero(np.diff(ids) > 1)+1):
            d.line(to_px(None, road[grp, 7], road[grp, 8], x0, y0, scale), fill=(255, 0, 255), width=1)
            d.line(to_px(None, road[grp, 9], road[grp, 10], x0, y0, scale), fill=(0, 255, 0), width=1)
    for kk, c in enumerate(kerbs):
        r = np.array(c['rows'], float)
        if not np.any((np.abs(r[:, 1]-cx) < half*1.5) & (np.abs(r[:, 2]-cz) < half*1.5)): continue
        d.line(to_px(None, r[:, 1], r[:, 2], x0, y0, scale), fill=(0, 255, 255), width=1)
        d.line(to_px(None, r[:, 3], r[:, 4], x0, y0, scale), fill=(255, 255, 0), width=1)
    for sd in secs:
        # detected kerb edge (red dot) and opposite edge (orange dot), section line (white)
        p0 = to_px(None, [sd['ax']], [sd['az']], x0, y0, scale)[0]
        p1 = to_px(None, [sd['bx']], [sd['bz']], x0, y0, scale)[0]
        d.line([p0, p1], fill=(255, 255, 255), width=1)
        for key, col in [('det', (255, 40, 40)), ('opp_det', (255, 150, 0))]:
            if np.isfinite(sd.get(key+'x', np.nan)):
                q = to_px(None, [sd[key+'x']], [sd[key+'z']], x0, y0, scale)[0]
                d.ellipse([q[0]-3, q[1]-3, q[0]+3, q[1]+3], outline=col, width=2)
    d.rectangle([0, 0, crop.width, 18], fill=(0, 0, 0))
    d.text((4, 3), title + '  cyan=CG kerb inner, yellow=CG outer, magenta/green=CG road L/R, red o=detected kerb edge, orange o=detected opp edge', fill=(255, 255, 255))
    crop.save(OUT / fname)
    return str(OUT / fname)

images = []
for k in INSIDE:
    c = kerbs[k]; side = c['side']; r = np.array(c['rows'], float)
    sgn_center = 1. if side == 'left' else -1.   # direction toward road centre = sgn*normal
    st0, st1 = r[0, 0], r[-1, 0]
    mid = r[len(r)//2]
    patch = Patch(mid[1], mid[2], half=max(70., (st1-st0)/2+20))
    # local k-means classes on a strip around the kerb + road edge
    strip_pts = []
    for row in r[::2]:
        rr = road_row(row[0]); n = rr[3:5]*sgn_center
        uu = np.arange(-2.5, 3.5, .1)
        strip_pts.append(np.column_stack([row[1]+uu*n[0], row[2]+uu*n[1]]))
    sp = np.concatenate(strip_pts)
    labs = patch.sample(sp[:, 0], sp[:, 1]).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, .1)
    _, lbl, centres = cv2.kmeans(labs, 5, None, crit, 5, cv2.KMEANS_PP_CENTERS)
    # asphalt class = dominant class at u in [1,3]
    inroad = np.concatenate([(np.arange(-2.5, 3.5, .1) > 1) & (np.arange(-2.5, 3.5, .1) < 3)]*len(strip_pts))
    asphalt_id = int(np.bincount(lbl.ravel()[inroad], minlength=5).argmax())
    ca = centres[asphalt_id]
    paint_ids = [i for i in range(5) if i != asphalt_id and (centres[i][0] > ca[0]+10 or centres[i][1] > ca[1]+12)]
    # curvature along the centreline over the kerb span -> apex station
    rs = road[int(round(st0/STEP)):int(round(st1/STEP))+1]
    hd = np.unwrap(np.arctan2(np.gradient(rs[:, 2]), np.gradient(rs[:, 1])))
    kap = gaussian_filter1d(np.gradient(hd, rs[:, 0]), 6)
    apex_s = float(rs[np.argmax(np.abs(kap)), 0])
    stations = np.linspace(st0+1.5, st1-1.5, NSEC)
    secs = []
    for s in stations:
        ri = int(np.argmin(np.abs(r[:, 0]-s))); row = r[ri]; s = row[0]
        rr = road_row(s); n = rr[3:5]
        dirc = n*sgn_center
        ix, iz = row[1], row[2]
        prof = patch.sample(ix+U*dirc[0], iz+U*dirc[1])
        res, q = detect_edge(prof)
        km = kmeans_edge(prof, centres, asphalt_id, paint_ids)
        # opposite edge (other side road edge from road rows)
        if side == 'left':
            ox, oz = rr[9], rr[10]; odir = -n
        else:
            ox, oz = rr[7], rr[8]; odir = n
        oprof = patch.sample(ox+U*odir[0], oz+U*odir[1])
        ores, _ = detect_edge(oprof)
        okm = np.nan
        # CG width: kerb inner to opposite road edge, measured along normal
        cg_width = abs(np.dot([ox-ix, oz-iz], n))
        sd = dict(station=float(s), observed=int(row[8]), cgRed=int(row[7]),
                  off=-res['edge'] if np.isfinite(res['edge']) else np.nan,  # + => CG edge toward centre vs detected
                  off_km=-km if np.isfinite(km) else np.nan,
                  red_off=-res['red_edge'] if np.isfinite(res['red_edge']) else np.nan,
                  kind=res['kind'], flag=res['flag'], plateau=res['plateau'], paint_width=res['paint_width'],
                  refL=res['refL'], cg_kerb_width=float(abs(row[6]-row[5])),
                  opp_off=-ores['edge'] if np.isfinite(ores['edge']) else np.nan,
                  opp_kind=ores['kind'], opp_flag=ores['flag'], opp_paint_width=ores['paint_width'],
                  opp_red_off=-ores['red_edge'] if np.isfinite(ores['red_edge']) else np.nan,
                  cg_width=float(cg_width),
                  ax=float(ix-3*dirc[0]), az=float(iz-3*dirc[1]), bx=float(ix+4*dirc[0]), bz=float(iz+4*dirc[1]))
        if np.isfinite(res['edge']):
            sd['detx'] = float(ix+res['edge']*dirc[0]); sd['detz'] = float(iz+res['edge']*dirc[1])
        if np.isfinite(ores['edge']):
            sd['opp_detx'] = float(ox+ores['edge']*odir[0]); sd['opp_detz'] = float(oz+ores['edge']*odir[1])
            if np.isfinite(res['edge']):
                sd['det_width'] = float(cg_width - res['edge'] - ores['edge'])
        secs.append(sd)
        np.save(OUT / f'prof_k{k}_s{int(s*10)}.npy', np.column_stack([U, prof, q]).astype(np.float32))
    results[k] = dict(side=side, start=st0, end=st1, apex=apex_s, name=NAMES.get(k, ''), sections=secs,
                      kmeans_centres=centres.tolist(), asphalt_id=asphalt_id, paint_ids=paint_ids)
    if k in PRIORITY:
        images.append(draw_overlay(f'k{k:02d}_mid_60m.png', mid[1], mid[2], 30, 3, k, secs, f'kerb {k} {side} mid s={mid[0]}'))
        ar = r[int(np.argmin(np.abs(r[:, 0]-apex_s)))]
        images.append(draw_overlay(f'k{k:02d}_apex_60m.png', ar[1], ar[2], 30, 3, k, secs, f'kerb {k} {side} apex s={apex_s}'))
        images.append(draw_overlay(f'k{k:02d}_apex_16m.png', ar[1], ar[2], 8, 10, k, secs, f'kerb {k} apex zoom'))
    print('done kerb', k, flush=True)

def st(v):
    v = np.asarray([x for x in v if np.isfinite(x)])
    if len(v) == 0: return dict(n=0)
    return dict(n=int(len(v)), med=float(np.median(v)), q1=float(np.percentile(v, 25)), q3=float(np.percentile(v, 75)),
                maxabs=float(v[np.argmax(np.abs(v))]))
summary = {}
for k, R in results.items():
    S = R['sections']
    summary[k] = dict(side=R['side'], name=R['name'], apex=R['apex'],
        kerb=st([s['off'] for s in S]), kerb_km=st([s['off_km'] for s in S]),
        kerb_red=st([s['red_off'] for s in S]),
        opp=st([s['opp_off'] for s in S]),
        width_err=st([s['cg_width']-s['det_width'] for s in S if 'det_width' in s]),
        kinds=[s['kind'] for s in S], flags=[s['flag'] for s in S if s['flag']],
        opp_kinds=[s['opp_kind'] for s in S], opp_flags=[s['opp_flag'] for s in S if s['opp_flag']])
json.dump(dict(results=results, summary=summary, images=images), open(OUT/'kerbcheck.json', 'w'), indent=1, default=float)
for k, sm in summary.items():
    print(k, sm['side'], sm['name'], 'kerb', {a: round(b, 3) for a, b in sm['kerb'].items()},
          'km', {a: round(b, 3) for a, b in sm['kerb_km'].items()},
          'red', {a: round(b, 3) for a, b in sm['kerb_red'].items()},
          'opp', {a: round(b, 3) for a, b in sm['opp'].items()},
          'werr', {a: round(b, 3) for a, b in sm['width_err'].items()})
    print('   kinds', ''.join(x[0] if x else '-' for x in sm['kinds']), 'opp', ''.join(x[0] if x else '-' for x in sm['opp_kinds']), sm['flags'][:4], sm['opp_flags'][:4])
print('\n'.join(images))
