"""T2 step 3: video/audio ground-truth event times and video<->logger offsets -> events_video.json.

Inputs (all local, outside the repo except the shipped lap/kerb JSON):
  <T2_WORK>/xrk_<race>.npz            (t2_extract_xrk.py)
  <T2_WORK>/audio/<tag>_a1.wav         (cabin microphone = audio stream index 1)
  <T2_WORK>/video/<tag>_motion.npz     (t2_video_motion.py; shake)
  <T2_WORK>/video/<tag>_ego.npz        (t2_video_egomotion.py; far-scene yaw proxy)
  <T2_WORK>/video/<tag>_ground.npz     (t2_video_ground.py; ground-plane yaw proxy)
  audio_streams.json                   (t2_audio_streams.py)
  datatrack_summary.json               (t2_datatrack.py)
Sign convention everywhere: lag / offset L > 0 means the logger (IMU / GPS) feature occurs L seconds LATER than
the video/audio feature when the video is placed on the lap clock with the overlay-timer mapping
(video-timer-reads.json). Equivalently: true lap t of a video frame = overlay-mapped lap t + L.
"""
import json
import sys

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.ndimage import gaussian_filter1d, median_filter, uniform_filter1d
from scipy.signal import butter, sosfiltfilt

from t2_common import HERE, ROOT, RACES, TAG, WORK, kerb_contacts, lap_info, load_lap, logger_to_t, pts_to_t, \
    t_to_pts, xcorr_lag
from t2_envelopes import audio_spec, band_db, imu_vibration, video_shake

sys.path.insert(0, str(ROOT / "pipeline"))
from register_gps_to_track import to_local  # noqa: E402

TRACK = json.loads((ROOT / "public/data/tracks/fuji/track.json").read_text(encoding="utf-8"))
KZ = json.loads((ROOT / "public/data/tracks/fuji/cg_study/kerb_zones.json").read_text(encoding="utf-8"))
KERB_ST = {k["id"]: (float(np.min(k["station"])), float(np.max(k["station"])), k["side"]) for k in KZ["kerbs"]}
LAP_START_STATION = 138.05

SHAKE_KEYS = ("FR_dx", "FR_dy", "D_pcx", "D_pcy")
# accelerometers only: the HP energy of the roll/pitch-rate gyros also rises with the turn-in roll before the kerb
# strike (their burst onsets lead the accelerometers by 0.06-0.10 s at the same events, imu_channel_timing.json)
IMU_KERB_CHANS = ("VerticalAcc", "LateralAcc", "InlineAcc")


def r3(x):
    return None if x is None or not np.isfinite(x) else round(float(x), 3)


def gps_course_rate(d):
    t = np.asarray(d["t"], float)
    xy = to_local(d["lat"], d["lng"], TRACK["origin"])
    cx, cz = CubicSpline(t, xy[:, 0]), CubicSpline(t, xy[:, 1])
    vx, vz = cx(t, 1), cz(t, 1)
    crs = np.unwrap(np.arctan2(vx, -vz))
    return t, np.degrees(np.gradient(crs, t)), np.maximum(np.hypot(vx, vz), 1.0)


def nearest_kerb(station):
    best = None
    for kid, (s0, s1, side) in KERB_ST.items():
        dd = 0.0 if s0 <= station <= s1 else min(abs(station - s0), abs(station - s1))
        if best is None or dd < best[1]:
            best = (kid, dd, side)
    return best


# ------------------------------------------------------------------ yaw
YAW_SIGNALS = {
    "farScene": ("ego", "uMedFar", "median horizontal Farneback flow of textured far-scene pixels (rows < 200 px, mostly "
                                   "the right windshield area); forward-motion parallax not removed"),
    "groundPlane": ("ground", "Ag", "shared intercept A of u = A + k_row*(x - 640) fitted (IRLS) over textured ground-band "
                                    "pixels (rows 200-292 px) on both sides of the FOE column; ground parallax removed"),
}
BANDS = {"bp0.5-3Hz": (0.5, 3.0), "bp1-4Hz": (1.0, 4.0)}


def prep(raw):
    """NaN-fill, 5-frame running median (wipers, rain), zero-phase Gaussian sigma 4 frames (0.13 s).
    Zero-phase filters do not move a cross-correlation peak."""
    v = np.asarray(raw, float)
    good = np.isfinite(v)
    v = np.interp(np.arange(len(v)), np.flatnonzero(good), v[good])
    return gaussian_filter1d(median_filter(v, 5, mode="nearest"), 4.0)


