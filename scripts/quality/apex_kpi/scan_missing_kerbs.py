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


# scan both road edges every 0.5 m for red paint within 2.5 m outward of the CG road edge
kerbs = geo['curbs']
covered = {'left': np.zeros(len(road), bool), 'right': np.zeros(len(road), bool)}
for c in kerbs:
    r = np.array(c['rows']); i0, i1 = int(r[0,0]/STEP), int(r[-1,0]/STEP)
    covered[c['side']][max(0,i0-2):i1+3] = True
found = {'left': np.zeros(len(road), bool), 'right': np.zeros(len(road), bool)}
U2 = np.arange(-2.5, 1.0001, 0.04)    # outward negative
chunk = 200
for start in range(0, len(road), chunk):
    rows = road[start:start+chunk]
    cx, cz = rows[len(rows)//2, 1], rows[len(rows)//2, 2]
    patch = Patch(cx, cz, half=80.)
    for j, rr in enumerate(rows):
        i = start + j
        n = rr[3:5]/np.linalg.norm(rr[3:5])
        for side, ex, ez, sgn in (('left', rr[7], rr[8], 1.), ('right', rr[9], rr[10], -1.)):
            nn = n*sgn   # toward road centre
            x = ex + U2*nn[0]; z = ez + U2*nn[1]
            lab = patch.sample(x, z)
            ref = patch.sample(ex + np.array([1.5, 2.0, 2.5, 3.0])*nn[0], ez + np.array([1.5, 2.0, 2.5, 3.0])*nn[1])
            refa = np.median(ref[:, 1])
            da = lab[:, 1] - refa
            m = (U2 < 0.2)
            found[side][i] = (gaussian_filter1d(da, 1.0)[m] > 18).sum() >= 4   # >= 0.16 m of clearly red
    print('scanned', start, flush=True)
segs = []
for side in ('left', 'right'):
    f = found[side] & ~covered[side]
    # close small gaps (white blocks between red) up to 3 m
    from scipy.ndimage import binary_closing
    f = binary_closing(f, structure=np.ones(7))
    idx = np.flatnonzero(f)
    if not len(idx): continue
    for grp in np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1):
        if len(grp) * STEP >= 4.0:
            segs.append(dict(side=side, start=float(grp[0]*STEP), end=float(grp[-1]*STEP), lengthM=float(len(grp)*STEP)))
json.dump(dict(missingKerbCandidates=segs, note='red paint within 2.5 m outside the CG road edge, not covered by any CG kerb (+-1 m). Candidate list for review.'), open(OUT/'missing_kerbs.json','w'), indent=1)
for s in segs: print(s)
