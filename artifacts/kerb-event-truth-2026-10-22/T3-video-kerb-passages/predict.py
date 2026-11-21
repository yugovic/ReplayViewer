"""T3: what the replay (registered GPS pose, course heading, kerb_zones geometry) predicts in the image rows.

edge line   = CG road edge (geometry.json road edge on the kerb side) with the kerb_zones PAINT line substituted over the
              kerb station range (as build_apex_kpi.zoned_limits does), +-60 m around the kerb
red start/end points = kerb_zones BLOCK line at the first / last station that is not inside an end blockGap
Camera = camera.Cam rigidly on the replay pose: position = reference point + fwd * cam.fwd, heading = GPS course.
"""
from __future__ import annotations

import json

import numpy as np

import common as C
from camera import Cam

_G = None


def geometry():
    global _G
    if _G is None:
        g = json.loads((C.ROOT / 'public/data/tracks/fuji/cg_study/geometry.json').read_text(encoding='utf-8'))
        _G = np.array([r[:11] for r in g['road']], float)
    return _G


def kerb_doc():
    return {k['id']: k for k in json.loads((C.ROOT / 'public/data/tracks/fuji/cg_study/kerb_zones.json').read_text(encoding='utf-8'))['kerbs']}


def edge_line(kerb: dict, margin=60.0):
    road = geometry()
    st = road[:, 0]
    col = slice(7, 9) if kerb['side'] == 'left' else slice(9, 11)
    s0, s1 = kerb['station'][0], kerb['station'][-1]
    m = (st >= s0 - margin) & (st <= s1 + margin)
    pts = road[m, col].copy()
    ss = st[m]
    paint = np.asarray(kerb['paint'], float)
    kst = np.asarray(kerb['station'], float)
    for i, s in enumerate(ss):
        j = np.searchsorted(kst, s)
        if j < len(kst) and abs(kst[j] - s) < 0.26:
            pts[i] = paint[j]
    return ss, pts


def red_extent(kerb: dict):
    kst = np.asarray(kerb['station'], float)
    gaps = kerb.get('observed', {}).get('blockGaps', []) or []
    a, b = kst[0], kst[-1]
    for g0, g1 in gaps:
        if abs(g0 - kst[0]) < 0.3:
            a = g1 + 0.5
        if abs(g1 - kst[-1]) < 0.3:
            b = g0 - 0.5
    blk = np.asarray(kerb['block'], float)
    i0 = int(np.clip(np.searchsorted(kst, a), 0, len(kst) - 1))
    i1 = int(np.clip(np.searchsorted(kst, b), 0, len(kst) - 1))
    return dict(s0=float(kst[i0]), s1=float(kst[i1]), P0=blk[i0], P1=blk[i1])


class Terrain:
    """CG road surface height, same construction as scripts/quality/apex_kpi/camproj.Course.height:
    track.json altLeft/altRight (minus origin alt) along the station, linear across the nominal width by the lateral
    offset from the geometry.json centre."""

    def __init__(self):
        tr = json.loads((C.ROOT / 'public/data/tracks/fuji/track.json').read_text(encoding='utf-8'))
        cl = tr['centerline']
        oa = tr['origin']['alt']
        self.width = tr['width']
        self.cd = np.array([q['dist'] for q in cl])
        self.altL = np.array([q['altLeft'] for q in cl]) - oa
        self.altR = np.array([q['altRight'] for q in cl]) - oa
        road = geometry()
        self.st = road[:, 0]
        self.centre = road[:, 1:3]
        n = road[:, 3:5]
        self.normal = n / np.linalg.norm(n, axis=1)[:, None]
        self.total = float(self.st[-1] + 0.5)

    def height(self, s, xy):
        s = np.mod(np.asarray(s, float), self.total)
        xy = np.atleast_2d(xy)
        aL = np.interp(s, self.cd, self.altL); aR = np.interp(s, self.cd, self.altR)
        i = np.clip(np.round(s / 0.5).astype(int), 0, len(self.st) - 1)
        q = np.einsum('ij,ij->i', xy - self.centre[i], self.normal[i])
        t = np.clip((q + self.width / 2) / self.width, -0.5, 1.5)
        return aL + (aR - aL) * t


_TER = None


def terrain():
    global _TER
    if _TER is None:
        _TER = Terrain()
    return _TER


