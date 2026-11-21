"""T4 — what the 3DCG replay shows: the drawn car pose of the ReplayViewer at any lap time.

    from cgpose import cg_state, cg_states, cg_events, SESSIONS
    s = cg_state('fuji_aim_2020_07_30', 2, 16.62)     # true lap time (s since the timing line)
    s['station'], s['lateral'], s['kerbs']['k13']['gBlockNearest']

Source of the pose
  traces/<race>_L<n>.npz, built by build_cg_traces.py from the viewer's OWN code
  (dump_viewer_pose.ts runs origin/master src/replay/interpolation.ts sampleReplay +
  src/engine/poseSmoothing.ts PoseSmoother under vite-node, 1x playback at 100 Hz):
    position  registered GPS (gps_registration.json, hash-verified, antenna offset 0)
              through the viewer's C1 Hermite (non-uniform Catmull-Rom) interpolation
    yaw       drawn yaw = 2 x 70 ms cascaded smoother chasing pose(t + 0.14 s) of the
              +-6-sample (+-0.6 s) chord heading (yawTarget = unsmoothed chord heading)
  cg_state() interpolates that 100 Hz trace linearly (unwrapped yaw) and recomputes the
  tyre / kerb geometry for the requested instant with the functions below, which are
  the same functions build_cg_traces.py used for the stored traces and cg_events.json.

Time bases
  lap_time (argument) = TRUE lap time = t + delta, delta = meta.first_sample_after_lap_start_seconds
  (HUD lap clock, the video overlay timer reads this base). t = recorded lap time = GPS base =
  viewer playback clock = time base of kerb_contacts.json / apex_kpi.json apexT.
  Outside [delta, delta + duration] the viewer clamps; the result carries clamped=True.

Geometry (as scripts/quality/apex_kpi/build_apex_kpi.py)
  station      continuous projection on the CG road centreline (cg_study/geometry.json, 0.5 m rows)
  stationNV    nearest centreline vertex (the apex_kpi.json 'apexStation' definition, 0.5 m steps)
  lateral      signed offset of the car reference point (= displayed GPS point, vehicle centre),
               + = driver's right of the centreline direction (viewer convention)
  stationSmooth / lateralSmooth
               the same on a periodic cubic spline through the 652 track.json centreline vertices,
               parametrised by CG station (equal at the vertices). The CG polyline has 4-31 deg kinks
               every ~7 m in corners, so `station` jumps by up to ~4 m when the car is metres inside a
               corner. USE stationSmooth (or pathDistM / time) FOR ALONG-TRACK DIFFERENCES.
  pathDistM    cumulative distance travelled by the drawn reference point since t = 0 (continuous)
  tyres        FL FR RL RR tyre OUTER edges: centre +- wheelbase/2 along the drawn yaw,
               +-(track + tyre width)/2 across (public/data/vehicles/mazda2-dj.json)
  kerb gaps    inner front / rear tyre outer edge to the kerb paint line (road-side start of the
               white strip) and block line (road-side start of the red/white blocks) of
               cg_study/kerb_zones.json; m, NEGATIVE = on the kerb; NaN where the tyre projects
               beyond the line's end vertices (apex_kpi line_gap). Only the 14 inside kerbs
               (build_apex_kpi.INSIDE) within +-80 m of the station are evaluated.
               inPass = station within the kerb station range +-3 m (apex_kpi pass window).

All values describe the DRAWN 3DCG, not the true car: GPS lateral error +-0.5-0.7 m remains.
Paths: the repository root is two levels above this folder (override REPLAYVIEWER_ROOT);
traces dir override CG_TRACES_DIR. Read-only: nothing here writes into the repository.
"""
from __future__ import annotations

import json
import math
import os
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True  # never drop __pycache__ into pipeline/ or scripts/

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('REPLAYVIEWER_ROOT', HERE.parents[2]))
TRACES_DIR = Path(os.environ.get('CG_TRACES_DIR', HERE / 'traces'))
EVENTS_PATH = HERE / 'cg_events.json'
for p in (ROOT / 'pipeline', ROOT / 'scripts/quality/apex_kpi'):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
import register_gps_to_track as base  # noqa: E402
import build_apex_kpi as apexkpi  # noqa: E402