def band_lag(tv, v, ty, g, T0, T1, band):
    """Lag of the band-passed video yaw vs band-passed gyro (100 Hz common grid, zero-phase Butterworth 2nd order).
    Band-passing keeps the transients (corrections, turn-in/out) and drops the slowly varying parallax/speed term."""
    grid = np.arange(T0 - 3.0, T1 + 3.0, 0.01)
    sos = butter(2, band, "bp", fs=100, output="sos")
    vb = sosfiltfilt(sos, np.interp(grid, tv, v))
    gb = sosfiltfilt(sos, np.interp(grid, ty, g))
    L, r, _ = xcorr_lag(grid, vb, grid, gb, T0, T1, max_lag=0.8)
    q = []
    edges = np.linspace(T0, T1, 5)
    for a, b in zip(edges[:-1], edges[1:]):
        q.append(xcorr_lag(grid, vb, grid, gb, a, b, max_lag=0.8)[0])
    return L, r, q


def yaw_analysis(race, z, sigs, info):
    """sigs: name -> raw per-frame series (common pts_mid). Per lap and estimator: broadband lag vs the gyro,
    band-passed lags (transient timing), sliding 20 s windows (broadband), gain; plus reference lags
    (gyro vs GPS course rate etc.) and corner turn-in onsets on the combined deg/s video yaw."""
    ty_all, gy_all = z["YawRate_t"], z["YawRate"]
    tp = sigs["pts_mid"]
    li1 = info[1]
    proc, signs = {}, {}
    for n in YAW_SIGNALS:
        raw = np.nan_to_num(np.asarray(sigs[n], float))
        tv1 = pts_to_t(li1, tp)
        s = np.sign(np.corrcoef(np.interp(np.clip(tv1, 2, li1["tEnd"] - 2), logger_to_t(li1, ty_all, "logger"), gy_all), raw)[0, 1])
        signs[n] = -1.0 if s < 0 else 1.0
        proc[n] = signs[n] * prep(sigs[n])
    laps, corners = {}, {}
    for k, li in info.items():
        d = load_lap(race, k)
        tv = pts_to_t(li, tp)
        ty = logger_to_t(li, ty_all, "logger")
        tg, cr, spd = gps_course_rate(d)
        gsign = np.sign(np.corrcoef(np.interp(tg, ty, gy_all), cr)[0, 1])
        gy = gy_all * gsign  # gyro in course-rate sign: + = right turn
        T0, T1 = 2.0, li["tEnd"] - 2.0
        est, degs = {}, []
        for n, v in proc.items():
            vyl = v * gsign
            L, r, _ = xcorr_lag(tv, vyl, ty, gy, T0, T1, max_lag=1.0)
            row = {"broadbandLagS": r3(L), "broadbandR": r3(r)}
            for bn, band in BANDS.items():
                Lb, rb, q = band_lag(tv, vyl, ty, gy, T0, T1, band)
                row[bn] = {"lagS": r3(Lb), "r": r3(rb), "quarterLagsS": [r3(x) for x in q]}
            win = []
            for c in np.arange(T0 + 10, T1 - 10 + 1e-9, 5.0):
                m = (ty >= c - 10) & (ty <= c + 10)
                if np.std(gy[m]) < 4.0:
                    continue
                Lw, rw, _ = xcorr_lag(tv, vyl, ty, gy, c - 10, c + 10, max_lag=0.8)
                if rw >= 0.7:
                    win.append((float(c), Lw, rw))
            win = np.array(win) if win else np.zeros((0, 3))
            if len(win) >= 4:
                coef = np.linalg.lstsq(np.vstack([win[:, 0], np.ones(len(win))]).T, win[:, 1], rcond=None)[0]
                row["slidingBroadband"] = {"n": int(len(win)), "medianLagS": r3(np.median(win[:, 1])),
                                           "iqrS": [r3(np.percentile(win[:, 1], 25)), r3(np.percentile(win[:, 1], 75))],
                                           "trendMsPer100s": r3(float(coef[0]) * 1e5),
                                           "windows": [[round(c, 1), round(l, 3), round(rr, 3)] for c, l, rr in win]}
            else:
                row["slidingBroadband"] = {"n": int(len(win))}
            m = (ty >= T0) & (ty <= T1)
            vv = np.interp(ty[m] - L, tv, vyl)
            gain = float(np.dot(vv, gy[m]) / np.dot(gy[m], gy[m]))
            row["gainVideoUnitsPerDegS"] = round(gain, 5)
            est[n] = row
            degs.append(vyl / gain)
        comb = np.mean(degs, axis=0)
        Lc, rc, _ = xcorr_lag(tv, comb, ty, gy, T0, T1, max_lag=1.0)
        Lg, rg, _ = xcorr_lag(tv, comb, tg, cr, T0, T1, max_lag=1.0)
        accy = np.asarray(d["accy"], float) * 9.80665
        aov = accy / spd
        sa = np.sign(np.corrcoef(aov, cr)[0, 1])
        La, ra, _ = xcorr_lag(tv, comb, tg, sa * aov, T0, T1, max_lag=1.0)
        Lgc, rgc, _ = xcorr_lag(ty, gy, tg, cr, T0, T1, max_lag=1.0)
        tgy = logger_to_t(li, z["GPS_Yaw_Rate_t"], "gps")
        sgy = np.sign(np.corrcoef(np.interp(tg, tgy, z["GPS_Yaw_Rate"]), cr)[0, 1])
        Lgy, rgy, _ = xcorr_lag(ty, gy, tgy, sgy * z["GPS_Yaw_Rate"], T0, T1, max_lag=1.0)
        # transient yaw lag = mean over estimators of the 0.5-3 Hz band lag (requires r >= 0.4 for each)
        bl = [est[n]["bp0.5-3Hz"]["lagS"] for n in est if est[n]["bp0.5-3Hz"]["r"] >= 0.4]
        allq = [x for n in est for x in est[n]["bp0.5-3Hz"]["quarterLagsS"] if est[n]["bp0.5-3Hz"]["r"] >= 0.4]
        ty_lag = float(np.mean(bl)) if len(bl) == len(est) else None
        se = None
        if ty_lag is not None:
            spread = abs(bl[0] - bl[-1]) / 2
            qse = float(np.std(allq) / np.sqrt(len(allq))) if allq else 0.0
            se = max(0.01, float(np.hypot(spread, qse)))
        laps[str(k)] = {"estimators": est,
                        "transientYawLagS": r3(ty_lag), "transientYawLagSeS": r3(se),
                        "transientYawLagRule": "mean of farScene and groundPlane 0.5-3 Hz band-passed lags; None if either r < 0.4",
                        "combinedBroadbandLagS": r3(Lc), "combinedBroadbandR": r3(rc),
                        "combinedVsGpsCourseRateLagS": r3(Lg), "rGpsCourse": r3(rg),
                        "combinedVsAccyOverSpeedLagS": r3(La), "rAccyOverSpeed": r3(ra),
                        "gyroVsGpsCourseRateLagS": r3(Lgc), "rGyroGps": r3(rgc),
                        "gyroVsXrkGpsYawRateLagS": r3(Lgy), "rGyroXrkGpsYawRate": r3(rgy)}
        lag_for_search = ty_lag if ty_lag is not None else Lc
        corners[str(k)] = turn_in(race, k, li, d, tv, comb, ty, gy, z, lag_for_search)
    return laps, corners, signs


