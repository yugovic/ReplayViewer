"""Verifier: own re-implementation of the viewer's drawn car pose and inner-tyre kerb gaps.

Written from the viewer source (src/replay/projection.ts latLngToLocal, visualAlignment.ts
displayedLapPoint, gpsRegistration.ts registrationOffset, interpolation.ts samplePosition /
sampleHermite / getLapMotionArrays POSE_WINDOW = 6) without importing cgpose.py, register_gps_to_track.py
or build_apex_kpi.py:
  position  latLngToLocal(lat, lng, track.origin) + registration offsetMeters (antenna offset 0 in the
            shipped files), C1 cubic Hermite with non-uniform Catmull-Rom tangents on the lap t samples
  heading   per-sample chord atan2(dx, dz) between samples i-6 and i+6 of the displayed points, unwrapped,
            linearly interpolated (the target pose; the drawn pose is the PoseSmoother output which chases
            the target 0.14 s ahead through two 70 ms lags, i.e. ~ the target for slow yaw; not modelled)
  tyres     outer edges: centre +- wheelbase/2 along the heading, +-(track + tyre width)/2 across
            (registration vehicleGeometry: mazda2-dj, 2.57 / 1.495 / 1.48 / 0.185 m)
  gap       signed distance of the inner tyre outer edge to the kerb_zones.json block / paint polyline;
            + = road side, - = on the kerb; NaN where the foot of the perpendicular falls beyond the
            polyline end vertices. Polyline direction = increasing station (= travel direction).
Time argument: lap t of the shipped lap JSON (GPS base, viewer playback clock).
"""
import json
import math
import os
from functools import lru_cache
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('REPLAYVIEWER_ROOT', HERE.parents[2]))
M_PER_DEG = 111_320.0