SESSIONS = {'fuji_aim_01': (1, 2, 3, 4), 'fuji_aim_2020_07_30': (1, 2, 3, 4, 5, 6)}
ALIASES = {'0729': 'fuji_aim_01', '7/29': 'fuji_aim_01', 'wet': 'fuji_aim_01', '2020-07-29': 'fuji_aim_01',
           '0730': 'fuji_aim_2020_07_30', '7/30': 'fuji_aim_2020_07_30', 'dry': 'fuji_aim_2020_07_30',
           '2020-07-30': 'fuji_aim_2020_07_30'}
INSIDE = list(apexkpi.INSIDE)
NAMES = dict(apexkpi.NAMES)
KERB_WINDOW_M = 80.0
PASS_MARGIN_M = apexkpi.PASS_MARGIN_M
TYRES = ('FL', 'FR', 'RL', 'RR')


def session_id(session: str) -> str:
    s = str(session)
    if s in SESSIONS:
        return s
    if s in ALIASES:
        return ALIASES[s]
    raise KeyError(f'unknown session {session!r}; use one of {list(SESSIONS)} or {list(ALIASES)}')


# ----------------------------------------------------------------- static geometry
class Geometry:
    """CG road centreline, kerb zones (inside kerbs) and vehicle dimensions."""

    def __init__(self, root: Path = ROOT):
        track_dir = root / 'public/data/tracks/fuji'
        g = json.loads((track_dir / 'cg_study/geometry.json').read_text(encoding='utf-8'))
        road = np.array([row[:5] for row in g['road']], float)
        self.dist, self.centre = road[:, 0], road[:, 1:3]
        self.total = float(self.dist[-1])
        seg = np.diff(self.centre, axis=0)
        self.seg_len = np.linalg.norm(seg, axis=1)
        self.seg_t = seg / np.maximum(self.seg_len, 1e-9)[:, None]
        from scipy.spatial import cKDTree
        self.tree = cKDTree(self.centre)
        # Smooth along-track reference: periodic cubic spline through the 652 track.json centreline
        # vertices (~7 m apart), parametrised by their CG station. The 0.5 m geometry.json rows are a
        # LINEAR resampling of those vertices, so the polyline has 4-31 deg kinks every 7 m in corners and
        # a point metres inside a corner projects with station jumps of up to ~4 m (measured in the traces).
        from scipy.interpolate import CubicSpline
        tj = json.loads((track_dir / 'track.json').read_text(encoding='utf-8'))
        cl = tj['centerline']
        cd = np.array([p['dist'] for p in cl], float)
        cxz = np.array([[p['x'], p['z']] for p in cl], float)
        if np.linalg.norm(cxz[-1] - cxz[0]) < 1e-6:
            cd, cxz = cd[:-1], cxz[:-1]
        self.smooth_period = float(tj['totalLength'])
        knots = np.concatenate([cd, [cd[0] + self.smooth_period]])
        pts = np.vstack([cxz, cxz[:1]])
        self.smooth = CubicSpline(knots, pts, bc_type='periodic')
        self.zones = apexkpi.load_zones(track_dir / 'cg_study/kerb_zones.json')
        self.inside = [k for k in INSIDE if k in self.zones]
        self.vehicle = base.load_vehicle_geometry(root / 'public/data/vehicles/mazda2-dj.json')
        v = self.vehicle
        self.half_wb = v['wheelbaseMeters'] / 2
        self.half_front = (v['frontTrackMeters'] + v['frontTyreWidthMeters']) / 2
        self.half_rear = (v['rearTrackMeters'] + v['rearTyreWidthMeters']) / 2

    def project(self, xy: np.ndarray):
        """(n,2) -> continuous station, signed lateral (+ right), nearest-vertex station."""
        q = np.atleast_2d(np.asarray(xy, float))
        _, i = self.tree.query(q)
        n = len(self.centre)
        best_d = np.full(len(q), np.inf)
        st = np.zeros(len(q))
        lat = np.zeros(len(q))
        for a in (np.clip(i - 1, 0, n - 2), np.clip(i, 0, n - 2)):
            t = self.seg_t[a]
            o = q - self.centre[a]
            u = np.clip(np.einsum('ij,ij->i', o, t), 0, self.seg_len[a])
            r = o - u[:, None] * t
            d = np.einsum('ij,ij->i', r, r)
            better = d < best_d
            best_d = np.where(better, d, best_d)
            st = np.where(better, self.dist[a] + u * (self.dist[a + 1] - self.dist[a]) / np.maximum(self.seg_len[a], 1e-9), st)
            lat = np.where(better, t[:, 0] * r[:, 1] - t[:, 1] * r[:, 0], lat)
        return st, lat, self.dist[i]

    def project_smooth(self, xy: np.ndarray, start: np.ndarray | None = None, iters: int = 8):
        """(n,2) -> station and signed lateral (+ right) on the smooth spline centreline (continuous;
        equals the CG station at the track.json vertices). start: initial stations (default: polyline)."""
        q = np.atleast_2d(np.asarray(xy, float))
        d = np.asarray(self.project(q)[0] if start is None else start, float).copy()
        P = self.smooth_period
        for _ in range(iters):
            dm = np.mod(d, P)
            c, c1, c2 = self.smooth(dm), self.smooth(dm, 1), self.smooth(dm, 2)
            o = q - c
            gval = np.einsum('ij,ij->i', o, c1)
            gder = -np.einsum('ij,ij->i', c1, c1) + np.einsum('ij,ij->i', o, c2)
            step = np.where(np.abs(gder) > 1e-9, gval / gder, 0.0)
            d = d - np.clip(step, -5.0, 5.0)
        dm = np.mod(d, P)
        c, c1 = self.smooth(dm), self.smooth(dm, 1)
        t = c1 / np.maximum(np.linalg.norm(c1, axis=1), 1e-9)[:, None]
        o = q - c
        # keep the station on the same lap branch as the start value (no wrap jumps mid-lap)
        if start is not None:
            d = np.asarray(start, float) + ((d - np.asarray(start, float) + P / 2) % P - P / 2)
        return d, t[:, 0] * o[:, 1] - t[:, 1] * o[:, 0]

    def tyres(self, xy: np.ndarray, yaw: np.ndarray) -> np.ndarray:
        """Tyre outer edges (n,4,2) FL FR RL RR for centre xy and viewer yaw (atan2(x, z))."""
        xy = np.atleast_2d(np.asarray(xy, float))
        yaw = np.atleast_1d(np.asarray(yaw, float))
        f = np.stack([np.sin(yaw), np.cos(yaw)], 1)
        r = np.stack([-f[:, 1], f[:, 0]], 1)       # driver's right
        hl, hf, hr = self.half_wb, self.half_front, self.half_rear
        return np.stack([xy + hl * f - hf * r, xy + hl * f + hf * r,
                         xy - hl * f - hr * r, xy - hl * f + hr * r], 1)

    def tyre_centres(self, tyres: np.ndarray) -> np.ndarray:
        """Outer edges (n,4,2) -> nominal tyre centre lines (edge moved inward by tyre width / 2)."""
        v = self.vehicle
        W = np.asarray(tyres, float)
        right = W[:, 1] - W[:, 0]
        right /= np.maximum(np.linalg.norm(right, axis=1), 1e-9)[:, None]
        hf, hr = v['frontTyreWidthMeters'] / 2, v['rearTyreWidthMeters'] / 2
        return np.stack([W[:, 0] + hf * right, W[:, 1] - hf * right, W[:, 2] + hr * right, W[:, 3] - hr * right], 1)

    def kerb_gaps(self, tyres: np.ndarray, kid: str) -> np.ndarray:
        """(n,4) gaps [paintFront, paintRear, blockFront, blockRear] of the INNER tyres to kerb kid."""
        z = self.zones[kid]
        sign = z['sign']
        fi, ri = (0, 2) if sign > 0 else (1, 3)
        return np.stack([apexkpi.line_gap(z['paint'], tyres[:, fi], sign),
                         apexkpi.line_gap(z['paint'], tyres[:, ri], sign),
                         apexkpi.line_gap(z['block'], tyres[:, fi], sign),
                         apexkpi.line_gap(z['block'], tyres[:, ri], sign)], 1)

    def kerbs_near(self, station: float, window: float = KERB_WINDOW_M) -> list[str]:
        return [k for k in self.inside if self.zones[k]['s0'] - window <= station <= self.zones[k]['s1'] + window]

    def in_pass(self, kid: str, station_nv) -> np.ndarray:
        z = self.zones[kid]
        s = np.asarray(station_nv, float)
        return (s >= z['s0'] - PASS_MARGIN_M) & (s <= z['s1'] + PASS_MARGIN_M)


