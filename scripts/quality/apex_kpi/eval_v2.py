"""Apex KPI v2 (responds to Sonnet/Fable review round 1).
- Course v2: kerb zones (paint start / block start / outer), missing Dunlop-right kerb added.
- Detector v2: roll-rate HP RMS intervals (merged), side from wheel-speed ripple (GPS-independent).
- Registration reported as feasible sets -> KPI intervals; variants raw / shipped / session-only / v2 centroid / v2+labels (LOKO).
- KPI per corner-pass: front/rear inner gap to block and paint, apex station, on-block length, exit outer-wheel gap.
Scratch analysis; repo untouched."""
from __future__ import annotations
import json, sys, copy, math
import numpy as np
import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from paths import ROOT, WORK, XRK, VIDEO  # noqa: E402
from scipy.interpolate import CubicSpline
from scipy.ndimage import uniform_filter1d
from vib import L, geo, g, lap_data, SHIFT, SP, wheel_points
from register_gps_to_track import Boundary, huber, racing_mask

IT2 = str(WORK)
CV2 = json.load(open(f'{IT2}/course_v2_kerbs.json'))
INSIDE = [1, 3, 7, 9, 10, 13, 15, 18, 19, 20, 21, 23, 24, 25]   # 25 = Dunlop right (new)
NAMES = {13: 'T1 (R)', 1: 'Coca-Cola (L)', 15: '1500m (R)', 3: 'Hairpin (L)', 18: '2420m (R)', 19: '2650m (R)',
         25: 'Dunlop-R (new)', 7: 'Dunlop-L', 20: '3090m (R)', 21: '3250m (R)', 9: '3350m (L)', 10: '3540m (L)',
         23: '3600m (R)', 24: 'Final (R)'}
STRONG, SILENT, ON_TOL = 1.5, 0.8, 0.15     # labels; contradiction tolerance 0.15 m
TAU = 0.05

# ---------- course v2 ----------
KERB = {}
for k, kb in enumerate(CV2['kerbs']):
    st = np.array(kb['station'], float); paint = np.array(kb['paint'], float); outer = np.array(kb['outer'], float)
    block = np.array(kb['block'], float)
    if kb['id'] == 'kDunlopR':
        out_dir = (outer - paint); out_dir /= np.maximum(np.linalg.norm(out_dir, axis=1), 1e-6)[:, None]
        block = paint + 0.42 * out_dir
    sg = +1 if kb['side'] == 'left' else -1
    KERB[k] = dict(side=kb['side'], sign=sg, s0=st[0], s1=st[-1], st=st,
                   paint=Boundary.from_vertices(paint), block=Boundary.from_vertices(block), outer=Boundary.from_vertices(outer))


def limits_v2():
    """wet: road edge = paint start at kerbs; dry: kerb outer edge (adds Dunlop-R outer)."""
    rl = L.road_left.vertices.copy(); rr = L.road_right.vertices.copy()
    dl = L.dry_left.vertices.copy(); dr = L.dry_right.vertices.copy()
    for k, kb in enumerate(CV2['kerbs']):
        tgt_r = rl if kb['side'] == 'left' else rr; tgt_d = dl if kb['side'] == 'left' else dr
        for s, p, o in zip(kb['station'], kb['paint'], kb['outer']):
            i = int(round(s / 0.5))
            if 0 <= i < len(tgt_r):
                tgt_r[i] = p
                if kb['id'] == 'kDunlopR':
                    tgt_d[i] = o
    L2 = copy.copy(L)
    L2.road_left, L2.road_right = Boundary.from_vertices(rl), Boundary.from_vertices(rr)
    L2.dry_left, L2.dry_right = Boundary.from_vertices(dl), Boundary.from_vertices(dr)
    return L2


LV2 = limits_v2()


