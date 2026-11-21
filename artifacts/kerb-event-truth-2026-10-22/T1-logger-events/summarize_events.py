"""T1 step 3: summary of events.json -> summary.json (+ marker_timing_spread.png when matplotlib is available).

Timing spread between markers (B - A, seconds; positive = B later), per session and pooled, for all records and for
'clean' records (no flags that make the pair ill-defined), agreement with the published kerb_contacts.json events,
contact detection vs the published pass labels, min-speed source agreement, VA vs RR onset, and lap-to-lap spread
(SD) of each marker per corner (time from the pass start; CG station, derived).

  python summarize_events.py        (no source media needed)
"""
import collections
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
D = json.loads((HERE / 'events.json').read_text(encoding='utf-8'))
RECS = D['records']

PAIRS = [
    ('brakeOnset', 'steerOnset'),
    ('steerRampOnset', 'steerOnset'),
    ('steerOnset', 'yawOnset'),
    ('steerOnset', 'steerRatePeak'),
    ('steerOnset', 'kerbContactOnset'),
    ('yawOnset', 'kerbContactOnset'),
    ('kerbContactOnset', 'kerbContactPeak'),
    ('kerbContactOnset', 'minSpeed'),
    ('kerbContactOnset', 'latGPeak'),
    ('kerbContactOnset', 'yawRatePeak'),
    ('kerbContactOnset', 'steerMax'),
    ('minSpeed', 'latGPeak'),
    ('latGPeak', 'yawRatePeak'),
    ('yawRatePeak', 'steerMax'),
    ('minSpeed', 'steerMax'),
]
# flags that make a marker ill-defined for the 'clean' subset
BAD = {
    'steerOnset': ('noSteerOnset', 'sharesSteerOnsetWith', 'steerYawOnsetGap', 'weakSteer', 'lapOutlierVsSessionMedian'),
    'steerRampOnset': ('noSteerOnset', 'sharesSteerOnsetWith', 'weakSteer'),
    'steerRatePeak': ('noSteerOnset', 'sharesSteerOnsetWith', 'weakSteer'),
    'yawOnset': ('sharesYawOnsetWith', 'steerYawOnsetGap', 'lapOutlierVsSessionMedian'),
    'brakeOnset': ('brakeRunShared',),
    'minSpeed': ('minSpeedAtWindowEdge', 'minSpeedFlat>1s'),
    'latGPeak': ('latGPeakAtWindowEdge',),
    'yawRatePeak': (),
    'steerMax': ('steerPeakAtWindowEdge', 'weakSteer'),
    'kerbContactOnset': ('kerbContactLowConfidenceOnly',),
    'kerbContactPeak': ('kerbContactLowConfidenceOnly',),
}


def stats(v):
    a = np.asarray([x for x in v if x is not None], float)
    if not len(a):
        return dict(n=0)
    q = np.percentile(a, [5, 25, 50, 75, 95])
    return dict(n=int(len(a)), median=round(float(q[2]), 3), q25=round(float(q[1]), 3), q75=round(float(q[3]), 3),
                p5=round(float(q[0]), 3), p95=round(float(q[4]), 3), min=round(float(a.min()), 3), max=round(float(a.max()), 3),
                mean=round(float(a.mean()), 3), sd=round(float(a.std(ddof=1)), 3) if len(a) > 1 else None)


def clean(r, key):
    fl = ' '.join(r.get('flags', []))
    if key.startswith('kerbContact') and (r.get('kerbContact') or {}).get('confidence') == 'low':
        return False
    return not any(b in fl for b in BAD.get(key, ()))


def speed_at(r, key):
    """Median-ish speed for converting dt to metres: ECU_VEH_SPD min of the corner is a lower bound; use the station
    difference instead when available (derived)."""
    return None


def pair_table(recs, clean_only):
    out = {}
    for a, b in PAIRS:
        dts, dss = [], []
        for r in recs:
            ta, tb = r['keyTimes'].get(a), r['keyTimes'].get(b)
            if ta is None or tb is None:
                continue
            if clean_only and not (clean(r, a) and clean(r, b)):
                continue
            dts.append(tb - ta)
            sa, sb = r['keyStationsDerived'].get(a), r['keyStationsDerived'].get(b)
            if sa is not None and sb is not None:
                dss.append(sb - sa)
        out[f'{a}->{b}'] = dict(dtS=stats(dts), dStationMDerived=stats(dss))
    return out