@lru_cache(maxsize=1)
def geometry() -> Geometry:
    return Geometry(ROOT)


# ----------------------------------------------------------------- traces
@lru_cache(maxsize=16)
def cg_trace(session: str, lap: int) -> dict:
    """The stored 100 Hz trace of one lap as a dict of numpy arrays (+ scalar meta)."""
    race = session_id(session)
    path = TRACES_DIR / f'{race}_L{int(lap)}.npz'
    if not path.exists():
        raise FileNotFoundError(f'{path} missing; run build_cg_traces.py')
    with np.load(path, allow_pickle=False) as z:
        d = {k: z[k] for k in z.files}
    d['race'], d['lap'] = race, int(lap)
    d['delta'] = float(d['deltaSeconds'])
    return d


def _interp(tr: dict, key: str, t: np.ndarray) -> np.ndarray:
    return np.interp(t, tr['t'], tr[key].astype(float))


def cg_states(session: str, lap: int, lap_times, kerbs: str | list[str] = 'near') -> dict:
    """Vectorised cg_state: arrays over lap_times (true lap time). kerbs='near' (+-80 m of each
    instant), 'all' (14 inside kerbs) or a list of kerb ids; gaps NaN where not evaluated."""
    tr = cg_trace(session, lap)
    g = geometry()
    lt = np.atleast_1d(np.asarray(lap_times, float))
    t_raw = lt - tr['delta']
    t = np.clip(t_raw, tr['t'][0], tr['t'][-1])
    xy = np.stack([_interp(tr, 'x', t), _interp(tr, 'z', t)], 1)
    yaw = _interp(tr, 'yawDrawn', t)
    yaw_t = _interp(tr, 'yawTarget', t)
    st, lat, st_nv = g.project(xy)
    W = g.tyres(xy, yaw)
    out = dict(race=tr['race'], lap=tr['lap'], deltaSeconds=tr['delta'], lapTime=lt, t=t,
               clamped=(t_raw < tr['t'][0]) | (t_raw > tr['t'][-1]),
               x=xy[:, 0], z=xy[:, 1], yawDeg=np.degrees(yaw), yawTargetDeg=np.degrees(yaw_t),
               yawRateDegS=_interp(tr, 'yawRateDrawnDegS', t), courseRateDegS=_interp(tr, 'courseRateDegS', t),
               speedMps=_interp(tr, 'speedMps', t), station=st, stationNV=st_nv, lateral=lat,
               stationSmooth=_interp(tr, 'stationSmooth', t), lateralSmooth=_interp(tr, 'lateralSmooth', t),
               pathDistM=_interp(tr, 'pathDistM', t), tyres=W, tyreCentres=g.tyre_centres(W), kerbs={})
    if kerbs == 'all':
        kids = g.inside
    elif kerbs == 'near':
        kids = sorted({k for s in st for k in g.kerbs_near(float(s))}, key=g.inside.index)
    else:
        kids = list(kerbs)
    for kid in kids:
        gaps = g.kerb_gaps(W, kid)
        z = g.zones[kid]
        if kerbs == 'near':
            far = (st < z['s0'] - KERB_WINDOW_M) | (st > z['s1'] + KERB_WINDOW_M)
            gaps[far] = np.nan
        with np.errstate(all='ignore'):
            out['kerbs'][kid] = dict(side=z['side'], inPass=g.in_pass(kid, st_nv),
                                     gPaintFront=gaps[:, 0], gPaintRear=gaps[:, 1],
                                     gBlockFront=gaps[:, 2], gBlockRear=gaps[:, 3],
                                     gPaintNearest=np.fmin(gaps[:, 0], gaps[:, 1]),
                                     gBlockNearest=np.fmin(gaps[:, 2], gaps[:, 3]))
    return out