def segments(t, y, thr, min_len):
    on = np.abs(y) > thr
    out = []
    i = 0
    n = len(y)
    while i < n:
        if on[i]:
            j = i
            sg = np.sign(y[i])
            while j + 1 < n and on[j + 1] and np.sign(y[j + 1]) == sg:
                j += 1
            if t[j] - t[i] >= min_len:
                out.append((i, j, sg))
            i = j + 1
        else:
            i += 1
    return out


def onset_rel(t, y, sg, t_lo, t_hi, frac=0.3):
    """Signed series y (corner sign sg). peak = max of sg*y on [t_lo, t_hi]; onset = FIRST upward crossing of
    frac*peak on [t_lo, t_peak] (linear interpolation). None when sg*y already exceeds the level at t_lo."""
    yy = sg * y
    m = np.flatnonzero((t >= t_lo) & (t <= t_hi))
    if len(m) < 3:
        return None, None
    ip = int(m[np.argmax(yy[m])])
    pk = float(yy[ip])
    if pk <= 0:
        return None, None
    thr = frac * pk
    if yy[m[0]] >= thr:
        return None, pk
    for i in range(m[0], ip):
        if yy[i] < thr <= yy[i + 1]:
            return float(t[i] + (thr - yy[i]) / (yy[i + 1] - yy[i]) * (t[i + 1] - t[i])), pk
    return None, pk