def body_state(L: C.Lap, t: float, cam: Cam, use_terrain: bool):
    """camera position (3D: x, y_up, z) and body axes (right, up, forward) at lap time t."""
    st = L.at(float(t))
    xy, fw, rt = st['xy'], st['fwd'] / np.linalg.norm(st['fwd']), None
    rt = np.array([-fw[1], fw[0]])
    f3 = np.array([fw[0], 0.0, fw[1]]); r3 = np.array([rt[0], 0.0, rt[1]]); u3 = np.array([0.0, 1.0, 0.0])
    y0 = 0.0
    if use_terrain:
        T = terrain(); s = st['s']
        hh = T.height(np.array([s, s + 3, s - 3, s, s]),
                      np.stack([xy, xy + 3 * fw, xy - 3 * fw, xy + 1.5 * rt, xy - 1.5 * rt]))
        y0 = float(hh[0])
        fb = f3 + u3 * (hh[1] - hh[2]) / 6.0
        rb = r3 + u3 * (hh[3] - hh[4]) / 3.0
        fb /= np.linalg.norm(fb)
        ub = np.cross(rb, fb); ub /= np.linalg.norm(ub)
        rb = np.cross(fb, ub)
        f3, r3, u3 = fb, rb, ub
    pos = np.array([xy[0], y0, xy[1]]) + f3 * cam.fwd + r3 * cam.right + u3 * cam.h
    return pos, r3, u3, f3


def project3(cam: Cam, state, P3):
    pos, r3, u3, f3 = state
    rel = P3 - pos
    pb = np.stack([rel @ r3, -(rel @ u3), rel @ f3], -1)
    pc = pb @ cam.R.T
    z = pc[..., 2]
    ok = (rel @ f3) > 0.8
    zz = np.where(ok, z, 1.0)
    return cam.cx + cam.f * pc[..., 0] / zz, cam.cy + cam.f * pc[..., 1] / zz, ok, rel @ r3, rel @ f3


def car_frame(L: C.Lap, t: np.ndarray, cam: Cam):
    """camera position and axes at lap-t times (arrays)."""
    xy = np.stack([np.interp(t, L.t, L.xy[:, 0]), np.interp(t, L.t, L.xy[:, 1])], 1)
    fw = np.stack([np.interp(t, L.t, L.fwd[:, 0]), np.interp(t, L.t, L.fwd[:, 1])], 1)
    fw /= np.linalg.norm(fw, axis=1)[:, None]
    rt = np.stack([-fw[:, 1], fw[:, 0]], 1)
    camxy = xy + fw * cam.fwd + rt * cam.right
    return camxy, fw, rt


def row_crossings(u, v, ok, y, side, road_row):
    """u where the projected polyline crosses image row y, nearest the image centre on the kerb side (vectorised)."""
    a, b = v[:-1] - y, v[1:] - y
    seg = ok[:-1] & ok[1:] & (a * b <= 0) & (a != b)
    if not seg.any():
        return None, False
    i = np.where(seg)[0]
    w = a[i] / (a[i] - b[i])
    uu = u[i] + w * (u[i + 1] - u[i])
    uu = uu[(uu >= 0) & (uu < 1280)]
    if len(uu) == 0:
        return None, False
    best = float(uu.max() if side == 'left' else uu.min())
    return best, bool(road_row[min(int(round(best)), 1279)])


