"""T1 step 2: logger ground-truth events per (session, lap, inside-kerb corner) from the native XRK channels.

Reads   $T1_WORK/xrk_native_<race>.npz            (extract_xrk_native.py; raw logger samples, never published)
        public/data/races/<race>/*.json             (shipped laps: GPS t base, channel_time_alignment shift)
        public/data/races/<race>/kerb_contacts.json (published IMU events, cross-check only)
        public/data/races/<race>/apex_kpi.json      (viewer clipping point, reference only)
        public/data/races/<race>/gps_registration.json, public/data/tracks/fuji/cg_study/{kerb_zones,geometry}.json
                                                    (only to locate the corner pass, attribute contacts to kerbs and
                                                     report CG stations)
Writes  events.json (next to this script)

Time base of every time field: lap t (s) of the shipped lap JSON = GPS time base = viewer playback clock
(0 = first GPS sample of the lap). Logger samples are mapped with t = (logger_ms - tc0Ms)/1000 + shiftSeconds,
shiftSeconds = meta.channel_time_alignment.shiftSeconds of the lap (7/30) or 0 (7/29). Lap clock (HUD, video
overlay timer) = t + lapClockOffsetS (meta.first_sample_after_lap_start_seconds).

All event times are MEASURED on logger samples with sub-sample interpolation; stations / pass windows are DERIVED
from the shipped registered GPS path (not ground truth). Method parameters are written into events.json.

  T1_WORK=<work dir> python build_logger_events.py
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import savgol_filter

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from t1common import (ROOT, RACES, load_npz, lap_numbers, lap_shift, to_lap_t, butter_hp, butter_lp,  # noqa: E402
                      centered_rms, crossing_time, parabolic_peak)
sys.path.insert(0, str(ROOT / 'scripts/quality/apex_kpi'))
import build_apex_kpi as K  # noqa: E402  (read-only use: zones, limits, registered 50 Hz paths)

HP_FC, RMS_N = 4.0, 3
F_CORNER = {'VA': 1.0, 'RR': 2.0}
GPS_SPEED_LAG_S = 0.09
PUB_EVENT_MIN, PUB_MERGE_SAMPLES = 1.0, 15
OVERLAP_PAD_S = 0.2

METHOD = dict(
    kerbContact=dict(
        detection=dict(
            rr='published kerb_contacts.json detector, recomputed from the native XRK: RollRate minus its 15-sample '
               'moving average, RMS over 10 samples (0.2 s), event = RMS > 1.0 deg/s, merged across gaps <= 15 samples '
               '(reproduces all 44 published events within the 0.01 s rounding)',
            va='VerticalAcc high-pass 4 Hz (4th-order Butterworth, zero-phase), centred 3-sample RMS; cluster = samples '
               'above detectLevel = max(median + 10 sigma, 5 median) of the lap straight-line baseline, merged across '
               'gaps < 0.25 s',
            candidate='RR events and VA clusters overlapping within 0.10 s form one contact candidate'),
        timing=dict(
            signals='VerticalAcc and RollRate, each high-pass 4 Hz (4th-order Butterworth, zero-phase filtfilt on the '
                    'native samples) and centred 3-sample RMS (0.06 s window; can lead a step onset by <= 1 sample)',
            baseline='straight-line, no-brake samples (|LateralAcc LP1.5Hz| < 0.25 g, |yaw LP1.5Hz - bias| < 4 deg/s, '
                     'ECU_VEH_SPD > 40 km/h, ECU_BRK_P < 3 bar) in [candidateStart - 8 s, candidateStart - 0.5 s]; '
                     '>= 25 samples, else the same selection over the whole lap (baselineScope = lap)',
            sigma='1.4826 * MAD of the baseline RMS',
            onsetLevel='F * max(median + 4 sigma, 2.5 median); F = 1 for VerticalAcc, 2 for RollRate (in-corner / '
                       'straight RollRate noise ratio measured 1.8-2.1 at > 0.8 g, both sessions; VerticalAcc ratio ~1)',
            onset='first upward crossing of onsetLevel (linear interpolation between the two samples) that starts a run '
                  'of >= 2 samples above it, searched in [candidateStart - 0.3 s, channel peak]; if the signal is already '
                  'above at the search start the walk back continues up to 0.5 s; the walk never crosses the end (detector '
                  'end or refined offset, whichever is later) of the previous candidate. No crossing found -> channel onset null (flag elevatedBackground / '
                  'onsetBoundedByPreviousContact); contact onset falls back to the detector start (flag onsetFromDetector)',
            offset='last downward crossing of onsetLevel in [channel peak, min(candidateEnd + 0.5 s, next candidate start)]; '
                   'none -> null (flag offsetAtSearchLimit / offsetBoundedByNextContact), contact offset falls back to the '
                   'detector end (flag offsetFromDetector)',
            peak='3-point parabola on the RMS maximum inside [candidateStart - 0.1 s, candidateEnd + 0.1 s] (clipped to the '
                 'previous / next candidate)',
            contactOnset='earliest onset of the responding channels (a channel responds when its peak exceeds its onsetLevel)',
            contactPeak='peakT = sharp RollRate peak when RollRate responds, else sharp VerticalAcc peak; detectorPeakT = '
                        'RR-detector peak (published tPeak) or VA-cluster peak, used for the kerb attribution'),
        attribution='as export_inputs.attribute: CG station at the contact peak (RR detector peak when present, else VA '
                    'peak); an inside kerb whose pass window (+-0.2 s) holds the peak time and whose range (+-3 m) holds the '
                    'station, deepest first; else any kerb zone within 3 m; else none',
        confidence='high = RR event and VA responds; medium = RR event only (VA below its onsetLevel); low = VA cluster '
                   'without an RR event (vertical-only: kerb paint, bump or kerb; not separable from the logger alone)'),
    steer=dict(
        channel='ECU_STEER_ANG (deg at the wheel, 20 Hz, 1 deg quantisation); right-hand corner = negative values',
        deltaDir='-cornerDir * (steer - straightOffset); straightOffset = session median at ECU_VEH_SPD > 120 km/h and '
                 '|LateralAcc LP| < 0.1 g',
        peak='max of deltaDir LP 2 Hz (zero-phase) in [passStart - 1.0 s, passEnd + 0.5 s], 3-point parabola',
        walkStart='the valley / onset walk starts at yaw onset + 0.3 s (or at the steering peak if earlier, or if the '
                  'steering there is still below its threshold) so that mid-corner steering dips are not taken as the '
                  'pre-corner valley; the steering peak is still the max over the pass window',
        valley='walking back from walkStart (<= 10 s), the first local minimum of deltaDir LP (5 deg hysteresis) from '
               'which deltaDir rises to the peak by >= max(10 deg, 0.4 * peak) and whose level is <= max(8 deg, 0.25 * '
               'peak), or <= max(8 deg, 0.5 * peak) when it lies before the pass window; other minima (mid-corner dips, '
               'partial unwinding) are walked through',
        cases='valley < -max(8 deg, 0.3 peak): linkedOpposite, baseline = 0 (straight-ahead); valley > 8 deg: '
              'linkedSame, baseline = valley level (LP); both with sigma = session straight-line sigma; otherwise isolated, '
              'baseline = raw median over [valley - 1 s, valley + 0.1 s], sigma = 1.4826 MAD clipped to '
              '[0.5 deg, 2 x session sigma]',
        onsetThreshold='baseline + max(3 deg, 3 sigma)',
        onset='last upward crossing of onsetThreshold by raw deltaDir before the peak (sustained up to the peak by '
              'construction, not earlier than valley - 0.5 s), linear interpolation between the 20 Hz samples',
        rampOnset='line fitted to the raw deltaDir samples between 20 % and 60 % of (peak - baseline) on the first ramp '
                  'after the onset, intercept with baseline (needs >= 3 samples; rejected if later than onset + 0.1 s or '
                  'earlier than onset - 2 s)',
        ratePeak='max of Savitzky-Golay derivative (5 samples, order 2) of raw deltaDir between onset and the first time '
                 'deltaDir LP reaches 90 % of (peak - baseline), parabola',
        release='first time after the peak with deltaDir LP < baseline + 0.3 (peak - baseline)'),
    yaw=dict(
        channel='YawRate (deg/s, 50 Hz IMU); right-hand corner = positive; bias = session median at ECU_VEH_SPD > 120 '
                'km/h and |LateralAcc LP| < 0.1 g',
        onset='computed BEFORE the steering: steering rule on cornerDir * (yaw LP 5 Hz - bias), with peak/valley on yaw '
              'LP 1.5 Hz; peak search [passStart - 1 s, passEnd + 0.5 s]; walk from the yaw peak; thresholds max(1.5 '
              'deg/s, 3 sigma), sigma clipped to [0.25 deg/s, 2 x session sigma]; valley hysteresis 2 deg/s, rise >= max(4 deg/s, 0.4 peak), level <= max(3 deg/s, 0.25 peak) (0.5 peak before the pass window); linkedOpposite if valley '
              '< -max(3, 0.3 peak) deg/s, linkedSame if valley > 3 deg/s'),
    clip=dict(
        window='[max(entry - 0.5 s, passStart - 2 s), min(steerRelease + 0.5 s, passEnd + 2 s)], entry = steering onset '
               '(yaw onset if missing); [passStart - 1 s, passEnd + 1 s] if both are missing',
        extremum='each clip marker = the highest interior local maximum (lowest interior local minimum for speed) inside '
                 'its window, + 3-point parabola; the window max/min (flag atWindowEdge) only when no interior extremum exists',
        latGPeak='cornerDir * LateralAcc LP 1.5 Hz (zero-phase), max + parabola; plateau = contiguous span >= 97 % of the peak',
        yawRatePeak='yaw LP 1.5 Hz, max + parabola, plateau >= 97 %',
        steerMax='deltaDir LP 2 Hz max + parabola (pass window based search), plateau >= 97 %',
        minSpeed='primary: mean of ECU_WS_RL/RR (non-driven rear wheels, 20 Hz, 1 km/h quantisation) LP 2 Hz, min in '
                 '[max(steerOnset - 1 s, passStart - 2.5 s), min(steerRelease + 1 s, passEnd + 2.5 s)] + parabola; '
                 'flat = span within +0.5 km/h of the min (bounded by the search window). Secondary: ECU_VEH_SPD LP 2 Hz; GPS Speed (10 Hz) with its '
                 'time moved 0.09 s earlier (Doppler-speed lag)'),
    brake=dict(
        channel='ECU_BRK_P (bar, 20 Hz); +inf = no CAN brake message -> 0 bar (as pipeline/convert_aim.py)',
        onset='upward crossing of 3.0 bar (linear interpolation) at the start of the last brake run (>= 3 bar, gaps < '
              '0.2 s merged, peak >= 5 bar) that starts in [entry - 8 s, entry + 0.5 s] (entry = steering onset, else yaw onset; '
              'without either: [passStart - 9 s, minSpeed time]); msgStart = first finite '
              'sample of the CAN run holding the onset; release = downward crossing of 3.0 bar at the end of the run'),
)


def r3(v):
    return None if v is None or not np.isfinite(v) else round(float(v), 3)


def lin_fit_intercept(t, y, level):
    A = np.vstack([t, np.ones_like(t)]).T
    slope, icpt = np.linalg.lstsq(A, y, rcond=None)[0]
    if slope <= 0:
        return None
    return float((level - icpt) / slope)


def plateau(t, y, i, frac=0.97):
    lvl = frac * y[i]
    a = i
    while a > 0 and y[a - 1] >= lvl:
        a -= 1
    b = i
    while b < len(y) - 1 and y[b + 1] >= lvl:
        b += 1
    return [float(t[a]), float(t[b])]


def runs_of(mask):
    idx = np.flatnonzero(mask)
    if not len(idx):
        return []
    br = np.flatnonzero(np.diff(idx) > 1)
    starts = np.r_[idx[0], idx[br + 1]]
    ends = np.r_[idx[br], idx[-1]]
    return list(zip(starts.tolist(), ends.tolist()))


class Session:
    """Session-wide filtered series (index domain on the native samples)."""

    def __init__(self, race):
        z = load_npz(race)
        self.z, self.race = z, race
        self.lapmap = z['lapmap']
        va, la, rr, yr = z['VerticalAcc'], z['LateralAcc'], z['RollRate'], z['YawRate']
        self.tcA, self.tcB = z['VerticalAcc_t'], z['RollRate_t']          # VA/LA share A, RR/YR share B
        assert np.array_equal(z['LateralAcc_t'], self.tcA) and np.array_equal(z['YawRate_t'], self.tcB)
        for name, arr in (('VA', va), ('LA', la), ('RR', rr), ('YR', yr)):
            if not np.isfinite(arr).all():
                raise SystemExit(f'{race}: non-finite {name}')
        self.e = {'VA': centered_rms(butter_hp(va, HP_FC, 50.0), RMS_N),
                  'RR': centered_rms(butter_hp(rr, HP_FC, 50.0), RMS_N)}
        hp = rr - uniform_filter1d(rr, 15)
        self.rr_pub = np.sqrt(uniform_filter1d(hp * hp, 10))
        self.la_lp = butter_lp(la, 1.5, 50.0)
        self.yr_lp5 = butter_lp(yr, 5.0, 50.0)
        self.yr_lp15 = butter_lp(yr, 1.5, 50.0)
        self.gap = {'VA': np.diff(self.tcA) > 40, 'RR': np.diff(self.tcB) > 40}
        self.tcE = z['ECU_STEER_ANG_t']
        for c in ('ECU_WS_RL', 'ECU_WS_RR', 'ECU_VEH_SPD', 'ECU_BRK_P'):
            assert np.array_equal(z[c + '_t'], self.tcE)
        steer = z['ECU_STEER_ANG']
        assert np.isfinite(steer).all()
        self.steer = steer
        self.steer_lp = butter_lp(steer, 2.0, 20.0)
        self.vrear = butter_lp((z['ECU_WS_RL'] + z['ECU_WS_RR']) / 2, 2.0, 20.0)
        self.vveh = butter_lp(z['ECU_VEH_SPD'], 2.0, 20.0)
        brk = z['ECU_BRK_P'].copy()
        self.brk_finite = np.isfinite(brk)
        brk[~self.brk_finite] = 0.0
        self.brk = brk
        self.tcG = z['GPS Speed_t']
        gs = z['GPS Speed']
        self.vgps = gs * (3.6 if np.nanmax(gs) < 100 else 1.0)
        # session straight-line references
        la_on_E = np.interp(self.tcE, self.tcA, self.la_lp)
        v_on_B = np.interp(self.tcB, self.tcE, self.vveh)
        la_on_B = np.interp(self.tcB, self.tcA, self.la_lp)
        fast_B = (v_on_B > 120) & (np.abs(la_on_B) < 0.1)
        self.yaw_bias = float(np.median(yr[fast_B]))
        fast_E = (self.vveh > 120) & (np.abs(la_on_E) < 0.1)
        self.steer0 = float(np.median(steer[fast_E]))
        d = steer[fast_E] - self.steer0
        self.steer_sigma = float(max(1.4826 * np.median(np.abs(d - np.median(d))), 0.5))
        y = yr[fast_B] - self.yaw_bias
        self.yaw_sigma = float(max(1.4826 * np.median(np.abs(y - np.median(y))), 0.2))
        v_on_A = np.interp(self.tcA, self.tcE, self.vveh)
        b_on_A = np.interp(self.tcA, self.tcE, self.brk)
        yr_on_A = np.interp(self.tcA, self.tcB, self.yr_lp15) - self.yaw_bias
        sA = (np.abs(self.la_lp) < 0.25) & (np.abs(yr_on_A) < 4.0) & (v_on_A > 40) & (b_on_A < 3.0)
        self.straight = {'VA': sA, 'RR': np.interp(self.tcB, self.tcA, sA.astype(float)) > 0.5}
        self.straight_counts = dict(nFastYawRef=int(fast_B.sum()), nFastSteerRef=int(fast_E.sum()))


class LapView:
    def __init__(self, S: Session, lap, lp):
        self.S, self.lap, self.lp = S, lap, lp
        self.meta = lp.meta
        self.shift = lap_shift(self.meta)
        self.tc0 = float(S.lapmap[str(lap)]['tc0Ms'])
        self.t = {'VA': to_lap_t(S.tcA, self.tc0, self.shift), 'RR': to_lap_t(S.tcB, self.tc0, self.shift)}
        self.tA, self.tB = self.t['VA'], self.t['RR']
        self.tE = to_lap_t(S.tcE, self.tc0, self.shift)
        self.tG = to_lap_t(S.tcG, self.tc0, 0.0) - GPS_SPEED_LAG_S       # GPS is the time base itself; Doppler lag removed
        self.t_end = lp.t_end
        self.offset_clock = float(self.meta.get('first_sample_after_lap_start_seconds') or 0.0)

    def logger_ms(self, t):
        return None if t is None else round((t - self.shift) * 1000.0 + self.tc0, 1)

    def station(self, t):
        if t is None or t < self.lp.t50[0] or t > self.lp.t50[-1]:
            return None
        return round(self.lp.station_at(t), 1)

    def steer_side(self, t):
        if t is None:
            return None
        v = float(np.interp(t, self.tE, self.S.steer_lp)) - self.S.steer0
        return 'right' if v < -3 else ('left' if v > 3 else 'straight')


# ---------------------------------------------------------------- kerb contacts
def baseline(L: LapView, ch, t_ref):
    S, t = L.S, L.t[ch]
    e, st = S.e[ch], S.straight[ch]
    m = st & (t >= t_ref - 8.0) & (t <= t_ref - 0.5)
    scope = 'pre-contact'
    if m.sum() < 25:
        m = st & (t >= 0) & (t <= L.t_end)
        scope = 'lap'
    vals = e[m]
    med = float(np.median(vals))
    sig = float(1.4826 * np.median(np.abs(vals - med)))
    F = F_CORNER[ch]
    return dict(median=med, sigma=sig, n=int(m.sum()), scope=scope,
                detectLevel=max(med + 10 * sig, 5 * med), onsetLevel=F * max(med + 4 * sig, 2.5 * med))


def rr_events(L: LapView):
    """Published-style RR events (identical rule to eval_v2.detector / export_inputs)."""
    t, e = L.tB, L.S.rr_pub
    m = np.flatnonzero((t >= 0) & (t <= L.t_end))
    tt, ee = t[m], e[m]
    idx = np.flatnonzero(ee > PUB_EVENT_MIN)
    out = []
    if len(idx):
        for gi in np.split(idx, np.flatnonzero(np.diff(idx) > PUB_MERGE_SAMPLES) + 1):
            seg = np.arange(gi[0], gi[-1] + 1)
            j = int(seg[np.argmax(ee[seg])])
            out.append(dict(t0=float(tt[gi[0]]), t1=float(tt[gi[-1]]), tPeak=float(tt[j]), peak=float(ee[j])))
    return out


def va_clusters(L: LapView):
    t, e = L.tA, L.S.e['VA']
    lapm = (t >= 0) & (t <= L.t_end)
    b = baseline(L, 'VA', -1e9)          # lap-wide straight-line baseline for detection
    above = lapm & (e > b['detectLevel'])
    rs = runs_of(above)
    merged = []
    for i0, i1 in rs:
        if merged and t[i0] - t[merged[-1][1]] < 0.25:
            merged[-1][1] = i1
        else:
            merged.append([i0, i1])
    out = []
    for i0, i1 in merged:
        seg = np.arange(i0, i1 + 1)
        j = int(seg[np.argmax(e[seg])])
        out.append(dict(t0=float(t[i0]), t1=float(t[i1]), tPeak=float(t[j]), peakRms=float(e[j]),
                        detectLevel=b['detectLevel']))
    return out, b


def refine_channel(L: LapView, ch, c0, c1, lo_b=-1e9, hi_b=1e9):
    """Onset / offset / peak of one channel for a contact candidate [c0, c1] (detector extent).
    lo_b / hi_b: detector end of the previous candidate / start of the next one; the walks never cross them."""
    S, t, e = L.S, L.t[ch], L.S.e[ch]
    base = baseline(L, ch, c0)
    O = base['onsetLevel']
    win = np.flatnonzero((t >= max(c0 - 0.1, lo_b)) & (t <= min(c1 + 0.1, hi_b)))
    res = dict(baseline={k: (r3(v) if isinstance(v, float) else v) for k, v in base.items()}, responds=False)
    if not len(win):
        return res
    ip = int(win[np.argmax(e[win])])
    tp, vp = parabolic_peak(t, e, ip)
    res.update(peakT=tp, peakRms=float(vp), peakRatio=float(vp / base['median']))
    if e[ip] <= O:
        return res
    flags = []
    start = max(c0 - 0.3, lo_b)
    s = np.flatnonzero((t >= start) & (t <= t[ip]))
    i_on = None
    for i in s:
        if e[i] > O and i + 1 < len(e) and e[i + 1] > O:
            i_on = i
            break
    if i_on is None:
        i_on = ip
    k = i_on
    lim = max((t[s[0]] if len(s) else t[ip]) - 0.5, lo_b)
    while k > 0 and e[k - 1] > O and t[k - 1] >= lim:
        k -= 1
    if e[k - 1] > O:
        flags.append('onsetBoundedByPreviousContact' if lim == lo_b else 'elevatedBackground')
        onset = None
    else:
        onset = crossing_time(t, e, k, O)
    end = min(c1 + 0.5, hi_b)
    f = np.flatnonzero((t >= t[ip]) & (t <= end))
    above_f = f[e[f] > O]
    j = int(above_f[-1]) if len(above_f) else ip
    if j + 1 < len(e) and e[j + 1] <= O:
        y0, y1 = e[j], e[j + 1]
        offset = float(t[j] + ((y0 - O) / (y0 - y1) if y0 != y1 else 0.0) * (t[j + 1] - t[j]))
    else:
        offset = None
        flags.append('offsetBoundedByNextContact' if end == hi_b else 'offsetAtSearchLimit')
    lo, hi = max(k - 25, 0), min(j + 25, len(S.gap[ch]))
    if S.gap[ch][lo:hi].any():
        flags.append('imuGapNear')
    res.update(responds=True, onset=onset, offset=offset, flags=flags)
    return res


def attribute(L: LapView, t_at, zones, windows):
    """(kerb, insidePass, alsoNear) for a time: export_inputs.attribute rule on the CG station at t_at."""
    sp = L.station(t_at)
    if sp is None:
        return None, False, [], None
    ins = [(min(sp - zones[k]['s0'], zones[k]['s1'] - sp), k) for k, (a, b) in windows.items()
           if a - OVERLAP_PAD_S <= t_at <= b + OVERLAP_PAD_S and min(sp - zones[k]['s0'], zones[k]['s1'] - sp) >= -K.PASS_MARGIN_M]
    if ins:
        ins.sort(reverse=True)
        return ins[0][1], True, [k for _, k in ins[1:]], sp
    oth = sorted(((min(sp - z['s0'], z['s1'] - sp), k) for k, z in zones.items()
                  if min(sp - z['s0'], z['s1'] - sp) >= -K.PASS_MARGIN_M), reverse=True)
    if oth:
        return oth[0][1], False, [k for _, k in oth[1:]], sp
    return None, False, [], sp


def lap_contacts(L: LapView, zones, windows):
    rre = rr_events(L)
    vac, vab = va_clusters(L)
    cands = [dict(rr=e, va=None, t0=e['t0'], t1=e['t1']) for e in rre]
    for v in vac:
        for c in cands:
            if v['t0'] <= c['t1'] + 0.10 and v['t1'] >= c['t0'] - 0.10:
                if c['va'] is None or v['peakRms'] > c['va']['peakRms']:
                    c['va'] = v
                c['t0'], c['t1'] = min(c['t0'], v['t0']), max(c['t1'], v['t1'])
                break
        else:
            cands.append(dict(rr=None, va=v, t0=v['t0'], t1=v['t1']))
    cands.sort(key=lambda c: c['t0'])
    out = []
    prev_end = -1e9
    for ci, c in enumerate(cands):
        lo_b = prev_end
        hi_b = cands[ci + 1]['t0'] if ci + 1 < len(cands) else 1e9
        ch = {k: refine_channel(L, k, c['t0'], c['t1'], lo_b, hi_b) for k in ('VA', 'RR')}
        resp = [k for k in ('VA', 'RR') if ch[k]['responds']]
        tpk = c['rr']['tPeak'] if c['rr'] else c['va']['tPeak']
        kerb, inside, alt, sp = attribute(L, tpk, zones, windows)
        conf = 'high' if (c['rr'] and 'VA' in resp) else ('medium' if c['rr'] else 'low')
        onsets = [ch[k]['onset'] for k in resp if ch[k]['onset'] is not None]
        offsets = [ch[k]['offset'] for k in resp if ch[k]['offset'] is not None]
        flags = sorted({f for k in resp for f in ch[k].get('flags', [])})
        if not resp:
            flags.append('noChannelAboveOnsetLevel')
        if not onsets:
            onsets = [c['t0']]
            flags.append('onsetFromDetector')
        if not offsets:
            offsets = [c['t1']]
            flags.append('offsetFromDetector')
        if len(resp) == 2 and ch['VA']['onset'] is not None and ch['RR']['onset'] is not None \
                and abs(ch['VA']['onset'] - ch['RR']['onset']) > 0.06:
            flags.append('channelOnsetsDiffer>0.06s')
        rec = dict(onsetT=r3(min(onsets)), offsetT=r3(max(offsets)),
                   peakT=r3(ch['RR']['peakT'] if ch['RR']['responds'] else (ch['VA']['peakT'] if ch['VA']['responds'] else tpk)),
                   detectorPeakT=r3(tpk), detectT=r3(c['t0']), confidence=conf, respondingChannels=resp,
                   kerb=kerb, insidePass=inside, alsoNear=alt, stationAtPeakDerived=sp,
                   stationAtOnsetDerived=L.station(min(onsets)),
                   steerSideAtPeak=L.steer_side(tpk), flags=flags,
                   rrEvent=None if c['rr'] is None else dict(t0=r3(c['rr']['t0']), t1=r3(c['rr']['t1']),
                                                              tPeak=r3(c['rr']['tPeak']), peak=round(c['rr']['peak'], 2)),
                   vaCluster=None if c['va'] is None else dict(t0=r3(c['va']['t0']), t1=r3(c['va']['t1']),
                                                                tPeak=r3(c['va']['tPeak']), peakRms=r3(c['va']['peakRms']),
                                                                detectLevel=r3(c['va']['detectLevel'])))
        for k in ('VA', 'RR'):
            x = ch[k]
            rec[k] = dict(responds=x['responds'], onsetT=r3(x.get('onset')), offsetT=r3(x.get('offset')),
                          peakT=r3(x.get('peakT')), peakRms=r3(x.get('peakRms')),
                          peakRatio=round(x['peakRatio'], 1) if 'peakRatio' in x else None,
                          baseline=x['baseline'], flags=x.get('flags', []))
        rec['loggerMsOnset'] = L.logger_ms(rec['onsetT'])
        prev_end = max(c['t1'], max(offsets))
        ko, io, _, _ = attribute(L, rec['onsetT'], zones, windows)
        rec['kerbAtOnset'] = ko
        if ko != kerb:
            rec['flags'].append('kerbAtOnsetDiffers')
        zside = zones[kerb]['side'] if kerb in zones else None
        if zside and rec['steerSideAtPeak'] in ('left', 'right') and rec['steerSideAtPeak'] != zside:
            rec['flags'].append('steeringAwayFromKerbSide(outsideKerbAtThatMoment)')
        out.append(rec)
    return out, vab, rre


# ---------------------------------------------------------------- steering / yaw onset
def onset_analysis(t, raw, lp, win, h_min, rise_min, thr_min, link_min, same_min, sigma_link, rate=None, walk_start=None,
                   pass_start=None):
    """Corner-entry onset on a direction-signed series (positive = into the corner).
    walk_start (optional): the valley / onset walk starts there instead of at the peak (used to start the steering walk
    at yaw onset + 0.3 s so that mid-corner steering dips are not taken as the pre-corner valley)."""
    m = np.flatnonzero((t >= win[0]) & (t <= win[1]))
    if len(m) < 3:
        return None
    ip = int(m[np.argmax(lp[m])])
    tpk, A = parabolic_peak(t, lp, ip)
    res = dict(peakT=tpk, peak=A, flags=[], peakAtWindowEdge=bool(ip in (m[0], m[-1])))
    if res['peakAtWindowEdge']:
        res['flags'].append('peakAtWindowEdge')
    i0 = ip
    if walk_start is not None and walk_start < t[ip]:
        i0 = max(int(np.searchsorted(t, walk_start)), 1)
        res['walkStartT'] = float(t[i0])
    # valley: walking back, the first local minimum (hysteresis h_min) from which the series rises to the peak by
    # at least max(rise_min, 0.4 * peak); shallower local minima (mid-corner dips, small unwinding) are walked through
    j, vmin, jmin, found = i0, lp[i0], i0, False
    while j > 0 and t[j - 1] > t[i0] - 10.0:
        j -= 1
        if lp[j] < vmin:
            vmin, jmin = lp[j], j
        elif lp[j] > vmin + h_min:
            deep = vmin <= max(same_min, 0.25 * max(A, 0.0))
            before_pass = pass_start is None or t[jmin] < pass_start
            moderate = before_pass and vmin <= max(same_min, 0.5 * max(A, 0.0))
            if A - vmin >= max(rise_min, 0.4 * max(A, rise_min)) and (deep or moderate):
                found = True
                break
            vmin, jmin = lp[j], j
    vmin = float(lp[jmin])
    tv = float(t[jmin])
    if not found and abs(vmin) > same_min:
        res['flags'].append('valleyAtSearchLimit')
    if vmin < -max(link_min, 0.3 * A):
        case, b, sig = 'linkedOpposite', 0.0, sigma_link
    elif vmin > same_min:
        case, b, sig = 'linkedSame', float(vmin), sigma_link
    else:
        case = 'isolated'
        bw = (t >= tv - 1.0) & (t <= tv + 0.1)
        vals = raw[bw]
        b = float(np.median(vals))
        sig = float(min(max(1.4826 * np.median(np.abs(vals - b)), thr_min / 6), 2 * sigma_link))
    if case != 'isolated':
        res['flags'].append(case)
    thr = b + max(thr_min, 3 * sig)
    res.update(valleyT=tv, valley=float(vmin), case=case, baseline=b, sigma=sig, threshold=thr)
    if A - thr < max(thr_min, 3 * sig):
        res['flags'].append('weak')
    if i0 != ip and raw[i0] <= thr:
        res['flags'].append('walkStartBelowThreshold(walkFromPeak)')
        i0 = ip
    i = i0
    while i > 0 and raw[i] > thr and t[i] > tv - 0.5:
        i -= 1
    if raw[i] > thr or i == i0:
        res['onset'] = None
        res['flags'].append('noOnset')
    else:
        res['onset'] = crossing_time(t, raw, i + 1, thr)
        i_on = i + 1
        lo, hi = b + 0.2 * (A - b), b + 0.6 * (A - b)
        k = i_on
        while k < ip and raw[k] <= hi:
            k += 1
        seg = np.arange(i_on, k + 1)
        seg = seg[(raw[seg] >= lo) & (raw[seg] <= hi)]
        tr = lin_fit_intercept(t[seg], raw[seg], b) if len(seg) >= 3 else None
        if tr is not None and (tr > res['onset'] + 0.1 or tr < res['onset'] - 2.0):
            res['flags'].append('rampOnsetRejected')
            tr = None
        res['rampOnset'] = tr
        if rate is not None:
            k = i_on
            hi90 = b + 0.9 * (A - b)
            while k < ip and lp[k] < hi90:
                k += 1
            seg = np.arange(i_on, max(k, i_on + 1) + 1)
            kk = int(seg[np.argmax(rate[seg])])
            trp, vr = parabolic_peak(t, rate, kk)
            res['ratePeakT'], res['ratePeak'] = trp, vr
    k = ip
    rel_lvl = b + 0.3 * (A - b)
    while k < len(lp) - 1 and lp[k] >= rel_lvl and t[k] < t[ip] + 8.0:
        k += 1
    res['release'] = float(t[k]) if lp[k] < rel_lvl else None
    res['plateau'] = plateau(t, lp, ip)
    return res


def interior_extremum(y, m, sign):
    """Index of the highest (sign=+1) / lowest (sign=-1) interior local extremum of y over the indices m;
    falls back to the window argmax/argmin (then at the window edge) when there is none."""
    v = sign * y[m]
    inner = [k for k in range(1, len(m) - 1) if v[k] > v[k - 1] and v[k] >= v[k + 1]]
    if inner:
        k = max(inner, key=lambda q: v[q])
        return int(m[k])
    return int(m[np.argmax(v)])


def peak_in(t, y, a, b, frac=0.97):
    m = np.flatnonzero((t >= a) & (t <= b))
    if len(m) < 3:
        return None
    i = interior_extremum(y, m, +1)
    tp, v = parabolic_peak(t, y, i)
    return dict(t=tp, value=v, plateau=plateau(t, y, i, frac), atWindowEdge=bool(i in (m[0], m[-1])))


def min_in(t, y, a, b, flat=0.5):
    m = np.flatnonzero((t >= a) & (t <= b))
    if len(m) < 3:
        return None
    i = interior_extremum(y, m, -1)
    tp, v = parabolic_peak(t, -y, i)
    v = -v
    lo, hi = i, i
    while lo > m[0] and y[lo - 1] <= v + flat:
        lo -= 1
    while hi < m[-1] and y[hi + 1] <= v + flat:
        hi += 1
    return dict(t=tp, value=v, flat=[float(t[lo]), float(t[hi])], atWindowEdge=bool(i in (m[0], m[-1])))


def brake_analysis(L: LapView, t_from, t_to):
    S = L.S
    t, p, fin = L.tE, S.brk, S.brk_finite
    m = np.flatnonzero((t >= t_from - 3.0) & (t <= t_to + 1.0))
    if not len(m):
        return None
    rs = [(m[a], m[b]) for a, b in runs_of(p[m] >= 3.0)]
    merged = []
    for r in rs:
        if merged and t[r[0]] - t[merged[-1][1]] < 0.2:
            merged[-1][1] = r[1]
        else:
            merged.append(list(r))
    cand = [r for r in merged if t_from <= t[r[0]] <= t_to and p[r[0]:r[1] + 1].max() >= 5.0]
    if not cand:
        return dict(onset=None, flags=['noBrake'])
    i0, i1 = cand[-1]
    t_on = crossing_time(t, p, i0, 3.0) if i0 > 0 else float(t[i0])
    k = i0
    while k > 0 and fin[k - 1]:
        k -= 1
    ipk = int(i0 + np.argmax(p[i0:i1 + 1]))
    if i1 + 1 < len(p):
        y0, y1 = p[i1], p[i1 + 1]
        t_rel = float(t[i1] + ((y0 - 3.0) / (y0 - y1) if y0 != y1 else 0.0) * (t[i1 + 1] - t[i1]))
    else:
        t_rel = float(t[i1])
    flags = [f'{len(cand)}BrakeRunsInWindow(lastUsed)'] if len(cand) > 1 else []
    return dict(onset=t_on, msgStart=float(t[k]), peakT=float(t[ipk]), peakBar=float(p[ipk]), release=t_rel, flags=flags)


# ---------------------------------------------------------------- per corner
def corner_record(L: LapView, zone, kid, w, labels, pub_events, apex, contacts):
    S = L.S
    cdir = +1 if zone['side'] == 'right' else -1
    rec = dict(kerb=kid, name=K.NAMES[kid], side=zone['side'], cornerDir='right' if cdir > 0 else 'left',
               kerbStationRange=[zone['s0'], zone['s1']])
    if w is None:
        rec['flags'] = ['noPass']
        return rec
    rec['passWindowT'] = [r3(w[0]), r3(w[1])]
    flags = []
    y_lp5 = cdir * (S.yr_lp5 - S.yaw_bias)
    y_lp15 = cdir * (S.yr_lp15 - S.yaw_bias)
    yw = onset_analysis(L.tB, y_lp5, y_lp15, (w[0] - 1.0, w[1] + 0.5), 2.0, 4.0, 1.5, 3.0, 3.0, S.yaw_sigma,
                        pass_start=w[0])
    y_on = yw.get('onset') if yw else None
    d_raw = -cdir * (S.steer - S.steer0)
    d_lp = -cdir * (S.steer_lp - S.steer0)
    d_rate = savgol_filter(d_raw, 5, 2, deriv=1, delta=0.05)
    st = onset_analysis(L.tE, d_raw, d_lp, (w[0] - 1.0, w[1] + 0.5), 5.0, 10.0, 3.0, 8.0, 8.0, S.steer_sigma, rate=d_rate,
                        walk_start=(y_on + 0.3) if y_on is not None else None, pass_start=w[0])
    t_on = st.get('onset') if st else None
    rel = st.get('release') if st else None
    t_entry = t_on if t_on is not None else y_on
    if t_entry is not None:
        ca, cb = max(t_entry - 0.5, w[0] - 2.0), min((rel if rel is not None else w[1] + 2.0) + 0.5, w[1] + 2.0)
        va_, vb_ = max(t_entry - 1.0, w[0] - 2.5), min((rel if rel is not None else w[1] + 2.5) + 1.0, w[1] + 2.5)
    else:
        ca, cb = w[0] - 1.0, w[1] + 1.0
        va_, vb_ = w[0] - 1.5, w[1] + 1.5
    la = peak_in(L.tA, cdir * S.la_lp, ca, cb)
    yp = peak_in(L.tB, y_lp15, ca, cb)
    vmin = min_in(L.tE, S.vrear, va_, vb_)
    vveh = min_in(L.tE, S.vveh, va_, vb_)
    vg = min_in(L.tG, S.vgps, va_, vb_)
    br = brake_analysis(L, (t_entry - 8.0) if t_entry is not None else w[0] - 9.0,
                        (t_entry + 0.5) if t_entry is not None else (vmin['t'] if vmin else w[1]))
    rec['steer'] = None if st is None else dict(
        onsetT=r3(st.get('onset')), rampOnsetT=r3(st.get('rampOnset')), ratePeakT=r3(st.get('ratePeakT')),
        ratePeakDegS=r3(st.get('ratePeak')), maxT=r3(st['peakT']), maxDeg=r3(st['peak']),
        maxPlateauT=[r3(x) for x in st['plateau']], releaseT=r3(st.get('release')), case=st['case'],
        valleyT=r3(st['valleyT']), valleyDeg=r3(st['valley']), baselineDeg=r3(st['baseline']),
        sigmaDeg=r3(st['sigma']), thresholdDeg=r3(st['threshold']), flags=st['flags'])
    rec['yaw'] = None if yw is None else dict(
        onsetT=r3(yw.get('onset')), rampOnsetT=r3(yw.get('rampOnset')), case=yw['case'], valleyT=r3(yw['valleyT']),
        baselineDegS=r3(yw['baseline']), sigmaDegS=r3(yw['sigma']), thresholdDegS=r3(yw['threshold']), flags=yw['flags'])
    rec['clip'] = dict(
        window=[r3(ca), r3(cb)],
        latGPeak=None if la is None else dict(t=r3(la['t']), g=r3(la['value']), plateauT=[r3(x) for x in la['plateau']],
                                              atWindowEdge=la['atWindowEdge']),
        yawRatePeak=None if yp is None else dict(t=r3(yp['t']), degS=r3(yp['value']), plateauT=[r3(x) for x in yp['plateau']],
                                                 atWindowEdge=yp['atWindowEdge']),
        steerMax=None if st is None else dict(t=r3(st['peakT']), deg=r3(st['peak']), plateauT=[r3(x) for x in st['plateau']],
                                              atWindowEdge=st['peakAtWindowEdge']),
        minSpeedWindow=[r3(va_), r3(vb_)],
        minSpeedRearWheels=None if vmin is None else dict(t=r3(vmin['t']), kmh=r3(vmin['value']), flatT=[r3(x) for x in vmin['flat']],
                                                         atWindowEdge=vmin['atWindowEdge']),
        minSpeedEcuVeh=None if vveh is None else dict(t=r3(vveh['t']), kmh=r3(vveh['value']), flatT=[r3(x) for x in vveh['flat']],
                                                      atWindowEdge=vveh['atWindowEdge']),
        minSpeedGpsLagCorrected=None if vg is None else dict(t=r3(vg['t']), kmh=r3(vg['value']), flatT=[r3(x) for x in vg['flat']],
                                                             atWindowEdge=vg['atWindowEdge']))
    rec['brake'] = None if br is None else {k: (r3(v) if isinstance(v, float) else v) for k, v in br.items()}
    own = [c for c in contacts if c['kerb'] == kid and c['insidePass']]
    rec['kerbContacts'] = own
    prim = own[0] if own else None
    strongest = max(own, key=lambda c: (c['rrEvent'] or {}).get('peak', 0) + (c['RR']['peakRatio'] or 0) / 100) if own else None
    rec['kerbContact'] = None if prim is None else dict(
        onsetT=prim['onsetT'], offsetT=prim['offsetT'], peakT=prim['peakT'], detectT=prim['detectT'],
        onsetVAT=prim['VA']['onsetT'], onsetRRT=prim['RR']['onsetT'], confidence=prim['confidence'],
        respondingChannels=prim['respondingChannels'], steerSideAtPeak=prim['steerSideAtPeak'], flags=prim['flags'],
        nContacts=len(own), strongestPeakT=strongest['peakT'], strongestConfidence=strongest['confidence'],
        bestConfidence=('high' if any(c['confidence'] == 'high' for c in own) else
                        'medium' if any(c['confidence'] == 'medium' for c in own) else 'low'))
    # flags
    if st is None or st.get('onset') is None:
        flags.append('noSteerOnset')
    elif st['case'] != 'isolated':
        flags.append(st['case'] + 'Corner')
    if st is not None and 'weak' in st['flags']:
        flags.append('weakSteer')
    if st is not None and st['peakAtWindowEdge']:
        flags.append('steerPeakAtWindowEdge')
    if vmin and vmin['atWindowEdge']:
        flags.append('minSpeedAtWindowEdge')
    if vmin and (vmin['flat'][1] - vmin['flat'][0]) > 1.0:
        flags.append('minSpeedFlat>1s')
    if la and la['atWindowEdge']:
        flags.append('latGPeakAtWindowEdge')
    if prim is None:
        flags.append('noKerbContact')
    elif prim['confidence'] == 'low' and len(own) == sum(c['confidence'] == 'low' for c in own):
        flags.append('kerbContactLowConfidenceOnly')
    rec['flags'] = flags
    lab = labels.get(kid)
    rec['publishedPassLabel'] = None if lab is None else dict(cls=lab['class'], bin=lab['bin'], peakRollRms=lab['peakRollRms'],
                                                              t0=lab['t0'], t1=lab['t1'])
    pubs = [e for e in pub_events if e['kerb'] == kid and e['insidePass']]
    agree = []
    for e in pubs:
        m = [c for c in own if c['rrEvent'] and abs(c['rrEvent']['t0'] - e['t0']) < 0.011 and abs(c['rrEvent']['t1'] - e['t1']) < 0.011]
        if m and m[0]['onsetT'] is not None:
            c = m[0]
            agree.append(dict(pubT0=e['t0'], pubT1=e['t1'], pubTPeak=e['tPeak'], pubPeak=e['peak'], matched=True,
                              dOnset=r3(c['onsetT'] - e['t0']), dOffset=r3(c['offsetT'] - e['t1']),
                              dPeakRRsharp=r3(c['RR']['peakT'] - e['tPeak']) if c['RR']['peakT'] is not None else None,
                              confidence=c['confidence']))
        else:
            agree.append(dict(pubT0=e['t0'], pubT1=e['t1'], pubTPeak=e['tPeak'], pubPeak=e['peak'], matched=bool(m)))
    rec['publishedEventAgreement'] = agree
    rec['viewerApex'] = None if apex is None else dict(apexT=apex.get('apexT'), apexStation=apex.get('apexStation'),
                                                       gBlockFront=apex.get('gBlockFront'))
    keys = dict(
        kerbContactOnset=(rec['kerbContact'] or {}).get('onsetT'),
        kerbContactPeak=(rec['kerbContact'] or {}).get('peakT'),
        steerOnset=(rec['steer'] or {}).get('onsetT'),
        steerRampOnset=(rec['steer'] or {}).get('rampOnsetT'),
        steerRatePeak=(rec['steer'] or {}).get('ratePeakT'),
        yawOnset=(rec['yaw'] or {}).get('onsetT'),
        brakeOnset=(rec['brake'] or {}).get('onset'),
        minSpeed=(rec['clip']['minSpeedRearWheels'] or {}).get('t'),
        latGPeak=(rec['clip']['latGPeak'] or {}).get('t'),
        yawRatePeak=(rec['clip']['yawRatePeak'] or {}).get('t'),
        steerMax=(rec['clip']['steerMax'] or {}).get('t'),
    )
    rec['keyTimes'] = keys
    rec['keyTimesLapClock'] = {k: (r3(v + L.offset_clock) if v is not None else None) for k, v in keys.items()}
    rec['keyStationsDerived'] = {k: L.station(v) for k, v in keys.items()}
    rec['keyLoggerMs'] = {k: L.logger_ms(v) for k, v in keys.items()}
    return rec


def main():
    origin, geo = K.context()
    zones = K.load_zones()
    limits = K.zoned_limits(K.base.load_limits(K.TRACK_DIR, 0.0), zones)
    records, others, sessions, repro = [], [], {}, {}
    for race, label in RACES.items():
        S = Session(race)
        offsets, _ = K.registration_offsets(race)
        kc = json.loads((ROOT / f'public/data/races/{race}/kerb_contacts.json').read_text(encoding='utf-8'))
        ak = json.loads((ROOT / f'public/data/races/{race}/apex_kpi.json').read_text(encoding='utf-8'))
        sessions[race] = dict(label=label, xrkSha256=str(S.z['xrk_sha256']), steerStraightOffsetDeg=round(S.steer0, 2),
                              steerStraightSigmaDeg=round(S.steer_sigma, 2), yawBiasDegS=round(S.yaw_bias, 3),
                              yawStraightSigmaDegS=round(S.yaw_sigma, 3), straightReferenceCounts=S.straight_counts,
                              imuGapsOver40ms=dict(VA_LA=int(S.gap['VA'].sum()), RR_YR=int(S.gap['RR'].sum())), laps={})
        nrep, ntot = 0, 0
        for lap in lap_numbers(race):
            lp = K.LapPaths(race, lap, limits, geo, origin, offsets.get(lap, (0.0, 0.0)))
            L = LapView(S, lap, lp)
            windows = {kid: w for kid in K.INSIDE if (w := lp.pass_window(zones[kid])) is not None}
            contacts, vab, rre = lap_contacts(L, zones, windows)
            pub = kc['events'][str(lap)]
            # reproduction of the published detector (time-base check)
            for e in pub:
                ntot += 1
                nrep += any(abs(round(x['t0'], 2) - e['t0']) < 0.011 and abs(round(x['t1'], 2) - e['t1']) < 0.011
                            and abs(round(x['tPeak'], 2) - e['tPeak']) < 0.011 for x in rre)
            sessions[race]['laps'][str(lap)] = dict(
                tc0Ms=L.tc0, shiftSeconds=L.shift, lapClockOffsetS=L.offset_clock, tEnd=L.t_end,
                lapMapCheck={k: S.lapmap[str(lap)][k] for k in ('tc0OwnMs', 'rmsKmhPublished', 'agree')},
                vaDetectBaseline={k: (r3(v) if isinstance(v, float) else v) for k, v in vab.items()},
                nContactCandidates=len(contacts), nRrEvents=len(rre), nPublishedEvents=len(pub))
            labels = {r['kerb']: r for r in kc['laps'][str(lap)]}
            apex = {r['kerb']: r for r in ak['laps'][str(lap)]}
            recs = []
            for kid in K.INSIDE:
                r = corner_record(L, zones[kid], kid, windows.get(kid), labels, pub, apex.get(kid), contacts)
                recs.append(dict(session=race, sessionLabel=label, lap=lap, shiftSeconds=L.shift,
                                 lapClockOffsetS=L.offset_clock, **r))
            for a, b in zip(recs, recs[1:]):
                ba, bb = a.get('brake') or {}, b.get('brake') or {}
                if ba.get('onset') is not None and ba.get('onset') == bb.get('onset'):
                    bb.setdefault('flags', []).append(f'sameBrakeRunAs:{a["kerb"]}')
                    b['flags'].append('brakeRunShared')
            for a, b in zip(recs, recs[1:]):
                sa, sb = (a.get('steer') or {}).get('onsetT'), (b.get('steer') or {}).get('onsetT')
                if sa is not None and sb is not None and abs(sa - sb) < 0.011:
                    b['flags'].append(f'sharesSteerOnsetWith:{a["kerb"]}')
                ya, yb = (a.get('yaw') or {}).get('onsetT'), (b.get('yaw') or {}).get('onsetT')
                if ya is not None and yb is not None and abs(ya - yb) < 0.011:
                    b['flags'].append(f'sharesYawOnsetWith:{a["kerb"]}')
            for r in recs:
                so, yo = (r.get('steer') or {}).get('onsetT'), (r.get('yaw') or {}).get('onsetT')
                if so is not None and yo is not None and abs(so - yo) > 0.5:
                    r['flags'].append('steerYawOnsetGap>0.5s')
            records.extend(recs)
            for c in contacts:
                if not c['insidePass']:
                    m = [e for e in pub if c['rrEvent'] and abs(c['rrEvent']['t0'] - e['t0']) < 0.011]
                    others.append(dict(session=race, lap=lap, publishedEvent=m[0] if m else None,
                                       dOnsetVsPublishedT0=r3(c['onsetT'] - m[0]['t0']) if (m and c['onsetT'] is not None) else None,
                                       **c))
            print(race, lap, 'candidates', len(contacts), 'inside', sum(c['insidePass'] for c in contacts))
        repro[race] = dict(publishedEvents=ntot, reproduced=nrep)
    # per-corner consistency: marker time relative to the pass start vs the session median of the same corner
    import collections
    rel = collections.defaultdict(list)
    for r in records:
        if r.get('passWindowT'):
            for kname in ('steerOnset', 'yawOnset', 'kerbContactOnset', 'minSpeed', 'latGPeak', 'brakeOnset'):
                v = r['keyTimes'].get(kname)
                if v is not None:
                    rel[(r['session'], r['kerb'], kname)].append(v - r['passWindowT'][0])
    for r in records:
        if not r.get('passWindowT'):
            continue
        out_ = []
        for kname in ('steerOnset', 'yawOnset', 'minSpeed', 'latGPeak', 'brakeOnset'):
            v = r['keyTimes'].get(kname)
            vals = rel.get((r['session'], r['kerb'], kname), [])
            if v is not None and len(vals) >= 3 and abs((v - r['passWindowT'][0]) - float(np.median(vals))) > 1.0:
                out_.append(kname)
        if out_:
            r['flags'].append('lapOutlierVsSessionMedian>1s:' + '+'.join(out_))
    doc = dict(
        version=1, kind='logger-ground-truth-events', track='fuji', generated='2026-10-22',
        tool='artifacts/kerb-event-truth-2026-10-22/T1-logger-events/build_logger_events.py',
        timeBase=dict(basis='every *T / t field: lap t (s) of the shipped lap JSON = GPS time base = viewer playback clock '
                            '(0 = first GPS sample of the lap)',
                      loggerToLapT='t = (logger_ms - tc0Ms) / 1000 + shiftSeconds (keyLoggerMs gives logger_ms)',
                      shift='7/30: meta.channel_time_alignment.shiftSeconds of the lap, applied to IMU and ECU alike; 7/29: 0',
                      lapClock='HUD / video-overlay lap clock = t + lapClockOffsetS (keyTimesLapClock)',
                      tc0='XRK timecode of lap t = 0, re-derived from XRK GPS Speed vs the shipped lap speed (rms 0.0003 km/h) '
                          'and equal to kerb_contacts.json timeBase.lapMapping for all 10 laps',
                      gpsSpeed='GPS Speed sample times are moved 0.09 s earlier (Doppler speed lags positions)'),
        labels=dict(measured='event times and values from logger samples (with interpolation)',
                    derived='stations (keyStationsDerived, stationAtPeakDerived) and passWindowT from the shipped registered '
                            'GPS path; kerb attribution of contacts uses them',
                    reference='publishedPassLabel / publishedEventAgreement (kerb_contacts.json) and viewerApex (apex_kpi.json) '
                              'are viewer outputs, not ground truth'),
        publishedDetectorReproduction=repro,
        corners=[dict(kerb=k, name=K.NAMES[k], side=zones[k]['side']) for k in K.INSIDE],
        method=METHOD, sessions=sessions, records=records, otherContacts=others)
    out = HERE / 'events.json'
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding='utf-8')
    print('wrote', out.name, len(records), 'records', len(others), 'other contacts', repro)


if __name__ == '__main__':
    main()