# ---------- detector v2 ----------
def detector(z, tc0, shift, t_end):
    tr = (z['RollRate_t'] - tc0) / 1000 + shift; r = z['RollRate']
    hp = r - uniform_filter1d(r, 15); rms = np.sqrt(uniform_filter1d(hp * hp, 10))
    m = (tr >= 0) & (tr <= t_end); tr, rms = tr[m], rms[m]
    on = rms > 1.0
    idx = np.flatnonzero(on); ev = []
    if len(idx):
        groups = np.split(idx, np.flatnonzero(np.diff(idx) > 15) + 1)   # merge gaps < 0.3 s
        for gi in groups:
            t0, t1 = tr[gi[0]], tr[gi[-1]]; peak = float(rms[gi].max())
            rip = {}
            for w in ('FL', 'FR', 'RL', 'RR'):
                tw = (z[f'ECU_WS_{w}_t'] - tc0) / 1000 + shift; v = z[f'ECU_WS_{w}']
                h = v - uniform_filter1d(v, 5); mm = (tw >= t0 - 0.1) & (tw <= t1 + 0.1)
                rip[w] = float(np.sqrt(np.mean(h[mm] ** 2))) if mm.any() else 0.0
            lr = (rip['FL'] + rip['RL']) / max(1e-6, rip['FR'] + rip['RR'])
            side = 'left' if lr > 1.25 else ('right' if lr < 0.8 else 'both/unclear')
            ev.append(dict(t0=float(t0), t1=float(t1), peak=peak, side=side, ripLR=round(lr, 2)))
    return ev, tr, rms


# ---------- lap data ----------
class Lap:
    def __init__(self, race, k, z, maps):
        d, t, raw, reg = lap_data(race, k)
        self.race, self.k, self.t, self.raw = race, k, t, raw
        self.W10 = wheel_points(raw, t, geometry=geo)
        self.keep = racing_mask(LV2, raw, t)
        # 50 Hz wheel paths (course heading from spline)
        cx, cz = CubicSpline(t, raw[:, 0]), CubicSpline(t, raw[:, 1])
        self.t50 = np.arange(t[0], t[-1], 0.02)
        xy = np.stack([cx(self.t50), cz(self.t50)], 1)
        hv = np.stack([cx(self.t50, 1), cz(self.t50, 1)], 1); hv /= np.maximum(np.linalg.norm(hv, axis=1), 1e-6)[:, None]
        right = np.stack([-hv[:, 1], hv[:, 0]], 1)
        hl = geo['wheelbaseMeters'] / 2
        hf = (geo['frontTrackMeters'] + geo['frontTyreWidthMeters']) / 2; hr = (geo['rearTrackMeters'] + geo['rearTyreWidthMeters']) / 2
        self.W50 = np.stack([xy + hl * hv - hf * right, xy + hl * hv + hf * right, xy - hl * hv - hr * right, xy - hl * hv + hr * right], 1)
        idx, _ = LV2.lateral(xy); self.st50 = LV2.dist[idx]
        idx10, _ = LV2.lateral(raw); self.st10 = LV2.dist[idx10]
        self.events, self.rt, self.rrms = detector(z, maps[str(k)]['tc0'], SHIFT[race], t[-1])
        # pass windows + labels per inside kerb
        self.passes = {}
        for kb in INSIDE:
            K = KERB[kb]; m = (self.st50 >= K['s0'] - 3) & (self.st50 <= K['s1'] + 3)
            if m.sum() < 5:
                continue
            tw0, tw1 = self.t50[m][0], self.t50[m][-1]
            evs = [e for e in self.events if e['t1'] >= tw0 - 0.2 and e['t0'] <= tw1 + 0.2]
            same = [e for e in evs if e['side'] in (K['side'], 'both/unclear')]
            mm = (self.rt >= tw0) & (self.rt <= tw1)
            peak_any = float(self.rrms[mm].max()) if mm.any() else 0.0
            if same and max(e['peak'] for e in same) >= STRONG:
                cls = 'strong'
            elif peak_any <= SILENT:
                cls = 'silent'
            else:
                cls = 'mild/other-side'
            self.passes[kb] = dict(mask=m, cls=cls, peak=peak_any, events=evs)

    def track_cost(self, d, curb_use):
        m = LV2.margins(self.W10[self.keep] + np.asarray(d), curb_use)
        return float(huber(np.maximum(0.0, -m)).sum())

    def kpi(self, kb, d):
        K = KERB[kb]; p = self.passes[kb]; m = p['mask']
        W = self.W50[m] + np.asarray(d); st = self.st50[m]
        fi, ri = (0, 2) if K['sign'] > 0 else (1, 3)            # inner front / rear
        gb_f = K['block'].signed_gap(W[:, fi], K['sign']); gb_r = K['block'].signed_gap(W[:, ri], K['sign'])
        gp_f = K['paint'].signed_gap(W[:, fi], K['sign']); gp_r = K['paint'].signed_gap(W[:, ri], K['sign'])
        j = int(np.argmin(gb_f))
        on_len = float((gb_f < 0).sum() * 0.02 * np.median(np.linalg.norm(np.diff(W[:, fi], axis=0), axis=1) / 0.02)) if (gb_f < 0).any() else 0.0
        # exit: outer wheels vs opposite road edge (paint/road edge) within 120 m after the kerb end
        oi = (1, 3) if K['sign'] > 0 else (0, 2)
        me = (self.st50 > K['s1'] - 10) & (self.st50 < K['s1'] + 120)
        We = self.W50[me] + np.asarray(d)
        edge = LV2.road_right if K['sign'] > 0 else LV2.road_left
        ge = min(edge.signed_gap(We[:, oi[0]], -K['sign']).min(), edge.signed_gap(We[:, oi[1]], -K['sign']).min()) if me.any() else np.nan
        return dict(gBlockFront=float(gb_f.min()), gBlockRear=float(gb_r.min()), gPaintFront=float(gp_f.min()), gPaintRear=float(gp_r.min()),
                    apexStation=float(st[j]), onBlockLengthM=round(on_len, 1), exitOuterGap=float(ge))