def _f(v, nd=4):
    v = float(v)
    return None if not math.isfinite(v) else round(v, nd)


def cg_state(session: str, lap: int, lap_time: float, kerbs: str | list[str] = 'near') -> dict:
    """Drawn 3DCG state at one TRUE lap time (s since the timing line). JSON-friendly dict."""
    s = cg_states(session, lap, [lap_time], kerbs)
    W = s['tyres'][0]
    C = s['tyreCentres'][0]
    out = {k: s[k] for k in ('race', 'lap', 'deltaSeconds')}
    out.update(lapTime=float(lap_time), t=_f(s['t'][0], 4), clamped=bool(s['clamped'][0]),
               x=_f(s['x'][0]), z=_f(s['z'][0]), yawDeg=_f(s['yawDeg'][0], 3), yawTargetDeg=_f(s['yawTargetDeg'][0], 3),
               yawRateDegS=_f(s['yawRateDegS'][0], 3), courseRateDegS=_f(s['courseRateDegS'][0], 3),
               speedMps=_f(s['speedMps'][0], 3), station=_f(s['station'][0], 3), stationNV=_f(s['stationNV'][0], 1),
               lateral=_f(s['lateral'][0], 4), stationSmooth=_f(s['stationSmooth'][0], 3),
               lateralSmooth=_f(s['lateralSmooth'][0], 4), pathDistM=_f(s['pathDistM'][0], 3),
               tyres={n: [_f(W[i, 0]), _f(W[i, 1])] for i, n in enumerate(TYRES)},
               tyreCentres={n: [_f(C[i, 0]), _f(C[i, 1])] for i, n in enumerate(TYRES)}, kerbs={})
    for kid, k in s['kerbs'].items():
        vals = {f: _f(k[f][0]) for f in ('gPaintFront', 'gPaintRear', 'gBlockFront', 'gBlockRear', 'gPaintNearest', 'gBlockNearest')}
        if all(v is None for v in vals.values()):
            continue
        gf, gr = k['gBlockFront'][0], k['gBlockRear'][0]
        vals['nearestTyreBlock'] = None if not (math.isfinite(gf) or math.isfinite(gr)) else \
            ('front' if (not math.isfinite(gr) or (math.isfinite(gf) and gf <= gr)) else 'rear')
        out['kerbs'][kid] = dict(side=k['side'], name=NAMES.get(kid), inPass=bool(k['inPass'][0]), **vals)
    return out


