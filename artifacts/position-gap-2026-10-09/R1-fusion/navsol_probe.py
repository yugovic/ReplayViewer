"""R1 applicability probe: what does the XRK's own u-blox NAV-SOL stream say about
post-hoc GNSS fusion potential?

Reads the raw 56-byte GPS records (AIM logger timecode + u-blox NAV-SOL payload:
iTOW, ECEF position, pAcc, ECEF Doppler velocity, sAcc, pDOP, numSV) directly from
the XRK byte stream (libxrk only exposes speed/lat/lon; it drops the velocity
vector and iTOW), then measures:

  1. clock: logger timecode vs GPS iTOW (slope ppm, jitter)  -> is the GPS record
     stamped on arrival (jitter) or mapped from iTOW (no jitter)?
  2. position-vs-Doppler consistency: per-epoch increment residual and the
     structure function of (position displacement - integrated Doppler velocity)
     over windows of 0.1..60 s, split into along/cross-track. This is the part of
     the GNSS position error that a velocity/IMU-aided smoother can observe.
  3. per-lap cross-track excursions of that discrepancy after removing slow trends
     (does the known 7/30 L2 3250-3900 m anomaly show up?).
  4. receiver-reported quality (pAcc, numSV, pDOP) along the lap.

Inputs (local, never published): env FUJI_WET_XRK, FUJI_DRY_XRK.
Output: navsol_probe.json next to this script. No video data is used.
Run with the system python that has libxrk (only used for lap boundaries/offset).
"""
import json
import os
import struct
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SESSIONS = {
    "fuji_aim_01": os.environ.get("FUJI_WET_XRK", ""),
    "fuji_aim_2020_07_30": os.environ.get("FUJI_DRY_XRK", ""),
}
LAP_START_STATION_M = 138.05  # lap start lies at centreline station ~138.03-138.08 m (10/02 doc)
ANOMALY = {"fuji_aim_2020_07_30": {"lap": 2, "station": [3250.0, 3900.0]}}


def read_navsol(path):
    b = Path(path).read_bytes()
    recs = []
    bad = 0
    for tok in (b"GPS\x00", b"GPS ", b"GPS1"):
        pat = b"<h" + tok
        i = b.find(pat)
        while i >= 0:
            hlen = struct.unpack_from("<i", b, i + 6)[0]
            ok = 0 < hlen < 1 << 24 and b[i + 11:i + 12] == b">"
            if ok:
                p0 = i + 12
                f = p0 + hlen
                ok = (b[f:f + 1] == b"<" and b[f + 1:f + 5] == tok and b[f + 7:f + 8] == b">"
                      and struct.unpack_from("<H", b, f + 5)[0] == (sum(b[p0:f]) & 0xFFFF)
                      and hlen % 56 == 0)
            if ok:
                recs.append(b[p0:p0 + hlen])
                i = b.find(pat, f + 8)
            else:
                bad += 1
                i = b.find(pat, i + 1)
    raw = b"".join(recs)
    n = len(raw) // 56
    dt = np.dtype([("tc", "<i4"), ("itow", "<u4"), ("ftow", "<i4"), ("week", "<u2"), ("fix", "u1"),
                   ("flags", "u1"), ("x", "<i4"), ("y", "<i4"), ("z", "<i4"), ("pacc", "<u4"),
                   ("vx", "<i4"), ("vy", "<i4"), ("vz", "<i4"), ("sacc", "<u4"), ("pdop", "<u2"),
                   ("res1", "u1"), ("nsv", "u1"), ("res2", "<u4")])
    a = np.frombuffer(raw[:n * 56], dtype=dt)
    a = a[np.argsort(a["tc"], kind="stable")]
    return a, bad


def ecef_to_enu_mats(x0, y0, z0):
    lon = np.arctan2(y0, x0)
    p = np.hypot(x0, y0)
    lat = np.arctan2(z0, p * (1 - 6.69437999014e-3))
    for _ in range(5):
        N = 6378137.0 / np.sqrt(1 - 6.69437999014e-3 * np.sin(lat) ** 2)
        h = p / np.cos(lat) - N
        lat = np.arctan2(z0, p * (1 - 6.69437999014e-3 * N / (N + h)))
    sl, cl, so, co = np.sin(lat), np.cos(lat), np.sin(lon), np.cos(lon)
    R = np.array([[-so, co, 0], [-sl * co, -sl * so, cl], [cl * co, cl * so, sl]])
    return R


def pct(v, q):
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    return float(np.percentile(v, q)) if len(v) else None


def rms(v):
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    return float(np.sqrt(np.mean(v ** 2))) if len(v) else None


