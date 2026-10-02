"""Per-corner clipping-point KPI for the viewer (BACKLOG GPS-REG-16).

Writes public/data/races/<race>/apex_kpi.json for every inside kerb of every
distributed lap:

  gBlockFront/Rear  inner tyre outer edge -> road-side start of the red/white
                    block band (m, minimum over the pass; negative = on it)
  gPaintFront/Rear  same against the road-side start of the kerb paint
                    (white strip = track-limit line)
  apexStation/apexT station (m) and lap time where gBlockFront is minimal
  onBlockLengthM    path length of the inner front tyre while on the blocks
  exitOuterGapM     outer tyres vs the opposite road edge, 10 m before to
                    120 m after the kerb end (negative = beyond the paint)
  contact           IMU label of the pass from kerb_contacts.json
  delta             value minus the session median of the same corner

Inputs (all repo files, nothing local):
  public/data/races/<race>/osaki_hmr_demio_101_lap_NNN.json   shipped laps (GPS t base)
  public/data/races/<race>/gps_registration.json              SHIPPED translation per lap
  public/data/races/<race>/kerb_contacts.json                 export_inputs.py
  public/data/tracks/fuji/cg_study/kerb_zones.json            kerb_zones_v3.py
  public/data/tracks/fuji/cg_study/geometry.json              stations, road edges
  public/data/vehicles/mazda2-dj.json                         axle tracks, tyre width

Wheel paths: cubic spline of the registered GPS track at 50 Hz, heading from
the spline derivative, antenna assumed at the vehicle centre (as the shipped
registration). Reference: eval_v2.py Lap.kpi.

Absolute values carry about +-0.7 m (GPS lateral error left after the
road-limit registration plus 2019 ortho kerb lines); the lap-to-lap delta at
the same corner is the primary comparison.

Usage:
  python scripts/quality/apex_kpi/build_apex_kpi.py                # both Fuji races
  python scripts/quality/apex_kpi/build_apex_kpi.py --eval-out DIR  # + label-consistency evaluation

The helpers (zones, wheel paths, stations) are shared with export_inputs.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / 'pipeline'))
import register_gps_to_track as base  # noqa: E402
from register_gps_kerb_contact import robust_signed_gap  # noqa: E402  (sign from the road normal, not the segment direction)

TRACK_ID = 'fuji'
VEHICLE_PREFIX = 'osaki_hmr_demio_101'
RACES = ('fuji_aim_01', 'fuji_aim_2020_07_30')
TRACK_DIR = ROOT / 'public/data/tracks' / TRACK_ID
ZONES_PATH = TRACK_DIR / 'cg_study/kerb_zones.json'
VEHICLE_PATH = ROOT / 'public/data/vehicles/mazda2-dj.json'

# Inside kerbs at the clipping points, in station order (ids of kerb_zones.json).
INSIDE = ['k13', 'k1', 'k15', 'k3', 'k18', 'k19', 'kDunlopR', 'k7', 'k20', 'k21', 'k9', 'k10', 'k23', 'k24']
NAMES = {'k13': '1コーナー', 'k1': 'コカ・コーラ', 'k15': '1500m', 'k3': 'ヘアピン', 'k18': '2420m',
         'k19': '2650m', 'kDunlopR': 'ダンロップ右', 'k7': 'ダンロップ左', 'k20': '3090m', 'k21': '3250m',
         'k9': '3350m', 'k10': '3540m', 'k23': '3600m', 'k24': '最終コーナー'}
PASS_MARGIN_M = 3.0          # pass window = kerb station range +- this
EXIT_WINDOW_M = (-10.0, 120.0)
DT = 0.02                    # 50 Hz wheel paths
ABS_UNCERTAINTY_M = 0.7
ABS_UNCERTAINTY_REASON = (
    'The shipped registration is a road-limit fit: inside the road its cost is flat over 0.5-1 m, and the '
    'IMU kerb-contact labels bound the remaining lateral GPS error only to about +-0.4-0.7 m '
    '(docs/fuji-apex-kerb-kpi-2026-10-02.md). Kerb lines come from the 2019 orthophoto (georeferencing '
    '~0.2-0.3 m, not verified against 2020). Compare laps at the same corner (delta) first.')
# Label consistency (evaluation only): strong pass should touch the blocks, silent pass not be deep on them.
ON_TOL = 0.15
SILENT_DEEP = -0.40


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT)).replace('\\', '/')
    except ValueError:
        return Path(path).name


def lap_path(race: str, lap: int) -> Path:
    return ROOT / f'public/data/races/{race}/{VEHICLE_PREFIX}_lap_{lap:03d}.json'


def race_laps(race: str) -> list[int]:
    index = json.loads((ROOT / f'public/data/races/{race}/laps.json').read_text(encoding='utf-8'))
    return [int(s['lap']) for s in index['selected']]


def load_zones(path: Path = ZONES_PATH, legacy_v2: bool = False) -> dict:
    """id -> side/sign/station range/paint/block/outer boundaries.

    legacy_v2: course_v2_kerbs.json (work dir) with the assumed Dunlop-R block
    (paint + 0.42 m), only for the before/after evaluation."""
    doc = json.loads(Path(path).read_text(encoding='utf-8'))
    zones = {}
    for kb in doc['kerbs']:
        st = np.asarray(kb['station'], float)
        paint = np.asarray(kb['paint'], float)
        outer = np.asarray(kb['outer'], float)
        block = np.asarray(kb['block'], float)
        if legacy_v2 and kb['id'] == 'kDunlopR':
            d = outer - paint
            d /= np.maximum(np.linalg.norm(d, axis=1), 1e-6)[:, None]
            block = paint + 0.42 * d
        zones[kb['id']] = dict(id=kb['id'], side=kb['side'], sign=+1 if kb['side'] == 'left' else -1,
                               s0=float(st[0]), s1=float(st[-1]), station=st, paintXY=paint, outerXY=outer,
                               addedOutsideCg=bool(kb.get('addedOutsideCg')),
                               paint=base.Boundary.from_vertices(paint), block=base.Boundary.from_vertices(block),
                               outer=base.Boundary.from_vertices(outer))
    return zones


def zoned_limits(limits: base.Limits, zones: dict) -> base.Limits:
    """Road edge -> kerb paint start at kerbs (white strip = track limit); the
    dry edge -> kerb outer end where the kerb is missing in the CG."""
    rl, rr = limits.road_left.vertices.copy(), limits.road_right.vertices.copy()
    dl, dr = limits.dry_left.vertices.copy(), limits.dry_right.vertices.copy()
    step = float(np.median(np.diff(limits.dist)))
    for z in zones.values():
        left = z['side'] == 'left'
        for s, p, o in zip(z['station'], z['paintXY'], z['outerXY']):
            i = int(round(s / step))
            if 0 <= i < len(rl):
                (rl if left else rr)[i] = p
                if z['addedOutsideCg']:
                    (dl if left else dr)[i] = o
    out = base.Limits(**{f: getattr(limits, f) for f in limits.__dataclass_fields__})
    out.road_left, out.road_right = base.Boundary.from_vertices(rl), base.Boundary.from_vertices(rr)
    out.dry_left, out.dry_right = base.Boundary.from_vertices(dl), base.Boundary.from_vertices(dr)
    return out


def registration_offsets(race: str, path: Path | None = None) -> tuple[dict, Path]:
    path = path or ROOT / f'public/data/races/{race}/gps_registration.json'
    reg = json.loads(path.read_text(encoding='utf-8'))
    return {int(l['lap']): np.asarray(l['offsetMeters'], float) for l in reg['laps']}, path


class LapPaths:
    """50 Hz centre and tyre-edge paths of one shipped lap in the viewer frame."""

    def __init__(self, race: str, lap: int, limits: base.Limits, geo: dict, origin: dict, offset):
        self.race, self.lap = race, lap
        self.path = lap_path(race, lap)
        self.data = json.loads(self.path.read_text(encoding='utf-8'))
        self.meta = self.data['meta']
        self.offset = np.asarray(offset, float)
        t = np.asarray(self.data['t'], float)
        raw = base.to_local(self.data['lat'], self.data['lng'], origin)
        cx, cz = CubicSpline(t, raw[:, 0]), CubicSpline(t, raw[:, 1])
        self.t50 = np.arange(t[0], t[-1], DT)
        xy = np.stack([cx(self.t50), cz(self.t50)], 1) + self.offset
        hv = np.stack([cx(self.t50, 1), cz(self.t50, 1)], 1)
        hv /= np.maximum(np.linalg.norm(hv, axis=1), 1e-6)[:, None]
        right = np.stack([-hv[:, 1], hv[:, 0]], 1)
        hl = geo['wheelbaseMeters'] / 2
        hf = (geo['frontTrackMeters'] + geo['frontTyreWidthMeters']) / 2
        hr = (geo['rearTrackMeters'] + geo['rearTyreWidthMeters']) / 2
        # FL FR RL RR at the tyre outer edges
        self.W50 = np.stack([xy + hl * hv - hf * right, xy + hl * hv + hf * right,
                             xy - hl * hv - hr * right, xy - hl * hv + hr * right], 1)
        idx, _ = limits.lateral(xy)
        self.st50 = limits.dist[idx]
        self.t_end = float(t[-1])
        self.lap_start_offset = float(self.meta.get('first_sample_after_lap_start_seconds') or 0.0)

    def pass_mask(self, zone: dict) -> np.ndarray:
        return (self.st50 >= zone['s0'] - PASS_MARGIN_M) & (self.st50 <= zone['s1'] + PASS_MARGIN_M)

    def pass_window(self, zone: dict):
        m = self.pass_mask(zone)
        if m.sum() < 5:
            return None
        return float(self.t50[m][0]), float(self.t50[m][-1])

    def station_at(self, t: float) -> float:
        return float(np.interp(t, self.t50, self.st50))


def line_gap(line: base.Boundary, points: np.ndarray, sign: int) -> np.ndarray:
    """Signed gap to a kerb line; NaN where the point projects beyond the line's end vertices
    (there the clipped end-segment distance flips sign and is not a gap to the kerb)."""
    g = line.signed_gap(points, sign)
    _, idx = line.tree.query(points)
    g[(idx <= 0) | (idx >= len(line.vertices) - 1)] = np.nan
    return g


def nanmin(v: np.ndarray):
    return float(np.nanmin(v)) if np.isfinite(v).any() else None


def kpi(lp: LapPaths, zone: dict, limits: base.Limits) -> dict | None:
    m = lp.pass_mask(zone)
    if m.sum() < 5:
        return None
    sign = zone['sign']
    W, st, tt = lp.W50[m], lp.st50[m], lp.t50[m]
    fi, ri = (0, 2) if sign > 0 else (1, 3)          # inner front / rear
    gb_f = line_gap(zone['block'], W[:, fi], sign)
    if not np.isfinite(gb_f).any():
        return None
    gb_r = line_gap(zone['block'], W[:, ri], sign)
    gp_f = line_gap(zone['paint'], W[:, fi], sign)
    gp_r = line_gap(zone['paint'], W[:, ri], sign)
    j = int(np.nanargmin(gb_f))
    step = np.linalg.norm(np.diff(W[:, fi], axis=0), axis=1)
    on = np.nan_to_num(gb_f[:-1], nan=1.0) < 0
    on_len = float(step[on].sum())
    oi = (1, 3) if sign > 0 else (0, 2)
    me = (lp.st50 > zone['s1'] + EXIT_WINDOW_M[0]) & (lp.st50 < zone['s1'] + EXIT_WINDOW_M[1])
    # opposite road edge; robust sign: zoned kerb paint steps make Boundary.signed_gap flip at transverse segments
    edge = limits.road_right if sign > 0 else limits.road_left
    inward = -limits.normal if sign > 0 else limits.normal
    ge = None
    if me.any():
        We = lp.W50[me]
        ge = float(min(robust_signed_gap(edge, inward, We[:, oi[0]]).min(), robust_signed_gap(edge, inward, We[:, oi[1]]).min()))
    apex_t = float(tt[j])
    return dict(gBlockFront=nanmin(gb_f), gBlockRear=nanmin(gb_r), gPaintFront=nanmin(gp_f), gPaintRear=nanmin(gp_r),
                apexStation=float(st[j]), apexT=apex_t, apexLapTime=apex_t + lp.lap_start_offset,
                onBlockLengthM=on_len, exitOuterGapM=ge)


DELTA_FIELDS = ('gBlockFront', 'gBlockRear', 'gPaintFront', 'gPaintRear', 'apexStation', 'onBlockLengthM', 'exitOuterGapM')
ROUND = {'gBlockFront': 2, 'gBlockRear': 2, 'gPaintFront': 2, 'gPaintRear': 2, 'apexStation': 1, 'apexT': 2,
         'apexLapTime': 3, 'onBlockLengthM': 1, 'exitOuterGapM': 2}


def rnd(field: str, v):
    return None if v is None or not np.isfinite(v) else round(float(v), ROUND[field])


def label_check(r: dict):
    """IMU label vs geometry at this pass: 'ok', 'strong-but-off' (contact felt, tyres >0.15 m off the blocks),
    'silent-but-deep' (no vibration, tyres >0.55 m on the blocks); None for mild/unlabelled passes."""
    c = (r.get('contact') or {}).get('cls')
    gs = [v for v in (r['gBlockFront'], r['gBlockRear']) if v is not None]
    if c not in ('strong', 'silent') or not gs:
        return None
    g = min(gs)
    if c == 'strong' and g > ON_TOL:
        return 'strong-but-off'
    if c == 'silent' and g < SILENT_DEEP - ON_TOL:
        return 'silent-but-deep'
    return 'ok'


def auc(strong: list, silent: list):
    if not strong or not silent:
        return None
    s = np.asarray(strong)[:, None]
    n = np.asarray(silent)[None, :]
    return float(((s < n).sum() + 0.5 * (s == n).sum()) / (s.size * n.size))


def consistency(rows_by_lap: dict) -> dict:
    """Strong label vs g_min (min of front/rear g_block) and silent label vs deep on blocks."""
    S, N, contra = [], [], []
    for lap, rows in rows_by_lap.items():
        for r in rows:
            c = (r.get('contact') or {}).get('class')
            g = min(v for v in (r['gBlockFront'], r['gBlockRear']) if v is not None)
            if c == 'strong':
                S.append(g)
                if g > ON_TOL:
                    contra.append([int(lap), r['kerb'], 'strong-but-off', round(g, 2)])
            elif c == 'silent':
                N.append(g)
                if g < SILENT_DEEP - ON_TOL:
                    contra.append([int(lap), r['kerb'], 'silent-but-deep', round(g, 2)])
    a = auc(S, N)
    return dict(strong=len(S), silent=len(N), auc=None if a is None else round(a, 3),
                strongMedianGMin=round(float(np.median(S)), 2) if S else None,
                contradictions=contra, contradictionCount=len(contra))


def build_race(race: str, zones: dict, limits: base.Limits, geo: dict, origin: dict, contacts: dict | None,
               offsets: dict) -> dict:
    """Rows per lap (raw floats) + contact labels."""
    out = {}
    for lap in race_laps(race):
        lp = LapPaths(race, lap, limits, geo, origin, offsets.get(lap, (0.0, 0.0)))
        labels = {r['kerb']: r for r in (contacts or {}).get('laps', {}).get(str(lap), [])}
        rows = []
        for kid in INSIDE:
            if kid not in zones:
                continue
            k = kpi(lp, zones[kid], limits)
            if k is None:
                continue
            lab = labels.get(kid)
            k['kerb'] = kid
            k['contact'] = None if lab is None else dict(cls=lab['class'], bin=lab.get('bin'),
                                                         peak=lab.get('peakRollRms'))
            rows.append(k)
        out[lap] = dict(rows=rows, sha=sha256(lp.path))
    return out


def session_medians(laps: dict) -> dict:
    med = {}
    for kid in INSIDE:
        vals = {f: [r[f] for lap in laps.values() for r in lap['rows'] if r['kerb'] == kid and r[f] is not None]
                for f in DELTA_FIELDS}
        n = max((len(v) for v in vals.values()), default=0)
        if n:
            med[kid] = {f: (float(np.median(v)) if v else None) for f, v in vals.items()}
            med[kid]['laps'] = n
    return med


def to_document(race: str, laps: dict, med: dict, contacts_path: Path, reg_path: Path, zones_path: Path) -> dict:
    lap_docs = {}
    for lap, item in laps.items():
        rows = []
        for r in item['rows']:
            m = med.get(r['kerb'], {})
            delta = {f: (None if r[f] is None or m.get(f) is None else rnd(f, r[f] - m[f])) for f in DELTA_FIELDS}
            c = r['contact']
            rows.append({'kerb': r['kerb'], **{f: rnd(f, r[f]) for f in ROUND},
                         'contact': None if c is None else {'class': c['cls'], 'bin': c['bin'], 'peakRollRms': c['peak']},
                         'labelCheck': label_check(r),
                         'deltaVsSessionMedian': delta})
        lap_docs[str(lap)] = rows
    geo_path = TRACK_DIR / 'cg_study/geometry.json'
    return {
        'version': 1,
        'kind': 'apex-kpi',
        'raceId': race,
        'trackId': TRACK_ID,
        'definition': {
            'gBlockFront': 'm; inner front tyre outer edge to the road-side start of the red/white block band, minimum over the pass (negative = on the blocks)',
            'gBlockRear': 'm; same for the inner rear tyre',
            'gPaintFront': 'm; inner front tyre outer edge to the road-side start of the kerb paint (white strip = track-limit line)',
            'gPaintRear': 'm; same for the inner rear tyre',
            'apexStation': 'm; CG road station where gBlockFront is minimal (clipping point)',
            'apexT': 's; recorded lap t (GPS time base of the shipped lap JSON, playback clock) at the clipping point',
            'apexLapTime': 's; apexT + meta.first_sample_after_lap_start_seconds (time since the timing line, as the HUD lap clock)',
            'onBlockLengthM': 'm; path length of the inner front tyre while gBlockFront < 0',
            'exitOuterGapM': f'm; outer tyres vs the opposite road edge (paint start at kerbs), {EXIT_WINDOW_M[0]:+.0f} m to {EXIT_WINDOW_M[1]:+.0f} m from the kerb end (negative = beyond)',
            'contact': 'IMU label of this pass from kerb_contacts.json (class strong/silent/mild, strength bin low/mid/high = 弱/中/強); no left/right attribution',
            'labelCheck': f'IMU label vs geometry: strong-but-off = strong label with min(gBlockFront, gBlockRear) > {ON_TOL} m; silent-but-deep = silent label with it < {SILENT_DEEP - ON_TOL:.2f} m (a GPS/course disagreement, not a verdict); ok; null for mild',
            'deltaVsSessionMedian': 'value minus the median of the same corner over the distributed laps of this session (primary comparison: a common GPS bias cancels)',
            'pass': (f'samples whose CG station lies within the kerb station range +-{PASS_MARGIN_M:.0f} m; a gap counts only where '
                     'the tyre projects onto the kerb line between its end vertices'),
            'wheelPaths': '50 Hz cubic spline of the registered GPS track; heading from the spline derivative; tyre outer edges at +-(track + tyre width)/2, axles at +-wheelbase/2 from the antenna (assumed at the vehicle centre)',
        },
        'absoluteUncertaintyMeters': ABS_UNCERTAINTY_M,
        'absoluteUncertaintyReason': ABS_UNCERTAINTY_REASON,
        'primaryComparison': 'deltaVsSessionMedian',
        'deltaCaveat': 'Each lap has its own registration offset; a lap-specific GPS error (e.g. 7/30 L2 at 3250-3600 m) is not cancelled by the delta.',
        'registration': {'source': rel(reg_path), 'sha256': sha256(reg_path),
                         'note': ('SHIPPED track-limit registration; the opt-in kerb-contact candidate is not used here'
                                  if reg_path.name == 'gps_registration.json' else 'EVALUATION run with a non-shipped registration')},
        'inputs': {
            'kerbZones': {'source': rel(zones_path), 'sha256': sha256(zones_path)},
            'kerbContacts': {'source': rel(contacts_path), 'sha256': sha256(contacts_path)} if contacts_path.exists() else None,
            'roadGeometry': {'source': rel(geo_path), 'sha256': sha256(geo_path)},
            'vehicleProfile': {'source': rel(VEHICLE_PATH), 'sha256': sha256(VEHICLE_PATH)},
            'laps': {str(lap): {'source': rel(lap_path(race, lap)), 'sha256': item['sha']} for lap, item in laps.items()},
        },
        'tool': 'scripts/quality/apex_kpi/build_apex_kpi.py',
        'surveyTruth': False,
        'corners': [{'kerb': kid, 'name': NAMES[kid], 'order': i} for i, kid in enumerate(INSIDE)],
        'sessionMedian': {kid: {**{f: (None if v.get(f) is None else rnd(f, v[f])) for f in DELTA_FIELDS}, 'laps': v['laps']}
                          for kid, v in med.items()},
        'laps': lap_docs,
    }


def context():
    track = json.loads((TRACK_DIR / 'track.json').read_text(encoding='utf-8'))
    geo = base.load_vehicle_geometry(VEHICLE_PATH)
    return track['origin'], geo


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--race', choices=RACES, action='append', help='default: both Fuji races')
    ap.add_argument('--zones', type=Path, default=ZONES_PATH)
    ap.add_argument('--out-dir', type=Path, default=None, help='default: public/data/races/<race>/')
    ap.add_argument('--eval-out', type=Path, default=None,
                    help='write label-consistency metrics (and the legacy course v2 comparison when --legacy-zones is given)')
    ap.add_argument('--legacy-zones', type=Path, default=None, help='course_v2_kerbs.json for the before/after comparison')
    ap.add_argument('--registration-file', default='gps_registration.json',
                    help='registration file name in the race dir (evaluation of e.g. gps_registration_kerb.json; '
                         'write such runs to --out-dir, the shipped apex_kpi.json uses the shipped file)')
    args = ap.parse_args()
    origin, geo = context()
    zones = load_zones(args.zones)
    limits = zoned_limits(base.load_limits(TRACK_DIR, 0.0), zones)
    legacy = None
    if args.legacy_zones:
        lz = load_zones(args.legacy_zones, legacy_v2=True)
        legacy = (lz, zoned_limits(base.load_limits(TRACK_DIR, 0.0), lz))
    evaluation = {}
    for race in args.race or RACES:
        race_dir = ROOT / f'public/data/races/{race}'
        contacts_path = race_dir / 'kerb_contacts.json'
        contacts = json.loads(contacts_path.read_text(encoding='utf-8')) if contacts_path.exists() else None
        if contacts is None:
            print(f'WARNING {race}: {rel(contacts_path)} missing; contact labels left empty (run export_inputs.py first)')
        reg_file = race_dir / args.registration_file
        if not reg_file.exists():
            print(f'skip {race}: {rel(reg_file)} missing')
            continue
        offsets, reg_path = registration_offsets(race, reg_file)
        laps = build_race(race, zones, limits, geo, origin, contacts, offsets)
        med = session_medians(laps)
        doc = to_document(race, laps, med, contacts_path, reg_path, args.zones)
        out_dir = args.out_dir or race_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / 'apex_kpi.json'
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        rows = sum(len(v) for v in doc['laps'].values())
        print(f'{race}: {len(doc["laps"])} laps, {rows} corner passes -> {rel(out)}')
        if args.eval_out:
            ev = {'shippedRegistrationV3Zones': consistency(doc['laps'])}
            if legacy:
                old = build_race(race, legacy[0], legacy[1], geo, origin, contacts, offsets)
                per_kerb = {}
                for lap, item in laps.items():
                    olds = {r['kerb']: r for r in old[lap]['rows']}
                    for r in item['rows']:
                        o = olds.get(r['kerb'])
                        if o and r['gBlockFront'] is not None and o['gBlockFront'] is not None:
                            per_kerb.setdefault(r['kerb'], []).append(r['gBlockFront'] - o['gBlockFront'])
                ev['legacyZonesNote'] = 'course_v2_kerbs.json with the assumed Dunlop-R block (paint + 0.42 m), same code and labels'
                ev['gBlockFrontChangeV3MinusV2'] = {k: {'median': round(float(np.median(v)), 2),
                                                        'min': round(float(np.min(v)), 2), 'max': round(float(np.max(v)), 2),
                                                        'n': len(v)} for k, v in per_kerb.items()}
                old_doc = {str(lap): [{'kerb': r['kerb'], 'gBlockFront': r['gBlockFront'], 'gBlockRear': r['gBlockRear'],
                                       'contact': None if r['contact'] is None else {'class': r['contact']['cls']}}
                                      for r in item['rows']] for lap, item in old.items()}
                ev['shippedRegistrationV2Zones'] = consistency(old_doc)
            spread = {}
            for kid in INSIDE:
                d = [r['deltaVsSessionMedian']['gBlockFront'] for rows_ in doc['laps'].values() for r in rows_
                     if r['kerb'] == kid and r['deltaVsSessionMedian']['gBlockFront'] is not None]
                if d:
                    spread[kid] = {'n': len(d), 'maxAbsDelta': round(float(np.max(np.abs(d))), 2)}
            ev['gBlockFrontDeltaSpread'] = spread
            evaluation[race] = ev
            print(race, 'consistency', {k: v for k, v in ev['shippedRegistrationV3Zones'].items() if k != 'contradictions'},
                  ev['shippedRegistrationV3Zones']['contradictions'])
            if legacy:
                print(race, 'legacy v2 zones', {k: v for k, v in ev['shippedRegistrationV2Zones'].items() if k != 'contradictions'})
    if args.eval_out:
        args.eval_out.mkdir(parents=True, exist_ok=True)
        (args.eval_out / 'apex_kpi_eval.json').write_text(json.dumps(evaluation, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        print('evaluation ->', rel(args.eval_out / 'apex_kpi_eval.json'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