def turn_in(race, k, li, d, tv, vdeg, ty, gy, z, lap_lag):
    """Corners from the gyro: |yaw| > 8 deg/s for >= 1.2 s (0.2 s smoothed). Onset rule (all signals): last
    crossing of 30 % of the corner's own peak before the peak. Steering = ECU_STEER_ANG (logger clock + shift)."""
    gs = uniform_filter1d(gy, 10)
    vs = uniform_filter1d(vdeg, 6)
    tst = logger_to_t(li, z["ECU_STEER_ANG_t"], "logger")
    st = z["ECU_STEER_ANG"]
    st_sign = np.sign(np.corrcoef(np.interp(ty, tst, st), gs)[0, 1])
    st = st * st_sign
    dist = np.asarray(d["dist"], float)
    tl = np.asarray(d["t"], float)
    out = []
    for i, j, sg in segments(ty, gs, 8.0, 1.2):
        if ty[i] < 3.0 or ty[j] > li["tEnd"] - 1.0:
            continue
        tpk = float(ty[i + int(np.argmax(sg * gs[i:j + 1]))])
        tg_on, gpk = onset_rel(ty, gs, sg, ty[i] - 1.5, ty[j])
        if tg_on is None:
            continue
        # video quality gate: local correlation of the lag-aligned video yaw with the gyro around the corner
        mm = (ty >= tg_on - 2.0) & (ty <= tpk + 1.0)
        vloc = np.interp(ty[mm] - lap_lag, tv, vs)
        rloc = float(np.corrcoef(vloc, gs[mm])[0, 1]) if mm.sum() > 20 else np.nan
        tv_on, vpk = onset_rel(tv, vs, sg, tg_on - lap_lag - 1.0, tpk - lap_lag + 0.5)
        amp = (vpk / gpk) if (vpk is not None and gpk) else np.nan
        video_ok = bool(np.isfinite(rloc) and rloc >= 0.8 and 0.5 <= amp <= 2.0 and tv_on is not None)
        ts_on, spk = onset_rel(tst, st, sg, tg_on - 1.5, tpk)
        station = float(np.interp(tg_on, tl, dist)) + LAP_START_STATION
        kid, kd, kside = nearest_kerb(station)
        out.append({"gyroOnsetLapT": r3(tg_on), "direction": "right" if sg > 0 else "left",
                    "stationAtGyroOnsetM": round(station, 1),
                    "nearestKerb": kid, "nearestKerbDistM": round(kd, 1),
                    "peakYawDegS": r3(gpk), "gyroPeakLapT": r3(tpk),
                    "videoOnsetLapTOverlay": r3(tv_on) if video_ok else None,
                    "videoOnsetPts": r3(t_to_pts(li, tv_on)) if video_ok else None,
                    "videoLocalR": r3(rloc), "videoAmpRatio": r3(amp), "videoUsable": video_ok,
                    "steerOnsetLapT": r3(ts_on),
                    "gyroMinusVideoOnsetS": r3(tg_on - tv_on) if video_ok else None,
                    "steerMinusGyroOnsetS": r3(ts_on - tg_on) if ts_on is not None else None,
                    "steerMinusVideoOnsetS": r3(ts_on - tv_on) if (video_ok and ts_on is not None) else None})
    return out


# ------------------------------------------------------------------ kerb events
def burst_times(t, e, lo, hi, base, start_rise=6.0):
    """Main burst of envelope e (dB) on [lo, hi]: peak = max on [lo, hi]; onset50/offset50 = crossings of
    base + 0.5*(peak - base) walking backward/forward from the peak; start/end = crossings of base + start_rise dB
    (only when the burst rises >= start_rise + 2 dB). Linear interpolation between samples."""
    m = np.flatnonzero((t >= lo) & (t <= hi))
    if len(m) < 3:
        return None
    ip = int(m[np.argmax(e[m])])
    pk = float(e[ip])
    rise = pk - base
    out = {"tPeak": float(t[ip]), "riseDb": round(rise, 2), "peakAtWindowEdge": bool(ip in (m[0], m[-1]))}

    def back(thr):
        i = ip
        while i > 0 and e[i - 1] >= thr:
            i -= 1
        if i == 0:
            return None
        return float(t[i - 1] + (thr - e[i - 1]) / (e[i] - e[i - 1]) * (t[i] - t[i - 1]))

    def fwd(thr):
        j = ip
        while j < len(e) - 1 and e[j + 1] >= thr:
            j += 1
        if j == len(e) - 1:
            return None
        return float(t[j] + (e[j] - thr) / (e[j] - e[j + 1]) * (t[j + 1] - t[j]))

    out["onset50"] = back(base + 0.5 * rise) if rise > 0 else None
    out["offset50"] = fwd(base + 0.5 * rise) if rise > 0 else None
    ok = rise >= start_rise + 2.0
    out["start"] = back(base + start_rise) if ok else None
    out["end"] = fwd(base + start_rise) if ok else None
    return out


def grade(b_x, b_imu, r_edge):
    """high: both bursts rise >= 12 dB, peaks not at the window edge, edge xcorr r >= 0.8;
    medium: both rise >= 8 dB and r >= 0.6; low: otherwise; none: no burst."""
    if b_x is None or b_imu is None or b_x["onset50"] is None or b_imu["onset50"] is None:
        return "none"
    edge = b_x["peakAtWindowEdge"] or b_imu["peakAtWindowEdge"]
    if b_x["riseDb"] >= 12 and b_imu["riseDb"] >= 12 and not edge and r_edge >= 0.8:
        return "high"
    if b_x["riseDb"] >= 8 and b_imu["riseDb"] >= 8 and not edge and r_edge >= 0.6:
        return "medium"
    return "low"


