"""Body-roll gradient (deg per g) of the car from the logger IMU, for the camera-roll diagnostic of I3.

Reads the two local AiM XRK logs (paths ONLY from env FUJI_WET_XRK / FUJI_DRY_XRK; never copied or
published) with libxrk: RollRate (deg/s), LateralAcc (g), GPS Speed.  Two estimators:
  A  derivative regression  p(t) = a * d(accy)/dt + c       (no drift, no integration)
  B  integrated roll         HP(int p dt) = k * HP(accy) + c  (0.05 Hz high-pass by 20 s moving mean)
Both use 0.5 s moving-mean smoothing (removes kerb vibration > ~2 Hz) and samples with GPS speed
> 60 km/h.  Block bootstrap (10 s blocks) for the 95% interval.

Terrain-bank changes also produce roll rate.  For the two laps that carry video scenes (7/29 L3,
7/30 L2) a third fit C adds the drawn terrain-bank rate (from the runtime paint-line heights,
station(t) by piecewise-linear dist->station from the scene poses) as a second regressor.

Sign: the gyro axis sign is not documented; body roll is taken as outward (physical), so the camera
roll used by lateral_offset.py is droll = +k * accy (+ = camera right side up in right-hand corners,
accy > 0).  Fit C also reports the gyro-vs-terrain sign as a check.
Output: imu-roll-gradient.json next to this file (numbers only).
"""
import json
import os
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("REPLAYVIEWER_ROOT", HERE.parents[2]))
SRC = ROOT / "artifacts" / "video-cg-alignment-2026-10-03"
RNG = np.random.default_rng(7)
XRK = {"fuji_aim_01": os.environ.get("FUJI_WET_XRK", ""), "fuji_aim_2020_07_30": os.environ.get("FUJI_DRY_XRK", "")}
FS = 50.0