def solve_grid(cost, center=(0, 0), half=4.0, step=0.25):
    xs = np.arange(center[0] - half, center[0] + half + 1e-9, step); zs = np.arange(center[1] - half, center[1] + half + 1e-9, step)
    C = np.array([[cost((x, z)) for z in zs] for x in xs])
    i, j = np.unravel_index(np.argmin(C), C.shape)
    return np.array([xs[i], zs[j]]), C, xs, zs


def feasible_set(lap, curb_use, extra=None):
    """Coarse grid then fine grid (0.05 m) around the minimum; feasible = cost <= min + TAU."""
    c0, _, _, _ = solve_grid(lambda d: lap.track_cost(d, curb_use), half=5.0, step=0.5)
    best, C, xs, zs = solve_grid(lambda d: lap.track_cost(d, curb_use) + (extra(d) if extra else 0.0), center=c0, half=1.6, step=0.05)
    F = C <= C.min() + TAU
    pts = np.array([[xs[i], zs[j]] for i, j in zip(*np.nonzero(F))])
    return best, pts


def auc(scores_strong, scores_silent):
    """P(g_strong < g_silent)."""
    if not len(scores_strong) or not len(scores_silent):
        return None
    s = np.array(scores_strong)[:, None]; n = np.array(scores_silent)[None, :]
    return float(((s < n).sum() + 0.5 * (s == n).sum()) / (s.size * n.size))


