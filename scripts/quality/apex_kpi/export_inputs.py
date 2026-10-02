"""Export the publishable kerb-contact labels (BACKLOG GPS-REG-15/16).

  public/data/races/<race>/kerb_contacts.json     (--write-public)
  <out-dir>/kerb_contacts_<race>.json             (default, out-dir = APEX_WORK)

Logger-native roll-rate bursts per lap, from eval_v2.detector(): roll-rate
high-pass (>~3 Hz) RMS over 0.2 s; an event is RMS > 1.0 deg/s, merged across
gaps < 0.3 s. Event times are moved onto the lap's GPS time base (lap t of the
shipped JSON) with the same logger-clock shift the shipped channels use
(7/30: meta.channel_time_alignment.shiftSeconds per lap, GPS-REG-08; 7/29: 0,
the timing reference session whose lap channels are not re-timed, so its IMU
events stay on the same logger clock as its shipped aps/brake/steer/accx/accy).

Each event is attributed to ONE kerb (ids of public/data/tracks/fuji/cg_study/kerb_zones.json)
by the CG station at its RMS peak: an inside kerb whose pass window holds the
peak time and whose station range (+-3 m) holds the peak station, the one the
station lies deepest inside first; else any kerb zone there; else none.
One label per inside-kerb pass:
  strong  an event attributed to this kerb has peak >= 1.5
  silent  roll-rate RMS peak over the pass <= 0.8
  mild    otherwise
(The v3 report counted any event overlapping the pass and filtered by side;
without the side filter that rule leaks the 3540 m left-hander bursts into
the next right-hander k23, so the peak-station attribution replaces it.)
Strength bins for display: 弱 low 1.0-1.5, 中 mid 1.5-3.0, 強 high >= 3.0.

No left/right attribution is exported: the 4-wheel speed ripple ratio is
confounded with the corner direction (inner wheels ripple more without any
kerb; docs/fuji-apex-kerb-kpi-2026-10-02.md), so eval_v2's side output is
discarded and the strong rule no longer filters by side. The pass window uses
the SHIPPED registration only to find where the car is along the lap.

Raw XRK/video are never exported. The source XRK is identified by SHA-256 only
when FUJI_WET_XRK / FUJI_DRY_XRK point at it. Needs APEX_WORK with
xrk_<race>.npz (xrk_extract.py + xrk_map.py) and course_v2_kerbs.json (eval_v2 import).

  --eval-out DIR  also writes label_changes.json: published labels vs the
                  v3-report labels (eval_v2.json) and vs the overlap /
                  side-filter / session-shift variants, to attribute every change.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import ROOT, WORK, XRK  # noqa: E402
import eval_v2 as E  # noqa: E402  (detector + XRK work dir)
import build_apex_kpi as K  # noqa: E402  (zones, registered 50 Hz paths)

EVENT_MIN, STRONG_MIN, SILENT_MAX = 1.0, 1.5, 0.8
OVERLAP_PAD_S = 0.2
BINS = [('low', '弱', 1.0, 1.5), ('mid', '中', 1.5, 3.0), ('high', '強', 3.0, None)]
SESSION_SHIFT = E.SHIFT          # 7/29 0.014 s, 7/30 0.507 s (gyro-vs-GPS session estimates; evaluation variants only)
REFERENCE_SHIFT_SOURCE = ('0 s: reference session for the logger-clock timing (GPS-REG-08); its shipped lap channels are not '
                          're-timed, so the IMU events use the same logger clock as the lap JSON channels')


def strength_bin(peak: float):
    for bid, _, lo, hi in BINS:
        if peak >= lo and (hi is None or peak < hi):
            return bid
    return None


def lap_shift(meta: dict, race: str):
    """Logger-clock shift of the lap: the one its shipped channels use (0 when they are not re-timed)."""
    cta = meta.get('channel_time_alignment') or {}
    s = cta.get('shiftSeconds')
    if isinstance(s, (int, float)):
        method = cta.get('method')
        estimate = (cta.get('estimate') or {}).get('method')
        detail = ', '.join(x for x in (method, estimate) if x)
        return float(s), ('meta.channel_time_alignment.shiftSeconds (scripts/quality/align-fuji-channel-timing.py'
                          + (f'; {detail}' if detail else '') + ')')
    return 0.0, REFERENCE_SHIFT_SOURCE


def file_sha(path: str):
    p = Path(path) if path else None
    if not p or not p.is_file():
        return None
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def overlap_labels(events, windows, rt, rrms, side_filter=False, kerb_side=None):
    """Evaluation only. Overlap rule of the v3 report: any event overlapping the pass (+-0.2 s) counts;
    side_filter additionally keeps only events whose wheel-speed side matches the kerb (retracted)."""
    out = {}
    for kid, (tw0, tw1) in windows.items():
        evs = [e for e in events if e['t1'] >= tw0 - OVERLAP_PAD_S and e['t0'] <= tw1 + OVERLAP_PAD_S]
        cand = [e for e in evs if e['side'] in (kerb_side[kid], 'both/unclear')] if side_filter else evs
        if cand and max(e['peak'] for e in cand) >= STRONG_MIN:
            out[kid] = 'strong'
        else:
            out[kid] = 'silent' if window_peak(rt, rrms, tw0, tw1) <= SILENT_MAX else 'mild'
    return out


def window_peak(rt, rrms, tw0, tw1):
    mm = (rt >= tw0) & (rt <= tw1)
    return float(rrms[mm].max()) if mm.any() else 0.0


def margin(zone, s):
    """Metres the station lies inside the kerb's station range (negative = outside)."""
    return min(s - zone['s0'], zone['s1'] - s)


