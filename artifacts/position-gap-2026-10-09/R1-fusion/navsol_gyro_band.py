"""GNSS-independent check of the 7/30 L2 3250-3900 m anomaly with the logger gyro.

For every lap: heading discrepancy h(s) = course_from_positions(t) - integral(gyro YawRate)(t - lag),
lag = per-lap position-vs-gyro lag from navsol_yaw_lag.json. Inside a station band the curve is
detrended (removes gyro bias and the integration constant). What remains is sideslip angle
variation (driver/vehicle, similar lap to lap) + GNSS course error. Each lap is compared with the
median of the other laps at the same station. A lateral GNSS error of ~1-2 m that changes over
~100-300 m implies a course error of order 0.4-1 deg.

Also reports the lateral position change implied by the lap-minus-median heading residual
(integral of v*sin(dh) ds/v = integral dh ds), as an order-of-magnitude bound.
Inputs: env FUJI_WET_XRK / FUJI_DRY_XRK (local). Output: navsol_gyro_band.json.
"""
import json
import os
from pathlib import Path

import numpy as np

import navsol_probe as np_

HERE = Path(__file__).resolve().parent
BANDS = [(3250.0, 3900.0), (600.0, 1250.0), (1300.0, 1950.0), (1950.0, 2600.0), (2600.0, 3250.0), (3900.0, 4450.0)]


def run(race, path, lags):
    from libxrk import aim_xrk
    a, _ = np_.read_navsol(path)
    a = a[a["fix"] >= 3]
    log = aim_xrk(path)
    lib_tc = log.channels["GPS Speed"].column("timecodes").to_numpy().astype(np.int64)
    off = float(np.median(a["tc"].astype(np.int64) - lib_tc)) if len(lib_tc) == len(a) else float(a["tc"][0] - lib_tc[0])
    tg = (a["tc"].astype(np.float64) - off) / 1000.0
    X = np.vstack([a["x"], a["y"], a["z"]]).astype(np.float64) * 0.01
    x0 = X.mean(axis=1)
    R = np_.ecef_to_enu_mats(*x0)
    P = (R @ (X - x0[:, None])).T[:, :2]
    yr = log.channels["YawRate"]
    ty = yr.column("timecodes").to_numpy().astype(np.float64) / 1000.0
    yv = np.radians(yr.column(1).to_numpy().astype(np.float64))
    laps = log.laps.to_pydict()
    curves = {}
    for num, st, en in zip(laps["num"], laps["start_time"], laps["end_time"]):
        if num == 0 or (en - st) > 200000 or str(num) not in lags:
            continue
        m = (tg >= st / 1000) & (tg <= en / 1000)
        ii = np.where(m)[0]
        t = tg[ii]
        vp = np.gradient(P[ii], t, axis=0)
        crs = np.unwrap(np.arctan2(vp[:, 0], vp[:, 1]))
        lag = lags[str(num)]
        # integrate gyro on a fine grid, then sample at (t - lag)
        tf = np.arange(t[0] - 2, t[-1] + 2, 0.02)
        rf = np.interp(tf, ty, yv)
        sgn = 1.0 if np.corrcoef(np.interp(t - lag, ty, yv), np.gradient(crs, t))[0, 1] > 0 else -1.0
        psi = np.concatenate([[0], np.cumsum(0.5 * (rf[1:] + rf[:-1]) * np.diff(tf))]) * sgn
        h = crs - np.interp(t - lag, tf, psi)
        s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(P[ii], axis=0), axis=1))]) + np_.LAP_START_STATION_M
        curves[int(num)] = (s, h)
    out = {"race": race, "bands": {}}
    for lo, hi in BANDS:
        grid = np.arange(lo, hi, 2.0)
        H = {}
        for n, (s, h) in curves.items():
            y = np.interp(grid, s, h)
            A = np.vstack([grid - lo, np.ones_like(grid)]).T
            c, *_ = np.linalg.lstsq(A, y, rcond=None)
            H[n] = y - A @ c
        rec = {}
        for n in H:
            others = np.median(np.vstack([H[k] for k in H if k != n]), axis=0)
            d = H[n] - others
            lat = np.concatenate([[0], np.cumsum(0.5 * (d[1:] + d[:-1]) * 2.0)])  # metres, integral of heading residual over station
            A = np.vstack([grid - lo, np.ones_like(grid)]).T
            c, *_ = np.linalg.lstsq(A, lat, rcond=None)
            latd = lat - A @ c
            rec[str(n)] = {"headingResidualRmsDeg": round(float(np.degrees(np.sqrt(np.mean(d ** 2)))), 3),
                           "headingResidualMaxAbsDeg": round(float(np.degrees(np.max(np.abs(d)))), 3),
                           "impliedLateralDetrendedMaxAbsM": round(float(np.max(np.abs(latd))), 2)}
        out["bands"][f"{int(lo)}-{int(hi)}"] = rec
    return out


def main():
    yl = json.loads((HERE / "navsol_yaw_lag.json").read_text(encoding="utf-8"))
    res = {"script": "navsol_gyro_band.py", "kind": "measurement on local XRK (no video)", "sessions": {}}
    for race, env in (("fuji_aim_01", "FUJI_WET_XRK"), ("fuji_aim_2020_07_30", "FUJI_DRY_XRK")):
        p = os.environ.get(env, "")
        if not p:
            continue
        lags = {str(r["lap"]): r["positionCourseRateLagS"] for r in yl["sessions"][race]["perLap"]}
        res["sessions"][race] = run(race, p, lags)
    (HERE / "navsol_gyro_band.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
