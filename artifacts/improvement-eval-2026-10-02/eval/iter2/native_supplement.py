#!/usr/bin/env python3
"""Supplementary native-XRK checks for iteration 2 (evaluator).

(a) Raw XRK YawRate gyro vs GPS course rate: gyro sampled on each stage's grid (lap t -> tc0 + 1000*(t - shift)),
    lag vs the GPS course rate of the shipped lap (same lag definition as lags_vs_0729.py, method "sg"); minus the 7/29 median.
(b) 10 Hz decimation-phase spread of the per-lap max|accx|, max|accy| using native samples only (no interpolation):
    the 50 Hz series decimated by 5 at each of its 5 phases; shows how much a per-lap peak can move with phase alone.
Needs APEX_WORK (env) with xrk_<race>.npz. Writes native_supplement.json next to this file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

import lags_vs_0729 as L

HERE = Path(__file__).resolve().parent
APEX = Path(os.environ["APEX_WORK"])
SHIFTS = {"baseline": {k: 0.0 for k in range(1, 7)}}


def npz(race):
    z = np.load(APEX / f"xrk_{race}.npz", allow_pickle=True)
    return z, json.loads(str(z["maps"]))


def main() -> None:
    out = {}
    for stage in ("iter1", "iter2"):
        SHIFTS[stage] = {k: L.load(stage, k)["meta"]["channel_time_alignment"]["shiftSeconds"] for k in range(1, 7)}
    z29, m29 = npz("fuji_aim_01")
    z30, m30 = npz("fuji_aim_2020_07_30")
    # (a)
    lags = {"0729": {}}
    for k in range(1, 5):
        d = L.load("0729", k)
        f = L.features(d, "sg")
        yaw = np.interp(m29[str(k)]["tc0"] + 1000.0 * f["t"], z29["YawRate_t"], z29["YawRate"])
        lags["0729"][k] = L.lag(f["t"], f["gps"]["courseRate"], yaw)
    ref = float(np.median([v[0] for v in lags["0729"].values()]))
    out["yawGyro_ref0729MedianLagS"] = ref
    for stage in ("baseline", "iter1", "iter2"):
        lags[stage] = {}
        for k in range(1, 7):
            d = L.load("iter2", k)  # GPS arrays identical in all stages
            f = L.features(d, "sg")
            yaw = np.interp(m30[str(k)]["tc0"] + 1000.0 * (f["t"] - SHIFTS[stage][k]), z30["YawRate_t"], z30["YawRate"])
            lags[stage][k] = L.lag(f["t"], f["gps"]["courseRate"], yaw)
        rel = [round(lags[stage][k][0] - ref, 3) for k in range(1, 7)]
        out[f"yawGyro_{stage}_perLapMinus0729"] = rel
        out[f"yawGyro_{stage}_medianMinus0729"] = round(float(np.median(rel)), 3)
    out["yawGyroRawLags"] = lags
    # (b)
    dec = {}
    for k in range(1, 7):
        db, d2 = L.load("baseline", k), L.load("iter2", k)
        t = np.asarray(db["t"], float)
        tc0 = m30[str(k)]["tc0"]
        row = {}
        for ch, src in (("accx", "InlineAcc"), ("accy", "LateralAcc")):
            tt, vv = z30[f"{src}_t"], z30[src]
            m = (tt >= tc0 + 1000 * t[0]) & (tt <= tc0 + 1000 * t[-1])
            v = np.abs(vv[m])
            phases = [round(float(v[p::5].max()), 4) for p in range(5)]
            row[ch] = {"decimationPhaseMaxAbs": phases, "spreadPct": round(100 * (max(phases) - min(phases)) / max(phases), 1),
                       "baselineMaxAbs": round(float(np.max(np.abs(db[ch]))), 4),
                       "iter2MaxAbs": round(float(np.max(np.abs(d2[ch]))), 4)}
        # offset of the iter2 grid from the nearest native IMU timestamp (ms), median over the lap
        tt = z30["LateralAcc_t"]
        g = tc0 + 1000.0 * (t - SHIFTS["iter2"][k])
        idx = np.clip(np.searchsorted(tt, g), 1, len(tt) - 1)
        off = np.minimum(np.abs(tt[idx] - g), np.abs(tt[idx - 1] - g))
        gb = tc0 + 1000.0 * t
        idxb = np.clip(np.searchsorted(tt, gb), 1, len(tt) - 1)
        offb = np.minimum(np.abs(tt[idxb] - gb), np.abs(tt[idxb - 1] - gb))
        row["iter2GridOffsetFromNativeMs_median"] = round(float(np.median(off)), 1)
        row["baselineGridOffsetFromNativeMs_median"] = round(float(np.median(offb)), 1)
        dec[k] = row
    out["decimation"] = dec
    (HERE / "native_supplement.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps({kk: vv for kk, vv in out.items() if kk not in ("yawGyroRawLags",)}, indent=1))


if __name__ == "__main__":
    main()