# ----------------------------------------------------------------- onset of a rate series
ONSET = dict(smooth_s=0.3, peak_window=(-4.0, 4.0), near_peak=0.9, back_limit_s=8.0, hyst_min=0.3, hyst_frac=0.02,
             plateau_frac=0.15, plateau_s=0.25, slope_ref_s=3.0, fractions=(0.1, 0.25, 0.5), weak=8.0)


def rate_onset(t, rate, t_ref: float, **kw) -> dict | None:
    """Onset of the rise of a SIGNED rate series (positive = towards the corner) into its peak near t_ref.

    Generic so that the CG yaw rate, the logger yaw rate (IMU gyro, GPS course rate) or the steering
    angle / steering rate can be measured with the IDENTICAL definition:
      1. centred moving average of smooth_s (no time shift);
      2. peak = max in t_ref + peak_window (if that sits on the window edge, the largest interior local
         maximum >= half of it); the flank walk starts where the rate first reaches
         near_peak * peak before that maximum (tNearPeak), so noise on a plateau cannot end it;
      3. foot of the rising flank: walk back (at most back_limit_s before tNearPeak) and stop when
         (a) the rate rises more than max(hyst_min, hyst_frac * peak) above the running minimum, or
         (b) the slope stays below plateau_frac * (max slope in the slope_ref_s before the peak) for
             plateau_s (straight before the corner, or a plateau between same-direction corners);
         foot = running-minimum sample (a) / start of the flat run nearest the peak (b);
      4. base = max(rate at foot, 0) (a chicane starts from zero rate);
      5. tNN = upward crossing of base + NN% * (peak - base) on the flank (linear interpolation);
         tLinear0 = 2 * t25 - t50 (line through the 25 % and 50 % crossings extrapolated to the base).
    Times are returned in the time base of `t`. weakCorner: peak - base < weak (onset poorly defined).
    """
    o = {**ONSET, **kw}
    t = np.asarray(t, float)
    raw = np.asarray(rate, float)
    dt = float(np.median(np.diff(t)))
    w = max(1, int(round(o['smooth_s'] / dt))) | 1
    r = np.convolve(np.pad(raw, w // 2, mode='edge'), np.ones(w) / w, mode='valid')
    m = (t >= t_ref + o['peak_window'][0]) & (t <= t_ref + o['peak_window'][1])
    if m.sum() < 5:
        return None
    ii = np.flatnonzero(m)
    kmax = int(ii[np.argmax(r[ii])])
    if kmax in (ii[0], ii[-1]):
        # the window maximum sits on its edge (the neighbouring corner's flank): prefer the largest
        # interior local maximum of at least half that value, i.e. this corner's own peak
        inner = ii[1:-1]
        loc = inner[(r[inner] >= r[inner - 1]) & (r[inner] >= r[inner + 1]) & (r[inner] >= 0.5 * r[kmax]) & (r[inner] > 0)]
        if len(loc):
            kmax = int(loc[np.argmax(r[loc])])
    peak = float(r[kmax])
    out = {'tPeak': float(t[kmax]), 'ratePeak': peak, 'peakAtWindowEdge': bool(kmax in (ii[0], ii[-1]))}
    if not peak > 0:
        out['note'] = 'no rate towards the corner near the reference time'
        return out
    # walk-back starts where the flank first reaches near_peak * peak (a plateau's noise must not end the flank)
    kp = kmax
    while kp - 1 >= ii[0] and r[kp - 1] >= o['near_peak'] * peak:
        kp -= 1
    out['tNearPeak'] = float(t[kp])
    slope = np.gradient(r, t)
    lo = max(0, kp - int(round(o['back_limit_s'] / dt)))
    ref_lo = max(0, kp - int(round(o['slope_ref_s'] / dt)))
    ks = ref_lo + int(np.argmax(slope[ref_lo:kp + 1])) if kp > ref_lo else kp   # steepest point of the flank
    s_ref = float(slope[ks]) if kp > ref_lo else 0.0
    hyst = max(o['hyst_min'], o['hyst_frac'] * peak)
    need = max(1, int(round(o['plateau_s'] / dt)))
    km, rm, run, run_start, stop = kp, peak, 0, None, 'search-limit'
    for k in range(kp - 1, lo - 1, -1):
        if r[k] < rm:
            km, rm = k, float(r[k])
        elif r[k] > rm + hyst:
            stop = 'rate-rises-again'
            break
        # the flat-run test starts below the steepest point (the slope is ~0 at the peak itself)
        if k < ks and s_ref > 0 and slope[k] < o['plateau_frac'] * s_ref:
            if run == 0:
                run_start = k
            run += 1
            if run >= need:
                km, rm, stop = run_start, float(r[run_start]), 'flat'
                break
        else:
            run = 0
    base = max(rm, 0.0)
    out.update({'tFoot': float(t[km]), 'rateFoot': rm, 'rateBase': base, 'footStop': stop,
                'weakCorner': bool(peak - base < o['weak'])})
    for f in o['fractions']:
        key = f'{int(round(f * 100))}'
        thr = base + f * (peak - base)
        below = np.flatnonzero(r[km:kp + 1] < thr)
        if not len(below):
            out[f't{key}'] = None
            continue
        k = km + int(below[-1]) + 1
        a, b = r[k - 1], r[k]
        u = (thr - a) / (b - a) if b != a else 0.0
        out[f't{key}'] = float(t[k - 1] + min(max(u, 0.0), 1.0) * (t[k] - t[k - 1]))
    if out.get('t25') is not None and out.get('t50') is not None:
        out['tLinear0'] = 2 * out['t25'] - out['t50']
    return out


@lru_cache(maxsize=1)
def _events_doc() -> dict:
    return json.loads(EVENTS_PATH.read_text(encoding='utf-8'))


def cg_events(session: str, lap: int, kerb: str | None = None) -> dict:
    """Per-kerb CG events of one lap from cg_events.json (all kerbs, or one)."""
    lapdoc = _events_doc()['sessions'][session_id(session)]['laps'][str(int(lap))]
    return lapdoc['kerbs'] if kerb is None else lapdoc['kerbs'].get(kerb)


if __name__ == '__main__':
    import pprint
    pprint.pprint(cg_state('fuji_aim_2020_07_30', 2, 16.62))
