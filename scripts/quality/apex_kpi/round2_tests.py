"""Round-2 review tests (7/30):
 T1 dead-band sensitivity of the label-informed session translation
 T2 body-frame antenna offset (right, forward) as extra session parameters: identifiable? residual reduction?
 T3 track-only fit after adding all missing red-paint zones (incl. T1-entry left 772-874 m): does it converge to the label estimate?"""
import json, math, copy
import numpy as np
from scipy.optimize import minimize
import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from paths import ROOT, WORK  # noqa: E402
import eval_v2 as E  # noqa: E402
from register_gps_to_track import huber, Boundary  # noqa: E402

race, n, cu = 'fuji_aim_2020_07_30', 6, 1.0
z = dict(np.load(f'{E.SP}/xrk_{race}.npz')); maps = json.loads(str(z['maps']))
laps = [E.Lap(race, k, z, maps) for k in range(1, n + 1)]
LAB = [(l, kb) for l in laps for kb, p in l.passes.items() if p['cls'] in ('strong', 'silent')]
print('labels', len(LAB), 'strong', sum(1 for l, kb in LAB if l.passes[kb]['cls'] == 'strong'))


def body_shift(l, ant, which='50'):
    """antenna (right, forward) offset: vehicle centre = GPS - R(heading)*ant -> shift of wheel points per sample."""
    W0 = l.W50 if which == '50' else l.W10
    if ant is None or (ant[0] == 0 and ant[1] == 0):
        return np.zeros((len(W0), 1, 2))
    if which == '50':
        W = l.W50
    else:
        W = l.W10
    fwd = (W[:, 0] + W[:, 1]) / 2 - (W[:, 2] + W[:, 3]) / 2
    fwd /= np.maximum(np.linalg.norm(fwd, axis=1), 1e-6)[:, None]
    right = W[:, 1] - W[:, 0]; right /= np.maximum(np.linalg.norm(right, axis=1), 1e-6)[:, None]
    return -(ant[0] * right + ant[1] * fwd)[:, None, :]


def gmin(l, kb, d, ant=None):
    K = E.KERB[kb]; ps = l.passes[kb]; m = ps['mask']
    W = l.W50[m] + np.asarray(d) + (body_shift(l, ant)[m] if ant is not None else 0.0)
    fi, ri = (0, 2) if K['sign'] > 0 else (1, 3)
    return min(K['block'].signed_gap(W[:, fi], K['sign']).min(), K['block'].signed_gap(W[:, ri], K['sign']).min())


def track(l, d, limits, ant=None):
    W = l.W10[l.keep] + np.asarray(d) + (body_shift(l, ant, '10')[l.keep] if ant is not None else 0.0)
    m = limits.margins(W, cu)
    return float(huber(np.maximum(0.0, -m)).sum())


def label_cost(d, s_max, sil_min, ant=None):
    c = 0.0
    for l, kb in LAB:
        g = gmin(l, kb, d, ant); cls = l.passes[kb]['cls']
        v = max(0.0, g - s_max) if cls == 'strong' else max(0.0, sil_min - g)
        c += v * v
    return 50 * c


def fit(cost, x0):
    """Single Nelder-Mead from the known neighbourhood (cost is smooth there); initial simplex 0.3 m / 0.3 m."""
    x0 = np.asarray(x0, float)
    simplex = [x0] + [x0 + np.eye(len(x0))[i] * 0.3 for i in range(len(x0))]
    return minimize(cost, x0, method='Nelder-Mead',
                    options=dict(xatol=5e-3, fatol=1e-3, maxiter=400, initial_simplex=np.array(simplex)))


def contradictions(d, ant=None):
    out = []
    for l, kb in LAB:
        g = gmin(l, kb, d, ant); cls = l.passes[kb]['cls']
        if (cls == 'strong' and g > 0.15) or (cls == 'silent' and g < -0.55):
            out.append((l.k, kb, cls, round(g, 2)))
    return out