def predict(race, lap, kerb_id, cam: Cam, pts: np.ndarray, rows, road, L: C.Lap = None, course=None, lap_start_pts=None,
            use_terrain=False):
    if use_terrain:
        return predict3(race, lap, kerb_id, cam, pts, rows, road, L, course, lap_start_pts)
    kd = kerb_doc()[kerb_id]
    if L is None:
        L = C.Lap(race, lap, course or C.Course())
    p0 = C.lap_start_pts(race, lap, L.meta) if lap_start_pts is None else lap_start_pts
    t = np.asarray(pts) - p0 - L.delta
    ss, E = edge_line(kd)
    camxy, fw, rt = car_frame(L, t, cam)
    out = {}
    proj = []
    for k in range(len(t)):
        d = E - camxy[k]
        X, D = d @ rt[k], d @ fw[k]
        ok = D > 0.8
        u, v, _ = cam.project(np.where(ok, X, 0), np.where(ok, D, 1))
        proj.append((u, v, ok, X, D))
    for y in rows:
        us, vis, xs = [], [], []
        for k in range(len(t)):
            u, v, ok, X, D = proj[k]
            uu, inside = row_crossings(u, v, ok, y, kd['side'], road[y])
            us.append(uu); vis.append(inside)
        out[y] = dict(u=us, inside=vis)
    # red start / end crossing times (dense 200 Hz)
    rx = red_extent(kd)
    td = np.arange(t[0], t[-1], 0.005)
    cxy, cf, cr = car_frame(L, td, cam)
    ev = {}
    for name, P in (('redStart', rx['P0']), ('redEnd', rx['P1'])):
        d = P[None, :] - cxy
        X, D = np.einsum('ij,ij->i', d, cr), np.einsum('ij,ij->i', d, cf)
        u, v, _ = cam.project(X, np.maximum(D, 0.8))
        v = np.where(D > 0.8, v, np.inf)
        ev[name] = {}
        for y in rows:
            j = np.where((v[:-1] < y) & (v[1:] >= y))[0]
            if len(j) == 0:
                ev[name][y] = None
                continue
            j = j[0]
            w = (y - v[j]) / (v[j + 1] - v[j])
            tc = td[j] + w * (td[j + 1] - td[j])
            uc = u[j] + w * (u[j + 1] - u[j])
            ev[name][y] = dict(t=float(tc), pts=float(tc + p0 + L.delta), u=float(uc),
                               inside=bool(0 <= uc < 1279.5 and road[y][min(int(round(uc)), 1279)]),
                               X=float(np.interp(tc, td, X)), D=float(np.interp(tc, td, D)))
    return dict(rows=out, events=ev, redExtent=dict(s0=rx['s0'], s1=rx['s1']), t=t.tolist())


def predict3(race, lap, kerb_id, cam: Cam, pts, rows, road, L=None, course=None, lap_start_pts=None):
    """as predict() but with the CG road surface (heights, car pitch/roll from the local road plane)."""
    kd = kerb_doc()[kerb_id]
    if L is None:
        L = C.Lap(race, lap, course or C.Course())
    p0 = C.lap_start_pts(race, lap, L.meta) if lap_start_pts is None else lap_start_pts
    t = np.asarray(pts) - p0 - L.delta
    T = terrain()
    ss, E = edge_line(kd)
    E3 = np.stack([E[:, 0], T.height(ss, E), E[:, 1]], 1)
    out = {}
    proj = [project3(cam, body_state(L, tk, cam, True), E3) for tk in t]
    for y in rows:
        us, vis = [], []
        for k in range(len(t)):
            u, v, ok, X, D = proj[k]
            uu, inside = row_crossings(u, v, ok, y, kd['side'], road[y])
            us.append(uu); vis.append(inside)
        out[y] = dict(u=us, inside=vis)
    rx = red_extent(kd)
    td = np.arange(t[0], t[-1], 0.01)
    ev = {}
    for name, P, sP in (('redStart', rx['P0'], rx['s0']), ('redEnd', rx['P1'], rx['s1'])):
        P3 = np.array([P[0], float(T.height([sP], P[None, :])[0]), P[1]])
        uu, vv, XX, DD = [], [], [], []
        for tk in td:
            u, v, ok, X, D = project3(cam, body_state(L, tk, cam, True), P3[None, :])
            uu.append(float(u[0])); vv.append(float(v[0]) if ok[0] else np.inf); XX.append(float(X[0])); DD.append(float(D[0]))
        u, v = np.array(uu), np.array(vv)
        ev[name] = {}
        for y in rows:
            j = np.where((v[:-1] < y) & (v[1:] >= y))[0]
            if len(j) == 0:
                ev[name][y] = None
                continue
            j = j[0]
            w = (y - v[j]) / (v[j + 1] - v[j])
            tc = td[j] + w * (td[j + 1] - td[j])
            uc = u[j] + w * (u[j + 1] - u[j])
            ev[name][y] = dict(t=float(tc), pts=float(tc + p0 + L.delta), u=float(uc),
                               inside=bool(0 <= uc < 1279.5 and road[y][min(int(round(uc)), 1279)]),
                               X=float(np.interp(tc, td, XX)), D=float(np.interp(tc, td, DD)))
    return dict(rows=out, events=ev, redExtent=dict(s0=rx['s0'], s1=rx['s1']), t=t.tolist())
