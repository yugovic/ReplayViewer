#!/usr/bin/env python3
"""Evaluator's own lag check (independent of align-fuji-channel-timing.py): 7/30 channel-vs-GPS lags minus the 7/29 relation.

Lag definition: s maximising corr(gps_feature(t), channel(t + s)); s < 0 = channel features earlier than GPS.
GPS features are derived here from lat/lng/speed with an own local projection:
  method "sg":   Savitzky-Golay derivatives (window 9, order 3) of position / GPS speed on the 10 Hz grid
  method "bp":   np.gradient of cubic-spline position, both series band-passed 0.05-2.0 Hz (zero-phase Butterworth)
Pairs: accx*g vs GPS along-acc, accy*g vs GPS lateral-acc, brake vs GPS decel (-dv/dt), steer vs GPS course rate.
Stages for 7/30: baseline snapshot, iter1 candidates, iter2 (public). 7/29 = public fuji_aim_01 (unchanged).
Writes lags_vs_0729.json next to this file.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.signal import butter, filtfilt, savgol_filter

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
G = 9.80665
DIRS = {
    "0729": ROOT / "public/data/races/fuji_aim_01",
    "baseline": ROOT / "artifacts/improvement-eval-2026-10-02/baseline/public/data/races/fuji_aim_2020_07_30",
    "iter1": ROOT / "artifacts/improvement-eval-2026-10-02/after/channel-timing/candidates",
    "iter2": ROOT / "public/data/races/fuji_aim_2020_07_30",
}
NLAPS = {"0729": 4, "baseline": 6, "iter1": 6, "iter2": 6}
PAIRS = ("accx_along", "accy_lateral", "brake_decel", "steer_courseRate")


def load(stage: str, k: int) -> dict:
    return json.loads((DIRS[stage] / f"osaki_hmr_demio_101_lap_{k:03d}.json").read_text(encoding="utf-8"))


def local_xy(lat, lng):
    lat, lng = np.asarray(lat, float), np.asarray(lng, float)
    R = 6378137.0
    lat0 = np.radians(lat.mean())
    x = np.radians(lng - lng.mean()) * R * np.cos(lat0)
    y = np.radians(lat - lat.mean()) * R
    return x, y  # east, north


def bandpass(x, dt=0.1, lo=0.05, hi=2.0):
    b, a = butter(2, [lo * 2 * dt, hi * 2 * dt], btype="band")
    return filtfilt(b, a, x)


def features(d: dict, method: str) -> dict:
    t = np.asarray(d["t"], float)
    dt = 0.1
    x, y = local_xy(d["lat"], d["lng"])
    v_kmh = np.asarray(d["speed"], float)
    if method == "sg":
        vx = savgol_filter(x, 9, 3, deriv=1, delta=dt)
        vy = savgol_filter(y, 9, 3, deriv=1, delta=dt)
        ax = savgol_filter(x, 9, 3, deriv=2, delta=dt)
        ay = savgol_filter(y, 9, 3, deriv=2, delta=dt)
        dv = savgol_filter(v_kmh / 3.6, 9, 3, deriv=1, delta=dt)
    else:
        cx, cy = CubicSpline(t, x), CubicSpline(t, y)
        vx, vy = cx(t, 1), cy(t, 1)
        ax, ay = np.gradient(vx, t), np.gradient(vy, t)
        dv = np.gradient(v_kmh / 3.6, t)
    sp = np.maximum(np.hypot(vx, vy), 1.0)
    along = (ax * vx + ay * vy) / sp
    lateral = (vx * ay - vy * ax) / sp
    heading = np.unwrap(np.arctan2(vy, vx))
    course = savgol_filter(heading, 9, 3, deriv=1, delta=dt) if method == "sg" else np.gradient(heading, t)
    ch = {
        "accx": np.asarray(d["accx"], float) * G,
        "accy": np.asarray(d["accy"], float) * G,
        "brake": np.asarray(d["brake"], float),
        "steer": np.asarray(d["steer"], float),
    }
    gps = {"along": along, "lateral": lateral, "decel": -dv, "courseRate": course}
    if method == "bp":
        gps = {k: bandpass(v) for k, v in gps.items()}
        ch = {k: bandpass(v) for k, v in ch.items()}
    return {"t": t, "gps": gps, "ch": ch}


def lag(t, g, c, lo=-1.0, hi=1.0, step=0.002):
    """s maximising |corr(g(t), c(t+s))| (sign of the relation fixed by the zero-lag correlation)."""
    cg, cc = CubicSpline(t, g), CubicSpline(t, c)
    tt = np.arange(t[0] + 1.5, t[-1] - 1.5, 0.01)
    G0 = cg(tt)
    G0 = (G0 - G0.mean()) / G0.std()
    sgn = np.sign(np.corrcoef(g, c)[0, 1]) or 1.0
    best_s, best_r = np.nan, -2.0
    for s in np.arange(lo, hi + 1e-9, step):
        C = cc(tt + s)
        r = sgn * float(np.mean(G0 * (C - C.mean())) / C.std())
        if r > best_r:
            best_s, best_r = float(s), r
    return round(best_s, 3), round(best_r, 3)


def main() -> None:
    out: dict = {"definition": __doc__, "results": {}}
    for method in ("sg", "bp"):
        res = {}
        for stage in DIRS:
            res[stage] = {}
            for k in range(1, NLAPS[stage] + 1):
                f = features(load(stage, k), method)
                row = {}
                for pair in PAIRS:
                    cn, gn = pair.split("_")
                    row[pair] = lag(f["t"], f["gps"][gn], f["ch"][cn])
                res[stage][k] = row
        ref = {p: float(np.median([res["0729"][k][p][0] for k in res["0729"]])) for p in PAIRS}
        summ = {"ref0729MedianLagS": ref}
        for stage in ("baseline", "iter1", "iter2"):
            per = {k: {p: round(res[stage][k][p][0] - ref[p], 3) for p in PAIRS} for k in res[stage]}
            for k in per:
                per[k]["medianOverPairs"] = round(float(np.median([per[k][p] for p in PAIRS])), 3)
            med = {p: round(float(np.median([per[k][p] for k in per])), 3) for p in PAIRS}
            med["medianOverPairs_ofLapMedians"] = round(float(np.median([per[k]["medianOverPairs"] for k in per])), 3)
            med["meanAbsOverPairs"] = round(float(np.mean([abs(med[p]) for p in PAIRS])), 3)
            summ[stage] = {"perLapMinus0729": per, "sessionMedianMinus0729": med}
        out["results"][method] = {"rawLags": res, "summary": summ}
        print(method, json.dumps({s: summ[s]["sessionMedianMinus0729"] for s in ("baseline", "iter1", "iter2")}))
        print(method, "ref", ref)
        for k in summ["iter2"]["perLapMinus0729"]:
            print(method, "iter2 lap", k, summ["iter2"]["perLapMinus0729"][k])
    (HERE / "lags_vs_0729.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