@lru_cache(maxsize=None)
def track():
    return json.loads((ROOT / 'public/data/tracks/fuji/track.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=None)
def zones():
    d = json.loads((ROOT / 'public/data/tracks/fuji/cg_study/kerb_zones.json').read_text(encoding='utf-8'))
    out = {}
    for kb in d['kerbs']:
        st = np.asarray(kb['station'], float)
        order = np.argsort(st)
        out[kb['id']] = dict(side=kb['side'], station=st[order], paint=np.asarray(kb['paint'], float)[order],
                             block=np.asarray(kb['block'], float)[order])
    return out


@lru_cache(maxsize=None)
def lap(race, n):
    d = json.loads((ROOT / f'public/data/races/{race}/osaki_hmr_demio_101_lap_{n:03d}.json').read_text(encoding='utf-8'))
    reg = json.loads((ROOT / f'public/data/races/{race}/gps_registration.json').read_text(encoding='utf-8'))
    off = [l for l in reg['laps'] if int(l['lap']) == n][0]['offsetMeters']
    ant = reg['method'].get('antennaOffsetMeters') or {'right': 0.0, 'forward': 0.0}
    assert abs(ant['right']) < 1e-9 and abs(ant['forward']) < 1e-9, 'antenna offset not modelled'
    o = track()['origin']
    lat, lng = np.asarray(d['lat'], float), np.asarray(d['lng'], float)
    x = (lng - o['lng']) * (M_PER_DEG * math.cos(math.radians(o['lat']))) + off[0]
    z = -(lat - o['lat']) * M_PER_DEG + off[1]
    t = np.asarray(d['t'], float)
    n_ = len(t)
    hd = np.zeros(n_)
    prev = 0.0
    for i in range(n_):
        s, e = max(0, i - 6), min(n_ - 1, i + 6)
        dx, dz = x[e] - x[s], z[e] - z[s]
        h = prev
        if math.hypot(dx, dz) >= 0.001:
            h = math.atan2(dx, dz)
            while h - prev > math.pi:
                h -= 2 * math.pi
            while h - prev < -math.pi:
                h += 2 * math.pi
        hd[i] = h
        prev = h
    geo = reg['method']['vehicleGeometry']
    return dict(t=t, x=x, z=z, heading=hd, geo=geo, offset=off, delta=float(d['meta']['first_sample_after_lap_start_seconds']))


def _tangent(t, v, i):
    p, q = max(0, i - 1), min(len(v) - 1, i + 1)
    span = t[q] - t[p]
    return (v[q] - v[p]) / span if span > 0 else 0.0


def hermite(t, v, tq):
    tq = np.atleast_1d(np.asarray(tq, float))
    out = np.empty(len(tq))
    for k, q in enumerate(tq):
        q = min(max(q, t[0]), t[-1])
        i = int(np.searchsorted(t, q, side='right') - 1)
        i = min(max(i, 0), len(t) - 2)
        t0, t1 = t[i], t[i + 1]
        span = t1 - t0
        u = (q - t0) / span
        m0 = _tangent(t, v, i) * span
        m1 = _tangent(t, v, i + 1) * span
        out[k] = ((2 * u ** 3 - 3 * u ** 2 + 1) * v[i] + (u ** 3 - 2 * u ** 2 + u) * m0 +
                  (-2 * u ** 3 + 3 * u ** 2) * v[i + 1] + (u ** 3 - u ** 2) * m1)
    return out


def pose(race, n, tq):
    L = lap(race, n)
    x = hermite(L['t'], L['x'], tq)
    z = hermite(L['t'], L['z'], tq)
    h = np.interp(np.atleast_1d(tq), L['t'], L['heading'])
    return x, z, h


def tyres(race, n, tq):
    L = lap(race, n)
    x, z, h = pose(race, n, tq)
    g = L['geo']
    hl = g['wheelbaseMeters'] / 2
    hf = (g['frontTrackMeters'] + g['frontTyreWidthMeters']) / 2
    hr = (g['rearTrackMeters'] + g['rearTyreWidthMeters']) / 2
    c = np.stack([x, z], 1)
    f = np.stack([np.sin(h), np.cos(h)], 1)
    r = np.stack([-f[:, 1], f[:, 0]], 1)
    return dict(FL=c + hl * f - hf * r, FR=c + hl * f + hf * r, RL=c - hl * f - hr * r, RR=c - hl * f + hr * r, centre=c, heading=h)


def signed_gap(poly, pts, side):
    """+ road side, - beyond the line (on the kerb); NaN beyond the end vertices."""
    pts = np.atleast_2d(pts)
    a, b = poly[:-1], poly[1:]
    v = b - a
    vv = np.maximum(np.einsum('ij,ij->i', v, v), 1e-12)
    out = np.full(len(pts), np.nan)
    for k, p in enumerate(pts):
        w = p - a
        u = np.einsum('ij,ij->i', w, v) / vv
        uc = np.clip(u, 0, 1)
        res = w - uc[:, None] * v
        d2 = np.einsum('ij,ij->i', res, res)
        j = int(np.argmin(d2))
        if (j == 0 and u[j] < 0) or (j == len(v) - 1 and u[j] > 1):
            continue
        cross = v[j, 0] * res[j, 1] - v[j, 1] * res[j, 0]
        s = 1.0 if side == 'left' else -1.0
        out[k] = math.sqrt(d2[j]) * (1.0 if cross * s > 0 else -1.0)
    return out


def kerb_gaps(race, n, tq, kerb):
    T = tyres(race, n, tq)
    z = zones()[kerb]
    fi, ri = ('FL', 'RL') if z['side'] == 'left' else ('FR', 'RR')
    res = {}
    for line in ('block', 'paint'):
        gf = signed_gap(z[line], T[fi], z['side'])
        gr = signed_gap(z[line], T[ri], z['side'])
        res[line] = dict(front=gf, rear=gr, nearest=np.fmin(gf, gr))
    res['centre'] = T['centre']
    return res