def attribute(e, windows, lp, zones, rt, rrms):
    """One kerb per event, from the station at its RMS peak (no side information):
    an inside kerb whose pass window holds the peak time and whose range (+-3 m) holds the peak station,
    deepest inside its range first; else any kerb zone at that station; else None.
    Returns (kerb, insidePass, peak time, peak station, other candidate kerbs)."""
    m = (rt >= e['t0']) & (rt <= e['t1'])
    tp = float(rt[m][np.argmax(rrms[m])]) if m.any() else e['t0']
    sp = lp.station_at(tp)
    inside = [(margin(zones[k], sp), k) for k, (tw0, tw1) in windows.items()
              if tw0 - OVERLAP_PAD_S <= tp <= tw1 + OVERLAP_PAD_S and margin(zones[k], sp) >= -K.PASS_MARGIN_M]
    if inside:
        inside.sort(reverse=True)
        return inside[0][1], True, tp, sp, [k for _, k in inside[1:]]
    other = sorted(((margin(z, sp), k) for k, z in zones.items() if margin(z, sp) >= -K.PASS_MARGIN_M), reverse=True)
    if other:
        return other[0][1], False, tp, sp, [k for _, k in other[1:]]
    return None, False, tp, sp, []


def attributed_labels(attributed, windows, rt, rrms):
    """Published rule: strong = an event attributed to this kerb has peak >= 1.5; silent = pass peak <= 0.8."""
    out = {}
    for kid, (tw0, tw1) in windows.items():
        evs = [e for e, (k, inside, *_rest) in attributed if inside and k == kid]
        peak_any = window_peak(rt, rrms, tw0, tw1)
        if evs and max(e['peak'] for e in evs) >= STRONG_MIN:
            cls = 'strong'
        elif peak_any <= SILENT_MAX:
            cls = 'silent'
        else:
            cls = 'mild'
        out[kid] = dict(cls=cls, peak=peak_any, events=evs)
    return out


def race_contacts(race, zones, limits, geo, origin, offsets, z, maps):
    laps_out, events_out, shifts, mapping, lap_inputs, variants = {}, {}, {}, {}, {}, {}
    kerb_side = {kid: zones[kid]['side'] for kid in K.INSIDE}
    shift_src = None
    for lap in K.race_laps(race):
        lp = K.LapPaths(race, lap, limits, geo, origin, offsets.get(lap, (0.0, 0.0)))
        shift, shift_src = lap_shift(lp.meta, race)
        tc0 = maps[str(lap)]['tc0']
        events, rt, rrms = E.detector(z, tc0, shift, lp.t_end)
        windows = {kid: w for kid in K.INSIDE if kid in zones and (w := lp.pass_window(zones[kid])) is not None}
        attributed = [(e, attribute(e, windows, lp, zones, rt, rrms)) for e in events]
        labels = attributed_labels(attributed, windows, rt, rrms)
        # evaluation variants (not published)
        ev_s, rt_s, rr_s = E.detector(z, tc0, SESSION_SHIFT[race], lp.t_end)
        att_s = [(e, attribute(e, windows, lp, zones, rt_s, rr_s)) for e in ev_s]
        variants[lap] = {
            'published': {k: v['cls'] for k, v in labels.items()},
            'attributedSessionShift': {k: v['cls'] for k, v in attributed_labels(att_s, windows, rt_s, rr_s).items()},
            'overlapNoSideLapShift': overlap_labels(events, windows, rt, rrms),
            'overlapSideFilterLapShift': overlap_labels(events, windows, rt, rrms, True, kerb_side),
            'overlapSideFilterSessionShift': overlap_labels(ev_s, windows, rt_s, rr_s, True, kerb_side),
            'eventShiftSeconds': round(shift - SESSION_SHIFT[race], 3),
        }
        rows = []
        for kid in K.INSIDE:
            if kid not in labels:
                continue
            p = labels[kid]
            top = max((e['peak'] for e in p['events']), default=None)
            rows.append({'kerb': kid, 'class': p['cls'], 'peakRollRms': round(p['peak'], 2),
                         'bin': strength_bin(top) if top is not None and p['cls'] != 'silent' else None,
                         't0': round(windows[kid][0], 2), 't1': round(windows[kid][1], 2)})
        evs = []
        for e, (kid, inside, tp, sp, alt) in attributed:
            evs.append({'t0': round(e['t0'], 2), 't1': round(e['t1'], 2), 'tPeak': round(tp, 2), 'peak': round(e['peak'], 2),
                        'bin': strength_bin(e['peak']), 'kerb': kid, 'insidePass': inside,
                        'stationAtPeak': round(sp, 1), 'alsoNear': alt})
        laps_out[str(lap)] = rows
        events_out[str(lap)] = evs
        shifts[str(lap)] = round(shift, 3)
        mapping[str(lap)] = {'tc0Ms': maps[str(lap)]['tc0'], 'speedRmsKmh': round(maps[str(lap)]['rmsKmh'], 5)}
        lap_inputs[str(lap)] = {'source': K.rel(lp.path), 'sha256': K.sha256(lp.path)}
    return laps_out, events_out, shifts, shift_src, mapping, lap_inputs, variants