def kerb_events(race, z, mo, info, prior_lag):
    """Per published IMU kerb-contact event (kerb_contacts.json): main rumble burst in the cabin audio, main
    shake burst in the video and main IMU-vibration burst, all with the same burst rule (burst_times). The IMU
    burst is searched on the published event extent [t0 - 0.2, t1 + 0.2]; the audio / shake bursts on the same
    extent moved earlier by prior_lag (the session's video-yaw lag, an independent measurement) so that the
    same physical burst is picked. Lap t of audio/shake = overlay map."""
    ev_out = []
    kc = kerb_contacts(race)["events"]
    for k, evs in kc.items():
        li = info[int(k)]
        for idx, e in enumerate(evs):
            w0, w1 = e["t0"] - 2.0, e["t1"] + 2.0
            tp, f, P = audio_spec(race, t_to_pts(li, w0 - 1.5), t_to_pts(li, w1 + 1.5))
            ta = pts_to_t(li, tp)
            ea = band_db(ta, f, P, 20, 120, 0.1)
            ti, ei = imu_vibration(z, li, w0 - 1.5, w1 + 1.5, chans=IMU_KERB_CHANS)
            tvs, es = video_shake(mo, t_to_pts(li, w0 - 1.5), t_to_pts(li, w1 + 1.5), keys=SHAKE_KEYS)
            tsl = pts_to_t(li, tvs)

            def base_of(tt, ee):
                m = (tt >= w0) & (tt <= w1)
                return float(np.percentile(ee[m], 20))

            lo, hi = e["t0"] - 0.2, e["t1"] + 0.2
            bi = burst_times(ti, ei, lo, hi, base_of(ti, ei))
            ba = burst_times(ta, ea, lo - prior_lag, hi - prior_lag, base_of(ta, ea))
            bs = burst_times(tsl, es, lo - prior_lag, hi - prior_lag, base_of(tsl, es))

            def edge(tx, ex, b):
                if b is None or b["onset50"] is None:
                    return None, None
                L, r, _ = xcorr_lag(tx, ex, ti, ei, b["onset50"] - 0.3, b["onset50"] + 0.3, max_lag=0.5)
                return L, r

            La, ra = edge(ta, ea, ba)
            Ls, rs = edge(tsl, es, bs)
            ga = grade(ba, bi, ra or 0)
            gs = grade(bs, bi, rs or 0)

            def lag(bx, key):
                if bi is None or bx is None or bi.get(key) is None or bx.get(key) is None:
                    return None
                return r3(bi[key] - bx[key])

            def pt(x):
                return r3(t_to_pts(li, x)) if x is not None else None

            def pack(b, video):
                if b is None:
                    return {}
                d = {"onset50LapTOverlay": r3(b["onset50"]), "offset50LapTOverlay": r3(b["offset50"]),
                     "startLapTOverlay": r3(b["start"]), "endLapTOverlay": r3(b["end"]),
                     "peakLapTOverlay": r3(b["tPeak"]), "riseDb": b["riseDb"], "peakAtWindowEdge": b["peakAtWindowEdge"]}
                if video:
                    d.update({"onset50Pts": pt(b["onset50"]), "offset50Pts": pt(b["offset50"]),
                              "startPts": pt(b["start"]), "endPts": pt(b["end"])})
                return d

            row = {"lap": int(k), "index": idx, "kerb": e["kerb"], "insidePass": e["insidePass"], "bin": e["bin"],
                   "peakRollRms": e["peak"], "imuT0Published": e["t0"], "imuT1Published": e["t1"],
                   "imuTPeakPublished": e["tPeak"], "stationAtPeak": e["stationAtPeak"],
                   "imuVibration": dict(signal="HP > 3 Hz energy of VerticalAcc+LateralAcc+InlineAcc (50 Hz), 0.1 s centred, lap t",
                                        **{kk.replace("Overlay", ""): vv for kk, vv in pack(bi, False).items()}),
                   "audio": dict(stream=1, signal="20-120 Hz energy of the cabin microphone, 0.1 s centred",
                                 **pack(ba, True), onsetLagImuMinusAudioS=lag(ba, "onset50"),
                                 startLagImuMinusAudioS=lag(ba, "start"), edgeXcorrLagS=r3(La), edgeXcorrR=r3(ra),
                                 confidence=ga),
                   "shake": dict(signal="HP > 4 Hz frame-to-frame motion energy (scene FR dx/dy + dashboard phase shift), 0.1 s",
                                 **pack(bs, True), onsetLagImuMinusShakeS=lag(bs, "onset50"),
                                 startLagImuMinusShakeS=lag(bs, "start"), edgeXcorrLagS=r3(Ls), edgeXcorrR=r3(rs),
                                 confidence=gs),
                   "shakeMinusAudioOnset50S": r3(bs["onset50"] - ba["onset50"]) if (bs and ba and bs["onset50"] is not None and ba["onset50"] is not None) else None}
            ev_out.append(row)
    return ev_out


