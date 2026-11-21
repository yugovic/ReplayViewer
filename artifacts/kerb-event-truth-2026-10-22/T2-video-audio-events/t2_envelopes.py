"""T2 shared signal envelopes: audio rumble (stream 1 = cabin microphone), IMU vibration, video shake.

All envelopes are zero-phase (centred) so they add no lag; time stamps are video PTS (audio/video) or lap t
(IMU, logger clock + per-lap shift). Envelopes are returned in dB above the window's 20th percentile.
"""
import numpy as np
from scipy.io import wavfile
from scipy.ndimage import median_filter, uniform_filter1d
from scipy.signal import butter, decimate, sosfiltfilt, stft

from t2_common import TAG, WORK, logger_to_t

_AUDIO = {}


def audio(race, stream=1):
    key = (race, stream)
    if key not in _AUDIO:
        sr, a = wavfile.read(WORK / f"audio/{TAG[race]}_a{stream}.wav")
        _AUDIO[key] = (sr, a)
    return _AUDIO[key]


def audio_spec(race, pts0, pts1, fs_out=8000, nper=256, hop=40):
    """Decimated STFT power of the cabin-mic stream on [pts0, pts1]. Returns (t_pts, f, P)."""
    sr, a = audio(race)
    i0, i1 = max(0, int(pts0 * sr)), min(len(a), int(pts1 * sr))
    x = a[i0:i1].astype(np.float64) / 32768.0
    q = sr // fs_out
    y = decimate(x, q, ftype="fir", zero_phase=True)
    f, tt, Z = stft(y, fs=fs_out, nperseg=nper, noverlap=nper - hop, boundary="even", padded=False)
    P = np.abs(Z) ** 2
    return pts0 + tt, f, P


def hpss_percussive(P, t_width=51, f_width=9):
    """Median-filter harmonic/percussive split (Fitzgerald 2010); returns the soft-masked percussive power."""
    H = median_filter(P, size=(1, t_width), mode="nearest")
    Pm = median_filter(P, size=(f_width, 1), mode="nearest")
    mask = Pm ** 2 / (H ** 2 + Pm ** 2 + 1e-30)
    return P * mask


def band_db(t, f, P, lo, hi, smooth_s=0.02):
    m = (f >= lo) & (f < hi)
    e = P[m].sum(0)
    dt = float(np.median(np.diff(t)))
    k = max(1, int(round(smooth_s / dt)))
    e = uniform_filter1d(e, k)
    return 10 * np.log10(e + 1e-20)


def imu_vibration(z, info, t0, t1, chans=("RollRate", "PitchRate", "VerticalAcc"), hp_hz=3.0, smooth_s=0.1):
    """Zero-phase high-pass (> hp_hz) vibration energy of the 50 Hz IMU, each channel normalised by its
    window median energy, summed; returns lap t and dB. Logger clock mapped with the lap shift."""
    sos = butter(4, hp_hz, "hp", fs=50, output="sos")
    acc = None
    for c in chans:
        tt = logger_to_t(info, z[c + "_t"], "logger")
        m = (tt >= t0 - 3) & (tt <= t1 + 3)
        v = sosfiltfilt(sos, z[c][m])
        e = uniform_filter1d(v * v, max(1, int(round(smooth_s * 50))))
        e = e / (np.median(e) + 1e-12)
        if acc is None:
            acc, tm = e, tt[m]
        else:
            acc = acc + np.interp(tm, tt[m], e)
    return tm, 10 * np.log10(acc / len(chans) + 1e-12)


def roll_rms(z, info, t0, t1):
    """The published detector's roll-rate HP RMS (eval_v2.detector), lap t."""
    tr = logger_to_t(info, z["RollRate_t"], "logger")
    r = z["RollRate"]
    hp = r - uniform_filter1d(r, 15)
    rms = np.sqrt(uniform_filter1d(hp * hp, 10))
    m = (tr >= t0 - 3) & (tr <= t1 + 3)
    return tr[m], rms[m]


def video_shake(mo, pts0, pts1, hp_hz=4.0, smooth_s=0.1, keys=("FL_dy", "FR_dy", "FL_dx", "FR_dx", "D_pcx", "D_pcy")):
    """Zero-phase high-pass (> hp_hz) frame-to-frame motion energy (30 fps), normalised per key, summed."""
    t = mo["pts_mid"]
    m = (t >= pts0 - 3) & (t <= pts1 + 3)
    sos = butter(3, hp_hz, "hp", fs=30, output="sos")
    acc = 0
    for k in keys:
        v = np.nan_to_num(mo[k][m].astype(np.float64))
        hp = sosfiltfilt(sos, v)
        e = uniform_filter1d(hp * hp, max(1, int(round(smooth_s * 30))))
        acc = acc + e / (np.median(e) + 1e-12)
    return t[m], 10 * np.log10(acc / len(keys) + 1e-12)


def rel_db(e, q=20):
    return e - np.percentile(e, q)


def half_rise_onset(t, e, t_lo, t_hi, frac=0.5, base_q=20, min_rise_db=3.0):
    """Peak of e on [t_lo, t_hi]; onset = last crossing of base + frac*(peak - base) before the peak,
    offset = first crossing after the peak (linear interpolation). base = base_q percentile of e on the
    whole supplied series. Returns dict or None when the rise is below min_rise_db."""
    base = float(np.percentile(e, base_q))
    m = np.flatnonzero((t >= t_lo) & (t <= t_hi))
    if len(m) < 3:
        return None
    ip = int(m[np.argmax(e[m])])
    peak = float(e[ip])
    if peak - base < min_rise_db:
        return {"peakDb": round(peak - base, 2), "onset": None, "offset": None, "tPeak": float(t[ip])}
    thr = base + frac * (peak - base)
    i = ip
    while i > 0 and e[i - 1] >= thr:
        i -= 1
    on = float(t[i]) if i == 0 else float(t[i - 1] + (thr - e[i - 1]) / (e[i] - e[i - 1]) * (t[i] - t[i - 1]))
    j = ip
    while j < len(e) - 1 and e[j + 1] >= thr:
        j += 1
    off = float(t[j]) if j == len(e) - 1 else float(t[j] + (e[j] - thr) / (e[j] - e[j + 1]) * (t[j + 1] - t[j]))
    return {"peakDb": round(peak - base, 2), "onset": on, "offset": off, "tPeak": float(t[ip])}
