"""Follow-up to navsol_probe.py: slow (>10 s) position-vs-Doppler discrepancy per lap.

For each lap, D(s) = (GNSS position displacement) - (integrated Doppler velocity, with the
measured position->velocity lag compensated), expressed in a fixed lap frame.
Reports:
  - per-lap path length from positions vs integral of Doppler speed (scale check)
  - D in along/cross components at coarse stations, and how repeatable it is across laps
  - for the 3250-3900 m band: max deviation of D from its linear trend inside the band,
    lap by lap (is 7/30 L2 different from the other laps?)
Inputs: env FUJI_WET_XRK / FUJI_DRY_XRK (local only). Output: navsol_band_check.json.
"""
import json
import os
from pathlib import Path

import numpy as np

import navsol_probe as np_

HERE = Path(__file__).resolve().parent


def run(race, path, tau):
    from libxrk import aim_xrk
    a, _ = np_.read_navsol(path)
    a = a[a["fix"] >= 3]
    log = aim_xrk(path)
    lib_tc = log.channels["GPS Speed"].column("timecodes").to_numpy().astype(np.int64)
    off = float(np.median(a["tc"].astype(np.int64) - lib_tc)) if len(lib_tc) == len(a) else float(a["tc"][0] - lib_tc[0])
    tlib = a["tc"].astype(np.float64) - off
    t = a["week"] * 604800.0 + a["itow"] * 1e-3 + a["ftow"] * 1e-9
    X = np.vstack([a["x"], a["y"], a["z"]]).astype(np.float64) * 0.01
    V = np.vstack([a["vx"], a["vy"], a["vz"]]).astype(np.float64) * 0.01
    x0 = X.mean(axis=1)
    R = np_.ecef_to_enu_mats(*x0)
    P = (R @ (X - x0[:, None])).T[:, :2]
    Ve = (R @ V).T[:, :2]
    laps = log.laps.to_pydict()
    out = {"race": race, "tauS": tau, "laps": []}
    curves = {}
    for num, st, en in zip(laps["num"], laps["start_time"], laps["end_time"]):
        if num == 0 or (en - st) > 200000:
            continue
        ii = np.where((tlib >= st) & (tlib <= en))[0]
        if len(ii) < 600:
            continue
        tt = t[ii]
        vi = np.vstack([np.interp(tt + tau, t, Ve[:, k]) for k in range(2)]).T
        dP = np.diff(P[ii], axis=0)
        integ = 0.5 * (vi[1:] + vi[:-1]) * np.diff(tt)[:, None]
        D = np.vstack([[0, 0], np.cumsum(dP - integ, axis=0)])
        s = np.concatenate([[0], np.cumsum(np.linalg.norm(dP, axis=1))]) + np_.LAP_START_STATION_M
        u = vi / np.linalg.norm(vi, axis=1, keepdims=True)
        nrm = np.stack([-u[:, 1], u[:, 0]], axis=1)
        # express the discrepancy increments in the local along/cross frame and accumulate
        inc = dP - integ
        uu = 0.5 * (u[1:] + u[:-1]); nn = 0.5 * (nrm[1:] + nrm[:-1])
        Dal = np.concatenate([[0], np.cumsum(np.sum(inc * uu, axis=1))])
        Dcr = np.concatenate([[0], np.cumsum(np.sum(inc * nn, axis=1))])
        path_len = float(np.sum(np.linalg.norm(dP, axis=1)))
        dop_len = float(np.sum(np.linalg.norm(integ, axis=1)))
        curves[int(num)] = (s, Dal, Dcr)
        bm = (s >= 3250) & (s < 3900)
        sb = s[bm]
        rec = {"lap": int(num), "pathLenPosM": round(path_len, 2), "pathLenDopplerM": round(dop_len, 2),
               "scalePpm": round((path_len / dop_len - 1) * 1e6, 0),
               "endDiscrepancyM": {"along": round(float(Dal[-1]), 2), "cross": round(float(Dcr[-1]), 2)}}
        for name, Dx in (("along", Dal), ("cross", Dcr)):
            y = Dx[bm]
            A = np.vstack([sb - sb[0], np.ones_like(sb)]).T
            c, *_ = np.linalg.lstsq(A, y, rcond=None)
            r = y - A @ c
            rec[f"band3250_3900_{name}"] = {"trendM_per_100m": round(float(c[0] * 100), 3),
                                            "detrendedMaxAbsM": round(float(np.max(np.abs(r))), 3),
                                            "detrendedRmsM": round(float(np.sqrt(np.mean(r ** 2))), 3),
                                            "netChangeM": round(float(y[-1] - y[0]), 3)}
        out["laps"].append(rec)
    # cross-lap repeatability of the slow discrepancy (cross component), on a station grid
    grid = np.arange(250.0, 4500.0, 5.0)
    Gc = {n: np.interp(grid, c[0], c[2] - np.interp(grid, c[0], c[2]).mean()) for n, c in curves.items()}
    M = np.vstack(list(Gc.values()))
    med = np.median(M, axis=0)
    out["crossLapRepeatability"] = {
        "medianCurveRmsM": round(float(np.sqrt(np.mean(med ** 2))), 3),
        "lapMinusMedianRmsM": {str(n): round(float(np.sqrt(np.mean((Gc[n] - med) ** 2))), 3) for n in Gc},
        "note": "If the slow cross discrepancy is mostly the median curve (same every lap), it is systematic (receiver/geometry/processing), not lap-specific GNSS error."}
    return out


def main():
    probe = json.loads((HERE / "navsol_probe.json").read_text(encoding="utf-8"))
    res = {"script": "navsol_band_check.py", "kind": "measurement on local XRK (no video)", "sessions": {}}
    for race, env in (("fuji_aim_01", "FUJI_WET_XRK"), ("fuji_aim_2020_07_30", "FUJI_DRY_XRK")):
        path = os.environ.get(env, "")
        if not path:
            continue
        tau = probe["sessions"][race]["positionVelocityLag"]["tauStarS"]
        res["sessions"][race] = run(race, path, tau)
    (HERE / "navsol_band_check.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