# ------------------------------------------------------------------ whole-lap envelope xcorr
def lap_envelope_lags(race, z, mo, info):
    out = {}
    for k, li in info.items():
        T0, T1 = 1.0, li["tEnd"] - 1.0
        tp, f, P = audio_spec(race, t_to_pts(li, T0 - 3), t_to_pts(li, T1 + 3))
        ta = pts_to_t(li, tp)
        ea = band_db(ta, f, P, 20, 120, 0.1)
        ti, ei = imu_vibration(z, li, T0 - 2, T1 + 2, chans=IMU_KERB_CHANS)
        tvs, es = video_shake(mo, t_to_pts(li, T0 - 2), t_to_pts(li, T1 + 2), keys=SHAKE_KEYS)
        tsl = pts_to_t(li, tvs)
        hp = lambda tt, ee: ee - median_filter(ee, size=max(3, int(round(2.0 / np.median(np.diff(tt))))), mode="nearest")
        A, V, S = hp(ta, ea), hp(ti, ei), hp(tsl, es)
        La, ra, _ = xcorr_lag(ta, A, ti, V, T0 + 1, T1 - 1, max_lag=0.8)
        Ls, rs, _ = xcorr_lag(tsl, S, ti, V, T0 + 1, T1 - 1, max_lag=0.8)
        Las, ras, _ = xcorr_lag(ta, A, tsl, S, T0 + 1, T1 - 1, max_lag=0.5)
        # quarters for a spread estimate
        q = []
        edges = np.linspace(T0 + 1, T1 - 1, 5)
        for a, b in zip(edges[:-1], edges[1:]):
            q.append(xcorr_lag(ta, A, ti, V, a, b, max_lag=0.8)[0])
        out[str(k)] = {"audioVsImuLagS": r3(La), "rAudioImu": r3(ra), "audioVsImuQuarterLagsS": [r3(x) for x in q],
                       "shakeVsImuLagS": r3(Ls), "rShakeImu": r3(rs),
                       "audioVsShakeLagS": r3(Las), "rAudioShake": r3(ras)}
    return out


def med_iqr(v):
    v = [x for x in v if x is not None]
    if not v:
        return {"n": 0}
    return {"n": len(v), "median": r3(np.median(v)), "iqr": [r3(np.percentile(v, 25)), r3(np.percentile(v, 75))]}


def combine(yaw, evs, dtrack, info, calib):
    """Per lap: estimates of the offset L (true lap t of a frame = overlay-mapped lap t + L).
    best = transient video-yaw lag when available (measured); otherwise the data-track logger-clock time of the frame
    plus the 7/30 calibration constant (estimated: assumes the same camera/logger latency on both days)."""
    out = {}
    for k in info:
        ks = str(k)
        dl = dtrack["laps"][ks]["dtLoggerClock"]["median"]
        dg = dtrack["laps"][ks]["dtGpsLatest"]["median"]
        est = []
        y = yaw[ks]
        if y["transientYawLagS"] is not None:
            est.append({"method": "transientVideoYawVsGyro", "label": "measured", "lagS": y["transientYawLagS"],
                        "seS": y["transientYawLagSeS"]})
        for key, lk, name in (("audio", "onsetLagImuMinusAudioS", "kerbAudioOnsets"),
                              ("shake", "onsetLagImuMinusShakeS", "kerbShakeOnsets")):
            v = [e[key][lk] for e in evs if e["lap"] == k and e[key]["confidence"] in ("high", "medium") and e[key][lk] is not None]
            if v:
                se = max(0.03, 1.25 * float(np.std(v)) / np.sqrt(len(v))) if len(v) > 1 else None
                est.append({"method": name, "label": "measured", "lagS": r3(np.median(v)), "seS": r3(se), "n": len(v)})
        pred = dl + calib if calib is not None else None
        if pred is not None:
            est.append({"method": "dataTrackLoggerClockPlusCalibration", "label": "estimated", "lagS": r3(pred),
                        "seS": 0.015, "note": f"dtLoggerClock {dl} + calibration {r3(calib)} s (7/30 transient-yaw minus data-track mean)"})
        if y["transientYawLagS"] is not None:
            best, basis, label, se = y["transientYawLagS"], "transientVideoYawVsGyro", "measured", y["transientYawLagSeS"]
        else:
            best, basis, label, se = r3(pred), "dataTrackLoggerClockPlusCalibration", "estimated", 0.02
        out[ks] = {"estimates": est, "bestOffsetS": best, "bestOffsetSeS": se, "bestBasis": basis, "bestLabel": label,
                   "lapStartPtsOverlay": round(info[k]["lapStartPts"], 4),
                   "lapStartPtsCorrected": round(info[k]["lapStartPts"] - best, 4),
                   "mapping": "true lap t = PTS - lapStartPtsCorrected - delta (delta = lap meta first_sample_after_lap_start_seconds "
                              f"= {info[k]['delta']})",
                   "dataTrackLoggerClockDtS": dl, "bestMinusDataTrackLoggerS": r3(best - dl),
                   "dataTrackGpsLatestDtS": dg, "bestMinusDataTrackGpsLatestS": r3(best - dg)}
    return out