def movmean(x, n):
    n = max(1, int(round(n)))
    k = np.ones(n) / n
    return np.convolve(np.pad(x, (n // 2, n - 1 - n // 2), mode="edge"), k, mode="valid")


def load(race):
    from libxrk import aim_xrk

    log = aim_xrk(XRK[race])
    out = {}
    for c in ["RollRate", "LateralAcc", "GPS Speed"]:
        tb = log.channels[c]
        t = tb.column("timecodes").to_numpy().astype(float)
        v = (tb.column(c) if c in tb.column_names else tb.column(1)).to_numpy().astype(float)
        out[c] = (t, v)
    t0, t1 = out["RollRate"][0][0], out["RollRate"][0][-1]
    grid = np.arange(t0, t1, 1000.0 / FS)
    res = {"t": grid}
    for c, (t, v) in out.items():
        ok = np.isfinite(v)
        res[c] = np.interp(grid, t[ok], v[ok])
    if np.nanmax(res["GPS Speed"]) < 100:  # m/s -> km/h
        res["GPS Speed"] = res["GPS Speed"] * 3.6
    return res


def block_boot(X, y, B=1000, block=int(10 * FS)):
    n = len(y)
    nb = max(1, n // block)
    est = []
    for _ in range(B):
        idx = np.concatenate([np.arange(s, min(n, s + block)) for s in RNG.integers(0, n - block, nb)])
        beta, *_ = np.linalg.lstsq(X[idx], y[idx], rcond=None)
        est.append(beta)
    return np.array(est)


def fit(X, y):
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    r2 = 1 - np.sum((y - X @ beta) ** 2) / np.sum((y - y.mean()) ** 2)
    bb = block_boot(X, y)
    return beta, float(r2), bb


def main():
    out = {"method": __doc__.strip().splitlines()[0], "smoothingSeconds": 0.5, "speedMaskKmh": 60, "sessions": {}}
    G = json.loads((SRC / "runtime-geometry.json").read_text(encoding="utf-8"))
    kc = {r: json.loads((ROOT / "public" / "data" / "races" / r / "kerb_contacts.json").read_text(encoding="utf-8"))["timeBase"]
          for r in XRK}
    shots = {s["id"]: s for s in G["shots"]}
    seq = json.loads((SRC / "evidence" / "sequence" / "runtime-poses.json").read_text(encoding="utf-8"))
    cap = json.loads((SRC / "runtime-capture.json").read_text(encoding="utf-8"))
    held = json.loads((SRC / "evidence" / "heldout-boundary-picks.json").read_text(encoding="utf-8"))
    sample_t = {s["id"]: s["views"]["onboard"]["sampleTime"] for s in cap["shots"]}
    sample_t |= {s["id"]: s["afterPlaybackSeconds"] for s in held["shots"]}
    for s in seq["shots"]:
        sample_t[s["id"]] = s["afterPlaybackSeconds"]
        shots[s["id"]] = dict(next(p for p in s["poses"] if p["offset"] == 0), raceId=s["raceId"])
    # terrain bank from runtime paint-line heights (deg, + = left side higher)
    sL = np.array([r["s"] for r in G["lines"]["paintL"]])
    PL = np.array([r["point"] for r in G["lines"]["paintL"]])
    PR = np.array([r["point"] for r in G["lines"]["paintR"]])
    w = np.hypot(PL[:, 0] - PR[:, 0], PL[:, 2] - PR[:, 2])
    bank = np.degrees(np.arctan2(PL[:, 1] - PR[:, 1], w))
    bank = movmean(bank, 40)  # 20 m
    all_k = []
    for race in XRK:
        if not XRK[race] or not Path(XRK[race]).exists():
            out["sessions"][race] = {"error": "XRK path env var not set or missing"}
            continue
        d = load(race)
        p = movmean(d["RollRate"], 0.5 * FS)
        a = movmean(d["LateralAcc"], 0.5 * FS)
        da = np.gradient(a) * FS
        m = d["GPS Speed"] > 60
        X = np.column_stack([da[m], np.ones(m.sum())])
        beta, r2, bb = fit(X, p[m])
        phi = np.cumsum(d["RollRate"]) / FS
        phi_hp = movmean(phi - movmean(phi, 20 * FS), 0.5 * FS)
        a_hp = a - movmean(a, 20 * FS)
        Xb = np.column_stack([a_hp[m], np.ones(m.sum())])
        beta_b, r2b, bbb = fit(Xb, phi_hp[m])
        # sign of LateralAcc vs shipped lap accy (right-hand corners positive)
        tb = kc[race]
        lapmap = tb["lapMapping"]
        sess = {"samples": int(m.sum()),
                "A_derivative": {"slopeDegPerG": float(beta[0]), "r2": r2, "ci95": np.percentile(bb[:, 0], [2.5, 97.5]).tolist()},
                "B_integratedHighPass": {"slopeDegPerG": float(beta_b[0]), "r2": r2b,
                                         "ci95": np.percentile(bbb[:, 0], [2.5, 97.5]).tolist()}}
        # lateral-acc sign check against the shipped lap JSON accy for the lap that has scenes
        lap = 3 if race == "fuji_aim_01" else 2
        lj = json.loads((ROOT / "public" / "data" / "races" / race / f"osaki_hmr_demio_101_lap_{lap:03d}.json").read_text(encoding="utf-8"))
        shift = tb["shiftSecondsByLap"][str(lap)]
        tc0 = lapmap[str(lap)]["tc0Ms"]
        tl = np.asarray(lj["t"])
        tc = (tl - shift) * 1000 + tc0
        a_x = np.interp(tc, d["t"], d["LateralAcc"])
        sess["lateralAccVsLapAccyCorrelation"] = float(np.corrcoef(a_x, lj["accy"])[0, 1])
        # fit C: terrain-bank regressor on this lap
        ids = [i for i, s in shots.items() if s["raceId"] == race and f"-L{lap}-" in i]
        pairs = []
        for i in ids:
            dist = float(np.interp(sample_t[i], tl, lj["dist"]))
            pairs.append((dist, shots[i]["station"]))
        pairs.sort()
        dd = np.array([q[0] for q in pairs])
        ss = np.array([q[1] for q in pairs])
        off = np.median(ss - dd)
        lapdist = np.asarray(lj["dist"])
        station = np.where(lapdist < dd[0], lapdist + (ss[0] - dd[0]),
                           np.where(lapdist > dd[-1], lapdist + (ss[-1] - dd[-1]), np.interp(lapdist, dd, ss)))
        bk = np.interp(station % G["trackLength"], sL, bank)
        bk_rate = np.gradient(movmean(bk, 5)) / np.gradient(tl)
        # XRK samples within the lap
        sel = (d["t"] >= tc[0]) & (d["t"] <= tc[-1]) & m
        brate = np.interp(d["t"][sel], tc, bk_rate)
        Xc = np.column_stack([da[sel], brate, np.ones(sel.sum())])
        beta_c, r2c, bbc = fit(Xc, p[sel])
        sess["C_lapWithTerrain"] = {"lap": lap, "slopeDegPerG": float(beta_c[0]), "terrainRateCoef": float(beta_c[1]),
                                    "r2": r2c, "ci95Slope": np.percentile(bbc[:, 0], [2.5, 97.5]).tolist(),
                                    "ci95TerrainCoef": np.percentile(bbc[:, 1], [2.5, 97.5]).tolist(),
                                    "samples": int(sel.sum()), "medianStationMinusDist": float(off),
                                    "note": "terrainRateCoef ~ +1 or -1 tells the gyro sign relative to the bank convention (+ = left side higher)"}
        Xc0 = np.column_stack([da[sel], np.ones(sel.sum())])
        beta_c0, r2c0, _ = fit(Xc0, p[sel])
        sess["C_lapWithoutTerrain"] = {"slopeDegPerG": float(beta_c0[0]), "r2": r2c0}
        out["sessions"][race] = sess
        all_k.append(-beta_c[0] / beta_c[1])
        print(race, json.dumps(sess, indent=1))
    if all_k:
        out["adoptedRollGradientDegPerG"] = float(np.mean(all_k))
        out["perSessionCameraRollGradient"] = [float(v) for v in all_k]
        out["adoptedRule"] = ("camera roll gradient k = -(slope / terrainRateCoef) of fit C (terrain-corrected, gyro sign "
                              "taken from the terrain coefficient), mean of the two scene laps; droll = +k*accy. "
                              "Low r2: the roll-rate signal is dominated by vibration/terrain; treat k as an order of magnitude.")
    (HERE / "imu-roll-gradient.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