def analyse(race, path):
    from libxrk import aim_xrk
    a, bad = read_navsol(path)
    log = aim_xrk(path)
    lib_tc = log.channels["GPS Speed"].column("timecodes").to_numpy().astype(np.int64)
    laps = log.laps.to_pydict()
    out = {"race": race, "records": int(len(a)), "rejectedHeaderCandidates": bad,
           "libxrkGpsRecords": int(len(lib_tc))}
    # --- 1. clock -------------------------------------------------------------------
    t_gps = (a["week"].astype(np.float64) * 604800.0 + a["itow"] * 1e-3 + a["ftow"] * 1e-9)
    tc = a["tc"].astype(np.float64) * 1e-3
    keep = (a["fix"] >= 3)
    t_gps, tc, a = t_gps[keep], tc[keep], a[keep]
    tg0 = t_gps[0]
    A = np.vstack([t_gps - tg0, np.ones_like(t_gps)]).T
    coef, *_ = np.linalg.lstsq(A, tc, rcond=None)
    res = tc - A @ coef
    d_itow = np.diff(t_gps)
    d_tc = np.diff(tc)
    out["clock"] = {
        "loggerVsGpsSlopePpm": float((coef[0] - 1.0) * 1e6),
        "residualStdMs": float(np.std(res) * 1e3),
        "residualP99AbsMs": pct(np.abs(res) * 1e3, 99),
        "residualMaxAbsMs": float(np.max(np.abs(res)) * 1e3),
        "gpsEpochStepMsCounts": {str(k): int(v) for k, v in zip(*np.unique(np.round(d_itow * 1e3), return_counts=True))},
        "loggerStepMsCounts(top)": dict(sorted(((str(k), int(v)) for k, v in zip(*np.unique(np.round(d_tc * 1e3), return_counts=True))), key=lambda kv: -kv[1])[:8]),
        "note": "logger timecode = a + b*GPS time fitted over the session; residual = stamping jitter/drift. Absolute offset is arbitrary (logger clock epoch).",
    }
    # map raw timecode -> libxrk timecode (constant offset)
    lib_off = None
    if len(lib_tc) == len(keep):
        lib_off = float(np.median(a["tc"].astype(np.int64) - lib_tc[keep]))
    else:
        lib_off = float(a["tc"][0] - lib_tc[0])
    out["libxrkTimeOffsetMs"] = lib_off
    tlib = a["tc"].astype(np.float64) - lib_off  # ms, libxrk lap time base
    # --- 2. ENU ----------------------------------------------------------------------
    X = np.vstack([a["x"], a["y"], a["z"]]).astype(np.float64) * 0.01
    V = np.vstack([a["vx"], a["vy"], a["vz"]]).astype(np.float64) * 0.01
    x0 = X.mean(axis=1)
    R = ecef_to_enu_mats(*x0)
    P = (R @ (X - x0[:, None])).T  # E,N,U
    Ve = (R @ V).T
    sp = np.hypot(Ve[:, 0], Ve[:, 1])
    moving = sp > 5.0
    dt = np.diff(t_gps)
    dP = np.diff(P[:, :2], axis=0)
    mv = moving[1:] & moving[:-1] & (np.abs(dt - 0.1) < 0.02)
    yaw = np.degrees(np.unwrap(np.arctan2(Ve[:, 0], Ve[:, 1])))
    yr = np.gradient(yaw, t_gps)  # course rate deg/s from Doppler
    yr_mid = 0.5 * (np.abs(yr[1:]) + np.abs(yr[:-1]))

    def incr(tau):
        ve_i = np.vstack([np.interp(t_gps + tau, t_gps, Ve[:, k]) for k in range(2)]).T
        vb = 0.5 * (ve_i[1:] + ve_i[:-1])
        e = dP - vb * dt[:, None]
        u = vb / np.maximum(np.linalg.norm(vb, axis=1, keepdims=True), 1e-6)
        nrm = np.stack([-u[:, 1], u[:, 0]], axis=1)
        return e, np.sum(e * u, axis=1), np.sum(e * nrm, axis=1)

    # lag scan: velocity sampled at t+tau vs position increment (positive tau = Doppler velocity lags position)
    taus = np.round(np.arange(-0.10, 0.2501, 0.01), 3)
    scan = {}
    for name, sel in (("all", mv), ("straight(|course rate|<3deg/s)", mv & (yr_mid < 3)),
                      ("turning(|course rate|>15deg/s)", mv & (yr_mid > 15))):
        vals = []
        for tau in taus:
            e, ea, ec = incr(tau)
            vals.append((rms(ea[sel]), rms(ec[sel])))
        vals = np.array(vals)
        tot = np.hypot(vals[:, 0], vals[:, 1])
        scan[name] = {"n": int(sel.sum()), "bestTauS": float(taus[np.argmin(tot)]),
                      "rmsAtTau0M": float(tot[np.argmin(np.abs(taus))]), "rmsAtBestM": float(tot.min()),
                      "alongBestTauS": float(taus[np.argmin(vals[:, 0])]), "crossBestTauS": float(taus[np.argmin(vals[:, 1])])}
    tau_star = scan["all"]["bestTauS"]
    out["positionVelocityLag"] = {"scan": scan, "tauStarS": tau_star,
        "note": "tau>0: the motion between epochs k and k+1 is best matched by the Doppler velocity reported tau LATER, i.e. the Doppler velocity stream LAGS the position stream by ~tau. navsol_yaw_lag.py (gyro cross-correlation) shows the positions are the stream aligned with the gyro (within ~+-0.05 s incl. sideslip dynamics)."}

    def hp(x, win):
        k = int(win / 0.1)
        ker = np.ones(k) / k
        pad = np.pad(x, (k // 2, k - 1 - k // 2), mode="edge")
        return x - np.convolve(pad, ker, mode="valid")

    def block(tau):
        e, ea, ec = incr(tau)
        res = {"tauS": tau, "incrementResidual": {
            "epochs": int(mv.sum()), "alongRmsM": rms(ea[mv]), "crossRmsM": rms(ec[mv]),
            "alongP99AbsM": pct(np.abs(ea[mv]), 99), "crossP99AbsM": pct(np.abs(ec[mv]), 99),
            "crossMaxAbsM": float(np.max(np.abs(ec[mv])))}}
        D = np.zeros((len(P), 2))
        D[1:] = np.cumsum(np.where(mv[:, None], e, 0.0), axis=0)
        okc = np.concatenate([[0], np.cumsum(~mv)])
        sf = {}
        for W in (0.5, 1, 2, 5, 10, 20, 30, 60):
            k = int(round(W / 0.1))
            idx = np.arange(len(D) - k)
            ok = (okc[idx + k] - okc[idx]) == 0
            dd = D[idx + k] - D[idx]
            nn = np.stack([-Ve[idx + k, 1], Ve[idx + k, 0]], axis=1) / np.maximum(sp[idx + k, None], 1e-6)
            cr = np.sum(dd * nn, axis=1)
            sf[str(W)] = {"horizRmsM": rms(np.linalg.norm(dd[ok], axis=1)), "crossRmsM": rms(cr[ok]),
                          "crossP95AbsM": pct(np.abs(cr[ok]), 95), "n": int(ok.sum())}
        res["discrepancyStructureFunction"] = sf
        per_lap = []
        station_of = {}
        for num, st, en in zip(laps["num"], laps["start_time"], laps["end_time"]):
            if num == 0:
                continue
            m = (tlib >= st) & (tlib <= en)
            if m.sum() < 600 or (en - st) > 200000:
                continue
            ii = np.where(m)[0]
            if not moving[ii].all():
                continue
            s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(P[ii, :2], axis=0), axis=1))])
            station = s + LAP_START_STATION_M
            Dl = D[ii] - D[ii[0]]
            nn = np.stack([-Ve[ii, 1], Ve[ii, 0]], axis=1) / sp[ii, None]
            cr10 = np.sum(np.stack([hp(Dl[:, 0], 10.0), hp(Dl[:, 1], 10.0)], axis=1) * nn, axis=1)
            cr4 = np.sum(np.stack([hp(Dl[:, 0], 4.0), hp(Dl[:, 1], 4.0)], axis=1) * nn, axis=1)
            station_of[int(num)] = (station, cr10)
            top = np.argsort(-np.abs(cr10))
            peaks = []
            for j in top:
                if all(abs(station[j] - q["stationM"]) > 150 for q in peaks):
                    peaks.append({"stationM": round(float(station[j]), 1), "crossHp10sM": round(float(cr10[j]), 3)})
                if len(peaks) >= 3:
                    break
            rec = {"lap": int(num), "lapTimeS": round((en - st) / 1000, 3), "epochs": int(m.sum()),
                   "crossHp10sRmsM": rms(cr10), "crossHp10sMaxAbsM": float(np.max(np.abs(cr10))),
                   "crossHp4sRmsM": rms(cr4), "crossHp4sMaxAbsM": float(np.max(np.abs(cr4))),
                   "topCrossExcursions": peaks,
                   "pAccM": {"median": pct(a["pacc"][ii] * 0.01, 50), "max": float(a["pacc"][ii].max() * 0.01)},
                   "sAccMps": {"median": pct(a["sacc"][ii] * 0.01, 50), "max": float(a["sacc"][ii].max() * 0.01)},
                   "numSV": {"min": int(a["nsv"][ii].min()), "median": pct(a["nsv"][ii], 50)},
                   "pDOP": {"median": pct(a["pdop"][ii] * 0.01, 50), "max": float(a["pdop"][ii].max() * 0.01)},
                   "diffSolnOffEpochs": int(((a["flags"][ii] & 2) == 0).sum())}
            rec["bands"] = {}
            for lo, hi in [(3250.0, 3900.0), (3900.0, 4500.0), (0.0, 600.0)]:
                bm = (station >= lo) & (station < hi)
                if bm.sum() > 10:
                    rec["bands"][f"{int(lo)}-{int(hi)}"] = {
                        "crossHp10sRmsM": rms(cr10[bm]), "crossHp10sMaxAbsM": float(np.max(np.abs(cr10[bm]))),
                        "pAccMedianM": pct(a["pacc"][ii][bm] * 0.01, 50), "numSVmin": int(a["nsv"][ii][bm].min())}
            an = ANOMALY.get(race)
            if an and an["lap"] == num:
                rec["knownAnomaly"] = an
            per_lap.append(rec)
        # the same corner produces the same systematic residual every lap, so compare each lap
        # with the median of the other laps at the same station
        grid = np.arange(200.0, 4550.0, 1.0)
        G = {n: np.interp(grid, st_, c_) for n, (st_, c_) in station_of.items()}
        for rec in per_lap:
            n = rec["lap"]
            others = [G[k] for k in G if k != n]
            if len(others) < 2:
                continue
            dev = G[n] - np.median(np.vstack(others), axis=0)
            bm = (grid >= 3250) & (grid < 3900)
            rec["vsOtherLapsCrossHp10s"] = {"rmsM": rms(dev), "maxAbsM": float(np.max(np.abs(dev))),
                                            "atStationM": float(grid[np.argmax(np.abs(dev))]),
                                            "band3250_3900": {"rmsM": rms(dev[bm]), "maxAbsM": float(np.max(np.abs(dev[bm])))}}
        res["perLap"] = per_lap
        return res

    out["raw_tau0"] = block(0.0)
    out["lagCompensated"] = block(tau_star)
    # differential-correction flag transitions (NAV-SOL flags bit1 DiffSoln)
    diff = (a["flags"] & 2) != 0
    tr = np.where(diff[1:] != diff[:-1])[0]
    e0, _, _ = incr(tau_star)
    trs = []
    for j in tr[:40]:
        lapn = None
        for num, st, en in zip(laps["num"], laps["start_time"], laps["end_time"]):
            if st <= tlib[j] <= en:
                lapn = int(num)
        trs.append({"tLibS": round(float(tlib[j]) / 1000, 1), "lap": lapn, "to": "on" if diff[j + 1] else "off",
                    "speedMps": round(float(sp[j]), 1),
                    "incrResidualAtTransitionM": round(float(np.linalg.norm(e0[j])), 3) if j < len(e0) else None})
    out["diffSolnTransitions"] = {"count": int(len(tr)), "offEpochs": int((~diff).sum()), "first": trs,
        "note": "u-blox NAV-SOL flags bit1 = DiffSoln (differential corrections, e.g. SBAS, applied)."}
    out["receiverQualitySession"] = {
        "pAccM": {"p5": pct(a["pacc"][moving] * 0.01, 5), "median": pct(a["pacc"][moving] * 0.01, 50), "p95": pct(a["pacc"][moving] * 0.01, 95)},
        "sAccMps": {"median": pct(a["sacc"][moving] * 0.01, 50), "p95": pct(a["sacc"][moving] * 0.01, 95)},
        "numSV": {"min": int(a["nsv"][moving].min()), "median": pct(a["nsv"][moving], 50)},
        "fixTypes": {str(k): int(v) for k, v in zip(*np.unique(a["fix"], return_counts=True))},
        "flagsValues": {str(k): int(v) for k, v in zip(*np.unique(a["flags"], return_counts=True))},
    }
    return out


def main():
    res = {"script": "navsol_probe.py", "kind": "measurement on local XRK (no video)", "sessions": {}}
    for race, path in SESSIONS.items():
        if not path:
            print("skip", race, "(env var not set)")
            continue
        res["sessions"][race] = analyse(race, path)
        print(race, "done")
    (HERE / "navsol_probe.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(res, indent=1, ensure_ascii=False)[:6000])


if __name__ == "__main__":
    sys.exit(main())
