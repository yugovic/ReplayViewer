"""T2 shared helpers: paths (env only), lap <-> logger <-> video PTS mappings.

Env (local media are never written into outputs):
  FUJI_WET_XRK, FUJI_DRY_XRK       AiM XRK of 2020-07-29 (fuji_aim_01) / 2020-07-30 (fuji_aim_2020_07_30)
  FUJI_WET_VIDEO, FUJI_DRY_VIDEO   SmartyCam SCHD0613.MOV / SCHD0624.MOV
  T2_WORK                          local work dir OUTSIDE the repo (audio WAVs, decoded series, npz)

Time bases
  lap t        : shipped lap JSON t (s), 0 = first GPS sample of the lap = viewer clock (GPS time base).
  true lap time: t + delta, delta = meta.first_sample_after_lap_start_seconds.
  logger -> t  : t = (logger_ms - tc0Ms)/1000 + shift  (GPS channels: shift = 0; logger-clock channels
                 (IMU/ECU): shift = meta.channel_time_alignment.shiftSeconds on 7/30, 0 on 7/29).
  video PTS    : ffmpeg presentation time of SCHD06xx.MOV (start_time 0 for video and both audio streams).
  overlay map  : PTS = lapStartPts + delta + t, lapStartPts = (lap_start_time - referenceLapStart)
                 + referencePtsSeconds - perLapCorrectionSeconds[lap]  (video-timer-reads.json; identical to
                 scripts/quality/audit-fuji-channel-timing.py).
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RACES = {"fuji_aim_01": 4, "fuji_aim_2020_07_30": 6}
TAG = {"fuji_aim_01": "wet", "fuji_aim_2020_07_30": "dry"}
XRK = {"fuji_aim_01": os.environ.get("FUJI_WET_XRK", ""), "fuji_aim_2020_07_30": os.environ.get("FUJI_DRY_XRK", "")}
VIDEO = {"fuji_aim_01": os.environ.get("FUJI_WET_VIDEO", ""), "fuji_aim_2020_07_30": os.environ.get("FUJI_DRY_VIDEO", "")}
WORK = Path(os.environ.get("T2_WORK", ""))
TIMER = ROOT / "artifacts/video-position-gap-2026-10-02/video-timer-reads.json"


def wall(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text.replace("Z", ""))


def load_lap(race: str, lap: int) -> dict:
    p = ROOT / f"public/data/races/{race}/osaki_hmr_demio_101_lap_{lap:03d}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def kerb_contacts(race: str) -> dict:
    return json.loads((ROOT / f"public/data/races/{race}/kerb_contacts.json").read_text(encoding="utf-8"))


def lap_info(race: str) -> dict:
    """Per lap: delta, tc0Ms (XRK GPS timecode of lap t=0), shift (logger-clock channels), lapStartPts."""
    reads = json.loads(TIMER.read_text(encoding="utf-8"))["sessions"][race]
    kc = kerb_contacts(race)["timeBase"]
    out = {}
    for k in range(1, RACES[race] + 1):
        d = load_lap(race, k)
        m = d["meta"]
        shift = float((m.get("channel_time_alignment") or {}).get("shiftSeconds", 0.0))
        lsp = ((wall(m["lap_start_time"]) - wall(reads["referenceLapStart"])).total_seconds()
               + reads["referencePtsSeconds"] - reads["perLapCorrectionSeconds"][str(k)])
        out[k] = {"delta": float(m["first_sample_after_lap_start_seconds"]),
                  "tc0Ms": float(kc["lapMapping"][str(k)]["tc0Ms"]),
                  "shift": shift,
                  "kcShift": float(kc["shiftSecondsByLap"][str(k)]),
                  "lapStartPts": lsp,
                  "tEnd": float(d["t"][-1])}
    return out


def t_to_pts(info: dict, t):
    return info["lapStartPts"] + info["delta"] + np.asarray(t, float)


def pts_to_t(info: dict, pts):
    return np.asarray(pts, float) - info["lapStartPts"] - info["delta"]


def logger_to_t(info: dict, ms, clock: str):
    """clock = 'gps' (no shift) or 'logger' (IMU/ECU: + shift)."""
    s = info["shift"] if clock == "logger" else 0.0
    return (np.asarray(ms, float) - info["tc0Ms"]) / 1000.0 + s


def xcorr_lag(ta, a, tb, b, t0, t1, max_lag=1.0, step=0.002, dt_grid=0.005):
    """Lag L maximising corr(a(t), b(t + L)) on [t0, t1]; L > 0 means b's features occur L s LATER than a's.
    Returns (L, r, curve) with parabolic sub-step refinement."""
    grid = np.arange(t0, t1, dt_grid)
    A = np.interp(grid, ta, a)
    A = (A - A.mean()) / (A.std() + 1e-12)
    lags = np.arange(-max_lag, max_lag + 1e-9, step)
    cs = np.empty(len(lags))
    for i, L in enumerate(lags):
        B = np.interp(grid + L, tb, b)
        B = (B - B.mean()) / (B.std() + 1e-12)
        cs[i] = float(np.mean(A * B))
    i = int(np.argmax(cs))
    L = float(lags[i])
    if 0 < i < len(cs) - 1:
        y0, y1, y2 = cs[i - 1], cs[i], cs[i + 1]
        den = y0 - 2 * y1 + y2
        if den != 0:
            L += 0.5 * (y0 - y2) / den * step
    return L, float(cs[i]), (lags, cs)