def label_changes(race, variants):
    """Published labels vs the v3 report (eval_v2.json) and per-factor variants."""
    before = {}
    ev2 = ROOT / 'artifacts/apex-kerb-kpi-2026-10-02/eval_v2.json'
    if ev2.exists():
        for r in json.loads(ev2.read_text(encoding='utf-8')).get(race, {}).get('kpi', []):
            kid = 'kDunlopR' if r['kerb'] == 25 else f"k{r['kerb']}"
            before[(r['lap'], kid)] = {'mild/other-side': 'mild'}.get(r['label'], r['label'])
    out = {'counts': {}, 'changedVsReport': [], 'changedBy': {}}
    names = ('published', 'attributedSessionShift', 'overlapNoSideLapShift', 'overlapSideFilterLapShift',
             'overlapSideFilterSessionShift')
    for n in names:
        c = {'strong': 0, 'silent': 0, 'mild': 0}
        for v in variants.values():
            for cls in v[n].values():
                c[cls] += 1
        out['counts'][n] = c
    c = {'strong': 0, 'silent': 0, 'mild': 0}
    for cls in before.values():
        c[cls] += 1
    out['counts']['v3Report'] = c
    for lap, v in variants.items():
        for kid, cls in v['published'].items():
            b = before.get((lap, kid))
            if b is not None and b != cls:
                out['changedVsReport'].append({'lap': lap, 'kerb': kid, 'before': b, 'after': cls,
                                               **{n: v[n][kid] for n in names[1:]}})
    for n in names[1:]:
        out['changedBy'][n] = [{'lap': lap, 'kerb': k, 'published': v['published'][k], n: v[n][k]}
                               for lap, v in variants.items() for k in v['published'] if v['published'][k] != v[n][k]]
    out['eventShiftSecondsVsSession'] = {str(lap): v['eventShiftSeconds'] for lap, v in variants.items()}
    out['passesMissingInReport'] = sorted({f'{lap}:{k}' for lap, v in variants.items() for k in v['published']} -
                                          {f'{lap}:{k}' for (lap, k) in before})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out-dir', type=Path, default=WORK)
    ap.add_argument('--write-public', action='store_true', help='write public/data/races/<race>/kerb_contacts.json')
    ap.add_argument('--eval-out', type=Path, default=None)
    args = ap.parse_args()
    origin, geo = K.context()
    zones = K.load_zones(K.ZONES_PATH)
    limits = K.zoned_limits(K.base.load_limits(K.TRACK_DIR, 0.0), zones)
    evaluation = {}
    for race in K.RACES:
        z = dict(np.load(WORK / f'xrk_{race}.npz'))
        maps = json.loads(str(z['maps']))
        offsets, reg_path = K.registration_offsets(race)
        laps, events, shifts, shift_src, mapping, lap_inputs, variants = race_contacts(
            race, zones, limits, geo, origin, offsets, z, maps)
        doc = {
            'version': 2,
            'kind': 'kerb-contacts',
            'raceId': race,
            'trackId': K.TRACK_ID,
            'definition': ('Kerb-contact labels from the logger internal IMU: roll-rate high-pass (>~3 Hz) RMS over 0.2 s; '
                           f'events = RMS > {EVENT_MIN} deg/s merged across < 0.3 s gaps. Each event is attributed to one kerb by '
                           'the CG station at its RMS peak (inside-kerb pass first; kerb = null when no kerb zone is within 3 m). '
                           f'One label per inside-kerb pass: strong = an event attributed to it has peak >= {STRONG_MIN}, '
                           f'silent = pass peak <= {SILENT_MAX}, otherwise mild. GPS-independent apart from locating the pass/peak.'),
            'strengthBins': [{'id': b, 'label': l, 'minPeak': lo, 'maxPeak': hi} for b, l, lo, hi in BINS],
            'peakUnit': 'deg/s (roll-rate high-pass RMS, 0.2 s window)',
            'sideAttribution': ('not published: the 4-wheel ECU speed-ripple ratio is confounded with the corner direction '
                                '(0.82 in right-handers, 1.31 in left-handers without kerb contact, 7/30); an inside-kerb label '
                                'is read as "the inner wheels hit", never as a measured side'),
            'timeBase': {
                'basis': 'lap t (s) of the shipped lap JSON = GPS time base = viewer playback clock (0 = first GPS sample of the lap)',
                'loggerToGps': 't = (logger_ms - tc0Ms) / 1000 + shiftSeconds',
                'shiftSecondsByLap': shifts,
                'shiftSource': shift_src,
                'evaluationSessionShiftSeconds': SESSION_SHIFT[race],
                'evaluationSessionShiftUse': ('gyro-vs-GPS session estimate, used only for the attributedSessionShift / '
                                              'overlap...SessionShift evaluation variants (label_changes.json); '
                                              'not applied to the published events'),
                'lapMapping': mapping,
                'lapMappingMethod': 'XRK GPS speed vs shipped lap speed (scripts/quality/apex_kpi/xrk_map.py)',
            },
            'labelRule': {'eventMinPeak': EVENT_MIN, 'strongMinPeak': STRONG_MIN, 'silentMaxPeak': SILENT_MAX,
                          'mergeGapSeconds': 0.3, 'overlapPadSeconds': OVERLAP_PAD_S, 'passMarginMeters': K.PASS_MARGIN_M},
            'provenance': {
                'tool': 'scripts/quality/apex_kpi/export_inputs.py',
                'detector': 'scripts/quality/apex_kpi/eval_v2.py detector() (roll rate only; its wheel-speed side output is discarded)',
                'source': 'AiM XRK of the session (local file, not published); IMU via scripts/quality/apex_kpi/xrk_extract.py',
                'sourceSha256': file_sha(XRK.get(race, '')),
                'registrationForPassWindows': {'source': K.rel(reg_path), 'sha256': K.sha256(reg_path)},
                'kerbZones': {'source': K.rel(K.ZONES_PATH), 'sha256': K.sha256(K.ZONES_PATH)},
                'laps': lap_inputs,
            },
            'limitations': [
                'Strength depends on speed and sampling (aliasing); thresholds were set on the 7/30 data.',
                'Non-kerb vibration (brake lock, bumps) is not excluded; events with kerb = null are not attributed to a kerb.',
                'Where an inside and an outside kerb share stations, the label cannot tell which one was hit.',
                '7/29 (wet) has very few strong contacts.',
            ],
            'laps': laps,
            'events': events,
        }
        if args.write_public:
            out = ROOT / f'public/data/races/{race}/kerb_contacts.json'
        else:
            args.out_dir.mkdir(parents=True, exist_ok=True)
            out = args.out_dir / f'kerb_contacts_{race}.json'
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        n_lab = sum(len(v) for v in laps.values())
        n_ev = sum(len(v) for v in events.values())
        n_kerb = sum(1 for v in events.values() for e in v if e['kerb'])
        print(race, 'laps', len(laps), 'labels', n_lab, 'events', n_ev, 'kerb-attributed', n_kerb, '->', K.rel(out))
        if args.eval_out:
            evaluation[race] = label_changes(race, variants)
            evaluation[race]['events'] = {'total': n_ev, 'kerbAttributed': n_kerb,
                                          'insidePass': sum(1 for v in events.values() for e in v if e['insidePass']),
                                          'byBin': {b: sum(1 for v in events.values() for e in v if e['bin'] == b) for b, *_ in BINS},
                                          'durationSeconds': {'min': min((e['t1'] - e['t0'] for v in events.values() for e in v), default=None),
                                                              'median': float(np.median([e['t1'] - e['t0'] for v in events.values() for e in v])) if n_ev else None}}
            print(race, json.dumps(evaluation[race]['counts'], ensure_ascii=False), 'changedVsReport', len(evaluation[race]['changedVsReport']))
    if args.eval_out:
        args.eval_out.mkdir(parents=True, exist_ok=True)
        (args.eval_out / 'label_changes.json').write_text(json.dumps(evaluation, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
