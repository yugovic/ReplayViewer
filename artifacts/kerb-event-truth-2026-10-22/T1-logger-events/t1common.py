"""Shared helpers for T1 (logger ground-truth events). No local paths: the work directory comes from T1_WORK."""
import json
import os
from pathlib import Path

import numpy as np
from scipy.signal import butter, filtfilt

ROOT = Path(__file__).resolve().parents[3]
WORK = Path(os.environ['T1_WORK']) if os.environ.get('T1_WORK') else None
RACES = {'fuji_aim_01': '2020-07-29 wet', 'fuji_aim_2020_07_30': '2020-07-30 dry'}
LAP_PREFIX = 'osaki_hmr_demio_101_lap_'
IMU_FS = 50.0
ECU_FS = 20.0


def load_npz(race):
    z = dict(np.load(WORK / f'xrk_native_{race}.npz', allow_pickle=False))
    z['lapmap'] = json.loads(str(z['lapmap']))
    return z


def lap_json(race, lap):
    return json.loads((ROOT / f'public/data/races/{race}/{LAP_PREFIX}{lap:03d}.json').read_text(encoding='utf-8'))


def lap_numbers(race):
    idx = json.loads((ROOT / f'public/data/races/{race}/laps.json').read_text(encoding='utf-8'))
    return [int(s['lap']) for s in idx['selected']]


def lap_shift(meta):
    """Logger-clock -> GPS shift used by the shipped lap channels (7/30: per lap; 7/29: none -> 0)."""
    cta = meta.get('channel_time_alignment') or {}
    s = cta.get('shiftSeconds')
    return float(s) if isinstance(s, (int, float)) else 0.0


def to_lap_t(tc_ms, tc0_ms, shift_s):
    """lap t (s, shipped lap JSON / GPS time base, 0 = first GPS sample of the lap) of a logger timecode."""
    return (np.asarray(tc_ms, float) - tc0_ms) / 1000.0 + shift_s


def butter_hp(x, fc, fs, order=4):
    b, a = butter(order, fc / (fs / 2), 'highpass')
    return filtfilt(b, a, x)


def butter_lp(x, fc, fs, order=4):
    b, a = butter(order, fc / (fs / 2), 'lowpass')
    return filtfilt(b, a, x)


def centered_rms(x, n):
    """RMS over a centred window of n samples (n odd)."""
    k = np.ones(n) / n
    return np.sqrt(np.convolve(x * x, k, mode='same'))


def crossing_time(t, y, i, thr):
    """Sub-sample time where y crosses thr between samples i-1 and i (linear interpolation)."""
    if i <= 0:
        return float(t[0])
    y0, y1 = y[i - 1], y[i]
    if y1 == y0:
        return float(t[i])
    f = (thr - y0) / (y1 - y0)
    f = min(max(f, 0.0), 1.0)
    return float(t[i - 1] + f * (t[i] - t[i - 1]))


def parabolic_peak(t, y, i):
    """Sub-sample peak time/value from the 3-point parabola around sample i (uniform-ish spacing assumed)."""
    if i <= 0 or i >= len(y) - 1:
        return float(t[i]), float(y[i])
    y0, y1, y2 = y[i - 1], y[i], y[i + 1]
    den = y0 - 2 * y1 + y2
    if den == 0:
        return float(t[i]), float(y1)
    p = 0.5 * (y0 - y2) / den
    p = min(max(p, -0.5), 0.5)
    dt = (t[i + 1] - t[i - 1]) / 2
    return float(t[i] + p * dt), float(y1 - 0.25 * (y0 - y2) * p)
