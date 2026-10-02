"""Independent kerb-contact consistency scorer (evaluation only; written for the 2026-10-02 improvement eval).

Re-implements the KPI from its definition WITHOUT importing build_apex_kpi / register_gps_* code:

  g_block  = min over the label's pass window of the signed distance from the INNER front/rear tyre
             outer edge to the kerb BLOCK line (road-side start of the red/white blocks).
             Positive = road side, negative = on the blocks. g = min(front, rear).
  tyre     = antenna at vehicle centre (as the shipped registration), axles at +-wheelbase/2,
             inner tyre outer edge at (track + tyre width)/2 toward the kerb side; heading from the
             derivative of a cubic spline of the registered GPS track (sampled at DT).
  window   = 'label'   : t0..t1 of the label in kerb_contacts.json (time window; same for every offset)
             'station' : samples whose road station (nearest geometry.json centre vertex, computed at the
                         evaluated offset) lies in [s0-3, s1+3] of the kerb
  line end = a tyre point whose closest point on the block polyline is beyond an end vertex (projection
             parameter < 0 on the first segment or > 1 on the last) does not count (NaN)
  sign     = side of the closest segment w.r.t. the travel direction of the polyline (station order):
             left kerb -> road side is right of travel, right kerb -> road side is left of travel.

Kerb lines:
  'old' : geometry.json curbs[i] inner line (cols 1,2) as id k{i} (what the baseline viewer drew as the
          kerb start; the CG has no white strip and no Dunlop-R kerb)
  'new' : public/data/tracks/fuji/cg_study/kerb_zones.json 'block' line per id (k0..k24 + kDunlopR)
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline

ROOT = Path(__file__).resolve().parents[4]
TRACK_DIR = ROOT / 'public/data/tracks/fuji'
VEH = json.loads((ROOT / 'public/data/vehicles/mazda2-dj.json').read_text(encoding='utf-8'))
M_PER_DEG = 111_320.0
DT = 0.01
STRONG_TOL = 0.15
SILENT_DEEP = -0.55
PASS_MARGIN = 3.0

_track = json.loads((TRACK_DIR / 'track.json').read_text(encoding='utf-8'))
ORIGIN = _track['origin']
_geo = json.loads((TRACK_DIR / 'cg_study/geometry.json').read_text(encoding='utf-8'))
_road = np.array([r[:5] for r in _geo['road']], float)
ROAD_STATION, ROAD_CENTRE = _road[:, 0], _road[:, 1:3]


def to_local(lat, lng):
    lat, lng = np.asarray(lat, float), np.asarray(lng, float)
    sx = M_PER_DEG * math.cos(math.radians(ORIGIN['lat']))
    return np.stack([(lng - ORIGIN['lng']) * sx, -(lat - ORIGIN['lat']) * M_PER_DEG], 1)


def station_of(P):
    """nearest centre vertex station (brute force in chunks; 0.5 m grid)."""
    out = np.empty(len(P))
    for a in range(0, len(P), 2000):
        q = P[a:a + 2000]
        d2 = ((q[:, None, :] - ROAD_CENTRE[None, :, :]) ** 2).sum(-1)
        out[a:a + 2000] = ROAD_STATION[np.argmin(d2, 1)]
    return out


def load_lines(kind: str) -> dict:
    zones = json.loads((TRACK_DIR / 'cg_study/kerb_zones.json').read_text(encoding='utf-8'))
    out = {}
    if kind == 'new':
        for kb in zones['kerbs']:
            st = np.asarray(kb['station'], float)
            out[kb['id']] = dict(side=kb['side'], s0=float(st[0]), s1=float(st[-1]), line=np.asarray(kb['block'], float))
    elif kind == 'old':
        for i, c in enumerate(_geo['curbs']):
            rows = np.asarray(c['rows'], float)
            out[f'k{i}'] = dict(side=c['side'], s0=float(rows[0, 0]), s1=float(rows[-1, 0]), line=rows[:, 1:3].copy())
    else:
        raise ValueError(kind)
    return out


def signed_gap(P: np.ndarray, V: np.ndarray, side: str) -> np.ndarray:
    a, b = V[:-1], V[1:]
    ab = b - a
    L2 = np.maximum((ab ** 2).sum(1), 1e-12)
    rel = P[:, None, :] - a[None, :, :]
    tr = (rel * ab[None]).sum(-1) / L2[None]
    tc = np.clip(tr, 0.0, 1.0)
    proj = a[None] + tc[..., None] * ab[None]
    res = P[:, None, :] - proj
    d2 = (res ** 2).sum(-1)
    j = np.argmin(d2, 1)
    row = np.arange(len(P))
    r = res[row, j]
    s = ab[j]
    cross = s[:, 0] * r[:, 1] - s[:, 1] * r[:, 0]       # > 0: right of travel (x east, z south)
    sign = np.sign(cross) if side == 'left' else -np.sign(cross)
    sign[sign == 0] = 1.0
    g = np.sqrt(d2[row, j]) * sign
    beyond = ((j == 0) & (tr[row, j] < 0)) | ((j == len(ab) - 1) & (tr[row, j] > 1))
    g[beyond] = np.nan
    return g


class Lap:
    def __init__(self, race: str, lap: int):
        d = json.loads((ROOT / f'public/data/races/{race}/osaki_hmr_demio_101_lap_{lap:03d}.json').read_text(encoding='utf-8'))
        self.t = np.asarray(d['t'], float)
        xy = to_local(d['lat'], d['lng'])
        self.cx, self.cz = CubicSpline(self.t, xy[:, 0]), CubicSpline(self.t, xy[:, 1])
        self.tt = np.arange(self.t[0], self.t[-1], DT)
        self.p = np.stack([self.cx(self.tt), self.cz(self.tt)], 1)
        h = np.stack([self.cx(self.tt, 1), self.cz(self.tt, 1)], 1)
        self.h = h / np.maximum(np.linalg.norm(h, axis=1), 1e-6)[:, None]
        self.right = np.stack([-self.h[:, 1], self.h[:, 0]], 1)
        self._st_cache = {}

    def stations(self, offset):
        key = (round(float(offset[0]), 4), round(float(offset[1]), 4))
        if key not in self._st_cache:
            self._st_cache[key] = station_of(self.p + np.asarray(offset, float))
        return self._st_cache[key]

    def inner_edges(self, mask, offset, side):
        hl = VEH['wheelbaseMeters'] / 2
        hf = (VEH['frontTrackMeters'] + VEH['frontTyreWidthMeters']) / 2
        hr = (VEH['rearTrackMeters'] + VEH['rearTyreWidthMeters']) / 2
        s = -1.0 if side == 'left' else 1.0        # left kerb: inner side = left = -right
        p = self.p[mask] + np.asarray(offset, float)
        h, rt = self.h[mask], self.right[mask]
        return p + hl * h + s * hf * rt, p - hl * h + s * hr * rt


def gap_for(lapobj: Lap, line: dict, offset, window: str, label: dict):
    if window == 'label':
        m = (lapobj.tt >= label['t0']) & (lapobj.tt <= label['t1'])
    else:
        st = lapobj.stations(offset)
        m = (st >= line['s0'] - PASS_MARGIN) & (st <= line['s1'] + PASS_MARGIN)
    if m.sum() < 5:
        return None
    F, R_ = lapobj.inner_edges(m, offset, line['side'])
    gf = signed_gap(F, line['line'], line['side'])
    gr = signed_gap(R_, line['line'], line['side'])
    if not np.isfinite(gf).any() and not np.isfinite(gr).any():
        return None
    out = {'gFront': float(np.nanmin(gf)) if np.isfinite(gf).any() else None,
           'gRear': float(np.nanmin(gr)) if np.isfinite(gr).any() else None}
    out['g'] = min(v for v in (out['gFront'], out['gRear']) if v is not None)
    return out


def labels_for(race: str, classes=('strong', 'silent')) -> list:
    c = json.loads((ROOT / f'public/data/races/{race}/kerb_contacts.json').read_text(encoding='utf-8'))
    out = []
    for lap, rows in c['laps'].items():
        for r in rows:
            if r['class'] in classes:
                out.append(dict(lap=int(lap), kerb=r['kerb'], cls=r['class'], peak=r['peakRollRms'], t0=r['t0'], t1=r['t1']))
    return out


def score(race: str, lines: dict, offsets: dict, window: str = 'label', labels=None, laps=None, only=None) -> list:
    labels = labels if labels is not None else labels_for(race)
    laps = laps if laps is not None else {}
    rows = []
    for lab in labels:
        if only is not None and not only(lab):
            continue
        n = lab['lap']
        if n not in laps:
            laps[n] = Lap(race, n)
        line = lines.get(lab['kerb'])
        g = None if line is None else gap_for(laps[n], line, offsets[n], window, lab)
        rows.append(dict(lap=n, kerb=lab['kerb'], cls=lab['cls'],
                         g=None if g is None else round(g['g'], 3),
                         gFront=None if g is None or g['gFront'] is None else round(g['gFront'], 3),
                         gRear=None if g is None or g['gRear'] is None else round(g['gRear'], 3),
                         reason=None if g is not None else ('no kerb line with this id' if line is None else 'no pass samples / beyond line ends')))
    return rows


def auc(S, N):
    if not S or not N:
        return None
    s, n = np.asarray(S)[:, None], np.asarray(N)[None, :]
    return float(((s < n).sum() + 0.5 * (s == n).sum()) / (s.size * n.size))


def metrics(rows: list, key='g', strong_tol=STRONG_TOL, silent_deep=SILENT_DEEP, drop=None) -> dict:
    rs = [r for r in rows if r[key] is not None and not (drop and drop(r))]
    S = [r[key] for r in rs if r['cls'] == 'strong']
    N = [r[key] for r in rs if r['cls'] == 'silent']
    contra = [f"L{r['lap']} {r['kerb']} {r['cls']} {r[key]:+.2f}" for r in rs
              if (r['cls'] == 'strong' and r[key] > strong_tol) or (r['cls'] == 'silent' and r[key] < silent_deep)]
    pairs = [(a[key], b[key]) for a in rs if a['cls'] == 'strong' for b in rs if b['cls'] == 'silent' and b['kerb'] == a['kerb']]
    wk = None if not pairs else float(np.mean([1.0 if x < y else 0.5 if x == y else 0.0 for x, y in pairs]))
    a = auc(S, N)
    considered = [r for r in rows if not (drop and drop(r))]
    return dict(labels=len(considered), measurable=len(rs), unmeasurable=len(considered) - len(rs),
                unmeasurableList=[f"L{r['lap']} {r['kerb']} {r['cls']}" for r in considered if r[key] is None],
                strong=len(S), silent=len(N),
                contradictions=len(contra), contradictionList=contra,
                strongButOff=sum(1 for c in contra if ' strong ' in c), silentButDeep=sum(1 for c in contra if ' silent ' in c),
                auc=None if a is None else round(a, 3),
                withinKerbAuc=None if wk is None else round(wk, 3), withinKerbPairs=len(pairs),
                strongMedian=None if not S else round(float(np.median(S)), 3),
                silentMedian=None if not N else round(float(np.median(N)), 3))


def cluster_bootstrap(rows_a: list, rows_b: list, B=10000, seed=1, drop=None) -> dict:
    """kerb-cluster bootstrap of (a - b) on labels measurable in BOTH."""
    A = {(r['lap'], r['kerb']): r for r in rows_a if r['g'] is not None and not (drop and drop(r))}
    Bm = {(r['lap'], r['kerb']): r for r in rows_b if r['g'] is not None and not (drop and drop(r))}
    keys = sorted(set(A) & set(Bm))
    kerbs = sorted({k for _, k in keys})
    by = {k: [x for x in keys if x[1] == k] for k in kerbs}

    def stat(sel, Mp):
        S = [Mp[x]['g'] for x in sel if Mp[x]['cls'] == 'strong']
        N = [Mp[x]['g'] for x in sel if Mp[x]['cls'] == 'silent']
        c = sum(1 for x in sel if (Mp[x]['cls'] == 'strong' and Mp[x]['g'] > STRONG_TOL) or
                (Mp[x]['cls'] == 'silent' and Mp[x]['g'] < SILENT_DEEP))
        return c, auc(S, N)

    rng = np.random.default_rng(seed)
    dc, da = [], []
    for _ in range(B):
        sel = [x for i in rng.choice(len(kerbs), len(kerbs), replace=True) for x in by[kerbs[i]]]
        ca, aa = stat(sel, A)
        cb, ab = stat(sel, Bm)
        dc.append(ca - cb)
        if aa is not None and ab is not None:
            da.append(aa - ab)
    c0a, a0a = stat(keys, A)
    c0b, a0b = stat(keys, Bm)
    dc, da = np.asarray(dc), np.asarray(da)
    return dict(commonLabels=len(keys), clusters=len(kerbs), B=B,
                contradictionDiff=c0a - c0b, contradictionDiffCI95=[float(np.percentile(dc, 2.5)), float(np.percentile(dc, 97.5))],
                pContradictionDiffGE0=round(float((dc >= 0).mean()), 4),
                aucDiff=None if a0a is None or a0b is None else round(a0a - a0b, 4),
                aucDiffCI95=[round(float(np.percentile(da, 2.5)), 4), round(float(np.percentile(da, 97.5)), 4)] if len(da) else None,
                pAucDiffLE0=round(float((da <= 0).mean()), 4) if len(da) else None)
