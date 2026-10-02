"""Adversarial review quick-check (Sonnet 5.5 lens). Independent third scorer: different interpolation
(linear position + central-difference heading at the 10 Hz samples, linear heading interpolation to 100 Hz),
different polyline-distance routine (point-to-segment with explicit road-centre sign calibration).
Read-only on repo data."""
import json, math, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[4]
TR = ROOT / 'public/data/tracks/fuji'
VEH = json.loads((ROOT / 'public/data/vehicles/mazda2-dj.json').read_text(encoding='utf-8'))
ORG = json.loads((TR / 'track.json').read_text(encoding='utf-8'))['origin']
GEO = json.loads((TR / 'cg_study/geometry.json').read_text(encoding='utf-8'))
ZON = json.loads((TR / 'cg_study/kerb_zones.json').read_text(encoding='utf-8'))
MPD = 111_320.0


def loc(lat, lng):
    lat, lng = np.asarray(lat), np.asarray(lng)
    return np.stack([(lng - ORG['lng']) * MPD * math.cos(math.radians(ORG['lat'])), -(lat - ORG['lat']) * MPD], 1)


def seg_signed(P, V, side):
    """signed distance to polyline V (nearest-segment), + = road side. NaN when nearest point is an end vertex beyond the end."""
    out = np.full(len(P), np.nan)
    a, b = V[:-1], V[1:]
    ab = b - a
    L2 = (ab ** 2).sum(1)
    for i, p in enumerate(P):
        t = ((p - a) * ab).sum(1) / np.maximum(L2, 1e-12)
        tc = np.clip(t, 0, 1)
        q = a + tc[:, None] * ab
        d = np.hypot(*(p - q).T)
        j = int(np.argmin(d))
        if (j == 0 and t[j] < 0) or (j == len(ab) - 1 and t[j] > 1):
            continue
        r = p - q[j]
        cr = ab[j, 0] * r[1] - ab[j, 1] * r[0]          # >0: right of travel (x east, z south)
        sgn = 1.0 if (cr > 0) == (side == 'left') else -1.0   # left kerb: road side is right of travel
        out[i] = sgn * d[j]
    return out


def lap_path(race, lap):
    d = json.loads((ROOT / f'public/data/races/{race}/osaki_hmr_demio_101_lap_{lap:03d}.json').read_text(encoding='utf-8'))
    t = np.array(d['t'])
    xy = loc(d['lat'], d['lng'])
    h = np.gradient(xy, t, axis=0)
    h /= np.linalg.norm(h, axis=1)[:, None]
    tt = np.arange(t[0], t[-1], 0.01)
    P = np.stack([np.interp(tt, t, xy[:, 0]), np.interp(tt, t, xy[:, 1])], 1)
    H = np.stack([np.interp(tt, t, h[:, 0]), np.interp(tt, t, h[:, 1])], 1)
    H /= np.linalg.norm(H, axis=1)[:, None]
    return tt, P, H


def g_label(tt, P, H, off, V, side, t0, t1):
    m = (tt >= t0) & (tt <= t1)
    p = P[m] + np.asarray(off)
    h = H[m]
    rt = np.stack([-h[:, 1], h[:, 0]], 1)
    s = -1.0 if side == 'left' else 1.0
    wb = VEH['wheelbaseMeters'] / 2
    hf = (VEH['frontTrackMeters'] + VEH['frontTyreWidthMeters']) / 2
    hr = (VEH['rearTrackMeters'] + VEH['rearTyreWidthMeters']) / 2
    # subsample every 3rd sample (30 ms) for speed; the gap is smooth at that scale
    F = (p + wb * h + s * hf * rt)[::3]
    R = (p - wb * h + s * hr * rt)[::3]
    gf, gr = seg_signed(F, V, side), seg_signed(R, V, side)
    vals = [np.nanmin(x) for x in (gf, gr) if np.isfinite(x).any()]
    return min(vals) if vals else None


def lines(kind):
    out = {}
    if kind == 'new':
        for k in ZON['kerbs']:
            out[k['id']] = (k['side'], np.asarray(k['block'], float), k['station'][0], k['station'][-1])
    else:
        for i, c in enumerate(GEO['curbs']):
            r = np.asarray(c['rows'], float)
            out[f'k{i}'] = (c['side'], r[:, 1:3], r[0, 0], r[-1, 0])
    return out


def auc(S, N):
    S, N = np.array(S)[:, None], np.array(N)[None, :]
    return float(((S < N).sum() + .5 * (S == N).sum()) / (S.size * N.size))


def run(race, kind, offs):
    c = json.loads((ROOT / f'public/data/races/{race}/kerb_contacts.json').read_text(encoding='utf-8'))
    L = lines(kind)
    cache = {}
    rows = []
    for lap, rr in c['laps'].items():
        n = int(lap)
        if n not in cache:
            cache[n] = lap_path(race, n)
        tt, P, H = cache[n]
        for r in rr:
            if r['class'] not in ('strong', 'silent'):
                continue
            if r['kerb'] not in L:
                rows.append((n, r['kerb'], r['class'], None)); continue
            side, V, s0, s1 = L[r['kerb']]
            g = g_label(tt, P, H, offs[n], V, side, r['t0'], r['t1'])
            rows.append((n, r['kerb'], r['class'], g))
    return rows


def summarize(rows, tol=0.15):
    ok = [r for r in rows if r[3] is not None]
    S = [r[3] for r in ok if r[2] == 'strong']
    N = [r[3] for r in ok if r[2] == 'silent']
    con = [f'L{r[0]} {r[1]} {r[2]} {r[3]:+.2f}' for r in ok if (r[2] == 'strong' and r[3] > tol) or (r[2] == 'silent' and r[3] < -0.40 - tol)]
    return dict(n=len(rows), measurable=len(ok), strong=len(S), silent=len(N), contra=len(con), list=con, auc=round(auc(S, N), 3))


if __name__ == '__main__':
    out = {}
    for race in ('fuji_aim_2020_07_30', 'fuji_aim_01'):
        reg = json.loads((ROOT / f'public/data/races/{race}/gps_registration.json').read_text(encoding='utf-8'))
        offs = {int(l['lap']): l['offsetMeters'] for l in reg['laps']}
        for kind in ('old', 'new'):
            rows = run(race, kind, offs)
            out[f'{race}|shipped|{kind}'] = dict(summary=summarize(rows), rows=[[*r[:3], None if r[3] is None else round(float(r[3]), 3)] for r in rows])
            print(race, 'shipped', kind, out[f'{race}|shipped|{kind}']['summary'], flush=True)
    race = 'fuji_aim_2020_07_30'
    k = json.loads((ROOT / f'public/data/races/{race}/gps_registration_kerb.json').read_text(encoding='utf-8'))
    offs = {int(l['lap']): l['offsetMeters'] for l in k['laps']}
    rows = run(race, 'new', offs)
    out[f'{race}|candidate_insample|new'] = dict(summary=summarize(rows), rows=[[*r[:3], None if r[3] is None else round(float(r[3]), 3)] for r in rows])
    print(race, 'candidate in-sample', out[f'{race}|candidate_insample|new']['summary'])
    Path(__file__).with_name('review_check.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
