"""T3 step 5: passages.json - video kerb-passage truth vs the 3DCG replay, per lap and inside kerb.

Inputs: $T3_WORK/analysis_raw.json (analyze.py), passes_windows.json, vp_calib.json, vp_height.json, yaw_focal.json,
public/data/races/<race>/apex_kpi.json and kerb_contacts.json (labels only), the shipped lap JSONs (speed).
No video pixels are read or written here.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402
import video_tracks as VT  # noqa: E402

HERE = Path(__file__).resolve().parent
REF_ROW = '276'
ROWS = ['264', '276', '286']
NOM = 'f967_h1.26'
GRID = ['f472_h1.00', 'f472_h1.25', 'f472_h1.50', 'f711_h1.00', 'f711_h1.25', 'f711_h1.50',
        'f967_h1.00', 'f967_h1.25', 'f967_h1.50']
MAX_ABS_DT = 0.8      # s; larger tip offsets are tracking failures (wrong feature), rejected
SUSPECT_DDIST = 2.0   # m
EDGE_MARGIN = 30      # px; video and replay tips must lie >= this far inside the windshield road band (not cut by a pillar)
ROW_CONSIST = 0.15    # s; tip offsets at rows 264/276/286 must agree within this to count as consistent


def r3(x, n=3):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(float(x), n)


def main():
    work = C.WORK
    raw = json.loads((work / 'analysis_raw.json').read_text(encoding='utf-8'))
    win = {(r['race'], r['lap'], r['kerb']): r for r in json.loads((HERE / 'passes_windows.json').read_text(encoding='utf-8'))['rows']}
    vpc = json.loads((HERE / 'vp_calib.json').read_text(encoding='utf-8'))['summary']
    vph = json.loads((HERE / 'vp_height.json').read_text(encoding='utf-8'))['results']
    yaw = json.loads((HERE / 'yaw_focal.json').read_text(encoding='utf-8'))['summary']
    cam_meta = {c['label']: c for c in raw['meta']['cams']}
    course = C.Course()
    laps = {}

    def lap(race, n):
        if (race, n) not in laps:
            laps[(race, n)] = C.Lap(race, n, course)
        return laps[(race, n)]

    def speed_at_pts(race, n, p0, pts):
        L = lap(race, n)
        return L.at(pts - p0 - L.delta)['v']

    roadm = {race: VT.road_mask_v2(race) for race in C.RACES}

    def inside_band(race, y, u):
        cols = np.where(roadm[race][int(y)])[0]
        return bool(cols.min() + EDGE_MARGIN <= u <= cols.max() - EDGE_MARGIN)

    passes = []
    for p in raw['passes']:
        race, n, kid, side = p['race'], p['lap'], p['kerb'], p['side']
        w = win[(race, n, kid)]
        p0 = p['lapStartPts']
        sign = 1.0 if side == 'right' else -1.0
        rec = dict(race=race, date=C.DATE[race], lap=n, overlayLap=n + 1, kerb=kid, name=C.NAMES[kid], side=side,
                   kerbStations=[w['kerbStationStart'], w['kerbStationEnd']], redBlockStations=[p['redExtent']['s0'], p['redExtent']['s1']],
                   lapStartPts=r3(p0, 4), replay=w['replay'], rows={}, sensitivity={})
        for y in ROWS:
            row = p['rows'][y]
            v = row['video']
            ent = dict(nTrackedFrames=v['nValid'], nRedFrames=v['nRed'])
            vt = v['tip']
            if vt:
                ent['videoTip'] = dict(pts=r3(vt['t'], 4), lapTime=r3(vt['t'] - p0, 3), u=r3(vt['u'], 1), subFrame=vt['parabola'])
            for ev in ('redOn', 'redOff'):
                if v[ev]:
                    ent['video' + ev[0].upper() + ev[1:]] = dict(pts=r3(v[ev]['t'], 4), lapTime=r3(v[ev]['t'] - p0, 3),
                                                                u=v[ev]['u'], uncertaintyS=round(1 / 60, 4))
            c = row['cams'][NOM]
            comp = {}
            rt = c['replayTip']
            if vt and rt:
                vel = speed_at_pts(race, n, p0, rt['t'])
                dts = vt['t'] - rt['t']
                comp['tip'] = dict(replayPts=r3(rt['t'], 4), replayLapTime=r3(rt['t'] - p0, 3), dt=r3(dts), speed=r3(vel, 2),
                                   replayMinusVideoAlongM=r3(dts * vel, 2),
                                   videoX=r3(c.get('videoTipX'), 2), videoD=r3(c.get('videoTipD'), 2),
                                   replayX=r3(c.get('replayTipX'), 2), replayD=r3(c.get('replayTipD'), 2),
                                   dX=r3(c['videoTipX'] - c['replayTipX'], 2) if 'videoTipX' in c and 'replayTipX' in c else None,
                                   dDistance=r3(sign * (c['videoTipX'] - c['replayTipX']), 2) if 'videoTipX' in c and 'replayTipX' in c else None,
                                   videoDistance=r3(sign * c['videoTipX'], 2) if 'videoTipX' in c else None,
                                   replayDistance=r3(sign * c['replayTipX'], 2) if 'replayTipX' in c else None,
                                   tipsInsideBand=bool(inside_band(race, y, vt['u']) and inside_band(race, y, rt['u'])),
                                   valid=bool(vt['parabola'] and rt['parabola'] and abs(dts) < MAX_ABS_DT
                                              and inside_band(race, y, vt['u']) and inside_band(race, y, rt['u'])))
            for ev, rk in (('redOn', 'replayRedOn'), ('redOff', 'replayRedOff')):
                vv, rr = v[ev], c[rk]
                if vv and rr:
                    vel = speed_at_pts(race, n, p0, rr['pts'])
                    dts = vv['t'] - rr['pts']
                    comp[ev] = dict(replayPts=r3(rr['pts'], 4), replayLapTime=r3(rr['pts'] - p0, 3), replayU=r3(rr['u'], 1),
                                    replayCrossingVisible=rr['inside'], replayD=r3(rr['D'], 2), replayX=r3(rr['X'], 2),
                                    dt=r3(dts), speed=r3(vel, 2), replayMinusVideoAlongM=r3(dts * vel, 2),
                                    valid=bool(rr['inside'] and abs(dts) < MAX_ABS_DT))
            ent['nominal'] = comp
            rec['rows'][y] = ent
        # row consistency of the tip offset
        tdts = [rec['rows'][y]['nominal']['tip']['dt'] for y in ROWS
                if 'tip' in rec['rows'][y]['nominal'] and rec['rows'][y]['nominal']['tip']['valid']]
        rec['tipRowConsistent'] = bool(len(tdts) >= 2 and max(tdts) - min(tdts) <= ROW_CONSIST)
        rec['tipRowsValid'] = len(tdts)
        # camera sensitivity at the reference row
        row = p['rows'][REF_ROW]
        for cl in GRID + [NOM]:
            c = row['cams'][cl]
            vt, rt = row['video']['tip'], c['replayTip']
            s = {}
            if vt and rt:
                s['tipDt'] = r3(vt['t'] - rt['t'])
            if 'videoTipX' in c:
                s['videoTipX'] = r3(c['videoTipX'], 2); s['videoTipD'] = r3(c['videoTipD'], 2)
                s['videoDistance'] = r3(sign * c['videoTipX'], 2)
            if 'videoTipX' in c and 'replayTipX' in c:
                s['dDistance'] = r3(sign * (c['videoTipX'] - c['replayTipX']), 2)
            for ev, rk in (('redOn', 'replayRedOn'), ('redOff', 'replayRedOff')):
                if row['video'][ev] and c[rk] and c[rk]['inside']:
                    s[ev + 'Dt'] = r3(row['video'][ev]['t'] - c[rk]['pts'])
            rec['sensitivity'][cl] = s
        passes.append(rec)

    def ok_tip(rec, y=REF_ROW):
        t = rec['rows'][y]['nominal'].get('tip')
        return t is not None and t['valid'] and rec['tipRowConsistent']

    # ---------------------------------------------------------------- between-lap (bias-free) comparisons
    between = {}
    for race in C.RACES:
        between[race] = {}
        for kid in C.INSIDE:
            P_ = [r for r in passes if r['race'] == race and r['kerb'] == kid]
            k = {}
            for ev in ('tip', 'redOn', 'redOff'):
                vals = {}
                for r in P_:
                    e = r['rows'][REF_ROW]['nominal'].get(ev)
                    if e and e['valid'] and (ev != 'tip' or r['tipRowConsistent']):
                        vals[r['lap']] = e
                if len(vals) >= 2:
                    med = float(np.median([e['dt'] for e in vals.values()]))
                    entry = dict(medianDt=r3(med), laps={})
                    for lp, e in sorted(vals.items()):
                        entry['laps'][lp] = dict(dtMinusMedian=r3(e['dt'] - med), alongMMinusMedian=r3((e['dt'] - med) * e['speed'], 2))
                    dev = np.array([e['dt'] - med for e in vals.values()])
                    entry['madS'] = r3(float(np.median(np.abs(dev))))
                    if ev == 'tip':
                        dd = {lp: e['dDistance'] for lp, e in vals.items() if e.get('dDistance') is not None}
                        vd = {lp: e['videoDistance'] for lp, e in vals.items() if e.get('videoDistance') is not None}
                        if len(dd) >= 2:
                            mdd = float(np.median(list(dd.values())))
                            mvd = float(np.median(list(vd.values())))
                            entry['medianDDistance'] = r3(mdd, 2)
                            for lp in dd:
                                entry['laps'][lp]['dDistanceMinusMedian'] = r3(dd[lp] - mdd, 2)
                                entry['laps'][lp]['videoDistanceMinusMedian'] = r3(vd[lp] - mvd, 2)
                    k[ev] = entry
            if k:
                between[race][kid] = k

    # ---------------------------------------------------------------- summaries
    def stats(a):
        a = np.asarray([x for x in a if x is not None], float)
        if len(a) == 0:
            return None
        return dict(n=int(len(a)), median=r3(float(np.median(a))), q25=r3(float(np.percentile(a, 25))),
                    q75=r3(float(np.percentile(a, 75))), min=r3(float(a.min())), max=r3(float(a.max())))

    summary = {}
    for race in C.RACES:
        P_ = [r for r in passes if r['race'] == race]
        tips = [r['rows'][REF_ROW]['nominal']['tip'] for r in P_ if ok_tip(r)]
        s = dict(passesAnalysed=len(P_),
                 tipValid=len(tips),
                 tipDt=stats([t['dt'] for t in tips]),
                 tipReplayMinusVideoAlongM=stats([t['replayMinusVideoAlongM'] for t in tips]),
                 tipDDistance=stats([t['dDistance'] for t in tips]))
        for ev in ('redOn', 'redOff'):
            E = [r['rows'][REF_ROW]['nominal'].get(ev) for r in P_]
            E = [e for e in E if e and e['valid']]
            s[ev + 'Valid'] = len(E)
            s[ev + 'Dt'] = stats([e['dt'] for e in E])
            s[ev + 'ReplayMinusVideoAlongM'] = stats([e['replayMinusVideoAlongM'] for e in E])
        dev = []
        ddev = []
        for kid, k in between[race].items():
            if 'tip' in k:
                dev += [v['dtMinusMedian'] for v in k['tip']['laps'].values()]
                ddev += [v['dDistanceMinusMedian'] for v in k['tip']['laps'].values() if 'dDistanceMinusMedian' in v]
        s['betweenLapTipDtDeviation'] = stats(np.abs(dev))
        s['betweenLapTipDDistanceDeviation'] = stats(np.abs(ddev))
        # per kerb
        pk = {}
        for kid in C.INSIDE:
            Q = [r for r in P_ if r['kerb'] == kid]
            T_ = [r['rows'][REF_ROW]['nominal']['tip'] for r in Q if ok_tip(r)]
            On = [r['rows'][REF_ROW]['nominal'].get('redOn') for r in Q]
            On = [e for e in On if e and e['valid']]
            Off = [r['rows'][REF_ROW]['nominal'].get('redOff') for r in Q]
            Off = [e for e in Off if e and e['valid']]
            pk[kid] = dict(name=C.NAMES[kid], side=Q[0]['side'] if Q else None, laps=len(Q),
                           tipValidLaps=len(T_), tipDtMedian=r3(np.median([t['dt'] for t in T_])) if T_ else None,
                           tipAlongMMedian=r3(np.median([t['replayMinusVideoAlongM'] for t in T_]), 2) if T_ else None,
                           tipDDistanceMedian=r3(np.median([t['dDistance'] for t in T_ if t['dDistance'] is not None]), 2) if T_ else None,
                           tipVideoDistanceMedian=r3(np.median([t['videoDistance'] for t in T_ if t['videoDistance'] is not None]), 2) if T_ else None,
                           redOnValidLaps=len(On), redOnDtMedian=r3(np.median([e['dt'] for e in On])) if On else None,
                           redOffValidLaps=len(Off), redOffDtMedian=r3(np.median([e['dt'] for e in Off])) if Off else None)
        s['perKerb'] = pk
        suspect = [kid for kid, v in pk.items() if v['tipDDistanceMedian'] is not None and abs(v['tipDDistanceMedian']) > SUSPECT_DDIST]
        s['suspectKerbs'] = dict(kerbs=suspect, rule=f'kerb median |dDistance| > {SUSPECT_DDIST} m at the tip: implausible against the '
                                 'known ~0.5-1 m lateral GPS error, so the video edge and the kerb_zones/CG line are probably different '
                                 'painted features (e.g. the 1st-corner painted apex area, the Dunlop-right wedge); excluded below')
        tips2 = [r['rows'][REF_ROW]['nominal']['tip'] for r in P_ if ok_tip(r) and r['kerb'] not in suspect]
        s['excludingSuspectKerbs'] = dict(tipValid=len(tips2), tipDt=stats([t['dt'] for t in tips2]),
                                          tipReplayMinusVideoAlongM=stats([t['replayMinusVideoAlongM'] for t in tips2]),
                                          tipDDistance=stats([t['dDistance'] for t in tips2]))
        dev2 = []
        ddev2 = []
        for kid, k in between[race].items():
            if 'tip' in k and kid not in suspect:
                dev2 += [v['dtMinusMedian'] for v in k['tip']['laps'].values()]
                ddev2 += [v['dDistanceMinusMedian'] for v in k['tip']['laps'].values() if 'dDistanceMinusMedian' in v]
        per = {}
        for kid, k in between[race].items():
            if kid in suspect:
                continue
            for ev, e in k.items():
                for lp, v in e['laps'].items():
                    per.setdefault(lp, {}).setdefault(ev, []).append(v['dtMinusMedian'])
        timer = C.timer_reads()[race]['perLapCorrectionSeconds']
        s['perLapCommonOffset'] = dict(
            definition='per lap, median over kerbs/events of (dt - kerb median dt): a lap-wide timing offset of the replay vs the '
                       'video that the per-kerb medians do not remove (e.g. an error in the overlay-timer correction of that lap)',
            laps={lp: dict(n=len(sum(v.values(), [])), medianS=r3(float(np.median(sum(v.values(), [])))),
                           byEvent={ev: dict(n=len(x), medianS=r3(float(np.median(x)))) for ev, x in v.items()},
                           overlayTimerCorrectionS=timer.get(str(lp))) for lp, v in sorted(per.items())})
        cm = float(np.mean([timer[str(lp)] for lp in per]))
        for lp, e in s['perLapCommonOffset']['laps'].items():
            e['correctionMinusSessionMeanS'] = r3(timer[str(lp)] - cm)
            e['offsetMinusCorrectionDeviationS'] = r3(e['medianS'] - (timer[str(lp)] - cm))
        res = [e['offsetMinusCorrectionDeviationS'] for e in s['perLapCommonOffset']['laps'].values()]
        s['perLapCommonOffset']['note'] = (
            'If the per-lap overlay-timer corrections were replaced by one session-mean correction, the lap-wide offsets would '
            f'become offsetMinusCorrectionDeviationS (spread {r3(float(np.std(res)))} s RMS about their mean): the kerb events do '
            'not support lap-to-lap differences in the timer correction (e.g. 7/30 L5 +0.09 s).')
        s['excludingSuspectKerbs']['betweenLapTipDtDeviation'] = stats(np.abs(dev2))
        s['excludingSuspectKerbs']['betweenLapTipDDistanceDeviation'] = stats(np.abs(ddev2))
        # camera sensitivity of the session medians (same passes as the nominal tip set)
        sens = {}
        ids = [(r['lap'], r['kerb']) for r in P_ if ok_tip(r) and r['kerb'] not in suspect]
        for cl in GRID + [NOM]:
            S = [r['sensitivity'][cl] for r in P_ if (r['lap'], r['kerb']) in ids]
            sens[cl] = dict(tipDtMedian=r3(np.median([x['tipDt'] for x in S if 'tipDt' in x])) if S else None,
                            tipDDistanceMedian=r3(np.median([x['dDistance'] for x in S if 'dDistance' in x]), 2) if S else None,
                            tipVideoDistanceMedian=r3(np.median([x['videoDistance'] for x in S if 'videoDistance' in x]), 2) if S else None,
                            redOnDtMedian=r3(np.median([r['sensitivity'][cl]['redOnDt'] for r in P_ if 'redOnDt' in r['sensitivity'][cl]
                                                        and abs(r['sensitivity'][cl]['redOnDt']) < MAX_ABS_DT])) if any('redOnDt' in r['sensitivity'][cl] for r in P_) else None,
                            redOffDtMedian=r3(np.median([r['sensitivity'][cl]['redOffDt'] for r in P_ if 'redOffDt' in r['sensitivity'][cl]
                                                         and abs(r['sensitivity'][cl]['redOffDt']) < MAX_ABS_DT])) if any('redOffDt' in r['sensitivity'][cl] for r in P_) else None,
                            D276=r3(cam_meta[cl]['D_rows']['276'], 2), fTimesH=r3(cam_meta[cl]['f'] * cam_meta[cl]['h'], 0))
        for cl in GRID + [NOM]:
            kerbs_ = {}
            for r in P_:
                if (r['lap'], r['kerb']) in ids and 'tipDt' in r['sensitivity'][cl]:
                    kerbs_.setdefault(r['kerb'], []).append(r['sensitivity'][cl]['tipDt'])
            if len(kerbs_) >= 3:
                km = [float(np.median(x)) for x in kerbs_.values()]
                wk = np.concatenate([np.array(x) - np.median(x) for x in kerbs_.values()])
                sens[cl]['acrossKerbSdOfMedianTipDt'] = r3(float(np.std(km)))
                sens[cl]['withinKerbMadTipDt'] = r3(float(np.median(np.abs(wk))))
        s['cameraSensitivity'] = sens
        summary[race] = s

    # ---------------------------------------------------------------- f*h from the row dependence of the tip offset
    fh_scan = {}
    for cl in [c for c in cam_meta if c.endswith('h1.26')] + ['f472_h1.25', 'f711_h1.50']:
        X, Y, G = [], [], []
        for gi, p in enumerate(raw['passes']):
            pts = []
            for y in ('252', '264', '276', '286'):
                r = p['rows'][y]; v = r['video']['tip']; rp = r['cams'][cl]['replayTip']
                if not (v and rp and v.get('parabola') and rp.get('parabola')):
                    continue
                vel = speed_at_pts(p['race'], p['lap'], p['lapStartPts'], rp['t'])
                pts.append((y, v['t'] - rp['t'], vel))
            if len(pts) < 3 or np.ptp([q[1] for q in pts]) > 0.5:
                continue
            for y, d_, vel in pts:
                X.append(cam_meta[cl]['D_rows'][y] / vel); Y.append(d_); G.append(gi)
        X, Y, G = map(np.array, (X, Y, G))
        if len(X) < 10:
            continue
        Xd, Yd = X.copy(), Y.copy()
        for g in np.unique(G):
            m = G == g; Xd[m] -= X[m].mean(); Yd[m] -= Y[m].mean()
        b = float(np.sum(Xd * Yd) / np.sum(Xd * Xd))
        ug = np.unique(G); rng = np.random.default_rng(0); bs = []
        for _ in range(2000):
            s_ = rng.choice(ug, len(ug))
            xx = np.concatenate([Xd[G == g] for g in s_]); yy = np.concatenate([Yd[G == g] for g in s_])
            bs.append(np.sum(xx * yy) / np.sum(xx * xx))
        fh = cam_meta[cl]['f'] * cam_meta[cl]['h']
        fh_scan[cl] = dict(passes=int(len(ug)), points=int(len(X)), slope=r3(b), Dscale=r3(1 - b),
                           Dscale95=[r3(1 - float(np.percentile(bs, 97.5))), r3(1 - float(np.percentile(bs, 2.5)))],
                           impliedFTimesH=r3(fh * (1 - b), 0),
                           impliedFTimesH95=[r3(fh * (1 - float(np.percentile(bs, 97.5))), 0), r3(fh * (1 - float(np.percentile(bs, 2.5))), 0)])

    nom = cam_meta[NOM]
    doc = dict(
        kind='t3-video-kerb-passages', version=1,
        created=dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec='seconds'),
        track='T3 - video along-track / lateral truth at inside kerbs (clipping points)',
        evidenceClass=dict(
            measured='video pixel events (edge pixel u, frame PTS, sub-frame tip time) and their lap-time conversion',
            derived='replay predictions through the camera model; video-minus-replay offsets; between-lap deviations',
            estimated='absolute metres (lateral distance, forward distance D, along-track metres): depend on the self-calibrated '
                      'camera (h, f*h), a flat/CG road surface and no lens-distortion model'),
        timeBase=dict(
            videoPts='ffmpeg PTS (s), CFR 30 fps, frame k at k/30 s; frames decoded with accurate input seek',
            lapTime='T = PTS - lapStartPts; lapStartPts from artifacts/video-position-gap-2026-10-02/video-timer-reads.json '
                    '(reference lap start + per-lap overlay-timer correction). Replay lap t = T - meta.first_sample_after_lap_start_seconds.',
            overlayTimerLag='the overlay timer reads trail truth by 0-50 ms; the mapping inherits this, so the replay events '
                            'placed into video time are about 0.025 +- 0.015 s too late. dt below is NOT corrected for it '
                            '(add about +0.025 s to dt for the timer-lag-corrected value).'),
        definitions=dict(
            row='fixed image row (pixels, 1280x720). Primary reference row 276; rows 264 and 286 reported as checks. At a fixed row the '
                'camera sees the road at a fixed forward distance D (car-fixed; flat-road value per camera below).',
            edge='road-side edge of the inside kerb (white strip / paint = kerb_zones paint line) at the row, by colour classification',
            tip='clipping-point event: the time the kerb edge reaches its extreme position toward the image centre at the row, i.e. '
                'the minimum lateral camera-to-kerb distance at forward distance D (task item 3). Sub-frame by parabola (+-3 frames).',
            redOn='leading end of the red kerb blocks crossing the row in open view (task item 1, leading end)',
            redOff='trailing end of the red kerb blocks leaving the row in open view (task item 1, trailing end)',
            dt='video event time minus the replay-predicted time of the same event (s). Negative = the video shows the event '
               'earlier than the 3DCG replay (replay late / behind).',
            replayMinusVideoAlongM='dt * replay speed (m): along-track position of the replay minus the true (video) position at the '
                                   'event; negative = replay behind',
            dX='video minus replay lateral position of the kerb edge at the tip, camera frame (m, right +)',
            dDistance='video minus replay lateral camera-to-kerb distance at the tip (m); positive = in reality the car was '
                      'farther from the kerb than the replay shows',
            replayPrediction='camera rigidly on the replay pose (registered GPS reference point, camera 0.55 m behind, 0 m lateral, '
                             'heading = GPS course as in the viewer), CG road surface heights (track.json altLeft/altRight) for the '
                             'car pitch/roll and the kerb points, kerb_zones paint line (CG road edge beyond the kerb) and block line'),
        camera=dict(
            nominal=dict(label=NOM, f=nom['f'], h=nom['h'], pitchDeg=r3(nom['pitchDeg'], 2), yawDeg=r3(nom['yawDeg'], 2),
                         rollDeg=r3(nom['rollDeg'], 2), vanishingPoint=[r3(nom['u_vp'], 1), r3(nom['v_vp'], 1)],
                         forwardDistanceFlatM={k: r3(v, 2) for k, v in nom['D_rows'].items()},
                         status='estimated (self-calibrated from the video + logger; not a lens calibration)'),
            evidence=dict(
                vanishingPoint=dict(source='vp_calib.py / vp_calib.json', value=vpc.get('fuji_aim_2020_07_30'),
                                    note='7/30 main straight, white road-edge lines; used for both days (dashboard edge at the same pixels on both days); 7/29 wet fits too few/unstable'),
                heightFromRoadWidth=dict(source='vp_height.py / vp_height.json', f711=vph['f711']['summary'].get('fuji_aim_2020_07_30'),
                                         note='camber-corrected (track.json cross slope ~ -3 %); flat-road estimate 1.47-1.51 m is implausible for a camera inside a Mazda2'),
                focalFromRotation=dict(source='yaw_focal.py / yaw_focal.json', value=yaw.get('combined'), perCorner={k: v.get('fCorrected') for k, v in yaw.items() if k != 'combined'}),
                fTimesHFromRowTiming=dict(source='summarize.py (tip offsets at rows 252-286 vs D(row)/speed, within-pass demeaned, bootstrap over passes)',
                                          scan=fh_scan,
                                          note='self-consistent where Dscale = 1. A wrong f*h makes the tip offset depend on the row.'),
                acrossKerbSpread=dict(note='alternative scale criterion: if the replay error at the clipping points were one common clock '
                                           'offset, the right camera scale would minimise the spread of the per-kerb median tip dt; that '
                                           'spread (summary.<race>.cameraSensitivity.*.acrossKerbSdOfMedianTipDt) is smallest for f*h about '
                                           '700-1000 and grows above 1200, i.e. it prefers a smaller scale than the row-timing scan. Kerb-'
                                           'specific errors (kerb_zones stations, local GPS, body pitch at each corner) can produce either result.'),
                combinedScale='f*h about 1100 +- 250 px m (D at row 276 about 14 +- 3 m); nominal 1218 is the upper-central value',
                profile20261003=dict(f=472.3, h=1.25, pitchDeg=18.21,
                                     comparison=(json.loads((HERE / 'profile_camera.json').read_text(encoding='utf-8'))['summary']
                                                 if (HERE / 'profile_camera.json').exists() else None),
                                     note='camera-profile.json (Codex fit) used unmodified (profile_check.py). Its f is not supported by the '
                                          'rotation check (~930 px) or the row-timing scan (f*h ~1200), but it gives a small across-kerb spread.')),
            grid=[dict(label=c['label'], f=c['f'], h=c['h'], pitchDeg=r3(c['pitchDeg'], 2), D276=r3(c['D_rows']['276'], 2))
                  for c in raw['meta']['cams']]),
        straightLateralCheck=dict(
            source='vp_height.py / vp_height.json (f = 711; f-independent to < 2 %)',
            definition='main straight 380-680 m: video camera-to-left-road-edge distance from the two edge-line slopes and the road '
                       'width (camber-corrected), minus the replay camera position relative to the CG left road edge; per-frame '
                       'differences, median per lap. Not at kerbs; a second lateral truth source.',
            fuji_aim_2020_07_30=vph['f711']['summary'].get('fuji_aim_2020_07_30', {}).get('lateralOnStraightPerLap'),
            fuji_aim_01='not usable (wet, 11 unstable frames)'),
        referenceRow=int(REF_ROW), rowsReported=[int(y) for y in ROWS],
        summary=summary, betweenLap=between, passes=passes,
        sources=dict(passWindows='passes_windows.json (passes.py)', videoTracks='video_tracks.py (work folder output)',
                     analysis='analyze.py', prediction='predict.py', camera='camera.py', kerbZones='public/data/tracks/fuji/cg_study/kerb_zones.json',
                     roadGeometry='public/data/tracks/fuji/cg_study/geometry.json', laps='public/data/races/<race>/osaki_hmr_demio_101_lap_00N.json',
                     registration='public/data/races/<race>/gps_registration.json (shipped)',
                     timerReads='artifacts/video-position-gap-2026-10-02/video-timer-reads.json',
                     media='FUJI_WET_VIDEO / FUJI_DRY_VIDEO (local, read-only; no pixels stored in the repository)'))
    (HERE / 'passages.json').write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk not in ('perKerb', 'cameraSensitivity')} for k, v in summary.items()}, indent=1))
    print(json.dumps(fh_scan, indent=1))


if __name__ == '__main__':
    main()
