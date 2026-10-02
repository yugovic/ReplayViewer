"""Kerb zones v3 for Fuji: paint start / block start / outer edge for the 25 CG kerbs + the Dunlop-right kerb
that is missing from the CG (BACKLOG GPS-REG-14, data part).

Definitions (track local frame, x east, z south, metres)
  paint : road-side start of the kerb paint = white strip (track-limit line)
  block : road-side start of the red/white block band (the first block row)
  outer : outer end of the block band (outer edge of the last block row)

Detector (deliberately different from the validation checker in kerb_zones_validate.py)
  * cross-sections every road station (0.5 m) along a smoothed reference line, sampled every 0.04 m in Lab;
  * paint: first sustained CIE76 colour distance from the local asphalt reference, located at the sub-sample
    peak of the outward colour-distance gradient;
  * block/outer: the red/white band is measured from an along-track ENVELOPE of redness (80th percentile of
    a* over +-2.5 m of kerb arc length). Red and white blocks alternate (and kerbs 15/23 have staggered double
    rows), so a single cross-section through a white block cannot separate the white strip from a white block;
    the envelope carries the band edges across white blocks. block = road-side half-level crossing of the
    envelope, outer = outer half-level crossing;
  * robust along-track cleaning (Hampel, 2.5 m) and Gaussian smoothing (sigma 0.75 m) in offset space;
    two passes (pass 2 re-centres the cross-sections on the pass-1 paint line, which removes the CG zig-zag);
  * stations where a line cannot be observed are filled (interpolation, or paint + local median width where the
    red blocks have ended but the white paint continues) and reported as gaps.

Writes:  public/data/tracks/fuji/cg_study/kerb_zones.json   (or --out)
         $APEX_WORK/kerb_zones_v3_raw.json                  (per-station raw/clean offsets, for audit)
Input: geometry.json (read only; its SHA is used by the shipped registration), VIRTUAL SHIZUOKA 2019 ortho.

Rebuild:  python kerb_zones_v3.py && python kerb_zones_validate.py --write-back
          python -m pytest test_kerb_zones.py      (data contract, no ortho needed)
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter1d, median_filter

import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from paths import ROOT, WORK  # noqa: E402
import ortho_common as C  # noqa: E402

U = np.arange(-3.0, 6.0001, 0.04)          # + toward the road centre from the reference line
DU = 0.04
ENV_HALF_M = 2.5                             # along-track envelope half window (kerb arc length)
ENV_PCT = 80
RED_MIN = 14.0                               # a* above asphalt for a red block plateau
DUNLOP_SCAN = (2950.0, 3015.0)
DUNLOP_ID = 'kDunlopR'


def arclen(xy: np.ndarray) -> np.ndarray:
    return np.concatenate([[0.], np.cumsum(np.linalg.norm(np.diff(xy, axis=0), axis=1))])


def subsample_peak(g: np.ndarray, i: int) -> float:
    if 0 < i < len(g) - 1:
        a, b, c = g[i - 1], g[i], g[i + 1]
        den = a - 2 * b + c
        if den < 0:
            return float(np.clip(0.5 * (a - c) / den, -0.5, 0.5))
    return 0.0


def cross(prof: np.ndarray, level: float, i_above: int, i_below: int) -> float:
    """Linear interpolation of the u where prof crosses `level` between two adjacent samples."""
    q0, q1 = prof[i_below], prof[i_above]
    t = (level - q0) / (q1 - q0) if q1 != q0 else 0.5
    return float(U[i_below] + t * (U[i_above] - U[i_below]))


def detect_paint(lab: np.ndarray, asphalt: tuple[float, float], search: tuple[float, float]):
    """Per-station paint start (offset u) from the colour-distance gradient. Returns (u, contrast) or (nan, 0)."""
    m = (U >= asphalt[0]) & (U <= asphalt[1])
    ref = np.median(lab[m], axis=0)
    d = gaussian_filter1d(np.linalg.norm(lab - ref, axis=1), 1.5)
    base = float(np.median(d[m])); noise = float(1.4826 * np.median(np.abs(d[m] - base)))
    thr = max(base + 15.0, base + 6.0 * noise)
    idx = np.flatnonzero((U <= search[1]) & (U >= search[0]))[::-1]     # road side -> outward
    hit = None
    for k in range(len(idx) - 4):
        if np.all(d[idx[k:k + 4]] > thr):
            hit = idx[k]; break
    if hit is None:
        return np.nan, 0.0, ref
    g = -np.gradient(d, DU)                                             # increase going outward
    win = np.flatnonzero((U >= U[hit] - 0.08) & (U <= U[hit] + 0.40))
    i = int(win[np.argmax(g[win])])
    u = float(U[i] + subsample_peak(g, i) * DU)
    plateau = np.flatnonzero((U <= u - 0.10) & (U >= u - 0.30))
    contrast = float(np.median(d[plateau]) - base) if len(plateau) else 0.0
    if contrast < 12.0:
        return np.nan, contrast, ref
    return u, contrast, ref


def hampel_smooth(s: np.ndarray, u: np.ndarray, ok: np.ndarray, half_m=2.5, tol=0.20, sigma_m=0.75):
    """Robust along-track cleaning in offset space. Returns (smooth, accepted mask)."""
    u = np.asarray(u, float).copy(); ok = ok & np.isfinite(u)
    if ok.sum() < 4:
        return np.full_like(u, np.nan), ok
    filled = np.interp(s, s[ok], u[ok])
    step = max(np.median(np.diff(s)), 1e-3)
    med = median_filter(filled, size=max(3, int(round(2 * half_m / step)) | 1), mode='nearest')
    good = ok & (np.abs(u - med) <= tol)
    if good.sum() < 4:
        good = ok
    filled = np.interp(s, s[good], u[good])
    # uniform resampling for the Gaussian (stations may be unevenly spaced in arc length)
    g = np.arange(s[0], s[-1] + 1e-9, 0.1)
    sm = gaussian_filter1d(np.interp(g, s, filled), sigma_m / 0.1, mode='nearest')
    return np.interp(s, g, sm), good


def gaps(stations: np.ndarray, ok: np.ndarray) -> list[list[float]]:
    out = []; i = 0
    while i < len(ok):
        if not ok[i]:
            j = i
            while j + 1 < len(ok) and not ok[j + 1]:
                j += 1
            out.append([float(stations[i]), float(stations[j])]); i = j + 1
        else:
            i += 1
    return out


def measure(ref_xy, stations, side, patch, asphalt, search):
    """One detection pass along a reference line. Returns per-station raw offsets and diagnostics."""
    n_in = C.inward_normals(ref_xy, stations, side, sigma_pts=4)
    X = ref_xy[:, :1] + U[None, :] * n_in[:, :1]
    Z = ref_xy[:, 1:] + U[None, :] * n_in[:, 1:]
    lab = patch.sample(X.ravel(), Z.ravel()).reshape(len(stations), len(U), 3)
    up = np.full(len(stations), np.nan); contrast = np.zeros(len(stations)); aref = np.zeros(len(stations))
    for i in range(len(stations)):
        up[i], contrast[i], ref = detect_paint(lab[i], asphalt, search)
        aref[i] = ref[1]
    s_arc = arclen(ref_xy)
    a = gaussian_filter1d(lab[:, :, 1] - aref[:, None], 1.0, axis=1)
    # along-track envelope of redness
    env = np.empty_like(a)
    for i in range(len(stations)):
        w = np.abs(s_arc - s_arc[i]) <= ENV_HALF_M
        env[i] = np.percentile(a[w], ENV_PCT, axis=0)
    return dict(n_in=n_in, lab=lab, up=up, contrast=contrast, a=a, env=env, s_arc=s_arc)


def band_edges(env_row: np.ndarray, a_row: np.ndarray, u_paint: float):
    """Block start / outer edge from one envelope row, searched outward of the (smoothed) paint line."""
    res = dict(ub=np.nan, uo=np.nan, plateau=0.0, red_here=False)
    if not np.isfinite(u_paint):
        return res
    band = np.flatnonzero((U <= u_paint + 0.10) & (U >= u_paint - 2.8))[::-1]   # road side -> outward
    if len(band) < 5:
        return res
    P = float(np.percentile(env_row[band], 95))
    res['plateau'] = P
    res['red_here'] = bool(np.sum(a_row[band] > 0.5 * max(P, RED_MIN)) >= 4)
    if P < RED_MIN:
        return res
    e = env_row[band]
    lv = 0.5 * P
    above = np.flatnonzero(e > lv)
    if not len(above):
        return res
    k0 = above[0]
    if k0 == 0:
        return res                                  # red already at the paint line: ambiguous, leave unobserved
    k1 = k0                                         # end of the red band: first outward run of >= 0.20 m below lv
    while k1 < len(band) - 5 and not np.all(e[k1:k1 + 5] < lv):
        k1 += 1
    plateau = float(np.median(e[k0:k1])) if k1 > k0 + 2 else P
    res['plateau'] = max(P, plateau)
    # block start: half level between the white strip (road side of the band) and the red plateau
    strip = float(np.median(e[:max(1, k0 - 2)]))
    lvb = 0.5 * (plateau + strip)
    kb = next((k for k in range(1, k1 + 1) if e[k] > lvb and e[k - 1] <= lvb), None)
    if kb is not None:
        res['ub'] = cross(env_row, lvb, band[kb], band[kb - 1])
    # outer: half level between the red plateau and what lies outside (grass / green paint / runoff)
    if k0 < k1 < len(band) - 5:
        outside = float(np.median(e[k1 + 2:min(len(band), k1 + 12)]))
        lvo = 0.5 * (plateau + outside)
        cands = [k for k in range(k0 + 1, min(len(band), k1 + 4)) if e[k] <= lvo and e[k - 1] > lvo]
        ko = cands[-1] if cands else None           # last crossing at the end of the band (double rows: both)
        if ko is not None:
            res['uo'] = cross(env_row, lvo, band[ko - 1], band[ko])
    return res


def run_kerb(ref_xy, stations, side, patch, pass1_paint_offset):
    # pass 1: wide search around the CG inner line (or road edge) to find the paint line
    p1 = measure(ref_xy, stations, side, patch, asphalt=(pass1_paint_offset + 2.2, pass1_paint_offset + 4.6),
                 search=(-2.8, 3.5))
    up1, ok1 = hampel_smooth(p1['s_arc'], p1['up'], np.isfinite(p1['up']), half_m=3.0, tol=0.35, sigma_m=1.0)
    if not np.isfinite(up1).any():
        raise RuntimeError('pass 1 found no paint')
    paint1 = ref_xy + up1[:, None] * p1['n_in']
    ref2 = C.smooth_polyline(paint1, 2.0)
    # pass 2: re-centred on the pass-1 paint line
    p2 = measure(ref2, stations, side, patch, asphalt=(0.8, 3.0), search=(-1.2, 1.2))
    s = p2['s_arc']
    up, okp = hampel_smooth(s, p2['up'], np.isfinite(p2['up']))
    ub_raw = np.full(len(s), np.nan); uo_raw = np.full(len(s), np.nan)
    plateau = np.zeros(len(s)); red_here = np.zeros(len(s), bool)
    for i in range(len(s)):
        r = band_edges(p2['env'][i], p2['a'][i], up[i])
        ub_raw[i], uo_raw[i], plateau[i], red_here[i] = r['ub'], r['uo'], r['plateau'], r['red_here']
    okb0 = np.isfinite(ub_raw); oko0 = np.isfinite(uo_raw)
    # stations with blocks: red envelope present; trim where the red blocks have ended (white paint continues)
    # block range = first..last station whose own cross-section shows a red block, +-1 m for a closing white
    # block; beyond it the red blocks have ended (white paint may continue) and block/outer are not observed
    ri = np.flatnonzero(red_here)
    in_range = np.zeros(len(s), bool)
    if len(ri):
        in_range = (s >= s[ri[0]] - 1.0) & (s <= s[ri[-1]] + 1.0)
    has_red = (plateau >= RED_MIN) & in_range
    ub, okb = hampel_smooth(s, ub_raw, okb0 & has_red)
    uo, oko = hampel_smooth(s, uo_raw, oko0 & has_red)
    # fill outside the block range: paint + local median strip width / band width
    strip = up - ub; band = ub - uo
    first_b = np.flatnonzero(okb); fill_note = []
    if len(first_b):
        b0, b1 = first_b[0], first_b[-1]
        for lo, hi, ref_i in ((0, b0, slice(b0, min(len(s), b0 + 11))), (b1 + 1, len(s), slice(max(0, b1 - 10), b1 + 1))):
            if hi > lo:
                ws = float(np.nanmedian(strip[ref_i])); wb = float(np.nanmedian(band[ref_i]))
                ub[lo:hi] = up[lo:hi] - ws; uo[lo:hi] = ub[lo:hi] - wb
                fill_note.append(dict(range=[float(stations[lo]), float(stations[hi - 1])], stripWidth=round(ws, 3),
                                      bandWidth=round(wb, 3)))
        okb[:b0] = False; okb[b1 + 1:] = False; oko[:b0] = False; oko[b1 + 1:] = False
        if fill_note:                                # remove the small step where the fill meets the measured line
            g = np.arange(s[0], s[-1] + 1e-9, 0.1)
            ub = np.interp(s, g, gaussian_filter1d(np.interp(g, s, ub), 0.5 / 0.1, mode='nearest'))
            uo = np.interp(s, g, gaussian_filter1d(np.interp(g, s, uo), 0.5 / 0.1, mode='nearest'))
    out = dict(stations=stations, ref=ref2, n_in=p2['n_in'], s_arc=s,
               up_raw=p2['up'], ub_raw=ub_raw, uo_raw=uo_raw, up=up, ub=ub, uo=uo,
               okp=okp, okb=okb, oko=oko, plateau=plateau, red_here=red_here, contrast=p2['contrast'],
               fill=fill_note, pass1_ok=float(ok1.mean()))
    out['paint'] = ref2 + up[:, None] * p2['n_in']
    out['block'] = ref2 + ub[:, None] * p2['n_in']
    out['outer'] = ref2 + uo[:, None] * p2['n_in']
    return out


def trim_dunlop(res):
    """Kerb extent = stations with red blocks (+-1 m for a closing white block) where the paint is observed."""
    st = res['stations']; red = res['red_here'] & res['okp']
    idx = np.flatnonzero(red)
    if not len(idx):
        raise RuntimeError('Dunlop-R: no red blocks found')
    s0 = st[idx[0]] - 1.0; s1 = st[idx[-1]] + 1.0
    return (st >= s0) & (st <= s1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', type=Path, default=ROOT / 'public/data/tracks/fuji/cg_study/kerb_zones.json')
    ap.add_argument('--raw', type=Path, default=WORK / 'kerb_zones_v3_raw.json')
    ap.add_argument('--env-half', type=float, default=ENV_HALF_M, help='envelope half window, m (sensitivity runs)')
    ap.add_argument('--env-pct', type=float, default=ENV_PCT, help='envelope percentile (sensitivity runs)')
    args = ap.parse_args()
    globals()['ENV_HALF_M'] = args.env_half; globals()['ENV_PCT'] = args.env_pct
    geo_sha = hashlib.sha256(C.GEO_PATH.read_bytes()).hexdigest()
    kerbs_out, raw_out = [], []
    jobs = []
    for k, c in enumerate(C.KERBS):
        r = np.array(c['rows'], float)
        ref = C.smooth_polyline(r[:, 1:3], 6.0)               # removes the CG zig-zag (kerbs 15/23) for pass 1
        jobs.append((f'k{k}', c['side'], r[:, 0], ref, 0.4, False, r))
    st = np.arange(DUNLOP_SCAN[0], DUNLOP_SCAN[1] + 1e-9, C.STEP)
    edge = np.array([C.road_row(s)[9:11] for s in st], float)    # CG right road edge (has a rejected span)
    jobs.append((DUNLOP_ID, 'right', st, C.smooth_polyline(edge, 6.0), 0.0, True, None))
    for kid, side, stations, ref, p1off, added, rows in jobs:
        patch = C.patch_for(ref, margin=12.)
        res = run_kerb(ref, stations, side, patch, p1off)
        if added:
            keep = trim_dunlop(res)
            sel = np.flatnonzero(keep)
            st2 = stations[sel]
            ref2 = C.smooth_polyline(res['ref'][sel], 0.0)
            res = run_kerb(ref2, st2, side, patch, 0.0)          # re-run on the trimmed extent
            stations = st2
        n = len(stations)
        okp, okb, oko = res['okp'], res['okb'], res['oko']
        notes = []
        if kid in ('k15', 'k23'):
            notes.append('staggered double-row blocks: CG inner line zig-zags ~1 m; block/outer from the along-track '
                         'redness envelope of both rows (white inner-row areas are taken as blocks; flat paint cannot '
                         'be told apart at 0.2 m)')
        if added:
            notes.append(f'missing from the CG; red/white blocks visible in the 2019 ortho; block start detected '
                         f'(replaces the v2 assumption paint+0.42 m); extent = first..last red block +-1 m '
                         f'({stations[0]:.1f}-{stations[-1]:.1f} m; v2 2964.0-3004.0 m included white-only paint)')
        if rows is not None:
            # where the CG kerb inner line departs from the detected block start (approx., along the v3 normal)
            dev = np.sum((rows[:, 1:3] - res['block']) * res['n_in'], axis=1)
            far = np.abs(dev) > 0.5
            spans = gaps(stations, ~far)
            if spans:
                desc = (f'{len(spans)} short spans (zig-zag)' if len(spans) > 3 else
                        ', '.join(f'{a:.1f}-{b:.1f} m' for a, b in spans))
                notes.append(f'CG inner line departs > 0.5 m from the detected block start at {desc} '
                             f'(max {dev[np.argmax(np.abs(dev))]:+.2f} m, + = CG road-side)')
        for f in res['fill']:
            notes.append(f"no red blocks at {f['range'][0]:.1f}-{f['range'][1]:.1f} m (white paint only or kerb end): "
                         f"block/outer = paint - {f['stripWidth']:.2f} m / - {f['bandWidth']:.2f} m (not observed)")
        strip = np.linalg.norm(res['paint'] - res['block'], axis=1)
        band = np.linalg.norm(res['block'] - res['outer'], axis=1)
        kerbs_out.append(dict(
            id=kid, side=side, addedOutsideCg=added,
            station=[round(float(s), 1) for s in stations],
            paint=np.round(res['paint'], 3).tolist(), block=np.round(res['block'], 3).tolist(),
            outer=np.round(res['outer'], 3).tolist(),
            observed=dict(paint=round(float(okp.mean()), 3), block=round(float(okb.mean()), 3),
                          outer=round(float(oko.mean()), 3),
                          paintGaps=gaps(stations, okp), blockGaps=gaps(stations, okb), outerGaps=gaps(stations, oko)),
            stripWidthMedian=round(float(np.median(strip[okb])), 3) if okb.any() else None,
            bandWidthMedian=round(float(np.median(band[okb & oko])), 3) if (okb & oko).any() else None,
            notes='; '.join(notes)))
        raw_out.append(dict(id=kid, side=side, station=stations.tolist(),
                            ref=np.round(res['ref'], 3).tolist(), normal=np.round(res['n_in'], 5).tolist(),
                            up_raw=np.round(res['up_raw'], 3).tolist(), ub_raw=np.round(res['ub_raw'], 3).tolist(),
                            uo_raw=np.round(res['uo_raw'], 3).tolist(), up=np.round(res['up'], 3).tolist(),
                            ub=np.round(res['ub'], 3).tolist(), uo=np.round(res['uo'], 3).tolist(),
                            okp=okp.astype(int).tolist(), okb=okb.astype(int).tolist(), oko=oko.astype(int).tolist(),
                            plateau=np.round(res['plateau'], 1).tolist(), redHere=res['red_here'].astype(int).tolist(),
                            contrast=np.round(res['contrast'], 1).tolist()))
        print(f"{kid:9s} {side:5s} n={n:3d} paint obs {okp.mean():.2f} block obs {okb.mean():.2f} outer obs {oko.mean():.2f}"
              f"  strip {kerbs_out[-1]['stripWidthMedian']}  band {kerbs_out[-1]['bandWidthMedian']}", flush=True)
    doc = dict(
        version=3, trackId='fuji',
        definition=('Per kerb, three lines sampled at road stations (0.5 m grid of cg_study/geometry.json), XZ metres in '
                    'the track local frame (x east, z south). paint = road-side start of the kerb paint (white strip, '
                    'track-limit line); block = road-side start of the red/white block band; outer = outer end of the '
                    'block band. observed = fraction of stations where the line was measured in the ortho; gaps list '
                    'filled station ranges [from, to] (interpolated, or paint minus the local median width where the '
                    'red blocks have ended).'),
        source=('VIRTUAL SHIZUOKA 2019 LP orthophoto 20 cm (CC BY 4.0, Shizuoka Prefecture). 2019 imagery, not verified '
                'against the 2020 sessions.'),
        sourceUrl=C.META.get('sourceUrl', ''),
        license='CC BY 4.0',
        surveyTruth=False,
        geometrySha256=geo_sha,
        method=('scripts/quality/apex_kpi/kerb_zones_v3.py: colour-distance gradient (paint) and along-track redness '
                'envelope (block/outer) on 0.04 m Lab cross-sections, Hampel + Gaussian smoothing; validated with the '
                'independent Lab half-level checker scripts/quality/apex_kpi/kerb_zones_validate.py'),
        limitations=['Ortho pixel 0.2 m; ortho georeferencing error (~0.2-0.3 m) is not included in the residuals.',
                     'Lines are image boundaries of paint, not surveyed kerb geometry; block height not measured.',
                     'Block start is measured on red blocks; across white blocks it is carried by the along-track '
                     'envelope (white blocks and the white strip cannot be separated by colour).',
                     'Station ranges of k0..k24 are the CG kerb ranges; where the red blocks end earlier the block/outer '
                     'lines are filled (blockGaps).',
                     'Not a survey and not GPS ground truth.'],
        kerbs=kerbs_out)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n',
                        encoding='utf-8')
    args.raw.write_text(json.dumps(dict(U='offset along inward normal of ref (m)', kerbs=raw_out)), encoding='utf-8')
    print('wrote', args.out, args.out.stat().st_size, 'bytes; raw', args.raw)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
