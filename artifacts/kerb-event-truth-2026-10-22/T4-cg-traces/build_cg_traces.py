"""T4 — build the 100 Hz traces of the DRAWN 3DCG car and its kerb events for every Fuji lap.

Inputs
  $T4_VIEWER_POSE_DIR/viewer_pose_<race>_L<n>.json   dump_viewer_pose.ts output (viewer's own code)
       default: <repo>/../_work_events_2026-10-22/T4/viewer_pose
  public/data (read-only): cg_study/geometry.json, kerb_zones.json, mazda2-dj.json,
       races/<race>/apex_kpi.json, gps_registration.json, lap JSON (validation only)

Outputs (this folder)
  traces/<race>_L<n>.npz   compact 100 Hz traces for the join step (see cgpose.py)
  cg_events.json           per lap x inside kerb: CG first/last contact (paint, block), closest
                           approach (clipping point), heading-rate onset (turn-in seen in the CG)
  validation.json          reproduction checks + agreement with apex_kpi.json

Usage: python build_cg_traces.py      (system python with numpy/scipy; nothing is downloaded)
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cgpose  # noqa: E402
from cgpose import ROOT, TRACES_DIR, NAMES, geometry, base, apexkpi  # noqa: E402

POSE_DIR = Path(os.environ.get('T4_VIEWER_POSE_DIR', ROOT.parent / '_work_events_2026-10-22/T4/viewer_pose'))
OUT_EVENTS = HERE / 'cg_events.json'
OUT_VALID = HERE / 'validation.json'
ONSET_MAX_FLANK_S = 5.0           # quality 'good' needs foot->near-peak within this (longer = merged same-direction corners)
COURSE_SMOOTH_S = 0.5             # course rate (Hermite derivative) carries +-1.5 deg/s knot noise: heavier smoothing
VIEWER_SRC = ['src/replay/interpolation.ts', 'src/replay/visualAlignment.ts', 'src/replay/gpsRegistration.ts',
              'src/replay/lapClock.ts', 'src/replay/types.ts', 'src/replay/projection.ts',
              'src/replay/vehicleGeometry.ts', 'src/engine/poseSmoothing.ts']


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rel(p: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(ROOT)).replace('\\', '/')
    except ValueError:
        return '<work>/' + Path(p).name


def r(v, nd=3):
    if v is None:
        return None
    v = float(v)
    return None if not math.isfinite(v) else round(v, nd)


def viewer_src_check() -> dict:
    out = {}
    for f in VIEWER_SRC:
        res = subprocess.run(['git', '-C', str(ROOT), 'diff', '--quiet', 'origin/master', '--', f])
        out[f] = 'identical-to-origin/master' if res.returncode == 0 else 'DIFFERS'
    head = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'origin/master'], capture_output=True, text=True).stdout.strip()
    return {'originMaster': head, 'files': out}


# ------------------------------------------------------------------ per lap trace
def build_trace(dump: dict) -> dict:
    g = geometry()
    t = np.asarray(dump['t'], float)
    dt = float(np.median(np.diff(t)))
    xy = np.stack([np.asarray(dump['x'], float), np.asarray(dump['z'], float)], 1)
    yaw_d = np.asarray(dump['yawDrawn'], float)
    yaw_t = np.asarray(dump['yawTarget'], float)
    yaw_d2 = np.asarray(dump['yawDrawnFps2'], float)
    st, lat, st_nv = g.project(xy)
    st_s, lat_s = g.project_smooth(xy, start=st)
    W = g.tyres(xy, yaw_d)
    v = np.gradient(xy, t, axis=0)
    path = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(xy, axis=0), axis=1))])
    speed = np.linalg.norm(v, axis=1)
    course = np.unwrap(np.arctan2(v[:, 0], v[:, 1]))
    tr = dict(t=t, x=xy[:, 0], z=xy[:, 1], yawDrawn=yaw_d, yawTarget=yaw_t, yawDrawnFps2=yaw_d2,
              station=st, stationNV=st_nv, lateral=lat, stationSmooth=st_s, lateralSmooth=lat_s, pathDistM=path,
              tyres=W, speedMps=speed,
              yawRateDrawnDegS=np.degrees(np.gradient(yaw_d, t)), yawRateTargetDegS=np.degrees(np.gradient(yaw_t, t)),
              courseRateDegS=np.degrees(np.gradient(course, t)), deltaSeconds=float(dump['deltaSeconds']),
              registrationOffsetMeters=np.asarray(dump['registration']['offsetMeters'], float), dt=dt)
    tr['gaps'] = {}
    for kid in g.inside:
        z = g.zones[kid]
        m = (st >= z['s0'] - cgpose.KERB_WINDOW_M) & (st <= z['s1'] + cgpose.KERB_WINDOW_M)
        if not m.any():
            continue
        i0, i1 = int(np.flatnonzero(m)[0]), int(np.flatnonzero(m)[-1])
        tr['gaps'][kid] = (i0, g.kerb_gaps(W[i0:i1 + 1], kid))
    return tr


def save_trace(race: str, lap: int, tr: dict, dump: dict):
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    f32 = lambda a: np.asarray(a, np.float32)  # noqa: E731
    arrays = dict(t=tr['t'], x=f32(tr['x']), z=f32(tr['z']), yawDrawn=f32(tr['yawDrawn']), yawTarget=f32(tr['yawTarget']),
                  station=f32(tr['station']), stationNV=f32(tr['stationNV']),
                  lateral=f32(tr['lateral']), stationSmooth=f32(tr['stationSmooth']), lateralSmooth=f32(tr['lateralSmooth']),
                  pathDistM=f32(tr['pathDistM']), tyres=f32(tr['tyres']), speedMps=f32(tr['speedMps']),
                  yawRateDrawnDegS=f32(tr['yawRateDrawnDegS']), yawRateTargetDegS=f32(tr['yawRateTargetDegS']),
                  courseRateDegS=f32(tr['courseRateDegS']), deltaSeconds=np.float64(tr['deltaSeconds']),
                  registrationOffsetMeters=tr['registrationOffsetMeters'],
                  kerbIds=np.array(list(tr['gaps']), dtype='U12'),
                  lapSha256=np.array(dump['lapSha256'], dtype='U64'))
    for kid, (i0, gaps) in tr['gaps'].items():
        arrays[f'gapStart_{kid}'] = np.int32(i0)
        arrays[f'gap_{kid}'] = f32(gaps)   # columns: paintFront, paintRear, blockFront, blockRear
    np.savez_compressed(TRACES_DIR / f'{race}_L{lap}.npz', **arrays)


# ------------------------------------------------------------------ events
def crossing_time(t, g, k, entering: bool):
    """Sub-sample time where g crosses 0 between samples k-1 and k (entering: g goes <=0 at k)."""
    if k <= 0 or k >= len(g):
        return float(t[min(max(k, 0), len(t) - 1)])
    a, b = g[k - 1], g[k]
    if not (math.isfinite(a) and math.isfinite(b)) or a == b:
        return float(t[k])
    u = (0.0 - a) / (b - a)
    return float(t[k - 1] + min(max(u, 0.0), 1.0) * (t[k] - t[k - 1]))


def contact_events(tr, idx, gap_full, label):
    """first/last contact (gap <= 0) of one gap series restricted to the pass indices idx."""
    gp = gap_full[idx]
    on = np.nan_to_num(gp, nan=1.0) <= 0
    if not on.any():
        return None
    ks = np.flatnonzero(on)
    k_first, k_last = int(idx[ks[0]]), int(idx[ks[-1]])
    t_first = crossing_time(tr['t'], gap_full, k_first, True)
    t_last = crossing_time(tr['t'], gap_full, k_last + 1, False) if k_last + 1 < len(gap_full) else float(tr['t'][k_last])
    runs = int(np.sum(np.diff(np.concatenate([[0], on.astype(int)])) == 1))
    d = tr['deltaSeconds']
    return {'tFirst': r(t_first), 'lapTimeFirst': r(t_first + d), 'stationFirst': r(np.interp(t_first, tr['t'], tr['station']), 2),
            'stationSmoothFirst': r(np.interp(t_first, tr['t'], tr['stationSmooth']), 2),
            'tLast': r(t_last), 'lapTimeLast': r(t_last + d), 'stationLast': r(np.interp(t_last, tr['t'], tr['station']), 2),
            'stationSmoothLast': r(np.interp(t_last, tr['t'], tr['stationSmooth']), 2),
            'onSeconds': r(on.sum() * tr['dt'], 2), 'contactRuns': runs, 'series': label}


def turn_onset(tr, rate_key, sign, t_apex, **kw):
    """Heading-rate onset of the turn into this kerb's corner: cgpose.rate_onset (shared definition) on the
    signed rate (towards the kerb side), referenced to the CG clipping point; adds lap time / stations."""
    o = cgpose.rate_onset(tr['t'], sign * np.asarray(tr[rate_key], float), t_apex, **kw)
    if o is None:
        return None
    d = tr['deltaSeconds']
    out = {}
    for k, v in o.items():
        if k.startswith('t') and isinstance(v, float):
            out[k] = r(v)
            out['lapTime' + k[1:]] = r(v + d)
            out['stationSmooth' + k[1:]] = r(np.interp(v, tr['t'], tr['stationSmooth']), 2)
        elif isinstance(v, float):
            out[k + 'DegS'] = r(v, 2)
        else:
            out[k] = v
    flank = (o['tNearPeak'] - o['tFoot']) if 'tFoot' in o else None
    out['quality'] = ('good' if (flank is not None and flank <= ONSET_MAX_FLANK_S and not o.get('weakCorner')
                                 and not o.get('peakAtWindowEdge') and o.get('footStop') != 'search-limit'
                                 and o.get('t50') is not None) else 'check')
    return out


def kerb_events(tr, kid):
    g = geometry()
    z = g.zones[kid]
    pm = g.in_pass(kid, tr['stationNV'])
    if pm.sum() < 5:
        return None
    idx = np.flatnonzero(pm)
    gaps_full = np.full((len(tr['t']), 4), np.nan)
    if kid in tr['gaps']:
        i0, gp = tr['gaps'][kid]
        gaps_full[i0:i0 + len(gp)] = gp
    gp = gaps_full[idx]
    if not np.isfinite(gp[:, 2]).any():
        return None
    d = tr['deltaSeconds']
    t = tr['t']
    nearest_b = np.fmin(gaps_full[:, 2], gaps_full[:, 3])
    nearest_p = np.fmin(gaps_full[:, 0], gaps_full[:, 1])

    def at(k, label):
        return {'definition': label, 't': r(t[k]), 'lapTime': r(t[k] + d), 'station': r(tr['station'][k], 2),
                'stationNV': r(tr['stationNV'][k], 1), 'stationSmooth': r(tr['stationSmooth'][k], 2),
                'lateral': r(tr['lateral'][k], 3), 'lateralSmooth': r(tr['lateralSmooth'][k], 3),
                'gPaintFront': r(gaps_full[k, 0]), 'gPaintRear': r(gaps_full[k, 1]),
                'gBlockFront': r(gaps_full[k, 2]), 'gBlockRear': r(gaps_full[k, 3]),
                'speedMps': r(tr['speedMps'][k], 2), 'yawRateDrawnDegS': r(tr['yawRateDrawnDegS'][k], 2)}

    j = int(idx[np.nanargmin(gp[:, 2])])
    j2 = int(idx[np.nanargmin(np.fmin(gp[:, 2], gp[:, 3]))])
    ev = {'kerb': kid, 'name': NAMES.get(kid), 'side': z['side'], 'kerbStation': [r(z['s0'], 1), r(z['s1'], 1)],
          'pass': {'tStart': r(t[idx[0]]), 'tEnd': r(t[idx[-1]]), 'lapTimeStart': r(t[idx[0]] + d), 'lapTimeEnd': r(t[idx[-1]] + d)},
          'apex': at(j, 'argmin gBlockFront over the pass (apex_kpi.json clipping-point definition)'),
          'apexNearestTyre': at(j2, 'argmin over the pass of min(gBlockFront, gBlockRear)'),
          'minOverPass': {f: r(np.nanmin(gp[:, i])) if np.isfinite(gp[:, i]).any() else None
                          for i, f in enumerate(('gPaintFront', 'gPaintRear', 'gBlockFront', 'gBlockRear'))},
          'block': {'nearestTyre': contact_events(tr, idx, nearest_b, 'min(gBlockFront, gBlockRear) <= 0'),
                    'front': contact_events(tr, idx, gaps_full[:, 2], 'gBlockFront <= 0'),
                    'rear': contact_events(tr, idx, gaps_full[:, 3], 'gBlockRear <= 0')},
          'paint': {'nearestTyre': contact_events(tr, idx, nearest_p, 'min(gPaintFront, gPaintRear) <= 0'),
                    'front': contact_events(tr, idx, gaps_full[:, 0], 'gPaintFront <= 0'),
                    'rear': contact_events(tr, idx, gaps_full[:, 1], 'gPaintRear <= 0')}}
    sign = z['sign']        # left kerb = left turn = yaw (atan2(x, z)) increasing
    ev['turnIn'] = {'drawnYaw': turn_onset(tr, 'yawRateDrawnDegS', sign, float(t[j])),
                    'targetYaw': turn_onset(tr, 'yawRateTargetDegS', sign, float(t[j])),
                    'course': turn_onset(tr, 'courseRateDegS', sign, float(t[j]), smooth_s=COURSE_SMOOTH_S)}
    return ev


# ------------------------------------------------------------------ validation helpers
def station_jumps(tr) -> dict:
    """Discontinuities of the station series: |second difference| > 0.2 m at 100 Hz (a smooth station
    changes its 10 ms step by ~1 mm; inside tight corners the station RATE legitimately exceeds the
    speed by 1/(1 - lateral*curvature), which is continuous and not counted)."""
    out = {}
    for key in ('station', 'stationSmooth'):
        s = np.asarray(tr[key], float)
        ok = np.abs(np.diff(s)) < 100                       # ignore the lap wrap
        d2 = np.abs(np.diff(s, 2))[ok[1:] & ok[:-1]]
        out[key] = {'count': int((d2 > 0.2).sum()), 'maxSecondDifferenceM': r(d2.max(), 3)}
    return out



def kpi_from_trace(tr, kid, yaw_key='yawDrawn'):
    """apex_kpi.json fields recomputed from the viewer trace (same pass window / gap definitions)."""
    g = geometry()
    pm = g.in_pass(kid, tr['stationNV'])
    if pm.sum() < 5:
        return None
    idx = np.flatnonzero(pm)
    xy = np.stack([tr['x'][idx], tr['z'][idx]], 1)
    W = g.tyres(xy, tr[yaw_key][idx])
    gp = g.kerb_gaps(W, kid)
    if not np.isfinite(gp[:, 2]).any():
        return None
    j = int(np.nanargmin(gp[:, 2]))
    nm = lambda a: float(np.nanmin(a)) if np.isfinite(a).any() else None  # noqa: E731
    return {'gPaintFront': nm(gp[:, 0]), 'gPaintRear': nm(gp[:, 1]), 'gBlockFront': nm(gp[:, 2]), 'gBlockRear': nm(gp[:, 3]),
            'apexStation': float(tr['stationNV'][idx[j]]), 'apexT': float(tr['t'][idx[j]]),
            'apexLapTime': float(tr['t'][idx[j]] + tr['deltaSeconds'])}


def diff_stats(vals):
    a = np.abs(np.asarray([v for v in vals if v is not None and math.isfinite(v)], float))
    s = np.asarray([v for v in vals if v is not None and math.isfinite(v)], float)
    if not len(a):
        return None
    return {'n': int(len(a)), 'meanSigned': r(s.mean(), 4), 'medianAbs': r(np.median(a), 4),
            'p95Abs': r(np.quantile(a, 0.95), 4), 'maxAbs': r(a.max(), 4)}


FIELDS = ('gBlockFront', 'gBlockRear', 'gPaintFront', 'gPaintRear', 'apexStation', 'apexT', 'apexLapTime')


def main() -> int:
    g = geometry()
    srccheck = viewer_src_check()
    if any(v != 'identical-to-origin/master' for v in srccheck['files'].values()):
        print('WARNING viewer source differs from origin/master:', srccheck)
    manifest = json.loads((POSE_DIR / 'viewer_pose_manifest.json').read_text(encoding='utf-8'))
    # apex_kpi replica context (build_apex_kpi.py's own functions)
    origin, geo = apexkpi.context()
    limits = apexkpi.zoned_limits(base.load_limits(ROOT / 'public/data/tracks/fuji', 0.0), g.zones)

    sessions_doc = {}
    val = {'viewerSource': srccheck, 'laps': {}, 'replicaVsShipped': {f: [] for f in FIELDS},
           'viewerVsShipped': {f: [] for f in FIELDS}, 'viewerTargetYawVsShipped': {f: [] for f in FIELDS},
           'viewerVsReplicaRaw': {f: [] for f in FIELDS}, 'rows': []}
    for item in manifest:
        race, lap = item['race'], int(item['lap'])
        dump = json.loads((POSE_DIR / item['file']).read_text(encoding='utf-8'))
        assert dump['registration']['enabled'] and dump['registration']['verified'], (race, lap)
        tr = build_trace(dump)
        save_trace(race, lap, tr, dump)

        # --- reproduction checks
        lapj = json.loads((ROOT / f'public/data/races/{race}/osaki_hmr_demio_101_lap_{lap:03d}.json').read_text(encoding='utf-8'))
        assert hashlib.sha256((ROOT / f'public/data/races/{race}/osaki_hmr_demio_101_lap_{lap:03d}.json').read_bytes()).hexdigest() == dump['lapSha256']
        raw = base.to_local(lapj['lat'], lapj['lng'], origin) + tr['registrationOffsetMeters']
        tk = np.asarray(lapj['t'], float)
        kk = np.clip(np.round(tk / tr['dt']).astype(int), 0, len(tr['t']) - 1)
        on_grid = np.abs(tr['t'][kk] - tk) < 1e-6
        knot_err = np.linalg.norm(np.stack([tr['x'][kk], tr['z'][kk]], 1) - raw, axis=1)[on_grid]
        wrapd = lambda a: (a + np.pi) % (2 * np.pi) - np.pi  # noqa: E731
        dyaw_fps = np.degrees(np.abs(wrapd(tr['yawDrawn'] - tr['yawDrawnFps2'])))
        dyaw_tgt = np.degrees(np.abs(wrapd(tr['yawDrawn'] - tr['yawTarget'])))
        moving = tr['speedMps'] > 5
        lapval = {'samples': int(len(tr['t'])), 'hz': round(1 / tr['dt'], 3),
                  'registrationOffsetMeters': [r(v, 3) for v in tr['registrationOffsetMeters']],
                  'deltaSeconds': tr['deltaSeconds'],
                  'positionAtGpsKnotsVsRegisteredLatLngM': {'n': int(on_grid.sum()), 'max': r(knot_err.max(), 6)},
                  'drawnYaw100HzVs60fpsDeg': {'p95': r(np.quantile(dyaw_fps[moving], 0.95), 4), 'max': r(dyaw_fps[moving].max(), 4),
                                              'maxFrontAxleLateralM': r(g.half_wb * np.radians(dyaw_fps[moving].max()), 4)},
                  'drawnVsTargetYawDeg': {'p95': r(np.quantile(dyaw_tgt[moving], 0.95), 3), 'max': r(dyaw_tgt[moving].max(), 3)},
                  'stationContinuousVsNearestVertexM': {'maxAbs': r(np.abs(tr['station'] - tr['stationNV']).max(), 3)},
                  'stationDiscontinuities': station_jumps(tr),
                  'stationSmoothVsPolylineM': {'p95Abs': r(np.quantile(np.abs(tr['stationSmooth'] - tr['station']), 0.95), 3),
                                               'maxAbs': r(np.abs(tr['stationSmooth'] - tr['station']).max(), 3)},
                  'lateralSmoothVsPolylineM': {'p95Abs': r(np.quantile(np.abs(tr['lateralSmooth'] - tr['lateral']), 0.95), 3),
                                               'maxAbs': r(np.abs(tr['lateralSmooth'] - tr['lateral']).max(), 3)}}

        # --- apex_kpi agreement
        shipped = json.loads((ROOT / f'public/data/races/{race}/apex_kpi.json').read_text(encoding='utf-8'))
        offsets, _ = apexkpi.registration_offsets(race)
        lp = apexkpi.LapPaths(race, lap, limits, geo, origin, offsets.get(lap, (0.0, 0.0)))
        for row in shipped['laps'].get(str(lap), []):
            kid = row['kerb']
            rep = apexkpi.kpi(lp, g.zones[kid], limits)
            vw = kpi_from_trace(tr, kid, 'yawDrawn')
            vt = kpi_from_trace(tr, kid, 'yawTarget')
            rec = {'race': race, 'lap': lap, 'kerb': kid}
            for f in FIELDS:
                s = row.get(f)
                a = None if rep is None else rep.get(f)
                b = None if vw is None else vw.get(f)
                c = None if vt is None else vt.get(f)
                nd = apexkpi.ROUND[f]
                if s is not None and a is not None:
                    val['replicaVsShipped'][f].append(round(a, nd) - s)
                if s is not None and b is not None:
                    val['viewerVsShipped'][f].append(b - s)
                if s is not None and c is not None:
                    val['viewerTargetYawVsShipped'][f].append(c - s)
                if a is not None and b is not None:
                    val['viewerVsReplicaRaw'][f].append(b - a)
                rec[f] = {'shipped': s, 'replica': r(a, 4), 'viewer': r(b, 4), 'viewerTargetYaw': r(c, 4)}
            val['rows'].append(rec)

        # --- events
        kerbs = {}
        for kid in g.inside:
            ev = kerb_events(tr, kid)
            if ev is not None:
                kerbs[kid] = ev
        sessions_doc.setdefault(race, {'laps': {}})['laps'][str(lap)] = {
            'deltaSeconds': tr['deltaSeconds'], 'durationSeconds': r(tr['t'][-1]),
            'registrationOffsetMeters': [r(v, 3) for v in tr['registrationOffsetMeters']],
            'trace': rel(TRACES_DIR / f'{race}_L{lap}.npz'), 'kerbs': kerbs}
        val['laps'][f'{race}/L{lap}'] = lapval
        print(race, lap, 'kerbs', len(kerbs), 'knot max err', lapval['positionAtGpsKnotsVsRegisteredLatLngM']['max'],
              'yaw 100Hz vs 60fps max', lapval['drawnYaw100HzVs60fpsDeg']['max'])

    summary = {k: {f: diff_stats(v) for f, v in val[k].items()}
               for k in ('replicaVsShipped', 'viewerVsShipped', 'viewerTargetYawVsShipped', 'viewerVsReplicaRaw')}
    # per session viewer-vs-shipped
    per_session = {}
    for race in sessions_doc:
        rows = [x for x in val['rows'] if x['race'] == race]
        per_session[race] = {f: diff_stats([x[f]['viewer'] - x[f]['shipped'] for x in rows
                                            if x[f]['viewer'] is not None and x[f]['shipped'] is not None]) for f in FIELDS}
    worst = sorted(val['rows'], key=lambda x: -abs((x['gBlockFront']['viewer'] or 0) - (x['gBlockFront']['shipped'] or 0)))[:8]
    validation = {
        'version': 1, 'kind': 'cg-pose-validation',
        'what': 'Agreement of the reproduced viewer pose (drawn yaw, Hermite position, 100 Hz) with public/data/races/*/apex_kpi.json, '
                'and checks of the reproduction itself.',
        'labels': {'replicaVsShipped': 'MEASURED: build_apex_kpi.py functions re-run on the current repo inputs (50 Hz cubic spline, '
                                       'spline heading) minus the shipped apex_kpi.json value (rounded as shipped) - proves the shipped KPI '
                                       'matches the current inputs',
                   'viewerVsShipped': 'MEASURED: viewer reconstruction (this T4 trace) minus shipped apex_kpi.json; differences come from the '
                                      'viewer Hermite vs cubic spline position, drawn (smoothed, look-ahead) chord yaw vs spline-derivative '
                                      'heading, 100 Hz vs 50 Hz sampling, and the shipped 2-decimal rounding',
                   'viewerTargetYawVsShipped': 'MEASURED: same with the unsmoothed target yaw (sampleReplay heading) - isolates the pose smoother',
                   'viewerVsReplicaRaw': 'MEASURED: viewer reconstruction minus unrounded replica'},
        'viewerSource': srccheck,
        'summary': summary, 'viewerVsShippedPerSession': per_session,
        'worstGBlockFrontRows': worst,
        'laps': val['laps'],
        'rows': val['rows'],
    }
    OUT_VALID.write_text(json.dumps(validation, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')

    inputs = {p: sha256(ROOT / p) for p in ['public/data/tracks/fuji/cg_study/geometry.json',
                                            'public/data/tracks/fuji/cg_study/kerb_zones.json',
                                            'public/data/tracks/fuji/track.json',
                                            'public/data/vehicles/mazda2-dj.json',
                                            'public/data/races/fuji_aim_01/gps_registration.json',
                                            'public/data/races/fuji_aim_2020_07_30/gps_registration.json']}
    doc = {
        'version': 1, 'kind': 'cg-kerb-events', 'track': 'T4 what the 3DCG shows',
        'surveyTruth': False,
        'label': 'DERIVED from the viewer\'s drawn pose (registered GPS + viewer interpolation/smoothing); not the true car position',
        'timeBases': {'t': 'recorded lap t = GPS base = viewer playback clock = kerb_contacts.json / apex_kpi.json apexT base',
                      'lapTime': 't + deltaSeconds (meta.first_sample_after_lap_start_seconds) = true lap time = HUD lap clock '
                                 '(video overlay timer base; overlay lap = data lap + 1, see artifacts/video-position-gap-2026-10-02)'},
        'pose': {'source': 'dump_viewer_pose.ts: origin/master src/replay/interpolation.ts sampleReplay + src/engine/poseSmoothing.ts '
                           'PoseSmoother(DEFAULT: 2 x 70 ms, look-ahead 0.14 s, linear input) run under vite-node; continuous 1x playback '
                           'from the lap start (snap at t=0) stepped at 100 Hz',
                 'position': 'registered GPS (gps_registration.json, lap/limits/track SHA-256 verified -> enabled; antenna offset 0 = vehicle '
                             'centre) through the viewer C1 Hermite; no positional smoothing in the viewer',
                 'yaw': 'drawn = smoothed pose quaternion yaw; target = sampleReplay heading (+-6-sample = +-0.6 s chord of registered '
                        'points, linearly interpolated between 10 Hz samples)',
                 'tyres': 'outer edges, centre +- wheelbase/2 along the DRAWN yaw, +-(track+tyre width)/2 across (mazda2-dj.json); '
                          'same convention as build_apex_kpi.py'},
        'definitions': {
            'gap': 'm, inner tyre outer edge to kerb line; NEGATIVE = on the kerb; paint = road-side start of the white strip '
                   '(track-limit line), block = road-side start of the red/white blocks (cg_study/kerb_zones.json); NaN beyond the '
                   'line end vertices (apex_kpi line_gap)',
            'pass': 'samples whose nearest-vertex CG station lies within the kerb station range +-3 m (apex_kpi pass window)',
            'apex': 'CG clipping point = argmin gBlockFront over the pass (apex_kpi definition); apexNearestTyre = argmin of '
                    'min(front, rear)',
            'block/paint .nearestTyre/.front/.rear': 'CG first contact tFirst = first sub-sample crossing to gap <= 0 inside the pass, '
                    'last contact tLast = crossing back above 0 after the last on-kerb sample; onSeconds = time with gap <= 0; '
                    'contactRuns = separate on-kerb intervals; null = no contact in the CG',
            'turnIn': 'heading-rate onset of the corner as drawn = cgpose.rate_onset (same function usable on logger series) on the '
                      'yaw rate towards the kerb side (left kerb = left turn), referenced to the CG apex: '
                      + json.dumps(cgpose.ONSET) + '; see the rate_onset docstring. Keys: tPeak/ratePeakDegS, tFoot/rateFootDegS '
                      '(foot of the rising flank; footStop = flat | rate-rises-again | search-limit), rateBaseDegS = max(foot rate, 0), '
                      't10/t25/t50 = crossings of base + NN % of (peak - base), tLinear0 = 2*t25 - t50; every t has lapTime*/stationSmooth* '
                      'twins. weakCorner = peak - base < 8 deg/s. quality = good when not weak, peak not on the window edge, footStop != search-limit, '
                      'foot->tNearPeak <= ' + str(ONSET_MAX_FLANK_S) + ' s and t50 defined, else check (use t50 first; t10/tLinear0 are the most sensitive). drawnYaw = drawn car body (primary); targetYaw = unsmoothed chord '
                      'heading; course = direction of motion of the drawn position (Hermite derivative; smooth_s ' + str(COURSE_SMOOTH_S) + ' s)',
            'station': 'continuous projection on the CG road centreline polyline (geometry.json); stationNV = nearest centreline vertex '
                       '(0.5 m, apex_kpi apexStation); lateral = signed offset of the car reference point, + = driver right. '
                       'stationSmooth/lateralSmooth = projection on a periodic cubic spline through the 652 track.json vertices '
                       '(parametrised by CG station, equal at the vertices): continuous, use it for along-track differences. The polyline '
                       'has 4-31 deg kinks every ~7 m in corners, so `station` jumps by up to ~4.4 m inside corners (measured)',
            'pathDistM': 'cumulative distance of the drawn reference point since t = 0 (traces only)'},
        'caveats': ['The chord heading (+-0.6 s) blurs the yaw-rate onset in time (drawn/target onsets lead the course onset); compare '
                    'each logger/video onset with the same definition.',
                    'Kerb lines come from the 2019 orthophoto; GPS lateral error after registration ~+-0.5-0.7 m (docs/fuji-position-gap-status-2026-10-09.html).',
                    'Lap start/end: the viewer clamps and the chord heading is one-sided within 0.6 s of the lap ends.',
                    'Along-track differences: use stationSmooth, pathDistM or time; the polyline station (and apex_kpi apexStation) '
                    'jumps by up to ~4 m at the 7 m centreline kinks when the car is metres inside a corner.',
                    'Turn-in in Fuji sector 3 / 300R: several kerbs sit in same-direction sequences (k18->k19, k20->k21, k9->k10, '
                    'k23->k24); their onset is measured from the valley between the corners, k15 (100R) and k23 are long/weak.'],
        'inputs': inputs,
        'validationSummary': {'file': 'validation.json', 'viewerVsShipped': summary['viewerVsShipped'],
                              'replicaVsShipped': summary['replicaVsShipped']},
        'sessions': sessions_doc,
    }
    OUT_EVENTS.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print('summary viewerVsShipped', json.dumps(summary['viewerVsShipped'], indent=0))
    print('summary replicaVsShipped', json.dumps(summary['replicaVsShipped'], indent=0))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
