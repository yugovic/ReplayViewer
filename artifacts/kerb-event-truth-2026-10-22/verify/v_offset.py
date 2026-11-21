"""Verifier step 3: video<->logger offset L per lap from my own signals (no T1-T4 intermediates).

Inputs (all own extractions or raw repository data):
  $VERIFY_WORK/xrk_<race>.npz          v_xrk.py (native XRK channels, logger ms) + lap map tc0
  $VERIFY_WORK/flow_<tag>.npz          v_video_flow.py (median horizontal image motion per frame pair, mid PTS)
  $VERIFY_WORK/audio_<tag>.npz         v_audio_env.py (band energies of audio streams 1/2, PTS base)
  public/data/races/<race>/osaki_hmr_demio_101_lap_NNN.json   (t, lat, lng, meta delta / shift)
  artifacts/video-position-gap-2026-10-02/video-timer-reads.json (overlay timer reads -> lap start PTS)

Time bases
  lap t   : shipped lap JSON t (GPS base, viewer clock)
  gyro    : t = (logger_ms - tc0) / 1000 + shift   (shift = lap meta channel_time_alignment.shiftSeconds on
            7/30, 0 on 7/29); the unshifted variant is reported too
  video   : overlay-mapped t_ov = PTS - lapStartPts - delta, lapStartPts = referencePts +
            (lap_start_time - referenceLapStart) - perLapCorrection  (video-timer-reads.json)
  L       : argmax_L corr(video(t_ov), logger(t_ov + L));  L > 0 = logger feature later than the
            overlay-placed video feature; true lap t of a frame = t_ov + L (same convention as the join)

Methods (each lap):
  yawLevel     video yaw proxy (-dx/dt, sign fixed by the correlation) vs IMU YawRate, both 0.2 s boxcar,
               whole lap, normalised xcorr, parabolic peak
  yawTransient same on the time derivative of 0.3 s-smoothed signals (corner entry / exit flanks)
  gpsCourse    video yaw vs GPS course rate from the shipped lat/lng (no logger-clock channel involved)
  audioVib     log band energy 20-120 Hz of audio stream 1 vs log IMU vibration energy
               (VerticalAcc band 4-20 Hz), both high-passed 0.5 Hz, whole lap
  segments     yawTransient on 4 quarter-lap segments (spread = robustness)
Writes v_offset.json next to this script (numbers only).
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfiltfilt

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('REPLAYVIEWER_ROOT', HERE.parents[2]))
WORK = Path(os.environ['VERIFY_WORK'])
RACE = {'wet': 'fuji_aim_01', 'dry': 'fuji_aim_2020_07_30'}
TR = json.loads((ROOT / 'artifacts/video-position-gap-2026-10-02/video-timer-reads.json').read_text(encoding='utf-8'))
GRID = 0.01


def ts(s):
    return datetime.fromisoformat(s.replace('Z', ''))


def lap_doc(race, lap):
    return json.loads((ROOT / f'public/data/races/{race}/osaki_hmr_demio_101_lap_{lap:03d}.json').read_text(encoding='utf-8'))


def lap_start_pts(race, lap, meta):
    s = TR['sessions'][race]
    dt = (ts(meta['lap_start_time']) - ts(s['referenceLapStart'])).total_seconds()
    return s['referencePtsSeconds'] + dt - s['perLapCorrectionSeconds'][str(lap)]


def boxcar(y, w_s, dt=GRID):
    w = max(1, int(round(w_s / dt))) | 1
    return np.convolve(np.pad(y, w // 2, mode='edge'), np.ones(w) / w, mode='valid')


def xcorr_lag(a, b, max_lag_s=1.0, dt=GRID):
    """L maximising corr(a(t), b(t + L)) for equal-grid arrays a, b (NaN-free). Returns L, r, sign."""
    a = (a - a.mean()) / (a.std() + 1e-12)
    b = (b - b.mean()) / (b.std() + 1e-12)
    m = int(round(max_lag_s / dt))
    n = len(a)
    lags = np.arange(-m, m + 1)
    r = np.empty(len(lags))
    for i, k in enumerate(lags):
        if k >= 0:
            r[i] = np.mean(a[:n - k] * b[k:])
        else:
            r[i] = np.mean(a[-k:] * b[:n + k])
    sgn = 1.0 if np.max(r) >= -np.min(r) else -1.0
    rr = r * sgn
    j = int(np.argmax(rr))
    off = 0.0
    if 0 < j < len(rr) - 1:
        y0, y1, y2 = rr[j - 1], rr[j], rr[j + 1]
        den = y0 - 2 * y1 + y2
        off = 0.5 * (y0 - y2) / den if den != 0 else 0.0
    return float((lags[j] + off) * dt), float(rr[j]), sgn


def hp(y, fc, fs):
    sos = butter(2, fc, btype='high', fs=fs, output='sos')
    return sosfiltfilt(sos, y)


def to_local(lat, lng, origin):
    k = 111_320.0
    return np.stack([(np.asarray(lng) - origin['lng']) * k * np.cos(np.radians(origin['lat'])),
                     -(np.asarray(lat) - origin['lat']) * k], 1)


ORIGIN = json.loads((ROOT / 'public/data/tracks/fuji/track.json').read_text(encoding='utf-8'))['origin']

def main():
    results = {}
    for tag in sys.argv[1:]:
        sess, lapname = tag.split('_')
        race, lap = RACE[sess], int(lapname[1:])
        d = lap_doc(race, lap)
        meta = d['meta']
        delta = float(meta['first_sample_after_lap_start_seconds'])
        shift = float((meta.get('channel_time_alignment') or {}).get('shiftSeconds', 0.0))
        t_lap = np.array(d['t'], float)
        z = np.load(WORK / f'xrk_{race}.npz', allow_pickle=False)
        maps = json.loads(str(z['maps']))
        tc0 = maps[str(lap)]['tc0Ms']
        start_pts = lap_start_pts(race, lap, meta)
        g = np.arange(2.0, t_lap[-1] - 2.0, GRID)
        # gyro
        yt = (z['YawRate_t'] - tc0) / 1000.0
        yaw_shift = np.interp(g, yt + shift, z['YawRate'])
        yaw_noshift = np.interp(g, yt, z['YawRate'])
        # GPS course rate from shipped (smoothed) lat/lng, central differences
        xy = to_local(d['lat'], d['lng'], ORIGIN)
        hdg = np.unwrap(np.arctan2(np.gradient(xy[:, 0], t_lap), np.gradient(xy[:, 1], t_lap)))
        crs = np.degrees(np.gradient(hdg, t_lap))
        course = np.interp(g, t_lap, crs)
        out = dict(race=race, lap=lap, delta=delta, shiftSeconds=shift, tc0Ms=tc0, lapStartPtsOverlay=round(start_pts, 4))
        from scipy.ndimage import median_filter
        variants = []
        p2 = WORK / f'flow2_{tag}.npz'
        if p2.exists():
            f2 = np.load(p2)
            # yaw proxy: per-row intercept at the principal column, median over rows 10-49 (full-res y 170-250)
            a_rows = f2['a'].astype(float)[:, 10:50]
            variants.append(('rowFit', f2['mid'], np.nanmedian(a_rows, axis=1) / np.diff(f2['pts'])))
        p1 = WORK / f'flow_{tag}.npz'
        if p1.exists():
            f1 = np.load(p1)
            variants.append(('medianDx', f1['mid'], f1['dx'] / np.diff(f1['pts'])))
        for vname, mid, vrate in variants:
            t_ov = mid - start_pts - delta
            vrate = np.where(np.isfinite(vrate), vrate, 0.0)
            # wiper / occlusion: replace frames that disagree strongly with a 0.5 s running median
            med = median_filter(vrate, size=15, mode='nearest')
            bad = np.abs(vrate - med) > 30.0
            vr = np.where(bad, med, vrate)
            video = np.interp(g, t_ov, vr)
            o = dict(framesReplaced=int(bad.sum()), frames=int(len(vrate)))
            L, r, sg = xcorr_lag(boxcar(video, 0.2), boxcar(yaw_shift, 0.2))
            o['yawLevel'] = dict(L=round(L, 4), r=round(r, 3), sign=sg)
            L0, r0, _ = xcorr_lag(boxcar(video, 0.2), boxcar(yaw_noshift, 0.2))
            o['yawLevel_noShift'] = dict(L=round(L0, 4), r=round(r0, 3))
            dv = np.gradient(boxcar(video, 0.3), GRID)
            dy = np.gradient(boxcar(yaw_shift, 0.3), GRID)
            L, r, sg = xcorr_lag(dv, dy)
            o['yawTransient'] = dict(L=round(L, 4), r=round(r, 3), sign=sg)
            segs = []
            n = len(g)
            for q in range(4):
                sl = slice(q * n // 4, (q + 1) * n // 4)
                Lq, rq, _ = xcorr_lag(boxcar(video, 0.2)[sl], boxcar(yaw_shift, 0.2)[sl])
                segs.append(dict(L=round(Lq, 4), r=round(rq, 3)))
            o['yawLevelQuarters'] = segs
            L, r, sg = xcorr_lag(boxcar(video, 0.2), boxcar(course, 0.2))
            o['gpsCourse'] = dict(L=round(L, 4), r=round(r, 3), sign=sg)
            L, r, sg = xcorr_lag(dv, np.gradient(boxcar(course, 0.3), GRID))
            o['gpsCourseTransient'] = dict(L=round(L, 4), r=round(r, 3), sign=sg)
            # gyro vs course on the transient (same filters)
            l2, r2, _ = xcorr_lag(np.gradient(boxcar(course, 0.3), GRID), dy)
            o['gyroMinusCourseTransient'] = dict(lag=round(l2, 4), r=round(r2, 3))
            # least-squares scale video -> gyro (deg/s per px/s) at the best lag: gives an implied focal length
            Lb = o['yawLevel']['L']
            vy = np.interp(g + Lb, g, boxcar(yaw_shift, 0.2))
            vv = boxcar(video, 0.2)
            k = float(np.dot(vv, vy) / np.dot(vv, vv))
            o['degPerPx'] = round(k, 5)
            o['impliedFocalPxFullRes'] = round(abs(2.0 / np.radians(k)), 1) if k != 0 else None
            k2 = float(np.dot(vv, vy) / np.dot(vy, vy))      # px/s per deg/s (regression of video on gyro)
            o['impliedFocalPxFullRes_reverse'] = round(abs(2.0 * k2 / np.radians(1.0)), 1)
            out[vname] = o
        # gyro vs GPS course (IMU-to-GPS alignment of this lap): corr(course(t), gyro(t + l))
        l, r, sg = xcorr_lag(boxcar(course, 0.2), boxcar(yaw_shift, 0.2))
        out['gyroMinusGpsCourse'] = dict(lag=round(l, 4), r=round(r, 3), note='gyro (with shift) features later than GPS course by lag')
        l, r, sg = xcorr_lag(boxcar(course, 0.2), boxcar(yaw_noshift, 0.2))
        out['gyroNoShiftMinusGpsCourse'] = dict(lag=round(l, 4), r=round(r, 3))
        # audio vs IMU vibration
        try:
            a = np.load(WORK / f'audio_{tag}.npz')
            ta = a['s1_t'] - start_pts - delta
            la = np.log10(a['s1_b20_120'] + 1e-12)
            aud = np.interp(g, ta, la)
            vt = (z['VerticalAcc_t'] - tc0) / 1000.0 + shift
            va = z['VerticalAcc']
            sos = butter(4, [4.0, 20.0], btype='band', fs=50.0, output='sos')
            vb = sosfiltfilt(sos, va)
            e = np.convolve(vb * vb, np.ones(3) / 3, mode='same')
            imu = np.interp(g, vt, np.log10(e + 1e-9))
            L, r, sg = xcorr_lag(hp(aud, 0.5, 100.0), hp(imu, 0.5, 100.0))
            out['audioVib'] = dict(L=round(L, 4), r=round(r, 3), sign=sg)
            for bn in ('b120_400', 'b400_2000'):
                aud2 = np.interp(g, ta, np.log10(a[f's1_{bn}'] + 1e-12))
                L, r, sg = xcorr_lag(hp(aud2, 0.5, 100.0), hp(imu, 0.5, 100.0))
                out[f'audioVib_{bn}'] = dict(L=round(L, 4), r=round(r, 3), sign=sg)
            aud3 = np.interp(g, a['s2_t'] - start_pts - delta, np.log10(a['s2_b20_120'] + 1e-12))
            L, r, sg = xcorr_lag(hp(aud3, 0.5, 100.0), hp(imu, 0.5, 100.0))
            out['audioStream2Vib'] = dict(L=round(L, 4), r=round(r, 3), sign=sg)
        except FileNotFoundError:
            pass
        results[tag] = out
        print(tag, json.dumps(out))
    prev = {}
    p = HERE / 'v_offset.json'
    if p.exists():
        prev = json.loads(p.read_text(encoding='utf-8'))
    prev.update(results)
    p.write_text(json.dumps(prev, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
