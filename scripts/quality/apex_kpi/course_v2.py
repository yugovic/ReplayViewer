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


cand = json.load(open(OUT/'kerb_paint_candidate.json'))
kerbs_v2 = []
for k, c in enumerate(geo['curbs']):
    r = np.array(c['rows'], float); cr = np.array([row[:5] for row in cand['curbs'][k]['rows']], float)
    kerbs_v2.append(dict(id=f'k{k}', side=c['side'], station=r[:,0].tolist(), paint=cr[:,1:3].round(3).tolist(),
                         block=cr[:,3:5].round(3).tolist(), outer=r[:,3:5].round(3).tolist(), source='cg+ortho-paint'))
# missing Dunlop right kerb: scan from CG right road edge
st = np.arange(2964.0, 3004.01, 0.5)
mid = road_row(2984.0); patch = Patch(mid[1], mid[2], half=40.)
P, B, O, ok = [], [], [], []
for s in st:
    rr = road_row(s); n = rr[3:5]/np.linalg.norm(rr[3:5]); nn = -n   # toward road centre for right side
    x = rr[9] + U*nn[0]; z = rr[10] + U*nn[1]
    res, q = detect_edge(patch.sample(x, z))
    if np.isfinite(res['edge']) and np.isfinite(res['paint_width']) and res['paint_width'] > 0.4:
        e = res['edge']; red = res['red_edge'] if np.isfinite(res['red_edge']) else e
        P.append([rr[9]+e*nn[0], rr[10]+e*nn[1]]); B.append([rr[9]+red*nn[0], rr[10]+red*nn[1]])
        O.append([rr[9]+(e-res['paint_width'])*nn[0], rr[10]+(e-res['paint_width'])*nn[1]]); ok.append(s)
        print(f'{s:.1f} paint u={e:+.2f} red={red:+.2f} width={res["paint_width"]:.2f} kind={res["kind"]}')
kerbs_v2.append(dict(id='kDunlopR', side='right', station=ok, paint=np.round(P,3).tolist(), block=np.round(B,3).tolist(),
                     outer=np.round(O,3).tolist(), source='ortho-paint scan (missing in CG)'))
json.dump(dict(version='candidate-v2', note='Kerb zones: paint start (white strip, track-limit line), block start (raised red/white), outer. Source VIRTUAL SHIZUOKA 2019 ortho CC BY 4.0. Not a survey.', kerbs=kerbs_v2), open(OUT/'course_v2_kerbs.json','w'))
print('kerbs', len(kerbs_v2), 'dunlop stations', len(ok))