def clip_spread(recs, clean_only):
    keys = ('minSpeed', 'latGPeak', 'yawRatePeak', 'steerMax')
    sp, ss = [], []
    for r in recs:
        ts = [r['keyTimes'].get(k) for k in keys]
        if any(t is None for t in ts):
            continue
        if clean_only and not all(clean(r, k) for k in keys):
            continue
        sp.append(max(ts) - min(ts))
        st = [r['keyStationsDerived'].get(k) for k in keys]
        if all(s is not None for s in st):
            ss.append(max(st) - min(st))
    return dict(definition='max - min of {minSpeed, latGPeak, yawRatePeak, steerMax} per record',
                dtS=stats(sp), dStationMDerived=stats(ss))


def main():
    sessions = sorted({r['session'] for r in RECS})
    summary = dict(kind='T1 logger-event summary', source='events.json', sessions={})
    # coverage
    for s in sessions + ['pooled']:
        recs = [r for r in RECS if s == 'pooled' or r['session'] == s]
        cov = collections.Counter()
        for r in recs:
            for k, v in r['keyTimes'].items():
                if v is not None:
                    cov[k] += 1
        conf = collections.Counter((r.get('kerbContact') or {}).get('bestConfidence', 'none') for r in recs)
        flags = collections.Counter(f.split(':')[0] for r in recs for f in r.get('flags', []))
        summary['sessions'][s] = dict(
            records=len(recs), markerCoverage=dict(cov), passesWithKerbContactByBestConfidence=dict(conf),
            flagCounts=dict(flags.most_common()),
            timingSpreadAll=pair_table(recs, False), timingSpreadClean=pair_table(recs, True),
            clipMarkerSpreadAll=clip_spread(recs, False), clipMarkerSpreadClean=clip_spread(recs, True))
    # agreement with published events (inside-pass) and other events
    agr = collections.defaultdict(list)
    unmatched = 0
    for r in RECS:
        for a in r.get('publishedEventAgreement', []):
            if a.get('matched') and a.get('dOnset') is not None:
                agr['dOnset(mine-pubT0)'].append(a['dOnset'])
                agr['dOffset(mine-pubT1)'].append(a['dOffset'])
                if a.get('dPeakRRsharp') is not None:
                    agr['dPeak(RRsharp-pubTPeak)'].append(a['dPeakRRsharp'])
                agr['conf_' + a.get('confidence', '?')].append(a['dOnset'])
            else:
                unmatched += 1
    for o in D.get('otherContacts', []):
        if o.get('publishedEvent') and o.get('dOnsetVsPublishedT0') is not None:
            agr['other_dOnset(mine-pubT0)'].append(o['dOnsetVsPublishedT0'])
    summary['publishedEventAgreement'] = dict(
        note='published t0/t1 come from a 0.2 s RMS (> 1.0 deg/s), so they lead / trail the sharp onset / offset by up to ~0.1 s '
             'by construction; onsetFromDetector contacts have dOnset = 0 by definition',
        insidePassUnmatchedOrNoOnset=unmatched, **{k: stats(v) for k, v in agr.items()})
    # label confusion
    conf = collections.Counter()
    for r in RECS:
        lab = (r.get('publishedPassLabel') or {}).get('cls', 'none')
        mine = (r.get('kerbContact') or {}).get('bestConfidence', 'none')
        conf[f'{lab}|{mine}'] += 1
    summary['passLabelVsDetection'] = dict(note='published pass label | best confidence of the contacts attributed here',
                                           counts=dict(sorted(conf.items())))
    # min speed source agreement
    ms = collections.defaultdict(list)
    for r in RECS:
        c = r.get('clip') or {}
        a, b, g = c.get('minSpeedRearWheels'), c.get('minSpeedEcuVeh'), c.get('minSpeedGpsLagCorrected')
        for tag in ('pooled', r['session']):
            if a and b:
                ms[f'{tag}|ecuVeh-rearWheels'].append(b['t'] - a['t'])
            if a and g:
                ms[f'{tag}|gpsLagCorrected-rearWheels'].append(g['t'] - a['t'])
                if not a['atWindowEdge'] and not g['atWindowEdge'] and abs(g['t'] - a['t']) < 0.5:
                    ms[f'{tag}|gpsLagCorrected-rearWheels(interior,|d|<0.5s)'].append(g['t'] - a['t'])
            if a:
                ms[f'{tag}|rearWheelsFlatWidth'].append(a['flatT'][1] - a['flatT'][0])
    summary['minSpeedSources'] = dict(note='times of the corner speed minimum from the three speed sources; the GPS '
                                      'Speed time already includes the -0.09 s Doppler-lag correction',
                                      **{k: stats(v) for k, v in sorted(ms.items())})
    # VA vs RR onset
    vr, ratios = [], collections.defaultdict(list)
    for r in RECS:
        for c in r.get('kerbContacts', []):
            if c['VA']['onsetT'] is not None and c['RR']['onsetT'] is not None:
                vr.append(c['VA']['onsetT'] - c['RR']['onsetT'])
            ratios['n_' + c['confidence']].append(1)
            for ch in ('VA', 'RR'):
                if c[ch]['peakRatio'] is not None:
                    ratios[ch].append(c[ch]['peakRatio'])
    summary['contactChannels'] = dict(vaMinusRrOnsetS=stats(vr), peakRatioVA=stats(ratios['VA']), peakRatioRR=stats(ratios['RR']),
                                      insidePassContactsByConfidence={k[2:]: len(v) for k, v in ratios.items() if k.startswith('n_')})
    # lap-to-lap spread per corner
    per = {}
    for s in sessions:
        for kid in [c['kerb'] for c in D['corners']]:
            rs = [r for r in RECS if r['session'] == s and r['kerb'] == kid and r.get('passWindowT')]
            row = {}
            for key in ('brakeOnset', 'steerOnset', 'yawOnset', 'kerbContactOnset', 'minSpeed', 'latGPeak', 'yawRatePeak', 'steerMax'):
                rel = [r['keyTimes'][key] - r['passWindowT'][0] for r in rs if r['keyTimes'].get(key) is not None]
                sta = [r['keyStationsDerived'][key] for r in rs if r['keyStationsDerived'].get(key) is not None]
                if rel:
                    row[key] = dict(n=len(rel), relPassStartMedianS=round(float(np.median(rel)), 3),
                                    relPassStartSdS=round(float(np.std(rel, ddof=1)), 3) if len(rel) > 1 else None,
                                    stationMedianM=round(float(np.median(sta)), 1) if sta else None,
                                    stationSdM=round(float(np.std(sta, ddof=1)), 1) if len(sta) > 1 else None)
            per[f'{s}|{kid}'] = row
    summary['lapToLapPerCorner'] = per
    summary['notesForDownstream'] = notes(summary)
    (HERE / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    print('wrote summary.json')
    try:
        chart(summary)
    except ImportError:
        print('matplotlib not available: chart skipped')


def notes(sm):
    def g(sess, pair, f='median'):
        return sm['sessions'][sess]['timingSpreadClean'][pair]['dtS'].get(f)
    ms = sm['minSpeedSources']
    a = sm['publishedEventAgreement']
    return [
        'Logger times only: every time is on the logger clock mapped to the GPS lap-t base (7/30 per-lap channel shift). '
        'The video <-> logger offset is NOT addressed here.',
        f"Kerb contacts: detection = published RR detector (reproduced {sum(v['reproduced'] for v in D['publishedDetectorReproduction'].values())}"
        f"/{sum(v['publishedEvents'] for v in D['publishedDetectorReproduction'].values())}) + VA clusters; onset refined on 4 Hz HP / 0.06 s RMS. "
        f"Refined onset - published t0: median {a['dOnset(mine-pubT0)']['median']} s (IQR {a['dOnset(mine-pubT0)']['q25']}..{a['dOnset(mine-pubT0)']['q75']}).",
        'Onset estimator on synthetic bursts in real noise (verify_contact_onset_bias.json): about -10..-35 ms (early) at burst/baseline '
        'ratio 16-32, about +/-15 ms at 8, late by +20..+140 ms at 4 (RR mostly undetected at 4).',
        f"7/29 (wet) has only {sum(1 for r in RECS if r['session'] == 'fuji_aim_01' and r.get('kerbContacts'))} inside passes with a contact "
        '(2 high, 2 VA-only low): kerb-event truth for 7/29 is thin; steering / yaw / clip markers exist for all passes.',
        'k15 and k23 contacts happen while the car still steers the other way (track-out of k1 / k10): at that moment they act as '
        'outside kerbs (flag steeringAwayFromKerbSide...).',
        'Merged corner sequences (shared steering / yaw onset): k19 with k18 (300R), and on some laps k10 with k9, k21 with k20, '
        'k24 with k23 (flags sharesSteerOnsetWith / sharesYawOnsetWith).',
        f"Telemetry clipping markers {{minSpeed, latGPeak, yawRatePeak, steerMax}} spread per pass: median "
        f"{sm['sessions']['fuji_aim_01']['clipMarkerSpreadClean']['dtS'].get('median')} s (7/29) / "
        f"{sm['sessions']['fuji_aim_2020_07_30']['clipMarkerSpreadClean']['dtS'].get('median')} s (7/30); the clipping point is not one instant.",
        f"7/30 kerb-contact onset vs yaw-rate peak: median {g('fuji_aim_2020_07_30', 'kerbContactOnset->yawRatePeak')} s, vs steer max "
        f"{g('fuji_aim_2020_07_30', 'kerbContactOnset->steerMax')} s, vs min speed {g('fuji_aim_2020_07_30', 'kerbContactOnset->minSpeed')} s "
        '(positive = marker later than the contact onset).',
        f"Steering onset -> yaw onset: {g('fuji_aim_01', 'steerOnset->yawOnset')} s (7/29) vs {g('fuji_aim_2020_07_30', 'steerOnset->yawOnset')} s "
        '(7/30). Both channels get the same shift on 7/30, so the difference is logger-internal (wet/dry response or ECU/IMU latency; not resolved).',
        f"Corner speed minimum: GPS Speed (-0.09 s) minus rear wheels = {ms['fuji_aim_01|gpsLagCorrected-rearWheels(interior,|d|<0.5s)']['median']} s "
        f"(7/29) / {ms['fuji_aim_2020_07_30|gpsLagCorrected-rearWheels(interior,|d|<0.5s)']['median']} s (7/30), interior minima: consistent with "
        'the 7/30 shift and the 0.09 s Doppler lag (derived cross-check, not a proof). ECU_VEH_SPD minimum is earlier by '
        f"{ms['pooled|ecuVeh-rearWheels']['median']} s.",
    ]


def chart(summary):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 7))
    labels, data, cols = [], [], []
    palette = {'fuji_aim_01': '#4C78A8', 'fuji_aim_2020_07_30': '#E45756'}
    for a, b in PAIRS:
        for s in ('fuji_aim_01', 'fuji_aim_2020_07_30'):
            v = []
            for r in RECS:
                if r['session'] != s:
                    continue
                ta, tb = r['keyTimes'].get(a), r['keyTimes'].get(b)
                if ta is None or tb is None or not (clean(r, a) and clean(r, b)):
                    continue
                v.append(tb - ta)
            data.append(v if v else [np.nan])
            labels.append(f'{a} -> {b}  ({"7/29" if s == "fuji_aim_01" else "7/30"}, n={len(v)})')
            cols.append(palette[s])
    pos = np.arange(len(data))[::-1]
    bp = ax.boxplot(data, positions=pos, vert=False, widths=0.6, patch_artist=True, showfliers=True,
                    medianprops=dict(color='black'), flierprops=dict(markersize=3))
    for patch, c in zip(bp['boxes'], cols):
        patch.set_facecolor(c)
        patch.set_alpha(0.55)
    ax.set_yticks(pos)
    ax.set_yticklabels(labels, fontsize=6.5)
    ax.axvline(0, color='grey', lw=0.8)
    ax.set_xlabel('B - A (s), clean records, logger time base (7/30 channels shifted per lap)')
    ax.set_title('Fuji logger events: timing between markers per inside-kerb corner pass (T1, measured)', fontsize=9)
    ax.grid(axis='x', alpha=0.3)
    plt.tight_layout()
    plt.savefig(HERE / 'marker_timing_spread.png', dpi=110)
    print('wrote marker_timing_spread.png')


if __name__ == '__main__':
    main()
