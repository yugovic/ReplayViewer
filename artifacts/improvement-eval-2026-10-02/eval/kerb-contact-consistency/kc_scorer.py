"""Independent kerb-contact consistency scorer (evaluation only, 2026-10-02 improvement round).

Written from the published definitions, NOT importing build_apex_kpi / register_gps_kerb_contact:
  - lap GPS (t, lat, lng) -> local XZ (x east, z south; 111320 m/deg, cos(origin lat) on x)
  - + per-lap translation (registration file)
  - 50 Hz cubic spline; heading = spline derivative; tyre outer edges at
    +-(track + tyre width)/2, axles at +-wheelbase/2 (antenna at vehicle centre)
  - pass = samples whose nearest CG centreline vertex station lies in [s0-3, s1+3]
  - g = signed distance of the inner front / rear tyre outer edge to the kerb BLOCK line
    (brute-force exact nearest segment; NaN where the point projects beyond the line ends);
    negative = on the blocks; g_min = min over the pass of both inner tyres
  - contradiction: strong & g_min > +0.15 ; silent & g_min < -0.55
  - AUC = P(g_strong < g_silent) over strong x silent pairs (ties 0.5)
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.spatial import cKDTree

ROOT = Path(r'D:\00_Dev\ReplayViewer')
TRACK_DIR = ROOT / 'public/data/tracks/fuji'
MPD = 111_320.0
DT = 0.02
PASS_MARGIN = 3.0
STRONG_TOL = 0.15
SILENT_DEEP = -0.55

_track = json.loads((TRACK_DIR / 'track.json').read_text(encoding='utf-8'))
ORIGIN = _track['origin']
_geo = json.loads((TRACK_DIR / 'cg_study/geometry.json').read_text(encoding='utf-8'))
_road = np.asarray([r[:3] for r in _geo['road']], float)
STATION = _road[:, 0]
CENTRE = _road[:, 1:3]
CTREE = cKDTree(CENTRE)
VEH = json.loads((ROOT / 'public/data/vehicles/mazda2-dj.json').read_text(encoding='utf-8'))


def to_local(lat, lng):
    lat, lng = np.asarray(lat, float), np.asarray(lng, float)
    sx = MPD * math.cos(math.radians(ORIGIN['lat']))
    return np.stack([(lng - ORIGIN['lng']) * sx, -(lat - ORIGIN['lat']) * MPD], 1)


def station_of(xy):
    _, i = CTREE.query(xy)
    return STATION[i]


# ------------------------------------------------------------------ kerb lines
def zones_new(path=TRACK_DIR / 'cg_study/kerb_zones.json', line='block'):
    doc = json.loads(Path(path).read_text(encoding='utf-8'))
    out = {}
    for kb in doc['kerbs']:
        st = np.asarray(kb['station'], float)
        out[kb['id']] = dict(sign=+1 if kb['side'] == 'left' else -1, s0=float(st[0]), s1=float(st[-1]),
                             V=np.asarray(kb[line], float))
    return out


def zones_old_cg():
    """geometry.json curbs: inner line (cols 1:3) = what the old CG drew as the kerb (block) start."""
    out = {}
    for i, c in enumerate(_geo['curbs']):
        rows = np.asarray(c['rows'], float)
        out[f'k{i}'] = dict(sign=+1 if c['side'] == 'left' else -1, s0=float(rows[0, 0]), s1=float(rows[-1, 0]),
                            V=rows[:, 1:3].copy())
    return out


def signed_gap(V, P, sign, end_rule='projection'):
    """Exact nearest segment of polyline V for points P. sign +1: road side = right of the line direction
    (left kerb), -1: road side = left of it (right kerb). Positive = road side."""
    P = np.asarray(P, float)
    A, B = V[:-1], V[1:]
    D = B - A
    L2 = np.maximum((D * D).sum(1), 1e-12)
    rel = P[:, None, :] - A[None, :, :]                       # (n, m, 2)
    traw = (rel * D[None]).sum(2) / L2[None]
    t = np.clip(traw, 0.0, 1.0)
    foot = A[None] + t[:, :, None] * D[None]
    r = P[:, None, :] - foot
    d2 = (r * r).sum(2)
    j = np.argmin(d2, 1)
    row = np.arange(len(P))
    rr, dd = r[row, j], D[j]
    cross = dd[:, 0] * rr[:, 1] - dd[:, 1] * rr[:, 0]          # >0: right of the direction (x east, z south)
    s = np.sign(cross) * sign
    s[s == 0] = 1.0
    g = np.sqrt(d2[row, j]) * s
    if end_rule == 'projection':
        beyond = ((j == 0) & (traw[row, j] < 0)) | ((j == len(D) - 1) & (traw[row, j] > 1))
    elif end_rule == 'nearestVertex':     # the implementer's rule (nearest vertex is an end vertex)
        _, iv = cKDTree(V).query(P)
        beyond = (iv <= 0) | (iv >= len(V) - 1)
    else:
        raise ValueError(end_rule)
    g[beyond] = np.nan
    return g


# ------------------------------------------------------------------ laps
class Lap:
    def __init__(self, race: str, lap: int):
        idx = json.loads((ROOT / f'public/data/races/{race}/laps.json').read_text(encoding='utf-8'))
        rec = next(s for s in idx['selected'] if int(s['lap']) == lap)
        d = json.loads((ROOT / f'public/data/races/{race}' / rec['data_file']).read_text(encoding='utf-8'))
        t = np.asarray(d['t'], float)
        xy = to_local(d['lat'], d['lng'])
        cx, cz = CubicSpline(t, xy[:, 0]), CubicSpline(t, xy[:, 1])
        self.t50 = np.arange(t[0], t[-1], DT)
        self.p = np.stack([cx(self.t50), cz(self.t50)], 1)
        h = np.stack([cx(self.t50, 1), cz(self.t50, 1)], 1)
        h /= np.maximum(np.linalg.norm(h, axis=1), 1e-6)[:, None]
        right = np.stack([-h[:, 1], h[:, 0]], 1)
        hl = VEH['wheelbaseMeters'] / 2
        hf = (VEH['frontTrackMeters'] + VEH['frontTyreWidthMeters']) / 2
        hr = (VEH['rearTrackMeters'] + VEH['rearTyreWidthMeters']) / 2
        self.W = np.stack([self.p + hl * h - hf * right, self.p + hl * h + hf * right,
                           self.p - hl * h - hr * right, self.p - hl * h + hr * right], 1)  # FL FR RL RR

    def g(self, zone: dict, d, end_rule='projection', twin=None):
        """g_min of the inner tyres; twin=(t0,t1) uses a time window instead of the station window."""
        d = np.asarray(d, float)
        if twin is None:
            st = station_of(self.p + d)
            m = (st >= zone['s0'] - PASS_MARGIN) & (st <= zone['s1'] + PASS_MARGIN)
        else:
            m = (self.t50 >= twin[0]) & (self.t50 <= twin[1])
        if m.sum() < 5:
            return None
        fi, ri = (0, 2) if zone['sign'] > 0 else (1, 3)
        gf = signed_gap(zone['V'], self.W[m, fi] + d, zone['sign'], end_rule)
        gr = signed_gap(zone['V'], self.W[m, ri] + d, zone['sign'], end_rule)
        g = np.concatenate([gf, gr])
        return float(np.nanmin(g)) if np.isfinite(g).any() else None


def race_laps(race):
    idx = json.loads((ROOT / f'public/data/races/{race}/laps.json').read_text(encoding='utf-8'))
    return [int(s['lap']) for s in idx['selected']]


def offsets_from(path):
    reg = json.loads(Path(path).read_text(encoding='utf-8'))
    return {int(l['lap']): np.asarray(l['offsetMeters'], float) for l in reg['laps']}


def labels(race, classes=('strong', 'silent')):
    kc = json.loads((ROOT / f'public/data/races/{race}/kerb_contacts.json').read_text(encoding='utf-8'))
    out = []
    for lap, rows in kc['laps'].items():
        for r in rows:
            if r['class'] in classes and r.get('kerb'):
                out.append(dict(lap=int(lap), kerb=r['kerb'], cls=r['class'], peak=r.get('peakRollRms'),
                                t0=r.get('t0'), t1=r.get('t1')))
    return out


def score(laps: dict, labs: list, zones: dict, offsets: dict, end_rule='projection', use_twin=False):
    rows = []
    for lb in labs:
        z = zones.get(lb['kerb'])
        g = None
        if z is not None:
            g = laps[lb['lap']].g(z, offsets[lb['lap']], end_rule, (lb['t0'], lb['t1']) if use_twin else None)
        rows.append(dict(lb, g=None if g is None else round(g, 3)))
    return rows


def contradiction(r):
    return r['g'] is not None and ((r['cls'] == 'strong' and r['g'] > STRONG_TOL) or
                                   (r['cls'] == 'silent' and r['g'] < SILENT_DEEP))


def auc(S, N):
    if not S or not N:
        return None
    s, n = np.asarray(S)[:, None], np.asarray(N)[None, :]
    return float(((s < n).sum() + 0.5 * (s == n).sum()) / (s.size * n.size))


def metrics(rows, keep=None):
    rs = [r for r in rows if (keep is None or keep(r))]
    meas = [r for r in rs if r['g'] is not None]
    S = [r['g'] for r in meas if r['cls'] == 'strong']
    N = [r['g'] for r in meas if r['cls'] == 'silent']
    pairs = [(a['g'], b['g']) for a in meas if a['cls'] == 'strong' for b in meas
             if b['cls'] == 'silent' and b['kerb'] == a['kerb']]
    a = auc(S, N)
    return dict(labels=len(rs), measurable=len(meas), unmeasurable=[f"L{r['lap']} {r['kerb']} {r['cls']}" for r in rs if r['g'] is None],
                strong=len(S), silent=len(N),
                contradictions=[f"L{r['lap']} {r['kerb']} {r['cls']} g={r['g']:+.2f}" for r in meas if contradiction(r)],
                contradictionCount=sum(contradiction(r) for r in meas),
                auc=None if a is None else round(a, 3),
                withinKerbAuc=None if not pairs else round(float(np.mean([1 if x < y else .5 if x == y else 0 for x, y in pairs])), 3),
                withinKerbPairs=len(pairs),
                strongMedianG=None if not S else round(float(np.median(S)), 3),
                silentMedianG=None if not N else round(float(np.median(N)), 3))


def cluster_bootstrap(rows_a, rows_b, B=10000, seed=1, keep=None):
    """(a - b) contradiction count and AUC, resampling kerbs; rows paired by (lap, kerb), common measurable set."""
    ka = {(r['lap'], r['kerb']): r for r in rows_a if r['g'] is not None and (keep is None or keep(r))}
    kb = {(r['lap'], r['kerb']): r for r in rows_b if r['g'] is not None and (keep is None or keep(r))}
    keys = sorted(set(ka) & set(kb))
    kerbs = sorted({k for _, k in keys})
    by = {k: [x for x in keys if x[1] == k] for k in kerbs}

    def stat(sel, M):
        S = [M[x]['g'] for x in sel if M[x]['cls'] == 'strong']
        N = [M[x]['g'] for x in sel if M[x]['cls'] == 'silent']
        return sum(contradiction(M[x]) for x in sel), auc(S, N)

    rng = np.random.default_rng(seed)
    dc, da = [], []
    for _ in range(B):
        sel = [x for i in rng.integers(0, len(kerbs), len(kerbs)) for x in by[kerbs[i]]]
        ca, aa = stat(sel, ka)
        cb, ab = stat(sel, kb)
        dc.append(ca - cb)
        if aa is not None and ab is not None:
            da.append(aa - ab)
    c0a, a0a = stat(keys, ka)
    c0b, a0b = stat(keys, kb)
    dc, da = np.asarray(dc), np.asarray(da)
    return dict(labels=len(keys), kerbs=len(kerbs), B=B, contradictionDiff=c0a - c0b,
                contradictionDiffCI95=[float(np.percentile(dc, 2.5)), float(np.percentile(dc, 97.5))],
                aucDiff=None if a0a is None or a0b is None else round(a0a - a0b, 4),
                aucDiffCI95=[round(float(np.percentile(da, 2.5)), 4), round(float(np.percentile(da, 97.5)), 4)],
                pAucDiffLE0=round(float((da <= 0).mean()), 4))
