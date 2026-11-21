"""Which GNSS stream is aligned with the IMU? Cross-correlate the logger gyro YawRate (50 Hz,
logger clock) with (a) the course rate from the Doppler velocity vector and (b) the course rate
from differenced GNSS positions, both from the raw NAV-SOL records (navsol_probe.read_navsol).

positive lag = the GNSS-derived signal happens LATER than the gyro.
Body yaw rate and course rate differ by the sideslip rate; that difference is common to (a) and
(b), so the (a)-(b) lag difference is the robust number. 7/30 gyro is known to be ~0.5 s early
(logger-clock channels, 10/02 audit) and serves as a sanity check.
Inputs: env FUJI_WET_XRK / FUJI_DRY_XRK (local only). Output: navsol_yaw_lag.json.
"""
import json
import os
from pathlib import Path

import numpy as np

import navsol_probe as np_

HERE = Path(__file__).resolve().parent


def xcorr_lag(t_ref, y_ref, t_sig, y_sig, t0, t1, max_lag=0.8, step=0.005):
    grid = np.arange(t0, t1, 0.01)
    r = np.interp(grid, t_ref, y_ref)
    r = (r - r.mean()) / r.std()
    best = (None, -2)
    out = []
    for L in np.arange(-max_lag, max_lag + 1e-9, step):
        s = np.interp(grid + L, t_sig, y_sig)
        s = (s - s.mean()) / s.std()
        c = float(np.mean(r * s))
        out.append(c)
        if c > best[1]:
            best = (float(L), c)
    # parabolic refinement
    i = int(np.argmax(out))
    if 0 < i < len(out) - 1:
        y0, y1, y2 = out[i - 1], out[i], out[i + 1]
        d = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2)
        best = (best[0] + d * step, best[1])
    return best


def run(race, path):
    from libxrk import aim_xrk
    a, _ = np_.read_navsol(path)
    a = a[a["fix"] >= 3]
    log = aim_xrk(path)
    lib_tc = log.channels["GPS Speed"].column("timecodes").to_numpy().astype(np.int64)
    off = float(np.median(a["tc"].astype(np.int64) - lib_tc)) if len(lib_tc) == len(a) else float(a["tc"][0] - lib_tc[0])
    tg = (a["tc"].astype(np.float64) - off) / 1000.0
    X = np.vstack([a["x"], a["y"], a["z"]]).astype(np.float64) * 0.01
    V = np.vstack([a["vx"], a["vy"], a["vz"]]).astype(np.float64) * 0.01
    x0 = X.mean(axis=1)
    R = np_.ecef_to_enu_mats(*x0)
    P = (R @ (X - x0[:, None])).T[:, :2]
    Ve = (R @ V).T[:, :2]
    sp = np.hypot(Ve[:, 0], Ve[:, 1])
    crs_d = np.unwrap(np.arctan2(Ve[:, 0], Ve[:, 1]))
    rate_d = np.degrees(np.gradient(crs_d, tg))
    vp = np.gradient(P, tg, axis=0)  # central difference: no added lag
    crs_p = np.unwrap(np.arctan2(vp[:, 0], vp[:, 1]))
    rate_p = np.degrees(np.gradient(crs_p, tg))
    yr = log.channels["YawRate"]
    ty = yr.column("timecodes").to_numpy().astype(np.float64) / 1000.0
    yv = yr.column(1).to_numpy().astype(np.float64)
    laps = log.laps.to_pydict()
    res = {"race": race, "perLap": []}
    for num, st, en in zip(laps["num"], laps["start_time"], laps["end_time"]):
        if num == 0 or (en - st) > 200000:
            continue
        t0, t1 = st / 1000 + 1, en / 1000 - 1
        m = (tg >= t0) & (tg <= t1)
        if m.sum() < 600 or sp[m].min() < 5:
            continue
        sgn = 1.0 if np.corrcoef(np.interp(tg[m], ty, yv), rate_d[m])[0, 1] > 0 else -1.0
        ld, cd = xcorr_lag(ty, sgn * yv, tg, rate_d, t0 + 1, t1 - 1)
        lp, cp = xcorr_lag(ty, sgn * yv, tg, rate_p, t0 + 1, t1 - 1)
        res["perLap"].append({"lap": int(num), "dopplerCourseRateLagS": round(ld, 4), "corrDoppler": round(cd, 4),
                              "positionCourseRateLagS": round(lp, 4), "corrPosition": round(cp, 4),
                              "dopplerMinusPositionS": round(ld - lp, 4)})
    v = np.array([[r["dopplerCourseRateLagS"], r["positionCourseRateLagS"], r["dopplerMinusPositionS"]] for r in res["perLap"]])
    res["median"] = {"dopplerLagS": float(np.median(v[:, 0])), "positionLagS": float(np.median(v[:, 1])),
                     "dopplerMinusPositionS": float(np.median(v[:, 2]))}
    return res


def main():
    out = {"script": "navsol_yaw_lag.py", "kind": "measurement on local XRK (no video)",
           "signConvention": "positive = GNSS-derived course rate occurs later than the logger gyro YawRate", "sessions": {}}
    for race, env in (("fuji_aim_01", "FUJI_WET_XRK"), ("fuji_aim_2020_07_30", "FUJI_DRY_XRK")):
        p = os.environ.get(env, "")
        if p:
            out["sessions"][race] = run(race, p)
    (HERE / "navsol_yaw_lag.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