def main():
    dtrack_all = json.loads((HERE / "datatrack_summary.json").read_text(encoding="utf-8"))["sessions"]
    streams = json.loads((HERE / "audio_streams.json").read_text(encoding="utf-8"))
    res = {}
    for race in RACES:
        tag = TAG[race]
        info = lap_info(race)
        z = np.load(WORK / f"xrk_{race}.npz")
        mo = np.load(WORK / f"video/{tag}_motion.npz")
        ego = np.load(WORK / f"video/{tag}_ego.npz")
        gr = np.load(WORK / f"video/{tag}_ground.npz")
        assert np.allclose(ego["pts_mid"], gr["pts_mid"])
        sigs = {"pts_mid": ego["pts_mid"], "farScene": ego["uMedFar"], "groundPlane": gr["Ag"]}
        yaw, corners, signs = yaw_analysis(race, z, sigs, info)
        print(race, "transient yaw", {k: (v["transientYawLagS"], v["transientYawLagSeS"]) for k, v in yaw.items()}, flush=True)
        tl = [v["transientYawLagS"] for v in yaw.values() if v["transientYawLagS"] is not None]
        prior = float(np.median(tl)) if tl else float(np.median([v["combinedBroadbandLagS"] for v in yaw.values()]))
        evs = kerb_events(race, z, mo, info, prior)
        env = lap_envelope_lags(race, z, mo, info)
        res[race] = dict(info=info, yaw=yaw, corners=corners, signs=signs, prior=prior, evs=evs, env=env)
    # calibration of the data-track mapping on 7/30 (measured transient yaw available on every lap)
    dry = "fuji_aim_2020_07_30"
    dd = [res[dry]["yaw"][k]["transientYawLagS"] - dtrack_all[dry]["laps"][k]["dtLoggerClock"]["median"]
          for k in res[dry]["yaw"] if res[dry]["yaw"][k]["transientYawLagS"] is not None]
    calib = float(np.mean(dd))
    calib_sd = float(np.std(dd))
    result = {"kind": "T2 video/audio ground-truth times and video<->logger offsets (Fuji 2020-07-29/30)",
              "labels": {"measured": "computed directly from the local video/audio/XRK by the scripts in this folder",
                         "derived": "arithmetic combination of measured values",
                         "estimated": "depends on an assumption stated next to it"},
              "signConvention": __doc__.split("Sign convention everywhere:")[1].strip(),
              "timeBases": {"lapT": "shipped lap JSON t (0 = first GPS sample of the lap), GPS time base = viewer clock",
                            "imu": "XRK 50 Hz, t = (logger_ms - tc0)/1000 + shift (shift = lap meta channel_time_alignment on 7/30, 0 on 7/29)",
                            "pts": "ffmpeg PTS of SCHD0613/0624.MOV; both files start mid-GOP: the first decodable frame has PTS 1.000 s",
                            "overlayMapping": "PTS = lapStartPts + delta + t (artifacts/video-position-gap-2026-10-02/video-timer-reads.json)"},
              "audioStreams": {"verdict": streams["verdict"], "detail": "audio_streams.json",
                               "roadKerbStream": 1, "dataTrackStream": 2},
              "dataTrackCalibration": {"label": "measured on 7/30 (6 laps)",
                                       "definition": "transientYawLag - dtLoggerClock (s): true lap t of a frame = data-track logger-clock lap t + this",
                                       "meanS": r3(calib), "sdS": r3(calib_sd), "perLapS": [r3(x) for x in dd]},
              "sessions": {}}
    for race, R in res.items():
        info, yaw, corners, evs, env = R["info"], R["yaw"], R["corners"], R["evs"], R["env"]
        comb = combine(yaw, evs, dtrack_all[race], info, calib)
        for e in evs:
            b = comb[str(e["lap"])]["bestOffsetS"]
            for key in ("audio", "shake"):
                for f in ("onset50", "start", "offset50", "end"):
                    v = e[key].get(f + "LapTOverlay")
                    e[key][f + "LapTCorrected"] = r3(v + b) if v is not None else None
            e["imuOnsetMinusCorrectedAudioOnsetS"] = r3(e["audio"]["onsetLagImuMinusAudioS"] - b) if e["audio"]["onsetLagImuMinusAudioS"] is not None else None
            e["imuOnsetMinusCorrectedShakeOnsetS"] = r3(e["shake"]["onsetLagImuMinusShakeS"] - b) if e["shake"]["onsetLagImuMinusShakeS"] is not None else None
        hi = lambda key, lk, conf: [e[key][lk] for e in evs if e[key]["confidence"] in conf and e[key][lk] is not None]
        dl = {str(k): dtrack_all[race]["laps"][str(k)]["dtLoggerClock"]["median"] for k in info}
        resid = lambda key, lk, conf: [e[key][lk] - dl[str(e["lap"])] for e in evs if e[key]["confidence"] in conf and e[key][lk] is not None]
        corr_res = lambda key: [e[key] for e in evs if e[key] is not None]
        cv = {"right": [], "left": []}
        cs_ = {"right": [], "left": []}
        for cl in corners.values():
            for c in cl:
                if c["gyroMinusVideoOnsetS"] is not None:
                    cv[c["direction"]].append(c["gyroMinusVideoOnsetS"])
                if c["steerMinusGyroOnsetS"] is not None:
                    cs_[c["direction"]].append(c["steerMinusGyroOnsetS"])
        bests = [v["bestOffsetS"] for v in comb.values()]
        result["sessions"][race] = {
            "videoYawSigns": R["signs"],
            "videoYawSignals": {n: YAW_SIGNALS[n][2] for n in YAW_SIGNALS},
            "perLapOffsets": comb,
            "sessionBestOffsetMedianS": r3(np.median(bests)),
            "sessionBestOffsetRangeS": [r3(min(bests)), r3(max(bests))],
            "yawPerLap": yaw,
            "turnInPerLap": corners,
            "turnInSummary": {"gyroMinusVideoOnsetS": {d: med_iqr(v) for d, v in cv.items()},
                              "steerMinusGyroOnsetS": {d: med_iqr(v) for d, v in cs_.items()},
                              "note": "video turn-in onset uses the mean of the two video-yaw estimators in deg/s; the two "
                                      "estimators have opposite left/right biases, so per-corner onsets carry about +-0.1 s of "
                                      "estimator-dependent error; use the lap-level transient lag for clock offsets"},
            "kerbEvents": evs,
            "kerbEventSummary": {
                "nEvents": len(evs), "priorLagUsedForSearchWindowS": r3(R["prior"]),
                "imuMinusAudioOnsetS": {"high": med_iqr(hi("audio", "onsetLagImuMinusAudioS", ("high",))),
                                        "highMedium": med_iqr(hi("audio", "onsetLagImuMinusAudioS", ("high", "medium")))},
                "imuMinusShakeOnsetS": {"high": med_iqr(hi("shake", "onsetLagImuMinusShakeS", ("high",))),
                                        "highMedium": med_iqr(hi("shake", "onsetLagImuMinusShakeS", ("high", "medium")))},
                "imuMinusAudioOnsetMinusDataTrackS": {"high": med_iqr(resid("audio", "onsetLagImuMinusAudioS", ("high",))),
                                                      "highMedium": med_iqr(resid("audio", "onsetLagImuMinusAudioS", ("high", "medium")))},
                "imuMinusShakeOnsetMinusDataTrackS": {"high": med_iqr(resid("shake", "onsetLagImuMinusShakeS", ("high",))),
                                                      "highMedium": med_iqr(resid("shake", "onsetLagImuMinusShakeS", ("high", "medium")))},
                "imuOnsetMinusCorrectedAudioOnsetS": med_iqr([e["imuOnsetMinusCorrectedAudioOnsetS"] for e in evs if e["audio"]["confidence"] in ("high", "medium")]),
                "imuOnsetMinusCorrectedShakeOnsetS": med_iqr([e["imuOnsetMinusCorrectedShakeOnsetS"] for e in evs if e["shake"]["confidence"] in ("high", "medium")]),
                "shakeMinusAudioOnset50S": med_iqr([e["shakeMinusAudioOnset50S"] for e in evs
                                                    if e["audio"]["confidence"] in ("high", "medium") and e["shake"]["confidence"] in ("high", "medium")])},
            "lapEnvelopeLags": env,
            "lapEnvelopeNote": "whole-lap audio/shake vs IMU vibration envelope correlations are weak (r < 0.3) and are not used for the offset"}
    (HERE / "events_video.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print("calibration", calib, calib_sd)
    print("written")


if __name__ == "__main__":
    main()
