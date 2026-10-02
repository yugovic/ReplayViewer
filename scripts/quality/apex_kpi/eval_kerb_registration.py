"""Held-out evaluation of the opt-in kerb-contact registration candidate (BACKLOG GPS-REG-15),
and of the course fix alone on the SHIPPED registration (task D, 2026-10-02 improvement round).

Everything is scored with the published KPI code (build_apex_kpi.kpi: inner front/rear tyre outer
edge vs the kerb BLOCK start of kerb_zones.json, NaN beyond line ends, pass window from the evaluated
offset) and the published labels (public/data/races/<race>/kerb_contacts.json, strong/silent only):

  contradiction  strong label with g_min > +tol, or silent label with g_min < -0.40 - tol (tol 0.15 m)
  AUC            P(g_strong < g_silent) over all strong x silent pairs
  withinKerbAUC  same, only pairs at the same kerb (different laps)

Fits use pipeline/register_gps_kerb_contact.py (the same code that writes the candidate):
  raw                       d = 0
  shipped                   gps_registration.json (track-limit fit, CG road edges)
  trackOnlySession          one translation, track term with the new zones, no labels
  trackOnlySessionPlusLap   + lap deviations (sigma 0.25 m), no labels  (= candidate without labels)
  candidate LOKO            held-out kerb: its labels removed for ALL laps, refit, score that kerb
  candidate LOLO            held-out lap: its labels removed, session refit on the other laps' labels,
                            held-out lap deviation from its track term + prior only, score that lap
Scored twice: all labels, and without the labels of the flagged GPS anomaly (7/30 L2 3250-3900 m).

Also: cluster bootstrap by kerb (candidate LOKO minus shipped / minus track-only), anomaly treatment
(exclude / down-weight / include), sensitivity to label weight, thresholds, loss, sigma_lap and the
tolerance, a 7/29 refusal record, accy-vs-GPS lag per sector (anomaly evidence from the shipped lap
files), and the course comparison CG kerb lines -> course v2 -> kerb_zones v3 on the shipped offsets.

Needs APEX_WORK only for the legacy course_v2_kerbs.json (course comparison); everything else is repo
data. Usage:
  python scripts/quality/apex_kpi/eval_kerb_registration.py [--out DIR] [--quick] [--workers N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / 'pipeline'))
sys.path.insert(0, str(HERE))
import register_gps_kerb_contact as R  # noqa: E402
import build_apex_kpi as K  # noqa: E402

RACE = 'fuji_aim_2020_07_30'
WET_RACE = 'fuji_aim_01'
TRACK = 'fuji'
VEHICLE = ROOT / 'public/data/vehicles/mazda2-dj.json'
DEFAULT_OUT = ROOT / 'artifacts/improvement-eval-2026-10-02/after/registration-candidate'
TOL = R.CONTRADICTION_TOL
SILENT_DEEP = -0.40

_CACHE = {}
# offsets where the base-sign artifact is probed (v3 report track-only optima and label fit, a grid point)
PROBES = {'v3ReportTrackOnlyA': (0.41, -2.87), 'v3ReportTrackOnlyB': (0.49, -3.47), 'v3ReportLabelFit': (0.515, -2.185),
          'shippedSession': (-0.221, -2.68), 'east+0.4_south-2.9': (0.4, -2.9)}


# ---------------------------------------------------------------- fitting (worker side)
def setup_for(pkey: tuple) -> dict:
    if pkey not in _CACHE:
        _CACHE[pkey] = R.setup(TRACK, RACE, 'dry', VEHICLE, params=R.Params(*pkey))
    return _CACHE[pkey]


def problem_for(pkey: tuple):
    return setup_for(pkey)['problem']


def base_sign_track_fit(pkey: tuple, x0) -> dict:
    """Track-only session fit with Boundary.signed_gap (base sign) on the SAME zoned limits, to measure
    what the transverse-step sign artifact does to the track-only optimum."""
    from scipy.optimize import minimize
    S = setup_for(pkey)
    L, P, cu = S['limits'], S['problem'], S['curb_use']
    Ws = [lap.w10[lap.keep] for lap in P.laps.values()]
    f = lambda d: sum(float(R.base.huber(np.maximum(0.0, -L.margins(W + np.asarray(d), cu))).sum()) for W in Ws)
    best = None
    for a in (np.asarray(x0, float), np.asarray(x0, float) + (0.4, 0.0), np.asarray(x0, float) + (0.0, -0.4)):
        r = minimize(f, a, method='Nelder-Mead', options={'xatol': 1e-3, 'fatol': 1e-6, 'maxiter': 800})
        if best is None or r.fun < best.fun:
            best = r
    spur = []
    for n, lap in P.laps.items():
        Q = lap.w10[lap.keep].reshape(-1, 2) + best.x
        for bnd, nin, sg in ((L.dry_left, L.normal, 1), (L.dry_right, -L.normal, -1)):
            gb, gr = bnd.signed_gap(Q, sg), R.robust_signed_gap(bnd, nin, Q)
            bad = (gb < -0.3) & (gr > 0)
            if bad.any():
                idx, _ = L.lateral(Q[bad])
                spur.append({'lap': n, 'samples': int(bad.sum()), 'worstBaseGapM': round(float(gb[bad].min()), 2),
                             'stations': sorted({float(v) for v in np.round(L.dist[idx], 0)})[:8]})
    probes = {}
    for name, d in PROBES.items():
        n_bad, worst = 0, 0.0
        for lap in P.laps.values():
            Q = lap.w10[lap.keep].reshape(-1, 2) + np.asarray(d)
            for bnd, nin, sg in ((L.dry_left, L.normal, 1), (L.dry_right, -L.normal, -1)):
                gb, gr = bnd.signed_gap(Q, sg), R.robust_signed_gap(bnd, nin, Q)
                bad = (gb < -0.3) & (gr > 0)
                n_bad += int(bad.sum())
                worst = min(worst, float(gb[bad].min()) if bad.any() else 0.0)
        probes[name] = {'offset': list(d), 'spuriousSamples': n_bad, 'worstBaseGapM': round(worst, 2),
                        'baseCost': round(f(d), 2),
                        'robustCost': round(sum(lap.track_cost(d) for lap in P.laps.values()), 2)}
    return {'session': np.round(best.x, 3).tolist(), 'spuriousSamplesAtOptimum': spur, 'probes': probes}


def run_fit(task: dict) -> dict:
    """task: pkey, mode ('full'|'loko'|'lolo'), holdout, x0. Returns session + lap offsets."""
    P = problem_for(task['pkey'])
    mode, h, x0 = task['mode'], task.get('holdout'), np.asarray(task['x0'], float)
    use_labels = task.get('labels', True)
    if mode == 'baseSign':
        return dict(task, **base_sign_track_fit(task['pkey'], x0))
    if mode == 'full':
        s = P.fit_session(x0, None, use_labels, spread=task.get('spread', 0.5))
        laps = {n: P.fit_lap(n, s, None, use_labels) for n in P.laps}
    elif mode == 'loko':
        ex = lambda lab, k=h: lab['kerb'] == k
        s = P.fit_session(x0, ex, True, spread=0.3)
        laps = {n: P.fit_lap(n, s, ex, True) for n in P.laps}
    elif mode == 'lolo':
        ex = lambda lab, n0=h: lab['lap'] == n0
        s = P.fit_session(x0, ex, True, spread=0.3)
        laps = {n: P.fit_lap(n, s, ex, True) if n != h else P.fit_lap(n, s, None, False) for n in P.laps}
    else:
        raise ValueError(mode)
    return dict(task, session=s.tolist(), laps={str(n): d.tolist() for n, d in laps.items()})


# ---------------------------------------------------------------- scoring (published KPI code)
class Shifted:
    """build_apex_kpi.LapPaths translated by d (stations re-projected at the evaluated offset)."""

    def __init__(self, lp0: K.LapPaths, d, limits):
        d = np.asarray(d, float)
        self.W50 = lp0.W50 + d
        idx, _ = limits.lateral(lp0.W50.mean(1) + d)
        self.st50 = limits.dist[idx]
        self.t50, self.lap_start_offset = lp0.t50, lp0.lap_start_offset

    pass_mask = K.LapPaths.pass_mask


class Scorer:
    def __init__(self, zones: dict, limits, contacts: dict, anomalies: list):
        origin, geo = K.context()
        self.zones, self.limits = zones, limits
        self.lp0 = {n: K.LapPaths(RACE, n, limits, geo, origin, (0.0, 0.0)) for n in K.race_laps(RACE)}
        self.labels = []
        for lap, rows in contacts['laps'].items():
            for r in rows:
                if r['class'] in ('strong', 'silent'):
                    z = zones.get(r['kerb'])
                    anom = z is not None and R.anomaly_for(int(lap), z, anomalies) is not None
                    self.labels.append(dict(lap=int(lap), kerb=r['kerb'], cls=r['class'], anomaly=anom))

    def g(self, lap: int, kerb: str, d):
        z = self.zones.get(kerb)
        if z is None:
            return None
        k = K.kpi(Shifted(self.lp0[lap], d, self.limits), z, self.limits)
        if k is None:
            return None
        gs = [v for v in (k['gBlockFront'], k['gBlockRear']) if v is not None]
        return min(gs) if gs else None

    def rows(self, offsets: dict, only=None) -> list:
        """offsets: lap -> d. only: predicate on label (e.g. held-out kerb)."""
        out = []
        for lab in self.labels:
            if only and not only(lab):
                continue
            g = self.g(lab['lap'], lab['kerb'], offsets[lab['lap']])
            out.append(dict(lab, g=None if g is None else round(float(g), 3)))
        return out


def auc(S, N):
    if not len(S) or not len(N):
        return None
    s, n = np.asarray(S)[:, None], np.asarray(N)[None, :]
    return float(((s < n).sum() + 0.5 * (s == n).sum()) / (s.size * n.size))


def metrics(rows: list, tol: float = TOL, drop_anomaly: bool = False) -> dict:
    rs = [r for r in rows if r['g'] is not None and not (drop_anomaly and r['anomaly'])]
    S = [r['g'] for r in rs if r['cls'] == 'strong']
    N = [r['g'] for r in rs if r['cls'] == 'silent']
    contra = [[r['lap'], r['kerb'], r['cls'], r['g']] for r in rs
              if (r['cls'] == 'strong' and r['g'] > tol) or (r['cls'] == 'silent' and r['g'] < SILENT_DEEP - tol)]
    pairs = [(a['g'], b['g']) for a in rs if a['cls'] == 'strong' for b in rs
             if b['cls'] == 'silent' and b['kerb'] == a['kerb']]
    wk = None if not pairs else float(np.mean([1.0 if a < b else 0.5 if a == b else 0.0 for a, b in pairs]))
    a = auc(S, N)
    return dict(n=len(rs), strong=len(S), silent=len(N), contradictionCount=len(contra), contradictions=contra,
                auc=None if a is None else round(a, 3), withinKerbAuc=None if wk is None else round(wk, 3),
                withinKerbPairs=len(pairs), unmeasurable=sum(1 for r in rows if r['g'] is None),
                strongMedianG=round(float(np.median(S)), 3) if S else None,
                silentMedianG=round(float(np.median(N)), 3) if N else None)


def both(rows: list, tol: float = TOL) -> dict:
    return {'allLabels': metrics(rows, tol), 'withoutAnomalyLabels': metrics(rows, tol, True)}


def bootstrap(rows_a: list, rows_b: list, B: int = 10000, seed: int = 0, drop_anomaly: bool = False) -> dict:
    """Cluster bootstrap by kerb of (a - b): contradiction count and AUC. rows aligned by (lap, kerb)."""
    def key(r):
        return (r['lap'], r['kerb'])
    A = {key(r): r for r in rows_a if r['g'] is not None and not (drop_anomaly and r['anomaly'])}
    Bm = {key(r): r for r in rows_b if r['g'] is not None and not (drop_anomaly and r['anomaly'])}
    keys = sorted(set(A) & set(Bm))
    kerbs = sorted({k for _, k in keys})
    by = {k: [x for x in keys if x[1] == k] for k in kerbs}

    def stat(sel, M):
        S = [M[x]['g'] for x in sel if M[x]['cls'] == 'strong']
        N = [M[x]['g'] for x in sel if M[x]['cls'] == 'silent']
        c = sum(1 for x in sel if (M[x]['cls'] == 'strong' and M[x]['g'] > TOL) or
                (M[x]['cls'] == 'silent' and M[x]['g'] < SILENT_DEEP - TOL))
        return c, auc(S, N)

    rng = np.random.default_rng(seed)
    dc, da = [], []
    for _ in range(B):
        pick = rng.choice(len(kerbs), len(kerbs), replace=True)
        sel = [x for i in pick for x in by[kerbs[i]]]
        ca, aa = stat(sel, A)
        cb, ab = stat(sel, Bm)
        dc.append(ca - cb)
        if aa is not None and ab is not None:
            da.append(aa - ab)
    c0a, a0a = stat(keys, A)
    c0b, a0b = stat(keys, Bm)
    dc, da = np.asarray(dc), np.asarray(da)
    return dict(clusters=len(kerbs), labels=len(keys), B=B,
                contradictionDiff=c0a - c0b, contradictionDiffCI95=[float(np.percentile(dc, 2.5)), float(np.percentile(dc, 97.5))],
                pContradictionDiffGE0=round(float((dc >= 0).mean()), 4),
                aucDiff=round(a0a - a0b, 4), aucDiffCI95=[round(float(np.percentile(da, 2.5)), 4), round(float(np.percentile(da, 97.5)), 4)],
                pAucDiffLE0=round(float((da <= 0).mean()), 4))


# ---------------------------------------------------------------- anomaly evidence
def _best_lag(t, ref, sig_t, sig, mask, span=0.6, step=0.02):
    tt = t[mask]
    best = (None, -2.0)
    for s_ in np.arange(-span, span + step / 2, step):
        y = np.interp(tt + s_, sig_t, sig)
        c = np.corrcoef(ref[mask], y)[0, 1]
        if np.isfinite(c) and c > best[1]:
            best = (round(float(s_), 2), round(float(c), 3))
    return best


def imu_gps_lag(race: str, contacts: dict, sector=(3250.0, 3900.0), flag_s: float = 0.15) -> dict:
    """Lag of the logger channels (accy, steer; re-timed onto GPS t by GPS-REG-08) against the GPS lateral
    acceleration (speed x course rate of the spline path; a translation does not change it). Per lap for
    the sector vs the rest, and per inside-kerb pass (window t0-2 s .. t1+2 s of kerb_contacts.json) with
    the deviation from the median of the same kerb over the laps. Evidence for GPS anomaly flags only;
    not used to flag anything automatically."""
    origin, _ = K.context()
    lim = R.base.load_limits(K.TRACK_DIR, 0.0)
    laps_out, passes = {}, []
    for lap in K.race_laps(race):
        d = json.loads(K.lap_path(race, lap).read_text(encoding='utf-8'))
        t = np.asarray(d['t'], float)
        xy = R.base.to_local(d['lat'], d['lng'], origin)
        cx, cz = CubicSpline(t, xy[:, 0]), CubicSpline(t, xy[:, 1])
        vx, vz, ax_, az_ = cx(t, 1), cz(t, 1), cx(t, 2), cz(t, 2)
        alat = (vx * az_ - vz * ax_) / np.maximum(np.hypot(vx, vz), 1e-3) / 9.80665
        chans = {}
        for ch in ('accy', 'steer'):
            v = np.asarray(d[ch], float)
            chans[ch] = (np.sign(np.corrcoef(alat, v)[0, 1]) or 1.0) * v
        idx, _ = lim.lateral(xy)
        st = lim.dist[idx]
        in_sec = (st >= sector[0]) & (st <= sector[1])
        e = {}
        for ch, v in chans.items():
            ls, rs = _best_lag(t, alat, t, v, in_sec), _best_lag(t, alat, t, v, ~in_sec)
            e[ch] = {'sectorLagS': ls[0], 'sectorR': ls[1], 'restLagS': rs[0], 'restR': rs[1],
                     'sectorMinusRestS': None if ls[0] is None or rs[0] is None else round(ls[0] - rs[0], 2)}
        laps_out[str(lap)] = e
        for r in contacts['laps'].get(str(lap), []):
            m = (t >= r['t0'] - 2.0) & (t <= r['t1'] + 2.0)
            if m.sum() < 30:
                continue
            row = {'lap': lap, 'kerb': r['kerb'], 'class': r['class']}
            for ch, v in chans.items():
                lag, c = _best_lag(t, alat, t, v, m)
                row[f'{ch}LagS'], row[f'{ch}R'] = lag, c
            passes.append(row)
    flagged = []
    for row in passes:
        same = [p for p in passes if p['kerb'] == row['kerb'] and p['lap'] != row['lap']]
        for ch in ('accy', 'steer'):
            vals = [p[f'{ch}LagS'] for p in same if p[f'{ch}LagS'] is not None]
            if len(vals) >= 2 and row[f'{ch}LagS'] is not None:
                dev = round(row[f'{ch}LagS'] - float(np.median(vals)), 2)
                row[f'{ch}DevFromOtherLapsS'] = dev
                if abs(dev) > flag_s:
                    flagged.append({'lap': row['lap'], 'kerb': row['kerb'], 'channel': ch, 'devS': dev,
                                    'lagS': row[f'{ch}LagS'], 'r': row[f'{ch}R']})
    return {'definition': ('lag s maximising corr(GPS lateral acceleration(t), channel(t + lag)); positive = the logger '
                           'channel matches the GPS at a later t (GPS leads). Sector %.0f-%.0f m vs the rest; per pass: '
                           'window t0-2 s..t1+2 s, deviation from the median of the other laps at the same kerb, '
                           'listed when |dev| > %.2f s' % (sector[0], sector[1], flag_s)),
            'laps': laps_out, 'passes': passes, 'passesDeviating': flagged}


# ---------------------------------------------------------------- course comparison (task 3)
def cg_zones() -> dict:
    """Old CG kerb lines (geometry.json curbs: inner line = what the CG renders as the kerb start).
    No Dunlop-R kerb. paint = block = CG inner line (the CG has no white-strip line)."""
    g = json.loads((K.TRACK_DIR / 'cg_study/geometry.json').read_text(encoding='utf-8'))
    out = {}
    for i, c in enumerate(g['curbs']):
        rows = np.asarray(c['rows'], float)
        inner = R.base.Boundary.from_vertices(rows[:, 1:3])
        out[f'k{i}'] = dict(id=f'k{i}', side=c['side'], sign=+1 if c['side'] == 'left' else -1,
                            s0=float(rows[0, 0]), s1=float(rows[-1, 0]), station=rows[:, 0], block=inner, paint=inner,
                            outer=R.base.Boundary.from_vertices(rows[:, 3:5]))
    return out


def course_comparison(contacts: dict, anomalies: list, shipped: dict, v3_zones: dict, v3_limits) -> dict:
    raw_limits = R.base.load_limits(K.TRACK_DIR, 0.0)
    out = {'definition': ('SHIPPED offsets (gps_registration.json), same published labels and KPI code; only the '
                          'kerb lines change. cgKerbLines = geometry.json curb inner lines (no Dunlop-R: those passes '
                          'are unmeasurable); courseV2 = course_v2_kerbs.json of the v3 report (Dunlop-R block assumed '
                          'paint + 0.42 m); kerbZonesV3 = public kerb_zones.json (block start detected, Dunlop-R '
                          'detected).')}
    courses = {'cgKerbLines': (cg_zones(), raw_limits)}
    v2 = Path(os.environ.get('APEX_WORK', '')) / 'course_v2_kerbs.json'
    if v2.is_file():
        z2 = K.load_zones(v2, legacy_v2=True)
        courses['courseV2'] = (z2, K.zoned_limits(R.base.load_limits(K.TRACK_DIR, 0.0), z2))
    else:
        out['courseV2'] = 'skipped: set APEX_WORK to the folder holding course_v2_kerbs.json'
    courses['kerbZonesV3'] = (v3_zones, v3_limits)
    rows_by = {}
    for name, (zones, limits) in courses.items():
        sc = Scorer(zones, limits, contacts, anomalies)
        rows = sc.rows(shipped)
        rows_by[name] = rows
        out[name] = {**both(rows),
                     'withoutDunlopR': metrics([r for r in rows if r['kerb'] != 'kDunlopR'])}
    # per-kerb change of g (v3 - CG), same passes
    a = {(r['lap'], r['kerb']): r['g'] for r in rows_by['cgKerbLines']}
    per = {}
    for r in rows_by['kerbZonesV3']:
        g0 = a.get((r['lap'], r['kerb']))
        if g0 is not None and r['g'] is not None:
            per.setdefault(r['kerb'], []).append(r['g'] - g0)
    out['gMinChangeV3MinusCg'] = {k: {'median': round(float(np.median(v)), 3), 'min': round(float(np.min(v)), 3),
                                      'max': round(float(np.max(v)), 3), 'n': len(v)} for k, v in per.items()}
    changed = []
    for r in rows_by['kerbZonesV3']:
        g0 = a.get((r['lap'], r['kerb']))
        def bad(g, cls):
            return None if g is None else ((cls == 'strong' and g > TOL) or (cls == 'silent' and g < SILENT_DEEP - TOL))
        b0, b1 = bad(g0, r['cls']), bad(r['g'], r['cls'])
        if b0 != b1:
            changed.append({'lap': r['lap'], 'kerb': r['kerb'], 'class': r['cls'], 'gCg': g0, 'gV3': r['g'],
                            'contradictionCg': b0, 'contradictionV3': b1})
    out['labelVerdictChangesCgToV3'] = changed
    return out


# ---------------------------------------------------------------- feasible-region sensitivity
def feasibility_sensitivity(S_inc: dict, centre, half: float = 2.0, step: float = R.FEASIBLE_STEP_M) -> dict:
    """Label-feasible region (every label within tol) for strongMaxGap x silentMinGap x tol, per session
    (one translation; with and without the anomaly labels) and per lap, plus the session track-limit
    near-optimal set, all on ONE grid around the candidate session offset. S_inc: setup with
    anomaly_treatment='include' so every strong/silent label has a gap grid."""
    P = S_inc['problem']
    c = np.asarray(centre, float)
    ax = np.arange(-half, half + step / 2, step)
    X, Z = np.meshgrid(ax + c[0], ax + c[1], indexing='ij')
    D = np.stack([X.ravel(), Z.ravel()], 1)
    T = sum(lap.track_cost_grid(D) for lap in P.laps.values())
    track_ok = T <= T.min() + R.FEASIBLE_TAU_PER_LAP * len(P.laps)
    labs = [l for l in P.labels if l['used']]
    G = np.array([P.laps[l['lap']].gap_grid(l['kerb'], D) for l in labs])
    anom = np.array([l['anomaly'] for l in labs])
    out = {'definition': (f'grid {step} m over +-{half} m around the candidate session offset; labelFeasible = every label '
                          'of the set has hinge violation <= tol; trackNearOptimal = session track cost <= grid min + '
                          f'{R.FEASIBLE_TAU_PER_LAP} per lap; widths in m (touchesWindow = lower bound)'),
           'centre': np.round(c, 3).tolist(), 'labels': len(labs), 'anomalyLabels': int(anom.sum()),
           'trackNearOptimal': R.region_stats(D, track_ok, step, half, c), 'combos': {}}
    keep_cells = {}
    for smax in (0.0, -0.1):
        for smin in (-0.4, -0.2, 0.0):
            V = np.zeros_like(G)
            for i, l in enumerate(labs):
                v = (G[i] - smax) if l['class'] == 'strong' else (smin - G[i])
                V[i] = np.where(np.isfinite(v), np.maximum(v, 0.0), 0.0)
            key = f'strongMax{smax:g}_silentMin{smin:g}'
            w_ex, w_in = V[~anom].max(0), V.max(0)
            e = {}
            for tol in (0.0, 0.15):
                e[f'tol{tol:g}'] = {
                    'session': R.region_stats(D, w_ex <= tol, step, half, c),
                    'sessionWithAnomalyLabels': R.region_stats(D, w_in <= tol, step, half, c),
                    'sessionAndTrack': R.region_stats(D, (w_ex <= tol) & track_ok, step, half, c),
                    'laps': {str(n): R.region_stats(D, V[np.array([l['lap'] == n for l in labs]) & ~anom].max(0) <= tol,
                                                    step, half, c) for n in sorted(P.laps)},
                }
                if key == 'strongMax0_silentMin-0.4':
                    keep_cells[f'labelFeasible_tol{tol:g}'] = np.flatnonzero(w_ex <= tol).tolist()
            e['minWorstViolationSessionM'] = round(float(w_ex.min()), 3)
            e['minWorstViolationSessionWithAnomalyM'] = round(float(w_in.min()), 3)
            out['combos'][key] = e
    keep_cells['trackNearOptimal'] = np.flatnonzero(track_ok).tolist()
    out['gridForPlot'] = {'origin': [round(float(c[0] - half), 3), round(float(c[1] - half), 3)], 'step': step,
                          'n': len(ax), 'index': 'i_east * n + i_south', 'cells': keep_cells}
    return out


# ---------------------------------------------------------------- driver
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', type=Path, default=DEFAULT_OUT)
    ap.add_argument('--workers', type=int, default=min(14, os.cpu_count() or 4))
    ap.add_argument('--quick', action='store_true', help='skip the sensitivity grid')
    ap.add_argument('--bootstrap', type=int, default=10000)
    args = ap.parse_args()
    t_start = time.time()
    args.out.mkdir(parents=True, exist_ok=True)

    base_params = R.Params()
    S = R.setup(TRACK, RACE, 'dry', VEHICLE, params=base_params)
    P0 = S['problem']
    anomalies = S['anomalies']
    contacts = S['contacts']
    zones_v3 = K.load_zones(S['zones_path'])
    limits_v3 = S['limits']
    scorer = Scorer(zones_v3, limits_v3, contacts, anomalies)
    shipped = {n: np.asarray(d, float) for n, d in S['ref_offsets'].items()}
    laps = sorted(P0.laps)
    lab_kerbs = sorted({l['kerb'] for l in P0.labels if l['used']})
    pkey0 = tuple(base_params.__dict__.values())
    x0 = S['ref_session']

    def fields(p: R.Params):
        return tuple(p.__dict__.values())

    # full fits: candidate (in-sample, grid start) and track-only comparators
    ax = np.arange(-3.0, 3.01, 0.5)
    grid = [x0 + np.array([a, b]) for a in ax for b in ax]
    s_cand = P0.fit_session(x0, grid=grid)
    s_track = P0.fit_session(x0, use_labels=False, grid=grid)
    print('session candidate', np.round(s_cand, 3), 'track-only', np.round(s_track, 3), f'{time.time() - t_start:.0f}s', flush=True)

    tasks = []
    variants = {
        'default': base_params,
        'anomalyDownweight': R.Params(anomaly_treatment='downweight'),
        'anomalyInclude': R.Params(anomaly_treatment='include'),
    }
    if not args.quick:
        for w in (12.5, 25.0, 100.0, 200.0, 400.0):
            variants[f'labelWeight{w:g}'] = R.Params(label_weight=w)
        for smax in (0.0, -0.1):
            for smin in (-0.4, -0.2, 0.0):
                if (smax, smin) != (0.0, -0.4):
                    variants[f'strongMax{smax:g}_silentMin{smin:g}'] = R.Params(strong_max_gap=smax, silent_min_gap=smin)
        for loss in ('soft_l1', 'squared'):
            variants[f'loss_{loss}'] = R.Params(loss=loss)
        for sig in (0.1, 0.5):
            variants[f'sigmaLap{sig:g}'] = R.Params(sigma_lap=sig)
        # what the robust loss buys: anomaly labels left IN the fit, three losses
        for loss in ('soft_l1', 'squared'):
            variants[f'anomalyInclude_loss_{loss}'] = R.Params(loss=loss, anomaly_treatment='include')
    for name, p in variants.items():
        pk = fields(p)
        tasks.append(dict(variant=name, pkey=pk, mode='full', x0=s_cand.tolist()))
        for k in lab_kerbs:
            tasks.append(dict(variant=name, pkey=pk, mode='loko', holdout=k, x0=s_cand.tolist()))
        if name in ('default', 'anomalyDownweight', 'anomalyInclude'):
            for n in laps:
                tasks.append(dict(variant=name, pkey=pk, mode='lolo', holdout=n, x0=s_cand.tolist()))
    tasks.append(dict(variant='trackOnly', pkey=pkey0, mode='full', labels=False, x0=s_track.tolist()))
    tasks.append(dict(variant='trackOnlyBaseSign', pkey=pkey0, mode='baseSign', x0=s_track.tolist()))
    print(f'{len(tasks)} fits on {args.workers} workers', flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(run_fit, tasks, chunksize=1))
    print(f'fits done {time.time() - t_start:.0f}s', flush=True)

    def offs(res):
        return {int(n): np.asarray(d) for n, d in res['laps'].items()}

    full = {r['variant']: r for r in results if r['mode'] == 'full'}
    out = {
        'task': 'TASK D (BACKLOG GPS-REG-15): opt-in kerb-contact registration candidate for 7/30 and its held-out evaluation',
        'raceId': RACE,
        'inputs': {
            'kerbZones': {'source': R.rel(S['zones_path']), 'sha256': R.sha256(S['zones_path'])},
            'kerbContacts': {'source': R.rel(S['contacts_path']), 'sha256': R.sha256(S['contacts_path'])},
            'shippedRegistration': {'source': R.rel(S['ref_registration']), 'sha256': R.sha256(S['ref_registration'])},
            'laps': {str(n): {'source': R.rel(P0.laps[n].path), 'sha256': P0.laps[n].sha} for n in laps},
        },
        'definitions': {
            'gMin': 'min(gBlockFront, gBlockRear) of build_apex_kpi.kpi at the evaluated offsets (m; negative = on the blocks)',
            'contradiction': f'strong with gMin > +{TOL} m, or silent with gMin < {SILENT_DEEP - TOL:.2f} m',
            'auc': 'P(gMin strong < gMin silent) over all strong x silent pairs',
            'withinKerbAuc': 'same, only strong/silent pairs at the same kerb (different laps)',
            'anomalyLabels': [f'L{l["lap"]} {l["kerb"]} {l["cls"]}' for l in scorer.labels if l['anomaly']],
            'trackTerm': ('candidate and track-only comparators use the zoned limits with the robust sign '
                          '(pipeline/register_gps_kerb_contact.py robust_signed_gap); the shipped fit is used as published'),
        },
        'params': {k: v for k, v in base_params.__dict__.items()},
    }

    # comparators (all labels are held out for them: no label enters these fits)
    comp = {
        'raw': {n: np.zeros(2) for n in laps},
        'shipped': shipped,
        'trackOnlySession': {n: s_track for n in laps},
        'trackOnlySessionPlusLap': offs(full['trackOnly']),
    }
    comp_rows = {k: scorer.rows(v) for k, v in comp.items()}
    out['offsets'] = {k: {str(n): [round(float(x), 3) for x in v[n]] for n in laps} for k, v in comp.items()}
    out['offsets']['candidateInSample'] = {str(n): [round(float(x), 3) for x in d] for n, d in offs(full['default']).items()}
    out['sessionOffsets'] = {'shipped': [round(float(v), 3) for v in x0], 'trackOnlySession': np.round(s_track, 3).tolist(),
                             'trackOnlySessionPlusLapSession': np.round(full['trackOnly']['session'], 3).tolist(),
                             'candidate': np.round(full['default']['session'], 3).tolist()}

    cand_offs = offs(full['default'])
    bl = {'definition': 'per lap, measured like gps_registration.json raw/registered (geometry.json limits, > 0.05 m beyond)'}
    for k, v in {**comp, 'candidate': cand_offs}.items():
        e = {str(n): R.beyond_limit_summary(P0.laps[n], S['raw_limits'], v[n], S['curb_use']) for n in laps}
        e['total'] = {'secondsBeyondLimit': round(sum(x['secondsBeyondLimit'] for x in e.values()), 2),
                      'maxBeyondLimitMeters': max(x['maxBeyondLimitMeters'] for x in e.values())}
        bl[k] = e
    out['beyondLimit'] = bl
    bs = next(r for r in results if r['mode'] == 'baseSign')
    out['trackOnlySignArtifact'] = {
        'note': ('track-only session fit on the zoned limits with Boundary.signed_gap (base sign) vs the robust sign; '
                 'spurious = base gap < -0.3 m where the robust gap is inside the road'),
        'baseSignSession': bs['session'], 'robustSignSession': np.round(s_track, 3).tolist(),
        'spuriousSamplesAtBaseOptimum': bs['spuriousSamplesAtOptimum'],
        'probes': bs['probes'],
        'baseSignHeldOut': both(scorer.rows({n: np.asarray(bs['session']) for n in laps}))}

    def loko_rows(variant, session_only=False):
        rows = []
        for r in results:
            if r['variant'] != variant or r['mode'] != 'loko':
                continue
            o = {n: np.asarray(r['session']) for n in laps} if session_only else offs(r)
            rows += scorer.rows(o, only=lambda lab, k=r['holdout']: lab['kerb'] == k)
        return rows

    def lolo_rows(variant, session_only=False):
        rows = []
        for r in results:
            if r['variant'] != variant or r['mode'] != 'lolo':
                continue
            o = {n: np.asarray(r['session']) for n in laps} if session_only else offs(r)
            rows += scorer.rows(o, only=lambda lab, n0=r['holdout']: lab['lap'] == n0)
        return rows

    cand_loko = loko_rows('default')
    cand_lolo = lolo_rows('default')
    table = {k: both(v) for k, v in comp_rows.items()}
    table['candidateInSample (not held out)'] = both(scorer.rows(offs(full['default'])))
    table['candidateLOKO'] = both(cand_loko)
    table['candidateLOKO_sessionOnly'] = both(loko_rows('default', True))
    table['candidateLOLO'] = both(cand_lolo)
    table['candidateLOLO_sessionOnly'] = both(lolo_rows('default', True))
    out['heldOut'] = table
    out['heldOutRows'] = {'candidateLOKO': cand_loko, 'candidateLOLO': cand_lolo, **comp_rows}
    out['lokoSessionOffsets'] = {r['holdout']: np.round(r['session'], 3).tolist()
                                 for r in results if r['variant'] == 'default' and r['mode'] == 'loko'}
    out['loloSessionOffsets'] = {str(r['holdout']): np.round(r['session'], 3).tolist()
                                 for r in results if r['variant'] == 'default' and r['mode'] == 'lolo'}
    ls = np.array(list(out['lokoSessionOffsets'].values()))
    out['lokoSessionSpread'] = {'east': [round(float(ls[:, 0].min()), 3), round(float(ls[:, 0].max()), 3)],
                                'south': [round(float(ls[:, 1].min()), 3), round(float(ls[:, 1].max()), 3)]}
    print('held-out', {k: (v['allLabels']['contradictionCount'], v['allLabels']['auc']) for k, v in table.items()}, flush=True)

    out['bootstrapByKerb'] = {
        'candidateLOKO_minus_shipped': {'allLabels': bootstrap(cand_loko, comp_rows['shipped'], args.bootstrap),
                                        'withoutAnomalyLabels': bootstrap(cand_loko, comp_rows['shipped'], args.bootstrap, drop_anomaly=True)},
        'candidateLOKO_minus_trackOnlySession': {'allLabels': bootstrap(cand_loko, comp_rows['trackOnlySession'], args.bootstrap),
                                                 'withoutAnomalyLabels': bootstrap(cand_loko, comp_rows['trackOnlySession'], args.bootstrap, drop_anomaly=True)},
        'candidateLOKO_minus_trackOnlySessionPlusLap': {'allLabels': bootstrap(cand_loko, comp_rows['trackOnlySessionPlusLap'], args.bootstrap),
                                                        'withoutAnomalyLabels': bootstrap(cand_loko, comp_rows['trackOnlySessionPlusLap'], args.bootstrap, drop_anomaly=True)},
        'candidateLOKO_minus_raw': {'allLabels': bootstrap(cand_loko, comp_rows['raw'], args.bootstrap)},
    }

    # anomaly treatment: exclude (default) / down-weight x0.25 / include
    out['anomalyTreatment'] = {}
    for name in ('default', 'anomalyDownweight', 'anomalyInclude'):
        fr = full[name]
        out['anomalyTreatment'][{'default': 'exclude'}.get(name, name)] = {
            'session': np.round(fr['session'], 3).tolist(),
            'laps': {n: np.round(d, 3).tolist() for n, d in fr['laps'].items()},
            'LOKO': both(loko_rows(name)), 'LOLO': both(lolo_rows(name)),
        }

    # sensitivity (LOKO, session + lap) and tolerance
    sens = {}
    dflt = offs(full['default'])
    for name in variants:
        if name in ('anomalyDownweight', 'anomalyInclude'):
            continue
        fr = full[name]
        o = offs(fr)
        sens[name] = {'params': {k: v for k, v in R.Params(*fields(variants[name])).__dict__.items()},
                      'session': np.round(fr['session'], 3).tolist(),
                      'laps': {n: np.round(d, 3).tolist() for n, d in fr['laps'].items()},
                      'maxLapShiftVsDefaultM': round(float(max(np.hypot(*(o[n] - dflt[n])) for n in laps)), 3),
                      'LOKO': both(loko_rows(name))}
    out['sensitivity'] = sens
    out['toleranceSensitivity'] = {
        f'tol{t:g}': {k: {'all': metrics(v, t)['contradictionCount'], 'withoutAnomaly': metrics(v, t, True)['contradictionCount']}
                      for k, v in {'candidateLOKO': cand_loko, 'candidateLOLO': cand_lolo, **comp_rows}.items()}
        for t in (0.0, 0.10, 0.15, 0.25, 0.7)}

    # 7/29: no candidate
    S29 = R.setup(TRACK, WET_RACE, 'wet', VEHICLE)
    n29, k29 = R.strong_evidence(S29['problem'].labels)
    out['wet0729'] = {
        'candidate': None,
        'strongLabels': n29, 'strongKerbs': k29,
        'silentLabels': sum(1 for l in S29['problem'].labels if l['class'] == 'silent' and l['used']),
        'required': {'strongLabels': R.MIN_STRONG_LABELS, 'strongKerbs': R.MIN_STRONG_KERBS},
        'reason': ('Only strong labels bound the offset from the kerb side; with one strong contact the hinge is one-sided '
                   '(silent labels only push the tyres away from the blocks), the fit stays on the flat track-limit set '
                   'and there is no held-out strong label to validate it (AUC undefined under leave-one-kerb-out). '
                   'pipeline/register_gps_kerb_contact.py refuses the race (exit 3).'),
    }

    # anomaly evidence from the shipped lap files (no XRK needed)
    out['anomalyEvidence'] = imu_gps_lag(RACE, contacts)

    # label-feasible widths: thresholds x tolerance, with/without anomaly labels, per session and lap
    out['feasibleRegionSensitivity'] = feasibility_sensitivity(setup_for(fields(variants['anomalyInclude'])),
                                                               np.asarray(full['default']['session']))

    # course fix alone on the shipped registration (task 3)
    out['courseFixShippedRegistration'] = course_comparison(contacts, anomalies, shipped, zones_v3, limits_v3)

    out['runtimeSeconds'] = round(time.time() - t_start, 1)
    path = args.out / 'evaluation.json'
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float) + '\n', encoding='utf-8')
    print('->', R.rel(path), f'{out["runtimeSeconds"]}s')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