res = {}
LV = E.LV2
# T1 dead band
t1 = []
for s_max, sil in ():
    r = fit(lambda d: sum(track(l, d, LV) for l in laps) + label_cost(d, s_max, sil), (0.5, -2.2))
    t1.append(dict(strongMax=s_max, silentMin=sil, session=np.round(r.x, 3).tolist(), contradictions=contradictions(r.x)))
    print('T1', s_max, sil, np.round(r.x, 3), len(t1[-1]['contradictions']), flush=True)
res['T1_deadBand'] = t1
# T2 antenna offset as free params (session-level, with labels)
r = fit(lambda p: sum(track(l, p[:2], LV, p[2:]) for l in laps) + label_cost(p[:2], 0.0, -0.4, p[2:]), (0.5, -2.2, 0.0, 0.0))
res['T2_antenna'] = dict(params=np.round(r.x, 3).tolist(), cost=r.fun, contradictions=contradictions(r.x[:2], r.x[2:]))
base_cost = sum(track(l, (0.51, -2.18), LV) for l in laps) + label_cost((0.51, -2.18), 0.0, -0.4)
print('T2 antenna fit (dx,dz,right,fwd)', np.round(r.x, 3), 'cost', round(r.fun, 2), 'vs no-antenna cost', round(base_cost, 2), 'contradictions', res['T2_antenna']['contradictions'], flush=True)
# profile: cost vs antenna right offset
prof = []
for ar in (-0.4, 0.4):
    rr = fit(lambda p: sum(track(l, p[:2], LV, (ar, p[2])) for l in laps) + label_cost(p[:2], 0.0, -0.4, (ar, p[2])), (0.5, -2.2, 0.0))
    prof.append(dict(right=ar, cost=round(rr.fun, 2), session=np.round(rr.x[:2], 3).tolist(), fwd=round(rr.x[2], 2), contradictions=len(contradictions(rr.x[:2], (ar, rr.x[2])))))
    print('T2 profile right', ar, prof[-1], flush=True)
res['T2_profile'] = prof
# T3 add missing red-paint zones as allowed (dry) area: widen dry edges outward by the scanned paint width (approx 1.0 m) at those stations
miss = json.load(open(WORK / 'missing_kerbs.json'))['missingKerbCandidates'] if (WORK / 'missing_kerbs.json').exists() else []
LV3 = copy.copy(LV)
dl, dr = LV.dry_left.vertices.copy(), LV.dry_right.vertices.copy()
for sgm in miss:
    for s in np.arange(sgm['start'], sgm['end'] + 0.01, 0.5):
        i = int(round(s / 0.5))
        if not (0 <= i < len(dl)):
            continue
        nrm = E.L.normal[i]
        if sgm['side'] == 'left':
            dl[i] = dl[i] - 1.0 * nrm
        else:
            dr[i] = dr[i] + 1.0 * nrm
LV3.dry_left, LV3.dry_right = Boundary.from_vertices(dl), Boundary.from_vertices(dr)
r_old = fit(lambda d: sum(track(l, d, LV) for l in laps), (0.4, -2.8))
r_new = fit(lambda d: sum(track(l, d, LV3) for l in laps), (0.4, -2.8))
res['T3_trackOnly'] = dict(v2=np.round(r_old.x, 3).tolist(), v2PlusMissingPaint=np.round(r_new.x, 3).tolist(),
                           labelEstimate=[0.51, -2.18], contradictionsV2PlusMissing=contradictions(r_new.x))
print('T3 track-only v2', np.round(r_old.x, 3), '-> with missing paint zones', np.round(r_new.x, 3), 'label estimate (0.51,-2.18); contradictions', res['T3_trackOnly']['contradictionsV2PlusMissing'], flush=True)
json.dump(res, open(WORK / 'round2_tests.json', 'w'), indent=1, default=float)
