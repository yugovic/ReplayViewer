#!/usr/bin/env python
"""Evaluator's raw-XRK cross-check of the 7/30 channel re-timing (2026-10-02).

Input: xrkraw_<race>.npz dumped by the evaluator from the original .xrk files with
libxrk (logger timecodes in ms; dump script extract_xrk.py kept in the scratchpad,
not in the repo). Path given by env XRKRAW_DIR. Nothing from the implementer's tools.

A. Map every lap JSON t to the XRK timecode: tc = tc0 + 1000 t, tc0 fitted so that the
   lap's GPS speed equals XRK 'GPS Speed' (1 ms refinement).
B. Conversion offset per logger channel: s = argmax_s corr(JSON_ch(t), XRK_ch(tc0+1000t+s)).
   s > 0 -> the JSON shows the XRK value from s ms LATER, i.e. the channel is early on
   the GPS time base and needs a delay of s (same sign as shiftSeconds).
C. Native XRK lags on the logger timeline (no CSV involved): tau = argmax corr(GPSref(tc),
   ch(tc + tau)) for the ECU/IMU channels against GPS-derived references, both sessions.
Output: indep_timing_xrk_results.json next to this script.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.ndimage import gaussian_filter1d

ROOT = Path(r"D:/00_Dev/ReplayViewer")
BASE = ROOT / "artifacts/improvement-eval-2026-10-02/baseline/public/data/races"
CUR = ROOT / "public/data/races"
XR = Path(os.environ["XRKRAW_DIR"])
OUT = Path(__file__).resolve().parent / "indep_timing_xrk_results.json"
RACES = {"fuji_aim_01": 4, "fuji_aim_2020_07_30": 6}
G = 9.80665


def lap(base, race, k):
    return json.loads((base / race / f"osaki_hmr_demio_101_lap_{k:03d}.json").read_text(encoding="utf-8"))


def fit_tc0(z, t, sp):
    gt, gs = z["GPS_Speed__t"], z["GPS_Speed"] * 3.6
    best = (1e18, None)
    for tc in gt[: len(gt) - len(t)]:
        if abs(np.interp(tc, gt, gs) - sp[0]) > 3:
            continue
        e = np.mean((np.interp(tc + 1000 * t, gt, gs) - sp) ** 2)
        if e < best[0]:
            best = (e, tc)
    c = best[1]
    for tc in np.arange(c - 100, c + 100.5, 1.0):
        e = np.mean((np.interp(tc + 1000 * t, gt, gs) - sp) ** 2)
        if e < best[0]:
            best = (e, tc)
    return float(best[1]), float(np.sqrt(best[0]))


def corr_offset(t, y, xt, xv, tc0, smin=-1500, smax=1500, step=2.0):
    """argmax_s Pearson(y(t), x(tc0+1000t+s)); x linearly interpolated (raw samples)."""
    ss = np.arange(smin, smax + step, step)
    yy = (y - y.mean()) / (y.std() + 1e-12)
    cc = []
    for s in ss:
        xx = np.interp(tc0 + 1000 * t + s, xt, xv)
        xx = (xx - xx.mean()) / (xx.std() + 1e-12)
        cc.append(np.mean(yy * xx))
    cc = np.array(cc)
    j = int(np.argmax(np.abs(cc)))
    frac = 0.0
    a = np.abs(cc)
    if 0 < j < len(cc) - 1:
        den = a[j - 1] - 2 * a[j] + a[j + 1]
        if den != 0:
            frac = 0.5 * (a[j - 1] - a[j + 1]) / den
    return float(ss[j] + frac * step), float(cc[j])


def grid(xt, xv, tg, sig_ms=100.0, dt=5.0):
    """Cubic-spline onto tg (ms), then zero-phase Gaussian sigma_ms."""
    m = np.isfinite(xv)
    xt, xv = xt[m], xv[m]
    _, ui = np.unique(xt, return_index=True)
    y = CubicSpline(xt[ui], xv[ui])(tg)
    return gaussian_filter1d(y, sig_ms / dt, mode="nearest")


def xlag(tg, ref, sig, maxlag_ms=1500, trim_ms=2000, dt=5.0, sign=None):
    K, i0 = int(maxlag_ms / dt), int(trim_ms / dt)
    i1 = len(tg) - i0
    a = ref[i0:i1]
    a = (a - a.mean()) / a.std()
    cc = []
    for k in range(-K, K + 1):
        b = sig[i0 + k:i1 + k]
        b = (b - b.mean()) / b.std()
        cc.append(np.mean(a * b))
    cc = np.array(cc)
    if sign is None:
        sign = 1.0 if cc.max() >= -cc.min() else -1.0
    cc *= sign
    j = int(np.argmax(cc))
    frac = 0.0
    if 0 < j < len(cc) - 1:
        den = cc[j - 1] - 2 * cc[j] + cc[j + 1]
        if den:
            frac = 0.5 * (cc[j - 1] - cc[j + 1]) / den
    return float((j - K + frac) * dt / 1000.0), float(cc[j]), sign


MAPS = [  # JSON channel -> XRK channel
    # ECU_LONG_ACC / ECU_LAT_ACC are constant 0 in both XRKs -> not usable
    ("brake", "ECU_BRK_P"), ("steer", "ECU_STEER_ANG"), ("accx", "InlineAcc"),
    ("accy", "LateralAcc"), ("aps", "ECU_PPS"), ("aps", "ECU_TPS"),
]


def main():
    res = {"script": Path(__file__).name, "doc": __doc__, "tc0": {}, "conversion_offset_ms": {}, "native_lag_s": {}}
    signs = {}
    for race, n in RACES.items():
        z = dict(np.load(XR / f"xrkraw_{race}.npz"))
        # ECU_BRK_P is stored as +inf when no pressure is reported -> 0 bar (CSV brake is 0 there too)
        z["ECU_BRK_P"] = np.where(np.isfinite(z["ECU_BRK_P"]), z["ECU_BRK_P"], 0.0)
        res.setdefault("first_timecode_ms", {})[race] = {
            c: float(z[c + "__t"][0]) for c in ("GPS_Speed", "ECU_STEER_ANG", "InlineAcc", "YawRate")}
        sets = [("ref0729", CUR)] if race == "fuji_aim_01" else [("before0730", BASE), ("after0730", CUR)]
        for k in range(1, n + 1):
            d0 = lap(sets[0][1], race, k)
            t, sp = np.asarray(d0["t"]), np.asarray(d0["speed"])
            tc0, rms = fit_tc0(z, t, sp)
            res["tc0"][f"{race}/lap{k}"] = {"tc0_ms": tc0, "speed_rms_kmh": round(rms, 4)}
            for label, base in sets:
                d = lap(base, race, k)
                rec = {}
                for jc, xc in MAPS:
                    s, r = corr_offset(t, np.asarray(d[jc], float), z[xc + "__t"], z[xc], tc0)
                    rec[f"{jc}<-{xc}"] = {"s_ms": round(s, 1), "r": round(r, 3)}
                res["conversion_offset_ms"][f"{label}/lap{k}"] = rec

            # C. native XRK lags on this lap window
            tg = np.arange(tc0, tc0 + 1000 * t[-1], 5.0)
            gt = z["GPS_Speed__t"]
            v = z["GPS_Speed"]
            a_long = np.gradient(v, gt / 1000.0) / G
            la, lo = np.radians(z["GPS_Latitude"]), np.radians(z["GPS_Longitude"])
            x = 6378137.0 * np.cos(np.nanmean(la)) * lo
            y = 6378137.0 * la
            # light centred smoothing of positions (5-pt MA like the pipeline) before heading
            ker = np.ones(5) / 5
            xs, ys = np.convolve(x, ker, "same"), np.convolve(y, ker, "same")
            psi = np.unwrap(np.arctan2(np.gradient(ys, gt), np.gradient(xs, gt)))
            rate = np.gradient(psi, gt / 1000.0)
            ref = {
                "gps_along": grid(gt, a_long, tg), "gps_decel": grid(gt, -a_long, tg),
                "gps_rate": grid(gt, rate, tg), "gps_lat": grid(gt, v * rate / G, tg),
            }
            ch = {c: grid(z[c + "__t"], z[c], tg) for c in
                  ("InlineAcc", "LateralAcc", "ECU_BRK_P", "ECU_STEER_ANG", "ECU_PPS", "YawRate")}
            pairs = [("InlineAcc", "gps_along"),
                     ("LateralAcc", "gps_lat"), ("ECU_BRK_P", "gps_decel"), ("ECU_STEER_ANG", "gps_rate"),
                     ("ECU_PPS", "gps_along"), ("YawRate", "gps_rate")]
            rec = {}
            for c, rk in pairs:
                key = f"{c}_vs_{rk}"
                tau, r, sg = xlag(tg, ref[rk], ch[c], sign=signs.get(key))
                signs.setdefault(key, sg)
                rec[key] = {"tau_s": round(tau, 4), "r": round(r, 3)}
            # logger-internal: ECU (CAN) vs internal IMU
            for c1, c2 in (("InlineAcc", "ECU_BRK_P"), ("YawRate", "ECU_STEER_ANG"), ("YawRate", "LateralAcc")):
                key = f"{c2}_vs_{c1}"
                tau, r, sg = xlag(tg, ch[c1], ch[c2], sign=signs.get(key))
                signs.setdefault(key, sg)
                rec[key] = {"tau_s": round(tau, 4), "r": round(r, 3)}
            res["native_lag_s"][f"{race}/lap{k}"] = rec
    res["signs"] = signs

    # summaries
    def med(prefix, key, field, src):
        v = [src[l][key][field] for l in src if l.startswith(prefix)]
        return (round(float(np.median(v)), 4), round(float(min(v)), 4), round(float(max(v)), 4)) if v else None

    summ = {"conversion_offset_ms": {}, "native_lag_s": {}}
    for label in ("ref0729", "before0730", "after0730"):
        summ["conversion_offset_ms"][label] = {f"{jc}<-{xc}": med(label, f"{jc}<-{xc}", "s_ms", res["conversion_offset_ms"]) for jc, xc in MAPS}
    keys = list(next(iter(res["native_lag_s"].values())).keys())
    for race in RACES:
        summ["native_lag_s"][race] = {k: med(race, k, "tau_s", res["native_lag_s"]) for k in keys}
    res["summary"] = summ
    OUT.write_text(json.dumps(res, indent=1), encoding="utf-8")

    print("tc0:", json.dumps(res["tc0"]))
    print("\nconversion offset s (ms) median [min,max]; s>0 = JSON channel early vs XRK timeline")
    for label, d in summ["conversion_offset_ms"].items():
        print(" ", label, json.dumps(d))
    print("\nper-lap conversion offsets:")
    for l, rec in res["conversion_offset_ms"].items():
        print("  %-16s" % l, " ".join("%s=%+.0f(r%.2f)" % (k, v["s_ms"], v["r"]) for k, v in rec.items()))
    print("\nfirst timecodes (ms):", json.dumps(res["first_timecode_ms"]))
    print("\nper-lap native lags (s):")
    for l, rec in res["native_lag_s"].items():
        print("  %-26s" % l, " ".join("%s=%+.3f(r%.2f)" % (k, v["tau_s"], v["r"]) for k, v in rec.items()))
    print("\nnative XRK lag tau (s) median [min,max]")
    for race, d in summ["native_lag_s"].items():
        print(" ", race)
        for k, v in d.items():
            print("    %-30s %s" % (k, v))


if __name__ == "__main__":
    main()