if __name__ == '__main__':
    out = {}
    for race, n, surface in (('fuji_aim_2020_07_30', 6, 'dry'), ('fuji_aim_01', 4, 'wet')):
        curb_use = 1.0 if surface == 'dry' else 0.0
        z = dict(np.load(f'{SP}/xrk_{race}.npz')); maps = json.loads(str(z['maps']))
        laps = [Lap(race, k, z, maps) for k in range(1, n + 1)]
        reg = json.load(open(f'{ROOT}/public/data/races/{race}/gps_registration.json', encoding='utf-8'))
        shipped = {l['lap']: np.array(l['offsetMeters']) for l in reg['laps']}
        # session-only (one translation)
        sess, _, _, _ = solve_grid(lambda d: sum(l.track_cost(d, curb_use) for l in laps), half=5.0, step=0.25)
        variants = {'raw': {l.k: np.zeros(2) for l in laps}, 'shipped': shipped, 'sessionOnlyV2': {l.k: sess for l in laps}}
        fsets = {}
        for l in laps:
            best, pts = feasible_set(l, curb_use)
            fsets[l.k] = pts
        variants['v2Centroid'] = {k: pts.mean(axis=0) for k, pts in fsets.items()}
        res = {'session': sess.round(3).tolist(), 'feasibleSetExtent': {k: (pts.max(0) - pts.min(0)).round(2).tolist() for k, pts in fsets.items()}}
        # evaluation per variant
        table = {}
        for name, offs in variants.items():
            S, N, contra = [], [], []
            for l in laps:
                for kb, p in l.passes.items():
                    kp = l.kpi(kb, offs[l.k]); gmin = min(kp['gBlockFront'], kp['gBlockRear'])
                    if p['cls'] == 'strong':
                        S.append(gmin)
                        if gmin > ON_TOL: contra.append((l.k, kb, 'strong-but-off', round(gmin, 2)))
                    elif p['cls'] == 'silent':
                        N.append(gmin)
                        if gmin < -0.4 - ON_TOL: contra.append((l.k, kb, 'silent-but-deep', round(gmin, 2)))
            table[name] = dict(strong=len(S), silent=len(N), auc=auc(S, N), contradictions=contra,
                               strongMedian=round(float(np.median(S)), 2) if S else None)
            print(race, f'{name:14s}', 'AUC', None if table[name]['auc'] is None else round(table[name]['auc'], 3), 'strong', len(S), 'silent', len(N),
                  'median g(strong)', table[name]['strongMedian'], 'contradictions', contra, flush=True)
        # KPI intervals over feasible set (v2) + label-constrained (LOKO)
        kpis = []
        for l in laps:
            pts = fsets[l.k]
            for kb, p in l.passes.items():
                vals = [l.kpi(kb, d) for d in pts[:: max(1, len(pts) // 60)]]
                gF = [v['gBlockFront'] for v in vals]; gP = [v['gPaintFront'] for v in vals]; aps = [v['apexStation'] for v in vals]
                # LOKO label-constrained subset: keep feasible d consistent with labels at OTHER kerbs
                keepd = []
                for d in pts[:: max(1, len(pts) // 60)]:
                    okd = True
                    for kb2, p2 in l.passes.items():
                        if kb2 == kb or p2['cls'] not in ('strong', 'silent'):
                            continue
                        kp2 = l.kpi(kb2, d); gm = min(kp2['gBlockFront'], kp2['gBlockRear'])
                        if p2['cls'] == 'strong' and gm > ON_TOL: okd = False; break
                        if p2['cls'] == 'silent' and gm < -0.4 - ON_TOL: okd = False; break
                    if okd: keepd.append(d)
                gL = [l.kpi(kb, d)['gBlockFront'] for d in keepd] if keepd else []
                c = l.kpi(kb, variants['v2Centroid'][l.k]); s_ = l.kpi(kb, variants['sessionOnlyV2'][l.k])
                kpis.append(dict(lap=l.k, kerb=kb, name=NAMES[kb], label=p['cls'], peak=round(p['peak'], 2),
                                 events=[(round(e['t0'], 2), round(e['peak'], 1), e['side']) for e in p['events']],
                                 gBlockFront=[round(min(gF), 2), round(max(gF), 2)], gPaintFront=[round(min(gP), 2), round(max(gP), 2)],
                                 gBlockFrontLabelConstrained=[round(min(gL), 2), round(max(gL), 2)] if gL else None,
                                 gBlockRearCentroid=round(c['gBlockRear'], 2), apexStation=[round(min(aps), 1), round(max(aps), 1)],
                                 onBlockLengthM=c['onBlockLengthM'], exitOuterGap=round(c['exitOuterGap'], 2),
                                 gBlockFrontSessionOnly=round(s_['gBlockFront'], 2)))
        res['variants'] = table; res['kpi'] = kpis
        res['events'] = {l.k: l.events for l in laps}
        out[race] = res
    json.dump(out, open(f'{IT2}/eval_v2.json', 'w'), indent=1, default=float)
    print('saved')
